"""Timezone runtime failures are distinct from invalid company settings."""
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from blueprint_library import calendar
from blueprint_library.store import LibraryError


class CalendarRuntimeTests(unittest.TestCase):
    def test_missing_timezone_database_is_service_unavailable(self):
        with patch.object(calendar, 'ZoneInfo', side_effect=ZoneInfoNotFoundError('missing database')):
            with self.assertRaises(LibraryError) as caught:
                calendar._zone('Asia/Hong_Kong')
        self.assertEqual(caught.exception.status, 503)
        self.assertIn('時區資料', str(caught.exception))

    def test_invalid_timezone_is_an_input_error_when_database_exists(self):
        def lookup(value):
            if value == 'UTC':
                return ZoneInfo('UTC')
            raise ZoneInfoNotFoundError(value)

        with patch.object(calendar, 'ZoneInfo', side_effect=lookup):
            with self.assertRaises(LibraryError) as caught:
                calendar._zone('Invalid/Not-A-Zone')
        self.assertEqual(caught.exception.status, 400)
        self.assertIn('IANA', str(caught.exception))

    def test_meta_converts_missing_timezone_database_to_a_clear_error(self):
        service = calendar.Calendar(None, {'username': 'owner', 'role': 'owner'})
        with patch.object(service, '_settings', return_value={'timezone': 'Asia/Hong_Kong', 'version': 1}), \
                patch.object(service, '_self', return_value=None), \
                patch.object(calendar, 'ZoneInfo', side_effect=ZoneInfoNotFoundError('missing database')):
            with self.assertRaises(LibraryError) as caught:
                service._meta(None)
        self.assertEqual(caught.exception.status, 503)


if __name__ == '__main__':
    unittest.main()
