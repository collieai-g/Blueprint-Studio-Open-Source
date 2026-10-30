"""V2.4 acceptance: workflows, access boundaries, retained data and real results."""
import base64
from datetime import date
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import Store, LibraryError
from blueprint_library.studio import Studio


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / 'private'
        initialize_library(self.directory, 'owner', 'test-owner-password')
        self.store = Store(self.directory)
        self.owner = Studio(self.store)
        self.member = Studio(self.store, {'username': 'staff', 'role': 'member', 'name': '職員'})

    def tearDown(self):
        self.temp.cleanup()

    def create(self, kind, title='測試內容', **data):
        return self.owner.create({'kind': kind, 'title': title, 'data': data})['record']

    def act(self, record, action, **arguments):
        return self.owner.action(record['id'], {'expected_version': record['version'], 'action': action, 'arguments': arguments})

    def assert_status(self, status, fn, *args, **kwargs):
        with self.assertRaises(LibraryError) as error:
            fn(*args, **kwargs)
        self.assertEqual(error.exception.status, status)

    def test_additive_migration_and_legacy_topics_keep_original_ids_and_bytes(self):
        collection = self.store.create_collection({'name': '原有收藏', 'description': '保留原文', 'icon': 'general'})
        item = self.store.add_file('原稿.txt', b'original bytes', collection_id=collection['id'])
        before = self.store.get_item(item['id'])
        self.owner.state()
        for _ in range(2):
            self.store = Store(self.directory)
            self.owner = Studio(self.store)
            self.owner.state()
        self.assertEqual(self.store.get_item(item['id']), before)
        self.assertEqual(self.store.get_file(item['id'])[0].read_bytes(), b'original bytes')
        self.assertEqual(self.owner.get(collection['id'])['title'], '原有收藏')
        topic = self.create('topic', '新主題')
        self.assertIn(topic['id'], [row['id'] for row in self.store.list_collections()])
        self.owner.update(topic['id'], {'expected_version': 1, 'title': '改名'})
        self.assertEqual(next(row for row in self.store.list_collections() if row['id'] == topic['id'])['name'], '改名')
        self.assertEqual(len(self.owner.list('specialist')['records']), 4)
        self.assertFalse(self.owner.state()['capabilities']['specialist_runtime'])

    def test_inbox_to_todo_deduplicates_and_version_conflict_preserves_completion(self):
        inbox = self.create('inbox', content='安排跟進')
        first = self.act(inbox, 'to_task')['created'][0]
        second = self.act(inbox, 'to_task')['created'][0]
        self.assertEqual(first['id'], second['id'])
        task = self.owner.update(first['id'], {'expected_version': 1, 'data': {'done': True}})
        self.assert_status(409, self.owner.update, task['id'], {'expected_version': 1, 'data': {'done': False}})
        self.assertTrue(self.owner.get(task['id'])['data']['done'])
        self.assertEqual(len(self.owner.history(task['id'])), 2)

    def test_lead_quote_signature_invoice_payment_flow_and_exact_money(self):
        lead = self.create('lead', '客戶 A', company='甲公司', contact='陳先生', email='hello@example.com')
        quote = self.act(lead, 'create_quote')['created'][0]
        self.assertEqual(quote['data']['email'], 'hello@example.com')
        quote = self.owner.update(quote['id'], {'expected_version': 1, 'data': {'lines': [{'description': '服務', 'quantity': 3, 'unit_price': '0.10'}]}})
        self.assertEqual(quote['data']['amount'], '0.30')
        self.assert_status(409, self.act, quote, 'create_invoice')
        quote = self.act(quote, 'quote_state', state='pending')['record']
        self.assert_status(403, self.member.action, quote['id'], {'expected_version': quote['version'], 'action': 'quote_state', 'arguments': {'state': 'approved'}})
        quote = self.act(quote, 'quote_state', state='approved')['record']
        self.assert_status(409, self.owner.update, quote['id'], {'expected_version': quote['version'], 'data': {'lines': []}})
        self.assert_status(400, self.act, quote, 'quote_state', state='sent')
        quote = self.act(quote, 'quote_state', state='sent', confirmed=True)['record']
        quote = self.act(quote, 'quote_state', state='signed', confirmed=True)['record']
        result = self.act(quote, 'create_invoice')
        customer, invoice = result['created']
        self.assertEqual(customer['data']['email'], 'hello@example.com')
        self.assertEqual(invoice['data']['company'], '甲公司')
        self.assertEqual(self.owner.get(lead['id'])['data']['state'], 'converted')
        invoice = self.act(invoice, 'record_payment', amount='0.10')['record']
        self.assert_status(400, self.act, invoice, 'record_payment', amount='0.21')
        finance = self.owner.summary()['finance']['HKD']
        self.assertEqual((finance['received'], finance['outstanding']), ('0.10', '0.20'))
        self.assertFalse(result['external_action_executed'])

    def test_commands_require_actual_result_source_and_preserve_human_gate(self):
        command = self.create('command', instruction='整理資料，起草郵件')
        self.assertEqual(command['data']['state'], 'awaiting_ai')
        self.assertEqual(self.owner.list('inbox')['total'], 1)
        self.assert_status(400, self.act, command, 'complete_command', result='草稿')
        command = self.act(command, 'plan_command', tasks=[{'title': '閱讀選定資料', 'done': False}])['record']
        result = self.act(command, 'complete_command', result='已根據提供的資料整理草稿。', source='實際 MCP 測試客戶端', external_action=True)
        self.assertEqual(result['record']['data']['state'], 'needs_confirmation')
        self.assertEqual(self.owner.list('inbox')['records'][0]['data']['state'], 'decision')
        self.assert_status(403, self.member.action, command['id'], {'expected_version': result['record']['version'], 'action': 'approve_draft', 'arguments': {'confirmed': True}})
        result = self.act(result['record'], 'approve_draft', confirmed=True)
        self.assertFalse(result['external_action_executed'])
        self.assertEqual(result['record']['data']['state'], 'completed')

    def test_topic_handoff_uses_references_and_does_not_copy_unrelated_data(self):
        keep = self.create('project', '相關項目')
        exclude = self.create('project', '其他項目')
        topic = self.owner.create({'kind': 'topic', 'title': '主題', 'refs': [{'kind': 'project', 'id': keep['id']}]})['record']
        command = self.act(topic, 'ask_ai')['created'][0]
        context = self.owner.context(command['id'])
        ids = [row['id'] for row in context['sources']]
        self.assertIn(keep['id'], ids)
        self.assertNotIn(exclude['id'], ids)
        self.assertEqual(context['scope'], 'selected_references_only')
        self.assertEqual(self.owner.list('project')['total'], 2)

    def test_new_link_inbox_capture_queue_multitag_and_real_summary(self):
        item = self.store.create_item({'kind': 'link', 'url': 'https://example.com', 'title': '設計參考'})
        self.owner.state(); self.owner.state()
        inspiration = self.owner.list('inspiration')['records'][0]
        self.assertEqual(inspiration['data']['item_id'], item['id'])
        self.assertEqual(self.owner.list('inspiration')['total'], 1)
        self.assertEqual(self.owner.list('command')['total'], 1)
        self.assert_status(400, self.owner.update, inspiration['id'], {'expected_version': 1, 'data': {'ai_summary': '沒有來源的摘要'}})
        inspiration = self.owner.update(inspiration['id'], {'expected_version': 1, 'data': {'categories': ['web','brand'], 'ai_summary': '測試工具已閱讀的內容', 'summary_source': '已連接的測試 AI'}})
        self.assertEqual(self.owner.list('inspiration', category='web')['records'][0]['id'], inspiration['id'])
        self.assertEqual(self.owner.list('inspiration', category='brand')['records'][0]['id'], inspiration['id'])

    def test_unconnected_specialists_real_only_metrics_and_scenarios(self):
        specialist = self.owner.get('specialist-email')
        self.assert_status(409, self.act, specialist, 'specialist_state', state='live')
        self.assert_status(403, self.member.update, specialist['id'], {'expected_version': 1, 'data': {'role': '改寫'}})
        self.create('conversation', specialist_id=specialist['id'], source='測試', occurred_at=date.today().isoformat(), test_only=True)
        self.assertEqual(next(m for m in self.owner.summary()['specialists'] if m['id']==specialist['id'])['conversations'], 0)
        self.create('conversation', specialist_id=specialist['id'], source='外部真實對話匯入', occurred_at=date.today().isoformat(), test_only=False, outcome='human')
        metric = next(m for m in self.owner.summary()['specialists'] if m['id']==specialist['id'])
        self.assertEqual((metric['conversations'], metric['human'], metric['automatic_ratio']), (1, 1, 0))
        self.assert_status(400, self.create, 'scenario', specialist_id=specialist['id'], input='測試問題', result='passed')

    def test_budget_pauses_on_recorded_cost_and_requires_room_to_resume(self):
        integration = self.create('integration', '測試接入', enabled=True, monthly_limit='1.00')
        result = self.act(integration, 'record_usage', amount='1.00', source='真實帳單', source_key='bill-1')
        paused = result['record']
        self.assertFalse(paused['data']['enabled'])
        self.assertTrue(paused['data']['budget_paused'])
        self.assert_status(409, self.act, paused, 'resume_budget')
        self.assertEqual(self.owner.list('cost')['total'], 1)
        self.act(paused, 'record_usage', amount='1.00', source='真實帳單', source_key='bill-1')
        self.assertEqual(self.owner.list('cost')['total'], 1)
        updated = self.owner.update(paused['id'], {'expected_version': paused['version'], 'data': {'monthly_limit': '2.00'}})
        self.assertTrue(self.act(updated, 'resume_budget')['record']['data']['enabled'])

    def test_private_references_propagate_to_search_history_context_and_original_file(self):
        item = self.store.add_file('人事.txt', b'private personnel')
        public = self.owner.create({'kind': 'report', 'title': '原本公開', 'data': {'content': '資料', 'source': '測試'}, 'refs': [{'kind': 'item','id':item['id']}]})['record']
        self.owner.create({'kind':'member','title':'員工資料','refs':[{'kind':'item','id':item['id']}]})
        for fn in [self.member.get,self.member.context,self.member.history]:
            self.assert_status(403, fn, public['id'])
        self.assertEqual(self.member.list(q='原本公開')['total'], 0)
        self.assertEqual(self.store.list_items(member_access=True)['total'], 0)
        self.assertEqual(self.store.stats(member_access=True)['total'], 0)
        self.assertFalse(self.member.resource_allowed('item', item['id']))
        self.assertEqual(self.owner.get(public['id'])['visibility'], 'admin')
        target = self.create('project', '公開項目')
        target = self.owner.update(target['id'], {'expected_version': 1, 'refs': [{'kind': 'report', 'id': public['id']}]})
        self.assertEqual(target['visibility'], 'admin')

    def test_manual_costs_also_enforce_budget_and_archiving_keeps_accounts(self):
        integration = self.create('integration', enabled=True, monthly_limit='5.00')
        cost = self.create('cost', amount='5.00', date=date.today().isoformat(), integration_id=integration['id'])
        self.assertTrue(self.owner.get(integration['id'])['data']['budget_paused'])
        self.owner.update(cost['id'], {'expected_version': 1, 'archived': True})
        self.assertEqual(self.owner.summary()['finance']['HKD']['cost'], '5.00')
        current = self.owner.get(integration['id'])
        self.assert_status(409, self.act, current, 'resume_budget')

    def test_typed_reference_update_inherits_private_visibility(self):
        project = self.owner.create({'kind': 'project', 'title': '私人項目', 'visibility': 'admin'})['record']
        task = self.create('task')
        task = self.owner.update(task['id'], {'expected_version': 1, 'data': {'project_id': project['id']}})
        self.assertEqual(task['visibility'], 'admin')
        self.assert_status(403, self.member.get, task['id'])

    def test_similar_inspiration_preserves_visibility_archive_and_self_exclusion(self):
        item = self.store.create_item({'url': 'https://example.test/synthetic-reference'})
        private_item = self.store.create_item({'url': 'https://example.test/synthetic-private-reference'})
        base = self.create('inspiration', 'Synthetic alpha', item_id=item['id'], tags=['shared-reference'])
        visible = self.create('inspiration', 'Synthetic beta', item_id=item['id'], tags=['shared-reference'])
        private = self.owner.create({'kind': 'inspiration', 'title': 'Synthetic private',
            'visibility': 'admin', 'data': {'item_id': private_item['id'], 'tags': ['shared-reference']}})['record']
        archived = self.create('inspiration', 'Synthetic archived', item_id=item['id'], tags=['shared-reference'])
        self.owner.update(archived['id'], {'expected_version': archived['version'], 'archived': True})
        member = self.member.similar(base['id'])
        self.assertEqual(member['method'], 'tags_and_text')
        self.assertEqual([entry['record']['id'] for entry in member['matches']], [visible['id']])
        self.assertEqual({entry['record']['id'] for entry in self.owner.similar(base['id'])['matches']},
                         {visible['id'], private['id']})

    def test_preferences_cas_monthly_queue_and_export_no_password_material(self):
        self.owner.save_user({'username': 'staff', 'password': 'safe-staff-password', 'role': 'member'})
        self.member.save_preferences({'expected_version': 0, 'data': {'monthly_review': True, 'groups': ['work','company']}})
        self.assert_status(409, self.member.save_preferences, {'expected_version': 0, 'data': {'narrow': True}})
        self.owner.state(); self.owner.state()
        self.assertEqual(self.owner.list('command')['total'], 1)
        self.assertEqual(self.owner.preferences()['version'], 0)
        exported = self.store.export_metadata()
        self.assertEqual(exported['studio']['users'][0]['username'], 'staff')
        self.assertNotIn('password_hash', json.dumps(exported))
        self.assertNotIn('safe-staff-password', json.dumps(exported))

    def test_real_http_member_auth_read_denial_revocation_and_static_assets(self):
        user = self.owner.save_user({'username': 'staff', 'password': 'safe-staff-password', 'role': 'member'})
        invoice = self.create('invoice', amount='10.00')
        server = make_server(self.directory)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            def get(path):
                auth = base64.b64encode(b'staff:safe-staff-password').decode()
                try:
                    with urlopen(Request(f'http://127.0.0.1:{server.server_port}'+path, headers={'Authorization':'Basic '+auth})) as response:
                        return response.status, response.read()
                except HTTPError as error:
                    return error.code, error.read()
            self.assertEqual(get('/studio.js')[0], 200)
            self.assertEqual(json.loads(get('/api/studio/state')[1])['identity']['role'], 'member')
            for suffix in ['', '/history', '/context', '/download']:
                self.assertEqual(get('/api/studio/records/'+invoice['id']+suffix)[0], 403)
            self.assertEqual(get('/api/studio/records?kind=invoice')[0], 403)
            self.assertEqual(get('/api/export')[0], 403)
            self.owner.save_user({'username': 'staff','role':'member','enabled':False,'expected_version':user['version']})
            self.assertEqual(get('/api/studio/state')[0], 401)
        finally:
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
