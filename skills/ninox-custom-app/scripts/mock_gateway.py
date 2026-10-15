#!/usr/bin/env python3
"""Run a Ninox Custom App locally against a stand-in for the Ninox app gateway.

Serves the app's dist/ folder and answers /_ninox/api/v1 the way the real gateway does, using the
app's own app.json and seed CSVs:

  * anonymous session start; calls without the session header get 401
  * list: declared tables and the read verb only, declared readable fields only, the rowFilter
    applied, filter / sort / limit (1-200) / offset / fields, refusals on undeclared fields
  * get: 403 on tables with a rowFilter
  * create: create verb, declared writable fields only; rows are kept in memory
  * attach: only to rows this session created, PDF/JPEG/PNG/GIF/WebP by content, at most 1 MiB
  * paths that are not files get a 404 instead of index.html (the real host also has no SPA fallback)

It is a test double, not the real server: values are stored as sent (no field types), and
workspace-side errors (wrong value types after retyping fields, quotas) do not happen here.

Usage:
  python3 mock_gateway.py <app-folder> [port]        (default: $PORT, else 8787)

Then open http://localhost:<port>/. GET /_mock/created lists everything created so far.
Standard library only.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import os
import re
import sys
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True  # keep the skill folder free of __pycache__
_spec = importlib.util.spec_from_file_location("check_app", HERE / "check_app.py")
check_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_app)

API = "/_ninox/api/v1"
MAX_ATTACH = 1024 * 1024
MAGIC = ((b"%PDF-", "application/pdf"), (b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"),
         (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"))


def load_app(folder: Path) -> tuple[dict, dict, dict]:
    manifest = json.loads((folder / "app.json").read_text(encoding="utf-8-sig"))
    report = check_app.Report()
    model = check_app.check_manifest(report, manifest)
    if report.errors:
        print("app.json has errors; run check_app.py first:\n  " + "\n  ".join(report.errors))
        sys.exit(1)
    files = check_app.list_files(folder)
    seeds, _ = check_app.check_csvs(check_app.Report(), folder, files, model)
    rows: dict[str, list[dict]] = {table: [] for table in model["tables"]}
    for table, rel in seeds.items():
        text = (folder / rel).read_bytes().decode("utf-8-sig")
        types = model["tables"][table].get("seed_types", {})
        for record in csv.DictReader(io.StringIO(text, newline="")):
            values = {k.strip(): typed(v, types.get(k.strip(), "text")) for k, v in record.items() if k}
            rows[table].append({"id": str(len(rows[table]) + 1), "values": values})
    return manifest, model, rows


def typed(value: str | None, kind: str):
    if value is None or value == "":
        return None
    if kind == "boolean":
        return value.lower() in {"1", "true", "yes"}
    if kind == "number" and check_app._is_number(value):
        number = float(value.replace(",", ".")) if value.count(",") == 1 and "." not in value else float(value.replace(",", ""))
        return int(number) if number.is_integer() else number
    return value


def compare(stored, op: str, literal) -> bool:
    """Row filter and list filter comparison; text compares as text, like the server."""
    if op in {"=", "IS"}:
        if literal is None:
            return stored is None or stored == ""
        literal_text = str(literal).lower() if isinstance(literal, bool) else str(literal)
        return stored == literal or str(stored) == literal_text
    if op in {"!=", "IS_NOT"}:
        return not compare(stored, "=", literal)
    if stored is None:
        return False
    if isinstance(stored, (int, float)) and isinstance(literal, (int, float)) and not isinstance(literal, bool):
        a, b = stored, literal
    else:
        a, b = str(stored), str(literal)
    return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]


def sort_key(value):
    if value is None:
        return (2, 0, "")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (0, value, "")
    return (1, 0, str(value))


LIST_OPS = {"GREATER_THAN": ">", "GREATER_THAN_OR_EQUAL": ">=", "LESS_THAN": "<", "LESS_THAN_OR_EQUAL": "<="}


def matches(stored, condition) -> bool:
    if not isinstance(condition, dict):
        return compare(stored, "IS", condition)
    op, value = condition.get("operator", "IS"), condition.get("value")
    if op in {"IS", "IS_NOT"}:
        return compare(stored, op, value)
    if op in LIST_OPS:
        return compare(stored, LIST_OPS[op], value)
    if op == "CONTAINS":
        return str(value).lower() in str(stored or "").lower()
    if op == "DOES_NOT_CONTAIN":
        return str(value).lower() not in str(stored or "").lower()
    if op == "IN":
        return any(compare(stored, "IS", v) for v in value or [])
    if op == "NOT_IN":
        return not any(compare(stored, "IS", v) for v in value or [])
    if op == "IS_EMPTY":
        return stored is None or stored == ""
    if op == "IS_NOT_EMPTY":
        return not (stored is None or stored == "")
    raise ValueError(op)


class Handler(SimpleHTTPRequestHandler):
    app_dir: Path
    manifest: dict
    model: dict
    rows: dict
    sessions: dict[str, set] = {}
    created: list = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.app_dir / "dist"), **kwargs)

    # -- helpers --------------------------------------------------------------------------------

    def send_json(self, status: int, body) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def fail(self, status: int, message: str) -> None:
        code = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
                413: "PAYLOAD_TOO_LARGE", 415: "UNSUPPORTED_MEDIA_TYPE"}.get(status, "HTTP_ERROR")
        self.send_json(status, {"error": {"code": code, "message": message}})

    def session(self) -> str | None:
        header = self.headers.get("authorization", "")
        value = header[len("Bearer "):] if header.startswith("Bearer ") else ""
        return value if value in self.sessions else None

    def table_spec(self, name: str, verb: str):
        spec = self.model["tables"].get(name)
        if spec is None:
            self.fail(403, f"This app does not declare the table {name}")
            return None
        if "anonymous" not in spec["verbs"].get(verb, []):
            self.fail(403, f"This app does not grant anonymous {verb} access to {name}")
            return None
        return spec

    def readable(self, spec: dict) -> list[str]:
        return [f for f, info in spec["fields"].items() if "anonymous" in info["readers"]]

    def visible_rows(self, table: str, spec: dict) -> list[dict]:
        terms = spec["row_filters"].get("anonymous", [])
        return [r for r in self.rows[table] if all(compare(r["values"].get(f), op, lit) for f, op, lit in terms)]

    # -- static files ---------------------------------------------------------------------------

    def send_head(self):
        path = unquote(urlparse(self.path).path)
        target = Path(self.directory) / path.lstrip("/")
        if path.endswith("/"):
            target = target / "index.html"
        if not target.is_file():
            self.send_response(404)
            self.send_header("content-type", "text/plain")
            self.send_header("content-length", "9")
            self.end_headers()
            self.wfile.write(b"Not found")
            return None
        return super().send_head()

    # -- API ------------------------------------------------------------------------------------

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/_mock/created":
            return self.send_json(200, self.created)
        if not url.path.startswith(API):
            return super().do_GET()
        if self.session() is None:
            return self.fail(401, "No valid session")
        parts = url.path[len(API):].strip("/").split("/")
        if len(parts) < 3 or parts[0] != "tables" or parts[2] != "records":
            return self.fail(404, "Not found")
        table = unquote(parts[1])
        spec = self.table_spec(table, "read")
        if spec is None:
            return
        readable = self.readable(spec)
        if not readable:
            return self.fail(403, f"This app declares no readable field on {table}")
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        fields = query["fields"].split(",") if "fields" in query else readable
        for f in fields:
            if f not in readable:
                return self.fail(400, f"{table} declares no field named {f} that may be read")
        if len(parts) == 4:
            if spec["row_filters"]:
                return self.fail(403, f"{table} has a row filter, so a record on it is read through the collection route")
            row = next((r for r in self.rows[table] if r["id"] == parts[3]), None)
            if row is None:
                return self.fail(404, "Not found")
            return self.send_json(200, {"data": {"id": row["id"], "values": {f: row["values"].get(f) for f in fields}}})

        data = self.visible_rows(table, spec)
        constrained = {f for f, _, _ in spec["row_filters"].get("anonymous", [])}
        try:
            conditions = json.loads(query.get("filter", "{}"))
            for f, condition in conditions.items():
                if f not in readable:
                    return self.fail(400, f"{table} declares no field named {f} that may be filtered on")
                if f in constrained:
                    return self.fail(400, f"{f} is constrained by the row filter")
                data = [r for r in data if matches(r["values"].get(f), condition)]
            limit = int(query.get("limit", "50"))
            offset = int(query.get("offset", "0"))
        except (ValueError, KeyError, TypeError, AttributeError):
            return self.fail(400, "The request parameters are not valid")
        if not 1 <= limit <= 200 or offset < 0:
            return self.fail(400, "The request parameters are not valid")
        for key in reversed([k for k in query.get("sort", "").split(",") if k]):
            name = key.lstrip("-")
            if name not in readable:
                return self.fail(400, f"{table} declares no field named {name} that may be sorted by")
            data.sort(key=lambda r: sort_key(r["values"].get(name)), reverse=key.startswith("-"))
        page = data[offset:offset + limit]
        out = [{"id": r["id"], "values": {f: r["values"].get(f) for f in fields}} for r in page]
        self.send_json(200, {"data": out, "page_info": {"has_more": offset + limit < len(data), "limit": limit, "offset": offset}})

    def do_POST(self):
        url = urlparse(self.path)
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length)
        if url.path == f"{API}/session/anonymous":
            if not self.manifest.get("auth", {}).get("anonymous"):
                return self.fail(403, "This app does not admit anonymous visitors")
            session_id = uuid.uuid4().hex
            self.sessions[session_id] = set()
            return self.send_json(200, {"token": session_id, "appRole": "anonymous", "expiresIn": 900})
        session_id = self.session()
        if session_id is None:
            return self.fail(401, "No valid session")
        parts = url.path[len(API):].strip("/").split("/")
        if len(parts) < 3 or parts[0] != "tables" or parts[2] != "records":
            return self.fail(404, "Not found")
        table = unquote(parts[1])
        spec = self.table_spec(table, "create")
        if spec is None:
            return

        if len(parts) == 5 and parts[4] == "documents":
            record = parts[3]
            if (table, record) not in self.sessions[session_id] or not self.headers.get("x-ninox-row-receipt"):
                return self.fail(403, "This session did not create that row")
            match = re.search(rb"\r\n\r\n(.*)\r\n--", body, re.DOTALL)
            content = match.group(1) if match else b""
            if len(content) > MAX_ATTACH:
                return self.fail(413, "The file is larger than 1 MiB")
            kind = next((t for magic, t in MAGIC if content.startswith(magic)), None)
            if kind is None and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
                kind = "image/webp"
            if kind is None:
                return self.fail(415, "Only PDF and image files (JPEG, PNG, GIF, WebP) are accepted")
            entry = {"table": table, "record": record, "attachment": kind, "bytes": len(content)}
            self.created.append(entry)
            print("ATTACH", json.dumps(entry))
            return self.send_json(201, {"data": {"documentId": uuid.uuid4().hex}})

        try:
            values = json.loads(body or b"{}")["fields"]
            assert isinstance(values, dict)
        except (ValueError, KeyError, AssertionError):
            return self.fail(400, "The request parameters are not valid")
        for f in values:
            info = spec["fields"].get(f)
            if info is None or "anonymous" not in info["writers"]:
                return self.fail(400, f"{table} declares no field named {f} that may be written")
        record = str(len(self.rows[table]) + 1)
        self.rows[table].append({"id": record, "values": dict(values)})
        self.sessions[session_id].add((table, record))
        entry = {"table": table, "id": record, "fields": values}
        self.created.append(entry)
        print("CREATE", json.dumps(entry))
        self.send_json(201, {"data": {"id": record, "receipt": uuid.uuid4().hex}})


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    folder = Path(sys.argv[1]).resolve()
    port = int(sys.argv[2] if len(sys.argv) > 2 else os.environ.get("PORT") or 8787)
    Handler.app_dir = folder
    Handler.manifest, Handler.model, Handler.rows = load_app(folder)
    print(f"Serving {folder / 'dist'} with a mock Ninox gateway at http://localhost:{port}/")
    sys.stdout.flush()
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
