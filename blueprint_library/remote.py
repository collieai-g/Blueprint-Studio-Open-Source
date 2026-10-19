"""Bounded, authenticated backend requests using normal proxy and TLS settings."""

from __future__ import annotations

import base64
from datetime import date as calendar_date
from http.client import HTTPException
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .classify import normalize_url
from .store import LibraryError


DEFAULT_USERNAME_ENV = "BLUEPRINT_BACKEND_USERNAME"
DEFAULT_PASSWORD_ENV = "BLUEPRINT_BACKEND_PASSWORD"
MAX_REQUEST_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
REQUEST_TIMEOUT = 20


def environment_name(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}", value):
        raise LibraryError("登入環境變數名稱需為 1 至 100 個英文字母、數字或底線，不能以數字開頭。")
    return value


def backend_url(value) -> str:
    """Accept an HTTPS origin, with literal loopback HTTP for local use only."""
    try:
        if not isinstance(value, str) or "?" in value or "#" in value:
            raise ValueError
        normalized = normalize_url(value)
        parts = urlsplit(normalized)
        if parts.path not in {"", "/"}:
            raise ValueError
        if parts.scheme != "https" and not (
                parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError
        return normalized.rstrip("/")
    except (ValueError, UnicodeError) as exc:
        raise LibraryError("後台網址需要 HTTPS，且不能包含帳密、路徑前綴、查詢或 fragment；本機測試可用 loopback HTTP。") from exc


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward Basic credentials or a private workday body elsewhere.
        return None


class WorkdayRemote:
    """Use environment references from a validated cloud workspace binding."""

    def __init__(self, binding):
        self.url = backend_url(binding.get("url"))
        self.username_env = environment_name(binding.get("username_env", DEFAULT_USERNAME_ENV))
        self.password_env = environment_name(binding.get("password_env", DEFAULT_PASSWORD_ENV))

    def _authorization(self):
        username = os.environ.get(self.username_env)
        password = os.environ.get(self.password_env)
        if not username or not password:
            raise LibraryError(
                "請先在目前 AI 工具的安全執行環境設定 " + self.username_env + " 及 " + self.password_env
                + "，再重試；不要把帳密貼到聊天或寫入工作室設定。")
        if (len(username) > 80 or ":" in username or any(ord(char) < 32 for char in username)
                or len(password) > 1024):
            raise LibraryError("登入環境變數的格式不正確，請在安全設定中修正；不要把值貼到聊天。")
        try:
            encoded = base64.b64encode((username + ":" + password).encode("utf-8")).decode("ascii")
        except UnicodeError:
            raise LibraryError("登入環境變數需要有效 UTF-8 文字，請在安全設定中修正。") from None
        return "Basic " + encoded

    def _request(self, method, path, payload=None, query=None):
        data = None
        if payload is not None:
            if not isinstance(payload, dict):
                raise LibraryError("整理結果必須是 JSON 物件。")
            try:
                data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            except (TypeError, ValueError, UnicodeError) as exc:
                raise LibraryError("整理結果需要有效 UTF-8 JSON。") from exc
            if len(data) > MAX_REQUEST_BYTES:
                raise LibraryError("整理結果最多 512 KiB，請先精簡內容。", 413)
        headers = {"Authorization": self._authorization(), "Accept": "application/json",
                   "X-Blueprint-Request": "1", "User-Agent": "BlueprintStudio-Workday"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        url = self.url + path + (("?" + urlencode(query)) if query else "")
        request = Request(url, data=data, headers=headers, method=method)
        try:
            # build_opener retains the standard environment proxy handler and
            # HTTPS CA verification. Only redirect behavior is overridden.
            with build_opener(_NoRedirect()).open(request, timeout=REQUEST_TIMEOUT) as response:
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    raise LibraryError("後台沒有回傳 JSON；請檢查部署網址及服務狀態。", 502)
                length = response.headers.get("Content-Length")
                if length is not None:
                    try:
                        if int(length) < 0 or int(length) > MAX_RESPONSE_BYTES:
                            raise ValueError
                    except ValueError as exc:
                        raise LibraryError("後台回覆超過大小上限或格式無效。", 502) from exc
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise LibraryError("後台回覆超過大小上限。", 502)
                try:
                    result = json.loads(raw.decode("utf-8"))
                except (UnicodeError, ValueError) as exc:
                    raise LibraryError("後台 JSON 回覆無效；請檢查服務狀態。", 502) from exc
                if not isinstance(result, dict):
                    raise LibraryError("後台回覆格式無效。", 502)
                return result
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if 300 <= status < 400:
                raise LibraryError("已拒絕後台重新導向，以免轉送私人資料或帳密；請直接設定正確的 HTTPS 網址。", 502) from None
            messages = {
                400: "後台拒絕整理結果，請檢查日期及資料格式。",
                401: "後台登入失敗，請在安全環境檢查帳密變數。",
                403: "後台拒絕存取，請檢查帳號權限及服務設定。",
                404: "找不到後台紀錄或 API，請檢查部署版本及紀錄編號。",
                409: "既有紀錄與本次內容衝突；請先讀取原有紀錄，不要隨機改 key 繞過。",
                413: "後台拒絕過大的內容，請先精簡整理結果。",
                429: "後台暫時限制請求，請稍後重試。",
            }
            raise LibraryError(messages.get(status, "後台暫時無法完成操作，請檢查服務狀態。"),
                               status if 400 <= status < 500 else 502) from None
        except (URLError, TimeoutError, OSError, HTTPException):
            # Transport errors can contain URLs, proxy credentials or response
            # text. Never reproduce their details in CLI/chat output.
            raise LibraryError("未能安全連接後台，請檢查網絡、HTTPS 憑證及部署狀態後重試。", 502) from None

    def save(self, payload):
        return self._request("POST", "/api/workdays", payload=payload)

    def list(self, *, q="", date=""):
        if not isinstance(q, str) or len(q) > 2000 or not isinstance(date, str):
            raise LibraryError("請提供有效的搜尋文字及日期。")
        if date:
            try:
                if calendar_date.fromisoformat(date).isoformat() != date:
                    raise ValueError
            except ValueError as exc:
                raise LibraryError("工作日期需要 YYYY-MM-DD。") from exc
        return self._request("GET", "/api/workdays", query={"q": q, "date": date})

    def show(self, workday_id):
        return self._request("GET", self._workday_path(workday_id))

    @staticmethod
    def _workday_path(workday_id):
        if not isinstance(workday_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,100}", workday_id):
            raise LibraryError("請提供有效的工作紀錄編號。")
        return "/api/workdays/" + workday_id

    def revise(self, workday_id, payload):
        return self._request("PATCH", self._workday_path(workday_id), payload=payload)

    def history(self, workday_id):
        return self._request("GET", self._workday_path(workday_id) + "/history")
