"""Schema 7 adds service tables without replacing existing company state."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from blueprint_library.agents import Agents
from blueprint_library.server import initialize_library
from blueprint_library.store import LibraryError, SCHEMA_VERSION, Store
from blueprint_library.studio import Studio


class IntegrationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-schema7-test-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'private'
        initialize_library(self.directory, 'owner', 'schema7-migration-test-password',
                           company_profile={'name': '保留既有公司'})
        self.store = Store(self.directory)

    def as_schema6(self):
        with sqlite3.connect(self.store.db_path) as conn:
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'integration_%'")]
            for table in tables:
                conn.execute('DROP TABLE ' + table)
            conn.execute('PRAGMA user_version=6')

    def test_schema6_additive_upgrade_preserves_auth_attachment_company_and_agent(self):
        studio = Studio(self.store)
        report = studio.create({'kind': 'report', 'title': '既有成果', 'data': {'content': '保留的資料', 'source': '人工'}})['record']
        attachment = self.store.add_file('original.txt', b'original company attachment')
        manifest = {'contract_version': 1, 'id': 'migration-example', 'name': '既有 Agent',
                    'version': '0.1.0', 'description': '只用於遷移驗收', 'adapter': 'connected_client',
                    'instructions': '保存結果', 'settings': [],
                    'operations': [{'id': 'summarize', 'name': '摘要', 'instructions': '核對來源', 'input_fields': []}],
                    'ui': {'layout': 'standard', 'result_label': '結果'}}
        agent = Agents(self.store).register({'manifest': manifest, 'source': {
            'repository': 'https://github.com/example/migration-test', 'revision': 'a' * 40}})['agent']
        profile = self.store.get_company_profile()
        auth = (self.directory / 'access.json').read_bytes()
        self.as_schema6()
        upgraded = Store(self.directory)
        with upgraded._connection() as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM integration_registry').fetchone()[0], 0)
            self.assertIsNotNone(conn.execute("SELECT value FROM integration_meta WHERE key='company_id'").fetchone())
        self.assertEqual(Studio(upgraded).get(report['id']), report)
        self.assertEqual(Agents(upgraded).get_agent(agent['id']), agent)
        self.assertEqual(upgraded.get_company_profile(), profile)
        self.assertEqual(upgraded.get_file(attachment['id'])[0].read_bytes(), b'original company attachment')
        self.assertEqual((self.directory / 'access.json').read_bytes(), auth)

    def test_failed_migration_rolls_back_schema_and_partial_tables_together(self):
        self.as_schema6()
        def interrupted(conn):
            conn.execute('CREATE TABLE integration_partial(id TEXT PRIMARY KEY)')
            raise RuntimeError('injected migration interruption')
        with patch('blueprint_library.integrations.initialize', interrupted):
            with self.assertRaises(RuntimeError):
                Store(self.directory)
        with sqlite3.connect(self.store.db_path) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 6)
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='integration_partial'").fetchone())
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM agent_registrations').fetchone()[0], 0)

    def test_older_code_refuses_newer_database_instead_of_downgrading_data(self):
        with patch('blueprint_library.store.SCHEMA_VERSION', 6):
            with self.assertRaises(LibraryError):
                Store(self.directory)
        with sqlite3.connect(self.store.db_path) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)


if __name__ == '__main__':
    unittest.main()
