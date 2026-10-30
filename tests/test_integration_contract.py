"""Shared fictional fixtures exercise the same contract independent clients pin."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from blueprint_library.integrations import Integrations, authenticate
from blueprint_library.integrations_http import error_body
from blueprint_library.server import initialize_library
from blueprint_library.store import LibraryError, Store


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'contracts' / 'integration-v1'


def fixture(name):
    return json.loads((CONTRACT / 'fixtures' / (name + '.json')).read_text(encoding='utf-8'))


class IntegrationContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='blueprint-contract-fixtures-')
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name) / 'private'
        initialize_library(directory, 'fixture-owner', 'fictional-contract-password')
        self.store = Store(directory)
        self.admin = Integrations(self.store, {'username': 'fixture-owner', 'role': 'owner'})
        registered = self.admin.register(fixture('manifest'))
        self.integration = registered['integration']
        integration_id = authenticate(self.store, registered['token'])
        self.service = Integrations(self.store, integration_id=integration_id)

    def command_request(self):
        request = fixture('command')
        request['integration_id'] = self.integration['id']
        return request

    def test_record_fixture_roundtrip_and_duplicate_receipt(self):
        request = fixture('record')
        stored = self.service.write_record(request)
        self.assertEqual(self.service.write_record(request), stored)
        readback = self.service.get_record(stored['record_id'])
        self.assertEqual(readback['payload'], request['payload'])
        self.assertEqual(readback['external_id'], request['external_id'])
        self.assertEqual(self.service.list_records()['total'], 1)
        events = self.service.events()
        self.assertEqual(len(events['events']), 1)
        self.assertEqual(events['events'][0]['record']['id'], stored['record_id'])

    def test_fixture_user_intention_and_actual_service_results(self):
        created = self.admin.create_command(self.command_request())
        self.assertEqual(created['command']['state'], 'pending')
        delivery = self.service.events()
        self.assertTrue(any(event.get('command', {}).get('id') == created['command_id']
                            for event in delivery['events']))
        for sample in fixture('command-results'):
            request = {**sample, 'command_id': created['command_id']}
            result = self.service.command_result(request)
            self.assertEqual(result['command']['state'], request['status'])
            retry = self.service.command_result(request)
            self.assertEqual(retry['result'], result['result'])
            self.assertEqual(retry['cursor'], result['cursor'])
            self.assertEqual(retry['command']['id'], result['command']['id'])
            self.assertEqual(retry['command']['version'], result['command']['version'])
        readback = self.admin.get_command(created['command_id'])
        self.assertEqual(readback['state'], 'completed')
        self.assertEqual([item['status'] for item in readback['results']], ['accepted', 'completed'])

    def test_invalid_shared_fixtures_are_rejected_without_partial_writes(self):
        for sample in fixture('invalid-requests'):
            with self.subTest(sample['name']):
                operation = sample['operation']
                request = {'record': fixture('record'), 'command': self.command_request(),
                           'manifest': fixture('manifest')}[operation]
                request.update(deepcopy(sample['change']))
                method = {'record': self.service.write_record, 'command': self.admin.create_command,
                          'manifest': self.admin.register}[operation]
                with self.assertRaises(LibraryError) as raised:
                    method(request)
                self.assertEqual(raised.exception.status, sample['expected_status'])
        self.assertEqual(self.service.list_records()['total'], 0)
        self.assertEqual(self.admin.list_commands()['total'], 0)
        self.assertEqual(len(self.admin.list_integrations()['integrations']), 1)

    def test_authoritative_knowledge_fixture_cannot_be_self_approved(self):
        request = fixture('knowledge')
        with self.assertRaises(LibraryError) as raised:
            self.service.write_record(request)
        self.assertEqual(raised.exception.status, 403)
        request['integration_id'] = self.integration['id']
        saved = self.admin.write_record(request)
        visible = self.service.get_record(saved['record_id'])
        self.assertEqual(visible['payload']['content'], request['payload']['content'])
        revoked = deepcopy(request)
        revoked.update(event_id='fictional-policy-revoked-event',
                       idempotency_key='fictional-policy-revoked',
                       expected_version=saved['version'], source_revision=2)
        revoked['payload']['state'] = 'revoked'
        self.admin.write_record(revoked)
        self.assertNotIn('content', self.service.get_record(saved['record_id'])['payload'])
        for historical in self.service.history(saved['record_id']):
            self.assertNotIn('content', historical['payload'])
        for event in self.service.events()['events']:
            self.assertNotIn('content', event['record']['payload'])

    def test_error_fixtures_match_machine_readable_runtime_envelope(self):
        for sample in fixture('errors'):
            with self.subTest(status=sample['status']):
                body = sample['body']
                self.assertEqual(error_body(body['error'], sample['status']), body)

    def test_contract_hashes_cover_every_pinned_file(self):
        lines = (CONTRACT / 'SHA256SUMS').read_text(encoding='utf-8').splitlines()
        pinned = {}
        for line in lines:
            digest, name = line.split('  ', 1)
            self.assertNotIn(name, pinned)
            path = CONTRACT / name
            self.assertTrue(path.resolve().is_relative_to(CONTRACT.resolve()))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest, name)
            pinned[name] = digest
        actual = {path.relative_to(CONTRACT).as_posix() for path in CONTRACT.rglob('*')
                  if path.is_file() and path.name != 'SHA256SUMS'}
        self.assertEqual(set(pinned), actual)
        self.assertEqual(list(pinned), sorted(pinned))

    def test_openapi_references_resolve_inside_pinned_contract(self):
        document = json.loads((CONTRACT / 'openapi.json').read_text(encoding='utf-8'))
        self.assertEqual(document['openapi'], '3.1.0')
        self.assertEqual(document['info']['version'], '1.0')
        prefix = '/api/integrations/v1/'
        self.assertIn(prefix + 'events', document['paths'])
        self.assertIn('202', document['paths'][prefix + 'commands']['post']['responses'])

        def inspect(value):
            if isinstance(value, dict):
                if '$ref' in value:
                    reference = value['$ref']
                    if reference.startswith('#/'):
                        target = document
                        for part in reference[2:].split('/'):
                            target = target[part]
                    else:
                        target = CONTRACT / reference
                        self.assertTrue(target.resolve().is_relative_to(CONTRACT.resolve()))
                        self.assertTrue(target.is_file(), reference)
                        json.loads(target.read_text(encoding='utf-8'))
                for child in value.values():
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)

        inspect(document)

    def test_isolated_real_http_simulator_completes_fictional_takeover(self):
        spec = importlib.util.spec_from_file_location('blueprint_fictional_simulator',
                                                     ROOT / 'examples' / 'integration_service.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        summary = module.run_demo()
        self.assertTrue(summary['fictional'])
        self.assertEqual(summary['states'], ['pending', 'accepted', 'completed'])
        self.assertTrue(summary['record_readback'])
        self.assertTrue(summary['duplicate_event_deduplicated'])
        self.assertTrue(summary['service_cursor_saved_atomically'])
        self.assertTrue(summary['command_readback'])
        self.assertFalse(summary['real_message_sent'])


if __name__ == '__main__':
    unittest.main()
