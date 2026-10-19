"""One private company profile, independent of public product defaults."""

from __future__ import annotations


def _fields(payload, *, versioned):
    from .store import LibraryError, _payload, _text

    allowed = {"name", "industry", "description", "goals"}
    if versioned:
        allowed.add("expected_version")
    payload = _payload(payload, allowed)
    result = {"name": _text(payload.get("name", ""), "公司名稱", 120, empty=False)}
    for field, label, maximum in (("industry", "行業", 120), ("description", "公司簡介", 4000), ("goals", "公司目標", 4000)):
        if field in payload:
            result[field] = _text(payload[field], label, maximum)
    expected = None
    if versioned:
        expected = payload.get("expected_version")
        if type(expected) is not int or expected < 0:
            raise LibraryError("公司設定更新必須提供非負整數 expected_version。")
    return result, expected


class CompanyMixin:
    """A singleton profile using Store's external SQLite data directory."""

    @staticmethod
    def _initialize_company(conn):
        conn.execute("""CREATE TABLE IF NOT EXISTS company_profile (
            id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL,
            industry TEXT NOT NULL, description TEXT NOT NULL, goals TEXT NOT NULL,
            version INTEGER NOT NULL CHECK(version>=1),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")

    @staticmethod
    def _company_profile(conn):
        row = conn.execute("SELECT name,industry,description,goals,version,created_at,updated_at FROM company_profile WHERE id=1").fetchone()
        if row is None:
            return {"name": "", "industry": "", "description": "", "goals": "", "version": 0,
                    "created_at": None, "updated_at": None, "configured": False}
        return {**dict(row), "configured": True}

    def get_company_profile(self) -> dict:
        with self._connection() as conn:
            return self._company_profile(conn)

    def _write_company_profile(self, conn, fields, current):
        from .store import _now

        values = {name: fields.get(name, current[name]) for name in ("name", "industry", "description", "goals")}
        now = _now()
        if current["configured"]:
            conn.execute("UPDATE company_profile SET name=?,industry=?,description=?,goals=?,version=version+1,updated_at=? WHERE id=1",
                         (values["name"], values["industry"], values["description"], values["goals"], now))
        else:
            conn.execute("INSERT INTO company_profile(id,name,industry,description,goals,version,created_at,updated_at) VALUES(1,?,?,?,?,1,?,?)",
                         (values["name"], values["industry"], values["description"], values["goals"], now, now))
        return self._company_profile(conn)

    def update_company_profile(self, payload) -> dict:
        from .store import LibraryError

        fields, expected = _fields(payload, versioned=True)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._company_profile(conn)
            if current["version"] != expected:
                raise LibraryError("公司設定已被其他操作更新，請重新讀取後再修改。", 409)
            return self._write_company_profile(conn, fields, current)

    def bootstrap_company_profile(self, payload) -> dict:
        fields, _ = _fields(payload, versioned=False)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._company_profile(conn)
            if current["configured"]:
                return current
            return self._write_company_profile(conn, fields, current)
