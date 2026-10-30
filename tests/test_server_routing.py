"""Authentication/order characterization before splitting the HTTP dispatcher."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from blueprint_library.server import LibraryHandler, serve_library
from blueprint_library.store import LibraryError


class RoutingOrderTests(unittest.TestCase):
    def handler(self, path):
        handler = object.__new__(LibraryHandler)
        handler.path = path
        handler.server = SimpleNamespace(installer_stopping=False)
        handler._authorized = Mock(return_value=False)
        handler._write_allowed = Mock()
        handler._json = Mock()
        return handler

    def test_public_health_does_not_enter_account_or_business_routes(self):
        handler = self.handler('/health')
        with patch('blueprint_library.installer_http.internal', return_value=False):
            handler._route('GET')
        handler._authorized.assert_not_called()
        handler._json.assert_called_once_with({'status': 'ok'})

    def test_internal_handshake_keeps_separate_authentication_boundary(self):
        handler = self.handler('/api/_installer')
        with patch('blueprint_library.installer_http.internal', return_value=True) as internal:
            handler._route('POST')
        internal.assert_called_once_with(handler, 'POST', '/api/_installer')
        handler._authorized.assert_not_called(); handler._write_allowed.assert_not_called()

    def test_unauthenticated_request_never_reaches_installer(self):
        handler = self.handler('/api/installer/apply')
        with patch('blueprint_library.installer_http.internal', return_value=False), \
                patch('blueprint_library.installer_http.handle') as installer:
            handler._route('POST')
        handler._write_allowed.assert_not_called(); installer.assert_not_called()

    def test_same_origin_write_check_precedes_installer_side_effect(self):
        handler = self.handler('/api/installer/apply')
        handler._authorized.return_value = True
        handler._write_allowed.side_effect = LibraryError('synthetic blocked origin', 403)
        with patch('blueprint_library.installer_http.internal', return_value=False), \
                patch('blueprint_library.installer_http.handle') as installer:
            with self.assertRaises(LibraryError) as raised:
                handler._route('POST')
        self.assertEqual(raised.exception.status, 403); installer.assert_not_called()

    def test_safe_stop_blocks_writes_before_dispatch(self):
        handler = self.handler('/api/installer/apply')
        handler._authorized.return_value = True; handler.server.installer_stopping = True
        with patch('blueprint_library.installer_http.internal', return_value=False), \
                patch('blueprint_library.installer_http.handle') as installer:
            with self.assertRaises(LibraryError) as raised:
                handler._route('POST')
        self.assertEqual(raised.exception.status, 503)
        handler._write_allowed.assert_called_once(); installer.assert_not_called()

    def test_oversize_url_rejected_before_internal_routes(self):
        handler = self.handler('/' + 'x' * 8192)
        with patch('blueprint_library.installer_http.internal') as internal:
            with self.assertRaises(LibraryError) as raised:
                handler._route('GET')
        self.assertEqual(raised.exception.status, 414); internal.assert_not_called()

    def test_native_serve_always_closes_owned_server_and_restores_signal(self):
        owned = Mock(server_port=12345)
        owned.serve_forever.side_effect = KeyboardInterrupt
        with patch('blueprint_library.server.make_server', return_value=owned) as make, \
                patch('blueprint_library.server.signal.signal', return_value='synthetic-previous') as signal, \
                patch('blueprint_library.server.webbrowser.open') as browser, patch('builtins.print'):
            serve_library('synthetic-data', open_browser=False)
        make.assert_called_once_with('synthetic-data', host='127.0.0.1', port=8787, managed=True)
        owned.server_close.assert_called_once(); browser.assert_not_called()
        self.assertEqual(signal.call_args.args[1], 'synthetic-previous')


if __name__ == '__main__':
    unittest.main()
