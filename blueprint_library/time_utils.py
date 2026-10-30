"""Shared ISO parsing and IANA lookup, independent of calendar business rules."""
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .store import LibraryError


def parse_iso(value):
    """Accept the existing ISO syntax, including Z, on Python 3.10 and later.

    Callers retain their own format, range and timezone-awareness validation.
    """
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def timezone_info(value, *, invalid_message='請使用有效的 IANA 時區，例如 Asia/Hong_Kong。', factory=None):
    """Separate an unavailable timezone database from a bad user-supplied name."""
    factory = factory or ZoneInfo
    try:
        return factory(value)
    except ValueError:
        raise LibraryError(invalid_message) from None
    except ZoneInfoNotFoundError:
        try:
            factory('UTC')
        except ZoneInfoNotFoundError:
            raise LibraryError('目前執行環境缺少 IANA 時區資料；請完成安裝器的依賴準備或安裝 tzdata。', 503) from None
        raise LibraryError(invalid_message) from None
