"""Real persistence, source boundaries and explicit AI handoff acceptance."""
from contextlib import closing
import base64
import copy
import gc
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from blueprint_library import backup
from blueprint_library.agents import Agents, validate_manifest
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import Store, LibraryError, SCHEMA_VERSION
from blueprint_library.studio import Studio


def sample_manifest():
    return {'contract_version': 1, 'id': 'summary-test', 'name': '摘要測試 Agent', 'version': '1.0.0',
            'description': '只使用指定資料。', 'adapter': 'connected_client', 'instructions': '由目前 AI 對話整理指定內容，不編造資料。',
            'settings': [{'key': 'language', 'label': '語言', 'type': 'select', 'default': 'zh-Hant',
                          'options': [{'value': 'zh-Hant', 'label': '繁體中文'}, {'value': 'en', 'label': 'English'}]}],
            'operations': [{'id': 'summarize', 'name': '整理摘要', 'instructions': '保留來源，沒有資料時標為失敗。',
                            'input_fields': [{'key': 'request', 'label': '要求', 'type': 'textarea', 'default': '整理重點。'}]}],
            'ui': {'layout': 'standard', 'result_label': '摘要結果'}}


def package():
    return {'manifest': sample_manifest(), 'source': {'repository': 'https://github.com/example/summary-test', 'revision': 'a' * 40}}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-agent-test-')
        self.addCleanup(self.temp.cleanup)
        # Release SQLite context-manager cycles before Windows removes fixtures.
        self.addCleanup(gc.collect)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'private'
        initialize_library(self.directory, 'owner', 'only-for-agent-tests')
        self.store = Store(self.directory)
        self.service = Agents(self.store)
        self.studio = Studio(self.store)
        self.member = Agents(self.store, {'username': 'staff', 'role': 'member', 'name': '同事'})
        self.agent = self.service.register(package())['agent']

    def error(self, status, callback, *args, **kwargs):
        with self.assertRaises(LibraryError) as raised:
            callback(*args, **kwargs)
        self.assertEqual(raised.exception.status, status, str(raised.exception))

    def connect(self):
        self.agent = self.service.agent_action(self.agent['id'], {'expected_version': self.agent['version'], 'action': 'connect'})

    def job(self, refs=None, **changes):
        return self.service.create_job({'agent_id': self.agent['id'], 'operation_id': 'summarize', 'title': '整理指定資料',
                                        'inputs': {}, 'refs': refs or [], **changes})['job']

    def action(self, job, action, **arguments):
        return self.service.job_action(job['id'], {'expected_version': job['version'], 'action': action, 'arguments': arguments})

    def start(self, job):
        context = self.service.context(job['id'])
        return self.action(job, 'start', source='測試客戶端', context_digest=context['context_digest'])

    def complete(self, job, **changes):
        return self.action(job, 'complete', **{'source': '測試客戶端', 'context_digest': job['context_digest'],
                                             'result': '由測試客戶端寫入的核對結果。', 'source_refs': job['refs'], **changes})

    def test_declared_package_is_not_automatically_running_and_rejects_code(self):
        self.assertEqual(self.agent['connection_state'], 'added')
        self.assertEqual(self.service.list_jobs()['total'], 0)
        job = self.job()
        self.error(409, self.start, job)
        self.assertEqual(self.service.get_job(job['id'])['state'], 'awaiting_codex')
        self.connect()
        self.assertEqual(self.agent['connection_state'], 'connected')
        invalid = sample_manifest()
        invalid['ui']['script'] = 'fetch("https://example.com")'
        self.error(400, validate_manifest, invalid)
        for revision in ['main', 'v1.0.0', 'a' * 39]:
            invalid = package()
            invalid['source']['revision'] = revision
            self.error(400, self.service.register, invalid)

    def test_register_is_idempotent_and_fixed_source_cannot_change_manifest(self):
        again = self.service.register(package())
        self.assertFalse(again['created'])
        self.assertEqual(again['agent']['id'], self.agent['id'])
        invalid = package()
        invalid['manifest']['instructions'] = '不同內容'
        self.error(409, self.service.register, invalid)
        self.assertEqual(self.service.list_agents()['total'], 1)

    def test_admin_only_settings_version_conflict_and_unknown_fields(self):
        self.error(403, self.member.register, package())
        self.error(403, self.member.update_agent, self.agent['id'], {'expected_version': 1, 'enabled': False})
        self.agent = self.service.update_agent(self.agent['id'], {'expected_version': 1, 'settings': {'language': 'en'}})
        self.error(409, self.service.update_agent, self.agent['id'], {'expected_version': 1, 'settings': {'language': 'zh-Hant'}})
        self.error(400, self.service.update_agent, self.agent['id'], {'expected_version': 2, 'settings': {'arbitrary': 'value'}})
        self.error(400, self.service.update_agent, self.agent['id'], {'expected_version': 2, 'enabled': 'true'})
        self.assertEqual(self.service.get_agent(self.agent['id'])['settings'], {'language': 'en'})

    def upgrade_request(self, **changes):
        manifest = sample_manifest()
        manifest['version'] = '1.1.0'
        manifest['instructions'] = '新版仍只使用已選定資料，保留來源。'
        manifest['settings'].append({'key': 'detail', 'label': '詳細程度', 'type': 'text', 'default': '簡潔'})
        return {'expected_version': self.service.get_agent(self.agent['id'])['version'],
                'upgrade_key': 'package-upgrade-001', 'manifest': manifest,
                'source': {**package()['source'], 'revision': 'b' * 40}, **changes}

    def test_package_upgrade_keeps_same_id_company_data_jobs_and_pinned_snapshots(self):
        attachment = self.store.add_file('retained.txt', b'retained attachment')
        original_login = (self.directory / 'access.json').read_bytes()
        self.agent = self.service.update_agent(self.agent['id'], {'expected_version': 1, 'settings': {'language': 'en'}})
        self.connect()
        pending = self.job(title='保留待辦工作')
        finished = self.complete(self.start(self.job(title='保留已完成工作')))
        old = self.service.get_agent(self.agent['id'])
        history = self.service.history('agent', old['id'])
        upgraded = self.service.update_agent(old['id'], self.upgrade_request())
        for key in ['id', 'created_at', 'enabled', 'connection_state']:
            self.assertEqual(upgraded[key], old[key])
        self.assertEqual(upgraded['settings'], {'language': 'en', 'detail': '簡潔'})
        self.assertEqual(upgraded['version'], old['version'] + 1)
        self.assertEqual(upgraded['source']['revision'], 'b' * 40)
        self.assertEqual(self.service.list_agents()['total'], 1)
        self.assertEqual(self.service.get_job(pending['id']), pending)
        self.assertEqual(self.service.get_job(finished['id']), finished)
        self.assertEqual(self.service.context(pending['id'])['agent']['source'], old['source'])
        self.assertEqual(self.job()['agent_snapshot']['manifest']['version'], '1.1.0')
        self.assertEqual(self.service.history('agent', old['id'])[1:], history)
        self.assertEqual(self.store.get_file(attachment['id'])[0].read_bytes(), b'retained attachment')
        self.assertEqual((self.directory / 'access.json').read_bytes(), original_login)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
        # The current package key follows the same registration, not a new card.
        self.assertEqual(self.service.register({'manifest': self.upgrade_request()['manifest'],
                                               'source': upgraded['source']})['agent']['id'], old['id'])

    def test_package_upgrade_retry_survives_restart_backup_and_later_setting_changes(self):
        request = self.upgrade_request(settings={'language': 'en'})
        upgraded = self.service.update_agent(self.agent['id'], request)
        self.service = Agents(Store(self.directory))
        retry = {**request, 'expected_version': upgraded['version']}
        self.assertEqual(self.service.update_agent(self.agent['id'], retry), upgraded)
        current = self.service.update_agent(self.agent['id'], {'expected_version': upgraded['version'],
                                             'settings': {'language': 'zh-Hant', 'detail': '完整'}})
        self.assertEqual(self.service.update_agent(self.agent['id'], request), upgraded)
        self.assertEqual(self.service.get_agent(self.agent['id']), current)
        self.error(409, self.service.update_agent, self.agent['id'], {**retry, 'settings': {'language': 'zh-Hant'}})
        snapshot, restored = self.root / 'upgrade-backup', self.root / 'upgrade-restored'
        backup.snapshot(self.directory, snapshot)
        backup.restore(snapshot, restored)
        restored_service = Agents(Store(restored))
        self.assertEqual(restored_service.update_agent(self.agent['id'], request), upgraded)
        self.assertEqual(restored_service.get_agent(self.agent['id']), current)

    def test_package_upgrade_rejects_permission_identity_source_and_incomplete_requests(self):
        request = self.upgrade_request()
        old = self.service.get_agent(self.agent['id'])
        self.error(403, self.member.update_agent, old['id'], request)
        for missing in ['manifest', 'source', 'upgrade_key']:
            invalid = {key: value for key, value in request.items() if key != missing}
            self.error(400, self.service.update_agent, old['id'], invalid)
        for invalid_key in ['', '../key', 'x' * 101, True]:
            self.error(400, self.service.update_agent, old['id'], {**request, 'upgrade_key': invalid_key})
        self.error(400, self.service.update_agent, old['id'], {**request, 'enabled': True})
        self.error(400, self.service.update_agent, old['id'], {**request, 'expected_version': True})
        wrong = copy.deepcopy(request); wrong['manifest']['id'] = 'different-agent'
        self.error(409, self.service.update_agent, old['id'], wrong)
        wrong = copy.deepcopy(request); wrong['source']['repository'] = 'https://github.com/example/different'
        self.error(409, self.service.update_agent, old['id'], wrong)
        wrong = copy.deepcopy(request); wrong['source']['revision'] = 'main'
        self.error(400, self.service.update_agent, old['id'], wrong)
        self.error(409, self.service.update_agent, old['id'], {**request, 'source': old['source']})
        self.error(409, self.service.update_agent, old['id'], {**request, 'expected_version': old['version'] + 1})
        self.assertEqual(self.service.get_agent(old['id']), old)

    def test_package_upgrade_retains_settings_and_refuses_silent_loss(self):
        old = self.service.update_agent(self.agent['id'], {'expected_version': 1, 'enabled': False,
                                                         'settings': {'language': 'en'}})
        for change in ['removed', 'type', 'options']:
            invalid = self.upgrade_request()
            if change == 'removed': invalid['manifest']['settings'] = []
            elif change == 'type': invalid['manifest']['settings'][0] = {'key': 'language', 'label': '語言', 'type': 'text'}
            else: invalid['manifest']['settings'][0]['options'] = [{'value': 'zh-Hant', 'label': '繁體中文'}]
            self.error(409, self.service.update_agent, old['id'], invalid)
            self.assertEqual(self.service.get_agent(old['id']), old)
        request = self.upgrade_request()
        request['manifest']['settings'].append({'key': 'new_required', 'label': '新版必要設定', 'type': 'text', 'required': True})
        self.error(409, self.service.update_agent, old['id'], request)
        upgraded = self.service.update_agent(old['id'], {**request, 'settings': {'new_required': '明確提供'}})
        self.assertFalse(upgraded['enabled'])
        self.assertEqual(upgraded['connection_state'], 'added')
        self.assertEqual(upgraded['settings']['language'], 'en')
        self.assertEqual(upgraded['settings']['new_required'], '明確提供')

    def test_package_upgrade_collision_and_failed_history_write_roll_back_everything(self):
        request = self.upgrade_request()
        other = self.service.register({'manifest': request['manifest'], 'source': request['source']})['agent']
        original = self.service.get_agent(self.agent['id'])
        self.error(409, self.service.update_agent, original['id'], request)
        self.assertNotEqual(original['id'], other['id'])
        self.assertEqual(self.service.get_agent(original['id']), original)
        request['source']['revision'] = 'c' * 40
        history = self.service.history('agent', original['id'])
        save_history = self.service._history
        def interrupted(conn, entity, record):
            if entity == 'agent_upgrade': raise RuntimeError('synthetic receipt interruption')
            return save_history(conn, entity, record)
        with patch.object(self.service, '_history', side_effect=interrupted):
            with self.assertRaises(RuntimeError): self.service.update_agent(original['id'], request)
        self.assertEqual(self.service.get_agent(original['id']), original)
        self.assertEqual(self.service.history('agent', original['id']), history)
        self.assertEqual(self.service.update_agent(original['id'], request)['id'], original['id'])

    def test_concurrent_package_upgrade_retries_commit_once(self):
        request = self.upgrade_request()
        def attempt(_):
            return Agents(self.store).update_agent(self.agent['id'], request)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt, range(8)))
        self.assertTrue(all(record == results[0] for record in results))
        self.assertEqual(results[0]['version'], self.agent['version'] + 1)
        self.assertEqual(len(self.service.history('agent', self.agent['id'])), 2)

    def test_package_upgrade_cannot_rewrite_a_historical_source_pin(self):
        original = self.service.get_agent(self.agent['id'])
        upgraded = self.service.update_agent(original['id'], self.upgrade_request())
        changed = copy.deepcopy(original['manifest'])
        changed['version'] = '2.0.0'
        changed['instructions'] = '不應該改寫舊 commit 的內容。'
        self.error(409, self.service.update_agent, original['id'], {
            'expected_version': upgraded['version'], 'upgrade_key': 'rewrite-old-pin',
            'manifest': changed, 'source': original['source']})
        self.error(409, self.service.register, {'manifest': changed, 'source': original['source']})
        self.assertEqual(self.service.get_agent(original['id']), upgraded)
        self.assertEqual(self.service.list_agents()['total'], 1)

    def test_job_pins_settings_and_manifest_then_preserves_actual_result_on_restart(self):
        source = self.studio.create({'kind': 'report', 'title': '資料', 'data': {'content': '星期一完成設計。', 'source': '手動測試'}})['record']
        refs = [{'kind': 'report', 'id': source['id']}]
        self.connect()
        job = self.job(refs)
        self.agent = self.service.update_agent(self.agent['id'], {'expected_version': self.agent['version'], 'settings': {'language': 'en'}})
        context = self.service.context(job['id'])
        self.assertEqual(context['agent']['settings']['language'], 'zh-Hant')
        self.assertEqual(context['sources'][0]['data']['content'], '星期一完成設計。')
        self.error(409, self.complete, job, context_digest=context['context_digest'])
        job = self.start(job)
        job = self.complete(job, result='設計於星期一完成。')
        self.assertEqual(job['state'], 'completed')
        self.assertEqual(job['source_refs'], refs)
        restarted = Agents(Store(self.directory))
        self.assertEqual(restarted.get_job(job['id']), job)
        self.assertEqual(len(restarted.history('job', job['id'])), 3)
        self.error(409, self.complete, job)
        self.error(409, self.action, job, 'cancel')

    def test_same_source_key_deduplicates_but_different_input_conflicts(self):
        payload = {'agent_id': self.agent['id'], 'operation_id': 'summarize', 'title': '一次工作', 'inputs': {}, 'refs': [], 'source_key': 'repeat-test'}
        first = self.service.create_job(payload)
        second = self.service.create_job(payload)
        self.assertTrue(first['created'])
        self.assertFalse(second['created'])
        self.assertEqual(first['job'], second['job'])
        self.error(409, self.service.create_job, {**payload, 'title': '不可覆蓋'})
        self.assertEqual(self.service.list_jobs()['total'], 1)

    def test_concurrent_start_has_one_winner_and_preserves_single_work_history(self):
        self.connect()
        job = self.job()
        context = self.service.context(job['id'])
        def attempt():
            try:
                return self.action(job, 'start', source='測試客戶端', context_digest=context['context_digest'])['state']
            except LibraryError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(results, ['in_progress', 409])
        self.assertEqual(len(self.service.history('job', job['id'])), 2)

    def test_reference_ids_are_normalized_before_storage_and_context_resolution(self):
        self.connect()
        item = self.store.create_item({'title': '原稿', 'url': 'https://example.com', 'notes': '可引用的內容'})
        padded = [{'kind': 'item', 'id': ' ' + item['id'] + ' '}]
        job = self.start(self.job(padded))
        self.assertEqual(job['refs'], [{'kind': 'item', 'id': item['id']}])
        self.assertEqual(job['source_versions'][0]['title'], '原稿')
        finished = self.complete(job, source_refs=padded)
        self.assertEqual(finished['source_refs'], job['refs'])

    def test_lists_are_compact_and_pagination_keeps_only_authorized_summaries(self):
        payload = package()
        payload['manifest']['id'] = 'large-package'
        payload['manifest']['instructions'] = '說明' * 7500
        payload['manifest']['description'] = '介紹' * 600
        large = self.service.register(payload)['agent']
        self.service.update_agent(large['id'], {'expected_version': large['version'], 'settings': {'language': 'en'}})
        self.connect()
        for index in range(6):
            job = self.start(self.job(title='摘要' + str(index), inputs={'request': 'x' * 36000}))
            self.complete(job, result='結果' * 18000)
        listing = self.service.list_jobs(limit=2, offset=1)
        self.assertEqual((listing['total'], len(listing['jobs'])), (6, 2))
        self.assertLess(len(json.dumps(listing).encode()), 16000)
        for summary in listing['jobs']:
            self.assertNotIn('inputs', summary)
            self.assertNotIn('settings', summary['agent_snapshot'])
            self.assertEqual(len(summary['result']), 500)
            self.assertIn('result', summary['truncated_fields'])
            self.assertGreater(len(self.service.get_job(summary['id'])['result']), 500)
        registrations = self.service.list_agents()
        self.assertLess(len(json.dumps(registrations).encode()), 16000)
        for summary in registrations['agents']:
            self.assertNotIn('instructions', summary['manifest'])
            self.assertNotIn('settings', summary)
            self.assertTrue(summary['detail_available'])

    def test_source_edits_after_context_or_start_cannot_complete_stale_work(self):
        source = self.store.create_item({'title': '來源', 'url': 'https://example.com', 'notes': '原稿'})
        self.connect()
        job = self.job([{'kind': 'item', 'id': source['id']}])
        context = self.service.context(job['id'])
        source = self.store.update_item(source['id'], {'expected_version': source['version'], 'notes': '修改稿'})
        self.error(409, self.action, job, 'start', source='測試客戶端', context_digest=context['context_digest'])
        job = self.start(job)
        self.store.update_item(source['id'], {'expected_version': source['version'], 'notes': '再修改'})
        self.error(409, self.complete, job)
        current_context = self.service.context(job['id'])
        self.error(409, self.complete, job, context_digest=current_context['context_digest'])
        job = self.action(job, 'fail', reason='來源在處理期間有更新，需要重新建立工作。')
        self.assertEqual(job['state'], 'failed')
        self.assertEqual(job['result'], '')

    def test_results_require_real_content_client_and_selected_source_refs(self):
        first = self.studio.create({'kind': 'project', 'title': '指定項目', 'data': {}})['record']
        other = self.studio.create({'kind': 'project', 'title': '無關項目', 'data': {}})['record']
        self.connect()
        job = self.start(self.job([{'kind': 'project', 'id': first['id']}]))
        self.error(400, self.complete, job, result='')
        self.error(409, self.complete, job, source='另一個客戶端')
        self.error(400, self.complete, job, source_refs=[])
        self.error(400, self.complete, job, source_refs=[{'kind': 'project', 'id': other['id']}])
        self.assertEqual(self.service.get_job(job['id'])['state'], 'in_progress')
        self.assertEqual(self.complete(job)['state'], 'completed')

    def test_private_source_hides_jobs_context_history_results_and_counts(self):
        source = self.studio.create({'kind': 'report', 'title': '私密原稿', 'data': {'content': '私人資料', 'source': '手動'}, 'visibility': 'admin'})['record']
        private = self.job([{'kind': 'report', 'id': source['id']}])
        public = self.job(title='可見工作')
        self.error(403, self.member.get_job, private['id'])
        self.error(403, self.member.context, private['id'])
        self.error(403, self.member.history, 'job', private['id'])
        listing = self.member.list_jobs(limit=1)
        self.assertEqual(listing['total'], 1)
        self.assertEqual(listing['jobs'][0]['id'], public['id'])
        self.error(403, self.member.create_job, {'agent_id': self.agent['id'], 'operation_id': 'summarize', 'title': '越權', 'inputs': {}, 'refs': [{'kind': 'report', 'id': source['id']}]})

    def test_source_becoming_private_revokes_existing_job_and_result_access(self):
        source = self.studio.create({'kind': 'report', 'title': '原先公開', 'data': {'content': '資料', 'source': '手動'}})['record']
        self.connect()
        job = self.complete(self.start(self.job([{'kind': 'report', 'id': source['id']}])) )
        self.assertEqual(self.member.get_job(job['id'])['state'], 'completed')
        private = self.studio.create({'kind': 'project', 'title': '私人項目', 'data': {}, 'visibility': 'admin'})['record']
        self.studio.update(source['id'], {'expected_version': source['version'], 'refs': [{'kind': 'project', 'id': private['id']}]})
        self.error(403, self.member.get_job, job['id'])
        self.error(403, self.member.history, 'job', job['id'])
        self.assertEqual(self.member.list_jobs()['total'], 0)
        self.assertEqual(self.service.get_job(job['id'])['result'], job['result'])

    def test_disconnect_during_work_stops_completion_but_allows_cancel(self):
        self.connect()
        job = self.start(self.job())
        self.agent = self.service.agent_action(self.agent['id'], {'expected_version': self.agent['version'], 'action': 'disconnect'})
        self.error(409, self.complete, job)
        cancelled = self.action(job, 'cancel')
        self.assertEqual(cancelled['state'], 'cancelled')
        self.assertEqual(cancelled['result'], '')
        self.error(409, self.action, cancelled, 'start', source='測試客戶端', context_digest='fake')

    def test_failed_and_duplicate_start_preserve_progress_without_fake_success(self):
        self.connect()
        waiting = self.job()
        started = self.start(waiting)
        self.error(409, self.start, waiting)
        self.error(400, self.action, started, 'fail', reason='')
        self.assertEqual(self.service.get_job(started['id'])['state'], 'in_progress')
        failed = self.action(started, 'fail', reason='目前對話沒有讀取附件的工具。')
        self.assertEqual(failed['state'], 'failed')
        self.assertTrue(failed['error'])
        self.assertEqual(failed['result_source'], '')

    def test_manifest_nested_types_limits_and_inputs_reject_invalid_shapes(self):
        invalids = []
        item = sample_manifest(); item['settings'][0]['type'] = []; invalids.append(item)
        item = sample_manifest(); item['settings'][0]['key'] = 'constructor'; invalids.append(item)
        item = sample_manifest(); item['contract_version'] = True; invalids.append(item)
        item = sample_manifest(); item['operations'] *= 17; invalids.append(item)
        item = sample_manifest(); item['adapter'] = 'shell'; invalids.append(item)
        item = sample_manifest(); item['instructions'] = 'x' * 16001; invalids.append(item)
        item = sample_manifest(); item['settings'][0]['default'] = 'missing'; invalids.append(item)
        for manifest in invalids:
            self.error(400, validate_manifest, manifest)
        self.error(400, self.job, inputs={'request': 42})
        self.error(400, self.job, inputs={'request': 'x' * 40001})
        self.error(400, self.job, inputs={'unsupported': 'value'})
        self.assertEqual(self.service.list_jobs()['total'], 0)

    def _make_v5(self):
        with closing(sqlite3.connect(self.store.db_path)) as conn, conn:
            for table in ['agent_history', 'agent_jobs', 'agent_registrations']:
                conn.execute('DROP TABLE ' + table)
            conn.execute('PRAGMA user_version = 5')

    def test_true_v5_upgrade_keeps_records_login_and_attachments(self):
        item = self.store.add_file('original.txt', b'original attachment')
        report = self.studio.create({'kind': 'report', 'title': '保留成果', 'data': {'content': '既有內容', 'source': '人工'}})['record']
        auth = (self.directory / 'access.json').read_bytes()
        self._make_v5()
        upgraded = Store(self.directory)
        with upgraded._connection() as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
        self.assertEqual(Studio(upgraded).get(report['id']), report)
        self.assertEqual(upgraded.get_file(item['id'])[0].read_bytes(), b'original attachment')
        self.assertEqual((self.directory / 'access.json').read_bytes(), auth)
        self.assertEqual(Agents(upgraded).list_agents()['total'], 0)

    def test_failed_additive_migration_rolls_back_tables_and_schema(self):
        self._make_v5()
        def interrupted(conn):
            conn.execute('CREATE TABLE agent_partial (id TEXT PRIMARY KEY)')
            raise RuntimeError('test interruption')
        with patch('blueprint_library.agents.initialize', interrupted):
            with self.assertRaises(RuntimeError):
                Store(self.directory)
        with closing(sqlite3.connect(self.store.db_path)) as conn, conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 5)
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='agent_partial'").fetchone())
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='agent_jobs'").fetchone())

    def test_backup_restore_includes_packages_jobs_and_history(self):
        self.connect()
        job = self.complete(self.start(self.job()))
        snapshot = self.root / 'snapshot'
        restored_path = self.root / 'restored'
        backup.snapshot(self.directory, snapshot)
        backup.restore(snapshot, restored_path)
        restored = Agents(Store(restored_path))
        self.assertEqual(restored.get_job(job['id']), job)
        self.assertEqual(restored.history('job', job['id']), self.service.history('job', job['id']))
        self.assertEqual(restored.get_agent(self.agent['id']), self.agent)

    def test_http_uses_existing_auth_same_store_and_write_guard(self):
        server = make_server(self.directory, '127.0.0.1', 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = 'http://127.0.0.1:' + str(server.server_port)
        def request(method, path, data=None, authenticated=True, marker=True):
            headers = {'Content-Type': 'application/json'}
            if authenticated:
                headers['Authorization'] = 'Basic ' + base64.b64encode(b'owner:only-for-agent-tests').decode()
            if marker:
                headers['X-Blueprint-Request'] = '1'
            req = Request(origin + path, data=json.dumps(data).encode() if data is not None else None, method=method, headers=headers)
            try:
                with urlopen(req, timeout=5) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, error.read()
        status, listing = request('GET', '/api/studio/agents')
        self.assertEqual(status, 200)
        self.assertEqual(listing['agents'][0]['id'], self.agent['id'])
        self.assertEqual(request('GET', '/api/studio/agents', authenticated=False)[0], 401)
        self.assertEqual(request('PATCH', '/api/studio/agents/' + self.agent['id'], {'expected_version': 1, 'enabled': False}, marker=False)[0], 403)
        status, result = request('PATCH', '/api/studio/agents/' + self.agent['id'], {'expected_version': 1, 'settings': {'language': 'en'}})
        self.assertEqual(status, 200)
        self.assertEqual(self.service.get_agent(self.agent['id']), result['agent'])
        self.assertEqual(request('GET', '/api/studio/agent-jobs?limit=bad')[0], 400)


if __name__ == '__main__':
    unittest.main()
