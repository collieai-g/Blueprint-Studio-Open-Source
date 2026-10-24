"""Durability and trust boundaries for external services, using fictional data."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from blueprint_library import backup
from blueprint_library.integrations import Integrations, authenticate
from blueprint_library.server import initialize_library
from blueprint_library.store import LibraryError, Store
from blueprint_library.studio import Studio


def registration(**changes):
    return {'name': '虛構外部服務', 'version': '0.1.0',
            'record_types': ['thread', 'message', 'work_item', 'artifact', 'note', 'status', 'knowledge'],
            'commands': [{'name': 'take_over', 'label': '我接手',
                          'input_schema': {'type': 'object', 'properties': {'reason': {'type': 'string', 'title': '原因', 'maxLength': 200}},
                                           'required': ['reason'], 'additionalProperties': False}}],
            'resource_refs': [], 'allowed_origins': ['https://example.com'], **changes}


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-external-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'private'
        initialize_library(self.directory, 'owner', 'fictional-test-password')
        self.store = Store(self.directory)
        self.admin = Integrations(self.store)
        result = self.admin.register(registration())
        self.integration = result['integration']
        self.token = result['token']
        self.external = Integrations(self.store, integration_id=self.integration['id'])

    def error(self, status, callback, *args, **kwargs):
        with self.assertRaises(LibraryError) as raised:
            callback(*args, **kwargs)
        self.assertEqual(raised.exception.status, status, str(raised.exception))

    def record(self, **changes):
        payload = {'schema_version': '1.0', 'record_type': 'message', 'external_id': 'fictional-message-1',
                   'event_id': 'fictional-event-1', 'source_revision': 1, 'source': 'fictional-service',
                   'occurred_at': '2026-10-24T00:00:00Z', 'payload': {'text': '測試訊息', 'sender_type': 'customer', 'direction': 'inbound'},
                   'idempotency_key': 'fictional-record-1', **changes}
        return self.external.write_record(payload)

    def command(self, **changes):
        return self.admin.create_command({'integration_id': self.integration['id'], 'name': 'take_over',
                                         'payload': {'reason': '測試接管'}, 'idempotency_key': 'fictional-command-1', **changes})

    def result(self, command, status='accepted', key='result-1', **changes):
        return self.external.command_result({'schema_version': '1.0', 'command_id': command['command_id'],
                                             'event_id': 'event-' + key, 'idempotency_key': key, 'status': status,
                                             'occurred_at': '2026-10-24T00:01:00Z', 'payload': {'evidence': '虛構執行證據'}, **changes})

    def test_credentials_are_hashed_bound_and_revocable(self):
        self.assertEqual(authenticate(self.store, self.token), self.integration['id'])
        with self.store._connection() as conn:
            stored = conn.execute('SELECT token_hash,document FROM integration_registry').fetchone()
        self.assertNotIn(self.token, ''.join(stored))
        other_path = self.root / 'other-company'
        initialize_library(other_path, 'other', 'another-test-password')
        self.error(401, authenticate, Store(other_path), self.token)
        revoked = self.admin.revoke(self.integration['id'], {'expected_version': 1})
        self.assertTrue(revoked['revoked'])
        self.error(401, authenticate, self.store, self.token)
        self.error(403, self.external.list_records)
        self.error(409, self.admin.update_integration, self.integration['id'], {'expected_version': 2, 'enabled': True})

    def test_external_identity_cannot_manage_or_claim_a_company(self):
        self.error(403, self.external.register, registration())
        self.error(403, self.external.list_integrations)
        self.error(403, self.external.get_integration, self.integration['id'])
        self.error(403, self.external.create_command, {})
        self.error(400, self.record, tenant_id='other')
        self.error(403, Integrations(self.store, {'username': 'staff', 'role': 'member'}).list_integrations)

    def test_instances_and_record_type_scopes_are_isolated(self):
        first = self.record()['record']
        other = self.admin.register(registration(record_types=['note']))['integration']
        external = Integrations(self.store, integration_id=other['id'])
        self.assertEqual(external.list_records()['total'], 0)
        self.error(403, external.get_record, first['id'])
        self.error(403, external.history, first['id'])
        self.error(403, external.list_records, integration_id=self.integration['id'])
        self.error(403, external.write_record, {'schema_version': '1.0', 'record_type': 'message', 'external_id': 'same', 'event_id': 'other', 'source_revision': 1, 'source': 'other', 'occurred_at': '2026-10-24T00:00:00Z', 'payload': {}, 'idempotency_key': 'other'})
        self.admin.update_integration(self.integration['id'], {'expected_version': 1, 'record_types': ['note']})
        self.error(403, self.external.get_record, first['id'])
        self.assertEqual(self.external.list_records()['total'], 0)

    def test_idempotency_is_atomic_and_never_changes_existing_intent(self):
        first = self.record()
        self.assertEqual(self.record(), first)
        self.error(409, self.record, payload={'text': '不同訊息'})
        self.error(409, self.record, idempotency_key='new-key')
        self.assertEqual(self.external.list_records()['total'], 1)
        self.assertEqual(len(self.external.events()['events']), 1)
        command = self.command()
        self.assertEqual(self.command(), command)
        self.error(409, self.command, payload={'reason': '不同意圖'})
        result = self.result(command)
        self.assertEqual(self.result(command)['result'], result['result'])
        self.error(409, self.result, command, payload={'evidence': '不同結果'})

    def test_concurrent_record_retries_create_one_evidence_event(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.record(), range(4)))
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(len(self.external.events()['events']), 1)
        self.assertEqual(len(self.external.history(results[0]['record_id'])), 1)

    def test_revisions_preserve_message_original_and_reject_stale_updates(self):
        first = self.record()['record']
        second = self.record(event_id='edit-1', idempotency_key='edit-1', expected_version=1, source_revision=2, payload={'text': '修訂文字'})['record']
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(second['version'], 2)
        history = self.external.history(first['id'])
        self.assertEqual([r['payload']['text'] for r in history], ['測試訊息', '修訂文字'])
        self.error(409, self.record, event_id='edit-2', idempotency_key='edit-2', expected_version=1, source_revision=3)
        self.error(409, self.record, event_id='edit-2', idempotency_key='edit-2', expected_version=2, source_revision=1)
        self.assertEqual(self.external.get_record(first['id']), second)
        self.assertEqual(second['actor'], 'integration:' + self.integration['id'])

    def test_cursors_resume_paginate_and_cannot_cross_instances_or_companies(self):
        first = self.record()
        first_page = self.external.events(limit=1)
        self.assertEqual(first_page['events'][0]['record']['id'], first['record_id'])
        self.record(event_id='second', idempotency_key='second', external_id='second')
        restarted = Integrations(Store(self.directory), integration_id=self.integration['id'])
        resumed = restarted.events(cursor=first_page['cursor'], limit=1)
        self.assertEqual(len(resumed['events']), 1)
        self.assertEqual(resumed['events'][0]['record']['external_id'], 'second')
        self.assertEqual(restarted.events(cursor=resumed['cursor'])['events'], [])
        other = self.admin.register(registration())['integration']
        self.error(410, Integrations(self.store, integration_id=other['id']).events, first_page['cursor'])
        self.error(400, self.external.events, 'not!a-cursor')
        self.error(400, self.external.events, limit=0)
        self.error(400, self.external.events, limit=101)

    def test_restore_branch_reusing_sequence_is_an_explicit_cursor_gap(self):
        self.record()
        snapshot = self.root / 'snapshot'
        backup.snapshot(self.directory, snapshot)
        self.record(event_id='after-backup', idempotency_key='after-backup', external_id='after-backup')
        stale = self.external.events()['cursor']
        restored_path = self.root / 'restored'
        backup.restore(snapshot, restored_path)
        restored = Integrations(Store(restored_path), integration_id=self.integration['id'])
        restored.write_record({'schema_version': '1.0', 'record_type': 'message', 'external_id': 'different-branch', 'event_id': 'different-branch', 'source_revision': 1, 'source': 'test', 'occurred_at': '2026-10-24T00:00:00Z', 'payload': {}, 'idempotency_key': 'different-branch'})
        self.error(410, restored.events, stale)
        self.assertEqual(len(restored.events()['events']), 2)

    def test_command_result_evidence_and_unknown_reconciliation_never_resend(self):
        command = self.command(control_epoch=5, input_cursor='message-4')
        self.assertEqual(command['command']['state'], 'pending')
        accepted = self.result(command)
        self.assertEqual(accepted['command']['state'], 'accepted')
        unknown = self.result(command, 'unknown', 'unknown')
        self.assertEqual(unknown['command']['state'], 'unknown')
        self.error(409, self.result, command, 'accepted', 'regress')
        self.error(409, self.result, command, 'completed', 'no-proof', payload={})
        completed = self.result(command, 'completed', 'verified', control_epoch=6)
        self.assertEqual(completed['command']['state'], 'completed')
        self.assertEqual(completed['command']['control_epoch'], 5)
        self.assertEqual(completed['result']['control_epoch'], 6)
        self.assertEqual(len(completed['command']['results']), 3)
        self.error(409, self.result, command, 'failed', 'late')
        self.assertEqual(self.admin.list_commands()['total'], 1)

    def test_result_belongs_to_credential_and_platform_cannot_fake_execution(self):
        command = self.command()
        payload = {'schema_version': '1.0', 'command_id': command['command_id'], 'event_id': 'result', 'idempotency_key': 'result', 'status': 'completed', 'occurred_at': '2026-10-24T00:00:00Z', 'payload': {}}
        self.error(403, self.admin.command_result, payload)
        other = self.admin.register(registration())['integration']
        self.error(403, Integrations(self.store, integration_id=other['id']).command_result, payload)

    def test_pending_expiry_applies_to_get_list_events_and_hides_intent(self):
        with patch('blueprint_library.integrations._now', return_value='2026-10-24T00:00:00.000000+00:00'):
            command = self.command(expires_at='2026-10-24T00:01:00Z', control_epoch=5, input_cursor='private-cursor')
        with patch('blueprint_library.integrations._now', return_value='2026-10-24T00:02:00.000000+00:00'):
            current = self.external.get_command(command['command_id'])
            self.assertEqual(current['state'], 'expired')
            self.assertEqual(current['payload'], {})
            self.assertNotIn('input_cursor', current)
            self.assertFalse(current['deliverable'])
            self.assertEqual(self.external.list_commands()['commands'][0]['state'], 'expired')
            self.assertTrue(all(e['command']['payload'] == {} for e in self.external.events()['events']))
            self.assertEqual(self.admin.get_command(command['command_id'])['payload'], {'reason': '測試接管'})

    def test_accepted_work_does_not_expire_just_because_completion_arrives_later(self):
        with patch('blueprint_library.integrations._now', return_value='2026-10-24T00:00:00.000000+00:00'):
            command = self.command(expires_at='2026-10-24T00:01:00Z')
            self.result(command)
        with patch('blueprint_library.integrations._now', return_value='2026-10-24T00:02:00.000000+00:00'):
            self.assertEqual(self.external.get_command(command['command_id'])['state'], 'accepted')
            self.assertEqual(self.result(command, 'completed', 'done')['command']['state'], 'completed')

    def test_removed_capability_withholds_queued_command_on_all_read_surfaces(self):
        command = self.command()
        self.admin.update_integration(self.integration['id'], {'expected_version': 1, 'commands': []})
        current = self.external.get_command(command['command_id'])
        self.assertEqual(current['state'], 'rejected')
        self.assertEqual(current['payload'], {})
        self.assertEqual(self.external.list_commands()['commands'][0]['payload'], {})
        self.assertTrue(all(e['command']['payload'] == {} for e in self.external.events()['events']))

    def test_revoked_operator_is_rechecked_at_delivery(self):
        studio = Studio(self.store)
        studio.save_user({'username': 'operator', 'name': '測試管理員', 'role': 'admin', 'password': 'fictional-operator-password'})
        actor = Integrations(self.store, {'username': 'operator', 'role': 'admin'})
        command = actor.create_command({'integration_id': self.integration['id'], 'name': 'take_over', 'payload': {'reason': '測試'}, 'idempotency_key': 'operator-command'})
        with self.store._connection() as conn:
            conn.execute("UPDATE studio_users SET enabled=0 WHERE username='operator'")
        current = self.external.get_command(command['command_id'])
        self.assertEqual(current['state'], 'rejected')
        self.assertTrue(current['redacted'])
        self.assertEqual(self.admin.get_command(command['command_id'])['actor'], 'operator')

    def test_knowledge_approval_expiry_and_revocation_are_platform_authority(self):
        payload = {'schema_version': '1.0', 'integration_id': self.integration['id'], 'record_type': 'knowledge', 'external_id': 'policy', 'event_id': 'approval', 'source_revision': 1, 'source': 'company-owner', 'occurred_at': '2026-10-24T00:00:00Z', 'payload': {'title': '虛構政策', 'content': '核准內容', 'state': 'approved', 'effective_at': '2026-10-23T00:00:00Z', 'expires_at': '2026-10-25T00:00:00Z'}, 'idempotency_key': 'approval'}
        self.error(403, self.external.write_record, payload)
        record = self.admin.write_record(payload)['record']
        with patch('blueprint_library.integrations._now', return_value='2026-10-24T00:00:00.000000+00:00'):
            self.assertEqual(self.external.get_record(record['id'])['payload']['content'], '核准內容')
            self.assertNotIn('content', self.external.events()['events'][0]['record']['payload'])
        with patch('blueprint_library.integrations._now', return_value='2026-10-26T00:00:00.000000+00:00'):
            self.assertEqual(self.external.get_record(record['id'])['payload']['state'], 'expired')
            self.assertNotIn('content', self.external.get_record(record['id'])['payload'])
        self.admin.write_record({**payload, 'event_id': 'revocation', 'idempotency_key': 'revocation', 'source_revision': 2, 'expected_version': 1, 'payload': {**payload['payload'], 'state': 'revoked'}})
        self.assertNotIn('content', self.external.get_record(record['id'])['payload'])
        self.assertTrue(all('content' not in r['payload'] for r in self.external.history(record['id'])))
        self.assertEqual(self.admin.history(record['id'])[0]['payload']['content'], '核准內容')

    def test_selected_resource_grants_recheck_current_private_visibility(self):
        item = self.store.create_item({'title': '虛構資料', 'url': 'https://example.com/info', 'notes': '可授權內容'})
        ref = {'kind': 'item', 'id': item['id']}
        self.admin.update_integration(self.integration['id'], {'expected_version': 1, 'resource_refs': [ref]})
        self.assertEqual(self.external.read_resource('item', item['id'])['version'], item['version'])
        other = self.store.create_item({'title': '未選來源', 'url': 'https://example.com/other'})
        self.error(403, self.external.read_resource, 'item', other['id'])
        Studio(self.store).create({'kind': 'report', 'title': '私人報告', 'data': {'content': '私人', 'source': '測試'}, 'visibility': 'admin', 'refs': [ref]})
        self.error(403, self.external.read_resource, 'item', item['id'])
        self.error(403, self.admin.update_integration, self.integration['id'], {'expected_version': 2, 'resource_refs': [ref]})

    def test_external_links_require_approved_origin_and_declarations_never_run_code(self):
        self.error(400, self.record, payload={'external_url': 'https://evil.example/case/1'})
        self.error(400, self.record, payload={'external_url': 'javascript:alert(1)'})
        self.error(400, self.admin.register, registration(allowed_origins=['https://example.com/path']))
        self.error(400, self.admin.register, registration(script='fetch("example.com")'))
        self.assertEqual(self.record(payload={'external_url': 'https://example.com/case/1'})['record']['payload']['external_url'], 'https://example.com/case/1')

    def test_declarative_schema_rejects_unknown_fields_wrong_types_and_stale_registry(self):
        self.error(400, self.command, payload={'reason': True})
        self.error(400, self.command, payload={'reason': 'a', 'extra': 'b'})
        self.error(400, self.command, payload={})
        self.error(400, self.admin.register, registration(commands=[{'name': 'unsafe', 'label': 'unsafe', 'input_schema': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': True}}]))
        self.admin.update_integration(self.integration['id'], {'expected_version': 1, 'name': '更新名稱'})
        self.error(409, self.admin.update_integration, self.integration['id'], {'expected_version': 1, 'enabled': False})
        self.error(400, self.record, source_revision=True)
        self.error(400, self.record, occurred_at='2026-10-24T00:00:00')
        self.error(400, self.record, occurred_at='20261024T000000Z')
        self.error(400, self.record, occurred_at='2026-10-24T000000+0000')

    def test_capabilities_explain_actual_scopes_without_admin_registry_access(self):
        item = self.store.create_item({'title': '已選文件', 'url': 'https://example.com/selected'})
        ref = {'kind': 'item', 'id': item['id']}
        self.admin.update_integration(self.integration['id'], {'expected_version': 1, 'resource_refs': [ref]})
        capabilities = self.external.capabilities()
        self.assertEqual(capabilities['resource_refs'], [ref])
        self.assertIn('knowledge', capabilities['permissions']['records_read'])
        self.assertNotIn('knowledge', capabilities['permissions']['records_write'])
        self.assertFalse(capabilities['permissions']['commands_create'])
        self.assertTrue(capabilities['permissions']['command_results_write'])

    def test_permission_changes_redact_execution_result_and_receipt_readback(self):
        command = self.command()
        self.result(command)
        self.admin.update_integration(self.integration['id'], {'expected_version': 1, 'commands': []})
        first = self.result(command, 'completed', 'done')
        self.assertTrue(first['command']['redacted'])
        self.assertEqual(first['command']['payload'], {})
        retried = self.result(command, 'completed', 'done')
        self.assertEqual(retried, first)

    def test_backup_restore_preserves_history_commands_receipts_credentials(self):
        record = self.record()
        command = self.command()
        self.result(command)
        snapshot = self.root / 'snapshot'
        backup.snapshot(self.directory, snapshot)
        restored_path = self.root / 'restored'
        backup.restore(snapshot, restored_path)
        restored = Store(restored_path)
        self.assertEqual(authenticate(restored, self.token), self.integration['id'])
        external = Integrations(restored, integration_id=self.integration['id'])
        self.assertEqual(external.get_record(record['record_id']), record['record'])
        self.assertEqual(external.get_command(command['command_id'])['state'], 'accepted')
        self.assertEqual(external.events()['events'], self.external.events()['events'])


if __name__ == '__main__':
    unittest.main()
