"""Explainable metadata rules for company files and saved references.

The classifier never fetches a URL or sends content to an AI provider. Suggestions
describe supplied metadata and text; callers can keep a user's manual labels.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit


_EXTENSIONS = {
    "code": frozenset({
        ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
        ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue",
        ".svelte", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".hpp",
        ".cs", ".rb", ".php", ".sh", ".bash", ".ps1", ".sql", ".json",
        ".yaml", ".yml", ".toml", ".ipynb", ".r", ".swift", ".kt",
    }),
    "document": frozenset({
        ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md",
        ".markdown", ".csv", ".tsv", ".xls", ".xlsx", ".ods", ".ppt",
        ".pptx", ".odp", ".epub",
    }),
    "image": frozenset({
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif",
        ".bmp", ".tif", ".tiff", ".heic", ".ico",
    }),
    "video": frozenset({".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v", ".mpeg", ".mpg"}),
}

_CODE_MIMES = frozenset({
    "application/javascript", "text/javascript", "application/json",
    "application/ld+json", "application/sql", "application/x-python-code",
    "text/x-python", "text/x-script.python", "text/css", "text/html",
    "application/x-sh", "text/x-shellscript", "application/yaml", "text/yaml",
    "application/toml", "text/x-java-source", "text/x-c", "text/x-c++",
})
_DOCUMENT_MIMES = frozenset({
    "application/pdf", "text/plain", "text/markdown", "text/csv",
    "text/tab-separated-values", "application/msword", "application/rtf",
    "application/vnd.ms-excel", "application/vnd.ms-powerpoint", "application/epub+zip",
})

# Order is intentional: it also defines the stable order of suggested tags.
_TOPICS = (
    ("ui", "UI", "web", ("ui", "user interface", "interface design", "介面設計", "界面設計", "使用者介面", "用戶界面")),
    ("ux", "UX", "web", ("ux", "user experience", "使用者體驗", "用戶體驗")),
    ("web", "網頁設計", "web", ("網頁", "網站", "網頁設計", "網站設計", "web design", "website", "webpage", "landing page", "frontend", "前端")),
    ("learning", "學習", "research", ("學習", "教學", "教程", "知識", "learning", "tutorial", "lesson", "course", "knowledge")),
    ("research", "研究", "research", ("研究", "參考", "資料搜集", "資料蒐集", "資料收集", "research", "reference")),
    ("finance", "金融", "finance", ("金融", "財務", "會計", "報稅", "finance", "financial", "accounting", "banking")),
    ("chat", "客服", "chat", ("客服", "客戶服務", "聊天", "對話", "whatsapp", "customer support", "customer service", "chatbot")),
    ("calendar", "預約", "calendar", ("預約", "行程", "排程", "calendar", "booking", "appointment", "schedule")),
    ("automation", "自動化", "automation", ("自動化", "工作流", "automation", "workflow")),
    ("ai", "AI", None, ("ai", "人工智能", "人工智慧", "openai", "gemini", "claude", "machine learning")),
)
_DESIGN_HOSTS = frozenset({
    "figma.com", "dribbble.com", "behance.net", "awwwards.com",
    "siteinspire.com", "mobbin.com", "land-book.com", "screenlane.com",
})
_SOURCE_TAGS = {
    "website": ("網頁",), "youtube": ("YouTube", "影片", "學習"),
    "code": ("程式碼",), "document": ("文件",), "image": ("圖片",),
    "video": ("影片",), "other": (),
}
_SOURCE_CATEGORIES = {
    "website": "web", "youtube": "research", "code": "automation",
    "document": "document", "image": "general", "video": "research", "other": "general",
}


def normalize_url(value: str) -> str:
    """Validate a saved HTTP(S) URL without visiting it.

    Credentials, ambiguous backslashes, whitespace and control characters are
    rejected. Hostnames use their ASCII IDNA form; explicit valid ports and URL
    paths, queries and fragments are retained. Bracketed hosts must be valid
    IPv6 literals, rather than DNS names or unsupported address forms.
    """
    if not isinstance(value, str):
        raise ValueError("網址必須係文字。")
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in value):
        raise ValueError("網址不能包含控制字元。")
    value = value.strip()
    if not value or "\\" in value or any(char.isspace() for char in value):
        raise ValueError("請提供完整而且不含空白嘅 HTTP 或 HTTPS 網址。")
    try:
        parts = urlsplit(value)
        if parts.scheme.casefold() not in {"http", "https"} or not parts.netloc:
            raise ValueError
        if "@" in parts.netloc or parts.username is not None or parts.password is not None:
            raise ValueError
        host = parts.hostname
        port = parts.port  # Access validates malformed and out-of-range ports.
        if not host or parts.netloc.endswith(":"):
            raise ValueError
        if ":" in host or "[" in parts.netloc or "]" in parts.netloc:
            if "%" in host or not re.fullmatch(r"\[[^\]]+\](?::[0-9]+)?", parts.netloc):
                raise ValueError
            host = "[" + ipaddress.IPv6Address(host).compressed + "]"
        else:
            host = host.encode("idna").decode("ascii").lower()
            labels = host.removesuffix(".").split(".")
            if len(host) > 254 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
                raise ValueError
        netloc = host + (f":{port}" if port is not None else "")
        return urlunsplit((parts.scheme.casefold(), netloc, parts.path, parts.query, parts.fragment))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("請提供有效嘅 HTTP 或 HTTPS 網址，網址不能包含登入憑證。") from exc


def _matches(text: str, word: str) -> bool:
    # English words need boundaries, so e.g. "build" does not match "ui".
    if word.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None
    return word in text


def _plain(value) -> str:
    return value if isinstance(value, str) else ""


def _source(name: str, url: str, mime: str) -> tuple[str, str]:
    host = (urlsplit(url).hostname or "").removesuffix(".") if url else ""
    if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        return "youtube", "source:trusted-youtube-host"
    if url and mime == "text/html":
        return "website", "source:webpage-mime"
    if mime.startswith("image/"):
        return "image", "source:image-mime"
    if mime.startswith("video/"):
        return "video", "source:video-mime"
    if mime in _CODE_MIMES:
        return "code", "source:code-mime"
    if mime in _DOCUMENT_MIMES or mime.startswith(("application/vnd.openxmlformats-officedocument.", "application/vnd.oasis.opendocument.")):
        return "document", "source:document-mime"
    path = unquote(urlsplit(url).path) if url else name.replace("\\", "/")
    suffix = PurePosixPath(path).suffix.casefold()
    for source_type, extensions in _EXTENSIONS.items():
        if suffix in extensions:
            return source_type, "source:extension:" + suffix
    if url:
        return "website", "source:http-url"
    if mime.startswith("text/"):
        return "document", "source:text-mime"
    return "other", "source:unrecognized-metadata"


def classify_item(*, name: str = "", url: str = "", mime: str = "", text: str = "") -> dict:
    """Suggest an icon/category and tags from supplied metadata and text.

    A nonempty URL must pass ``normalize_url``. Binary files need only their
    filename and MIME type; callers should not decode arbitrary binary data.
    Signals are stable rule identifiers, never instructions taken from content.
    """
    clean_url = normalize_url(url) if url else ""
    name = _plain(name)
    mime = _plain(mime).partition(";")[0].strip().casefold()
    source_type, source_signal = _source(name, clean_url, mime)
    # Limit scanning, not stored content. The first 200k characters are enough
    # for a lightweight suggestion and prevent large files blocking a request.
    search_text = " ".join((name, unquote(clean_url), _plain(text)[:200_000])).casefold()
    topics = [(key, tag, category) for key, tag, category, words in _TOPICS if any(_matches(search_text, word) for word in words)]
    host = (urlsplit(clean_url).hostname or "").removesuffix(".") if clean_url else ""
    design_host = any(host == domain or host.endswith("." + domain) for domain in _DESIGN_HOSTS)
    if design_host and not any(key == "web" for key, _, _ in topics):
        topics.append(("design-host", "網頁設計", "web"))
    category = _SOURCE_CATEGORIES[source_type]
    # Trusted YouTube links stay in research, source files in automation. Other
    # items follow subject matter, with UI references taking precedence.
    if source_type not in {"youtube", "code"}:
        for candidate in ("web", "finance", "chat", "calendar", "automation", "research"):
            if any(topic_category == candidate for _, _, topic_category in topics):
                category = candidate
                break
    tags = list(dict.fromkeys((*_SOURCE_TAGS[source_type], *(tag for _, tag, _ in topics))))
    signals = [source_signal, *("topic:" + key for key, _, _ in topics)]
    if not topics:
        signals.append("category:source-default")
    return {
        "kind": "link" if clean_url else "file",
        "source_type": source_type,
        "category": category,
        "tags": tags,
        "icon": category,
        "method": "rules",
        "signals": signals,
    }
