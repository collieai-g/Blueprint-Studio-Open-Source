"""HTTP dispatcher boundaries remain independent of domain refactoring."""
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from blueprint_library import agents_http, studio_http, integrations_http
from blueprint_library.store import LibraryError


class WorkflowRoutesTests(unittest.TestCase):
    def handler(self, headers=None):
        return SimpleNamespace(server=SimpleNamespace(store=object()),
            identity={'username': 'owner', 'role': 'owner'}, headers=headers or {},
            _body=Mock(return_value={'synthetic': True}), _json=Mock(),
            _headers=Mock(), wfile=io.BytesIO())

    def test_integration_record_and_command_retries_keep_original_status(self):
        handler = self.handler(); service = Mock()
        for endpoint, action, first_status in [('records', 'write_record', 201), ('commands', 'create_command', 202),
                                               ('command-results', 'command_result', 201)]:
            for created in (True, False):
                with self.subTest(endpoint=endpoint, created=created), \
                        patch.object(integrations_http, 'Integrations', return_value=service):
                    getattr(service, action).return_value = {'created': created, 'id': 'same-id'}
                    self.assertTrue(integrations_http.handle(handler, 'POST', '/api/integrations/v1/' + endpoint, {}))
                    self.assertEqual(handler._json.call_args.args[1], first_status if created else 200)

    def test_integration_query_errors_happen_before_domain_access(self):
        handler = self.handler(); service = Mock()
        for query in ({'limit': ['bad']}, {'integration_id': ['one', 'two']}):
            with self.subTest(query=query), patch.object(integrations_http, 'Integrations', return_value=service):
                with self.assertRaises(LibraryError) as caught:
                    integrations_http.handle(handler, 'GET', '/api/integrations/v1/records', query)
                self.assertEqual(caught.exception.status, 400)
        service.list_records.assert_not_called()

    def test_integration_identity_is_passed_through_and_other_routes_are_ignored(self):
        handler = self.handler(); handler.identity['integration_id'] = 'synthetic-integration'
        with patch.object(integrations_http, 'Integrations') as factory:
            self.assertFalse(integrations_http.handle(handler, 'GET', '/api/items', {}))
            factory.assert_not_called()
            factory.return_value.capabilities.return_value = {'synthetic': True}
            self.assertTrue(integrations_http.handle(handler, 'GET', '/api/integrations/v1/capabilities', {}))
            factory.assert_called_once_with(handler.server.store, handler.identity, integration_id='synthetic-integration')

    def test_presentation_stale_account_is_rejected_before_read_or_write(self):
        handler = self.handler({'X-Blueprint-Account': 'different-account'})
        service = Mock(actor='owner')
        for method in ('GET', 'PATCH'):
            with self.subTest(method=method), patch.object(studio_http, 'Studio', return_value=service):
                with self.assertRaises(LibraryError) as caught:
                    studio_http.handle(handler, method, '/api/studio/presentation', {})
                self.assertEqual(caught.exception.status, 403)
        service.presentation.assert_not_called()
        service.save_presentation.assert_not_called()
        handler._body.assert_not_called()

    def test_presentation_query_versions_are_validated_without_other_company_reads(self):
        handler = self.handler()
        service = Mock(actor='owner')
        service.presentation.return_value = {'version': 7}
        with patch.object(studio_http, 'Studio', return_value=service):
            self.assertTrue(studio_http.handle(handler, 'GET', '/api/studio/presentation', {'since_version': ['6']}))
            service.presentation.assert_called_once_with(key=None, since_version=6)
            with self.assertRaises(LibraryError):
                studio_http.handle(handler, 'GET', '/api/studio/presentation', {'since_version': ['-1']})
        service.list.assert_not_called()

    def test_quote_download_escapes_record_content_and_preserves_attachment_headers(self):
        handler = self.handler()
        service = Mock(actor='owner')
        service.get.return_value = {'kind': 'quote', 'title': '<script>synthetic</script>',
            'data': {'company': '<company>', 'lines': [{'description': '<item>', 'quantity': 1, 'unit_price': '2.00'}]}}
        with patch.object(studio_http, 'Studio', return_value=service):
            self.assertTrue(studio_http.handle(handler, 'GET', '/api/studio/records/synthetic/download', {}))
        result = handler.wfile.getvalue().decode('utf-8')
        self.assertNotIn('<script>', result)
        self.assertIn('&lt;company&gt;', result)
        self.assertIn('&lt;item&gt;', result)
        self.assertEqual(handler._headers.call_args.args[1], 'text/html; charset=utf-8')
        self.assertIn('blueprint-quote.html', handler._headers.call_args.args[3]['Content-Disposition'])

    def test_record_creation_preserves_replay_status(self):
        handler = self.handler()
        service = Mock(actor='owner')
        with patch.object(studio_http, 'Studio', return_value=service):
            for created, status in ((True, 201), (False, 200)):
                service.create.return_value = {'created': created, 'record': {'id': 'same-id'}}
                studio_http.handle(handler, 'POST', '/api/studio/records', {})
                self.assertEqual(handler._json.call_args.args[1], status)

    def test_agent_and_job_routes_do_not_cross_dispatch(self):
        handler = self.handler()
        service = Mock()
        service.context.return_value = {'context_digest': 'synthetic'}
        with patch.object(agents_http, 'Agents', return_value=service):
            self.assertTrue(agents_http.handle(handler, 'GET', '/api/studio/agent-jobs/job-one/context', {}))
            service.context.assert_called_once_with('job-one')
            with self.assertRaises(LibraryError) as caught:
                agents_http.handle(handler, 'GET', '/api/studio/agents/agent-one/context', {})
            self.assertEqual(caught.exception.status, 404)
        self.assertEqual(service.context.call_count, 1)

    def test_agent_pagination_and_foreign_paths_do_not_execute_business_actions(self):
        handler = self.handler()
        service = Mock()
        with patch.object(agents_http, 'Agents', return_value=service):
            self.assertFalse(agents_http.handle(handler, 'GET', '/api/unrelated', {}))
            with self.assertRaises(LibraryError) as caught:
                agents_http.handle(handler, 'GET', '/api/studio/agents', {'limit': ['bad']})
            self.assertEqual(caught.exception.status, 400)
        service.list_agents.assert_not_called()
        service.agent_action.assert_not_called()


if __name__ == '__main__':
    unittest.main()
