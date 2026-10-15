#!/usr/bin/env python3
"""Check a Ninox Custom App folder offline, and optionally package it for upload.

Replays the validation the "Deploy custom app" dialog and the server run, so problems show up
before the user uploads anything:

  * app.json: allowed keys, roles, auth, table verbs, fields, row filter grammar, actions
  * names Ninox has to create (lowercase snake_case)
  * seed CSVs: file naming, first-column key, columns that would be dropped, and the field
    types the first deploy will infer from the first 10 rows
  * dist/: index.html at the root, binary files the upload would corrupt, credential-shaped
    files, broken relative links, size and count limits, SDK import
  * SDK calls in dist/ against tables, verbs and fields app.json does not grant (static scan)
  * the subdomain rules (with --subdomain)

Usage:
  python3 check_app.py <app-folder> [--subdomain NAME] [--package OUT_DIR [--zip]]

Exit code 0 when there are no errors, 1 otherwise. Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------------------------
# Limits and rules mirrored from the Ninox deploy pipeline
# ---------------------------------------------------------------------------------------------

ADMIN_ROLE_ID = "000000000000000000000001"
BUILTIN_ROLE_IDS = {
    "000000000000000000000001": "admin",
    "000000000000000000000002": "editor",
    "000000000000000000000003": "viewer",
}
MANIFEST_KEYS = {"actions", "auth", "data", "entry", "name", "roles", "routing"}
AUTH_KEYS = {"anonymous", "providers", "signInRedirect"}
AUTH_PROVIDERS = {"app-user", "ninox"}
TABLE_KEYS = {"read", "create", "write", "delete", "fields", "rowFilter"}
VERBS = ("read", "create", "write", "delete")
FIELD_KEYS = {"read", "write"}
ACTION_KEYS = {"roles", "runAs", "script"}
MAX_NAME = 64
MAX_ROLES = 32
MAX_TABLES = 200
MAX_FIELDS = 500
MAX_ACTIONS = 100
MAX_ROW_FILTER_LEN = 2000
MAX_ROW_FILTER_TERMS = 20
RE_ROLE_NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
RE_ACTION_NAME = re.compile(r"^[a-zA-Z]\w{0,63}$")
RE_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
RE_INTERNAL_NAME = re.compile(r"^[a-z0-9_]+$")
RE_ROLE_ID = re.compile(r"^[0-9a-f]{24}$")

MAX_FILE_BYTES = 5_500_000  # 6 MB JSON body limit per upload request, minus escaping overhead
MAX_RELEASE_BYTES = 250 * 1024 * 1024
MAX_RELEASE_FILES = 5000
MAX_CSV_CHARS = 5_000_000
MAX_SEEDS = 20
MAX_SCHEMA_BODY = 5_800_000  # app.json + all CSVs travel in one 6 MB request
SAMPLE_SIZE = 10

TEXT_EXTENSIONS = {".html", ".htm", ".js", ".mjs", ".css", ".json", ".map", ".svg", ".txt", ".xml",
                   ".webmanifest", ".csv"}
KNOWN_BINARY_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".woff",
                           ".woff2", ".ttf", ".eot", ".otf", ".mp4", ".webm", ".mp3", ".pdf", ".wasm",
                           ".zip"}
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
JUNK_DIRS = {"__MACOSX", ".git", "node_modules"}
CREDENTIAL_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(^|/)\.env($|\.|/)", r"(^|/)\.npmrc$", r"(^|/)\.netrc$", r"(^|/)\.git/", r"(^|/)\.aws/",
        r"(^|/)id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$", r"\.(pem|pfx|p12|key|keystore|jks)$",
        r"(^|/)credentials\.(json|ya?ml)$", r"(^|/)service-account[^/]*\.json$",
        r"(^|/)secrets?\.(json|ya?ml)$",
    )
]
RESERVED_HANDLES = {
    "about", "account", "accounts", "admin", "api", "app", "apps", "assets", "auth", "billing",
    "blog", "cdn", "dashboard", "dev", "docs", "help", "internal", "login", "mail", "my", "ninox",
    "pay", "payment", "payments", "preview", "root", "secure", "security", "signin", "signup",
    "smtp", "staging", "static", "status", "support", "test", "verify", "wallet", "web", "www",
}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def warn(self, where: str, message: str) -> None:
        self.warnings.append(f"{where}: {message}")

    def note(self, where: str, message: str) -> None:
        self.notes.append(f"{where}: {message}")


# ---------------------------------------------------------------------------------------------
# Row filter grammar: field op literal [and field op literal ...]
# ---------------------------------------------------------------------------------------------

RE_FILTER_TOKEN = re.compile(
    r"""\s*(?:
        (?P<string>'[^'\\]*'|"[^"\\]*")
      | (?P<number>-?\d+(?:\.\d+)?)
      | (?P<op>>=|<=|!=|=|>|<)
      | (?P<word>[A-Za-z_]\w*)
    )""",
    re.VERBOSE,
)


def parse_row_filter(expression: str) -> tuple[list[tuple[str, str, object]] | None, str | None]:
    """Returns ([(field, op, literal)], None) or (None, reason)."""
    tokens: list[tuple[str, str]] = []
    position = 0
    text = expression.strip()
    while position < len(text):
        match = RE_FILTER_TOKEN.match(text, position)
        if not match or match.end() == position:
            return None, f"cannot read {text[position:position + 20]!r}"
        kind = match.lastgroup
        tokens.append((kind, match.group(kind)))
        position = match.end()
        while position < len(text) and text[position].isspace():
            position += 1
    terms: list[tuple[str, str, object]] = []
    index = 0
    while True:
        if index + 3 > len(tokens):
            return None, "expected <field> <operator> <literal>"
        (k1, field), (k2, op), (k3, raw) = tokens[index:index + 3]
        if k1 != "word" or field.lower() in {"and", "or", "true", "false", "null", "not"}:
            return None, f"expected a field name, got {field!r}"
        if k2 != "op":
            return None, f"expected an operator after {field!r}, got {op!r}"
        if k3 == "string":
            literal: object = raw[1:-1]
        elif k3 == "number":
            literal = float(raw) if "." in raw else int(raw)
        elif k3 == "word" and raw.lower() in {"true", "false"}:
            literal = raw.lower() == "true"
            if op not in {"=", "!="}:
                return None, f"{raw} can only be compared with = or !="
        elif k3 == "word" and raw.lower() == "null":
            literal = None
            if op not in {"=", "!="}:
                return None, "null can only be compared with = or !="
        else:
            return None, f"expected a literal (true, false, null, number or quoted text), got {raw!r}"
        if any(t[0] == field for t in terms):
            return None, f"{field!r} appears twice; use one condition per field"
        terms.append((field, op, literal))
        index += 3
        if index == len(tokens):
            break
        kind, word = tokens[index]
        if kind != "word" or word.lower() != "and":
            return None, f"conditions must be joined by 'and', got {word!r} ('or' and parentheses are not supported)"
        index += 1
    if len(terms) > MAX_ROW_FILTER_TERMS:
        return None, f"at most {MAX_ROW_FILTER_TERMS} conditions"
    return terms, None


# ---------------------------------------------------------------------------------------------
# Field type inference, ported from the Ninox CSV importer (first 10 rows)
# ---------------------------------------------------------------------------------------------

RE_US_NUMBER = re.compile(r"^-?\d{1,3}(,\d{3})*(\.\d+)?$")
RE_EU_NUMBER = re.compile(r"^-?\d+(\.\d{3})*(,\d+)?$")
RE_PLAIN_NUMBER = re.compile(r"^-?\d{1,100}(?:\.\d{1,100})?(?:[eE][+-]?\d{1,100})?$")
RE_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
RE_SEP_DATE = re.compile(r"^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}")
RE_TIME = re.compile(r"\d{1,2}:\d{2}")
RE_EPOCH = re.compile(r"^\d{10,13}$")
RE_RANGE_SEP = re.compile(r"\s-\s")
RE_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
RE_EMAIL = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
RE_URL = re.compile(r"^https?://")
RE_PHONE = re.compile(r"^[\d\s\-+()]{7,}$")


def _date_like(value: str) -> bool:
    return bool(RE_ISO_DATE.match(value) or RE_SEP_DATE.match(value))


def infer_type(samples: list[str | None], field_name: str) -> str:
    if samples and all(isinstance(s, str) and s.lower() in {"0", "1", "false", "no", "true", "yes"} for s in samples):
        return "boolean"
    if samples:
        ranges = 0
        for s in samples:
            if isinstance(s, str):
                parts = RE_RANGE_SEP.split(s.strip())
                if len(parts) == 2 and _date_like(parts[0]) and _date_like(parts[1]):
                    ranges += 1
        if ranges / len(samples) > 0.8:
            return "appointment"
        dates, has_time = 0, False
        for s in samples:
            if not isinstance(s, str):
                continue
            t = s.strip()
            if _date_like(t):
                dates += 1
                has_time = has_time or bool(RE_TIME.search(t))
            elif RE_EPOCH.match(t):
                dates += 1
                has_time = True
        if dates / len(samples) > 0.8:
            return "timestamp" if has_time else "date"

    def is_number(s: str | None) -> bool:
        if s is None or s.strip() == "":
            return False
        t = s.strip()
        if "," in t:
            return bool(RE_US_NUMBER.match(t) or RE_EU_NUMBER.match(t))
        cleaned = re.sub(r"\s", "", t)
        return len(cleaned) <= 100 and bool(RE_PLAIN_NUMBER.match(cleaned))

    if samples and all(is_number(s) for s in samples):
        return "number"
    if samples and sum(1 for s in samples if isinstance(s, str) and RE_HEX_COLOR.match(s.strip())) / len(samples) > 0.7:
        return "color"
    tokens = [t for t in re.split(r"[^a-z0-9]+", field_name.lower()) if t]
    if any(t in {"email", "mail"} for t in tokens):
        return "text (email)"
    if any(t in {"phone", "tel"} for t in tokens):
        return "text (phone)"
    if any(t in {"url", "website", "link"} for t in tokens):
        return "text (url)"
    size = len(samples) or 1
    strs = [s.strip() for s in samples if isinstance(s, str)]
    if sum(1 for s in strs if RE_EMAIL.match(s)) / size > 0.7:
        return "text (email)"
    if sum(1 for s in strs if RE_URL.match(s)) / size > 0.7:
        return "text (url)"
    if sum(1 for s in strs if RE_PHONE.match(s)) / size > 0.7:
        return "text (phone)"
    return "text"


# ---------------------------------------------------------------------------------------------
# app.json
# ---------------------------------------------------------------------------------------------


def check_internal_name(report: Report, where: str, name: str) -> None:
    ok = (1 <= len(name) <= 100 and RE_INTERNAL_NAME.match(name) and not name.startswith("_")
          and not name.endswith("_") and "__" not in name)
    if not ok:
        report.error(where, f"{name!r} is not a valid internal name: use lowercase letters, digits and "
                            "single underscores, not starting or ending with '_' (Ninox cannot create it)")


def check_role_list(report: Report, where: str, value: object, roles: dict) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(r, str) for r in value):
        report.error(where, "expected an array of app role names")
        return []
    seen: set[str] = set()
    for role in value:
        if role not in roles:
            report.error(where, f"role {role!r} is not declared in 'roles'")
        if role in seen:
            report.error(where, f"role {role!r} is listed twice")
        seen.add(role)
    return list(value)


def check_manifest(report: Report, manifest: object) -> dict:
    """Validates the manifest and returns a normalized model used by the other checks."""
    model: dict = {"tables": {}}
    if not isinstance(manifest, dict):
        report.error("app.json", "must be a JSON object")
        return model
    for key in manifest:
        if key not in MANIFEST_KEYS:
            report.error(f"app.json.{key}", f"unknown key (allowed: {', '.join(sorted(MANIFEST_KEYS))})")

    name = manifest.get("name")
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= MAX_NAME) or RE_CONTROL.search(name or ""):
        report.error("app.json.name", f"required: text of 1-{MAX_NAME} characters")

    entry = manifest.get("entry", "dist/index.html")
    segments = entry.split("/") if isinstance(entry, str) else []
    if (not isinstance(entry, str) or not entry or entry.startswith("/") or "\\" in entry
            or RE_CONTROL.search(entry) or any(s in {"", ".", ".."} for s in segments)
            or not entry.lower().endswith(".html")):
        report.error("app.json.entry", "must be a relative path ending in .html, like 'dist/index.html'")
    else:
        model["entry"] = entry[len("dist/"):] if entry.startswith("dist/") else entry
        if model["entry"] != "index.html":
            report.warn("app.json.entry", "the app's root URL always serves dist/index.html; keep entry 'dist/index.html'")

    routing = manifest.get("routing", "spa")
    if routing not in {"spa", "static"}:
        report.error("app.json.routing", "must be 'spa' or 'static'")

    roles = manifest.get("roles")
    if not isinstance(roles, dict) or not roles:
        report.error("app.json.roles", "required: an object mapping app role names to workspace role IDs")
        roles = {}
    if len(roles) > MAX_ROLES:
        report.error("app.json.roles", f"at most {MAX_ROLES} roles")
    for role, ref in roles.items():
        where = f"app.json.roles.{role}"
        if not RE_ROLE_NAME.match(role):
            report.error(where, "role names are lowercase, start with a letter, and use letters, digits, '-'")
        if not isinstance(ref, str) or not ref:
            report.error(where, "value must be a workspace role ID (text)")
        elif ref == ADMIN_ROLE_ID:
            report.error(where, "an app role may not map to the admin role")
        elif not RE_ROLE_ID.match(ref):
            report.error(where, f"{ref!r} is not a role ID; role names are not resolved. "
                                "Use the viewer ID 000000000000000000000003")
        elif ref not in BUILTIN_ROLE_IDS:
            report.note(where, f"{ref} is a custom workspace role ID; it must exist in the target workspace")
    model["roles"] = roles

    auth = manifest.get("auth", {})
    if not isinstance(auth, dict):
        report.error("app.json.auth", "must be an object")
        auth = {}
    for key in auth:
        if key not in AUTH_KEYS:
            report.error(f"app.json.auth.{key}", f"unknown key (allowed: {', '.join(sorted(AUTH_KEYS))})")
    anonymous = auth.get("anonymous")
    if anonymous is not None and not isinstance(anonymous, bool):
        report.error("app.json.auth.anonymous", "must be true or false")
    providers = auth.get("providers", [])
    if not isinstance(providers, list) or any(p not in AUTH_PROVIDERS for p in providers):
        report.error("app.json.auth.providers", f"allowed values: {', '.join(sorted(AUTH_PROVIDERS))}")
        providers = []
    elif providers:
        report.warn("app.json.auth.providers", "sign-in providers are not available yet; visitors are anonymous. Remove 'providers'")
    redirect = auth.get("signInRedirect")
    if redirect is not None and (not isinstance(redirect, str) or not redirect.startswith("/") or redirect.startswith("//")):
        report.error("app.json.auth.signInRedirect", "must be a path starting with '/'")
    if anonymous is True and "anonymous" not in roles:
        report.error("app.json.auth.anonymous", "auth.anonymous is true, so 'roles' must declare 'anonymous'")
    if "anonymous" in roles and anonymous is not True:
        report.error("app.json.roles.anonymous", "the 'anonymous' role requires \"auth\": {\"anonymous\": true}")
    if anonymous is not True and not providers:
        report.error("app.json.auth", "no visitor can get a session: set \"anonymous\": true")

    data = manifest.get("data", {})
    if not isinstance(data, dict):
        report.error("app.json.data", "must be an object")
        data = {}
    if len(data) > MAX_TABLES:
        report.error("app.json.data", f"at most {MAX_TABLES} tables")
    if not data:
        report.warn("app.json.data", "no tables declared; the app cannot read or write anything")
    for table, spec in data.items():
        where = f"app.json.data.{table}"
        check_internal_name(report, where, table)
        if not isinstance(spec, dict):
            report.error(where, "must be an object")
            continue
        for key in spec:
            if key not in TABLE_KEYS:
                hint = " (there is no update verb; only read and create are served)" if key in {"update", "upsert"} else ""
                report.error(f"{where}.{key}", f"unknown key{hint}")
        verbs = {v: check_role_list(report, f"{where}.{v}", spec[v], roles) for v in VERBS if v in spec}
        if not any(verbs.values()):
            report.error(where, "grants nothing: give at least one role 'read' or 'create'")
        for verb in ("write", "delete"):
            if verbs.get(verb):
                report.warn(f"{where}.{verb}", f"'{verb}' is accepted but not served yet; the app cannot {'update' if verb == 'write' else 'delete'} rows")

        fields_spec = spec.get("fields")
        fields: dict[str, dict] = {}
        if fields_spec is None:
            report.error(where, "has no 'fields' block, so the app can see and write nothing in it")
        elif not isinstance(fields_spec, dict):
            report.error(f"{where}.fields", "must be an object of field name -> {}")
        else:
            if len(fields_spec) > MAX_FIELDS:
                report.error(f"{where}.fields", f"at most {MAX_FIELDS} fields")
            for field, fspec in fields_spec.items():
                fwhere = f"{where}.fields.{field}"
                check_internal_name(report, fwhere, field)
                if not isinstance(fspec, dict):
                    report.error(fwhere, "expected an object; use {} to inherit the table roles")
                    continue
                for key in fspec:
                    if key not in FIELD_KEYS:
                        report.error(f"{fwhere}.{key}", "unknown key (allowed: read, write)")
                readers = check_role_list(report, f"{fwhere}.read", fspec["read"], roles) if "read" in fspec else verbs.get("read", [])
                writers = check_role_list(report, f"{fwhere}.write", fspec["write"], roles) if "write" in fspec else verbs.get("create", [])
                fields[field] = {"readers": set(readers), "writers": set(writers)}
            if verbs.get("read") and not any(f["readers"] for f in fields.values()):
                report.error(f"{where}.fields", "every field is hidden, so reading the table fails with 403")

        row_filters: dict[str, list] = {}
        rf = spec.get("rowFilter")
        if rf is not None:
            if not isinstance(rf, dict):
                report.error(f"{where}.rowFilter", "must be an object of role -> expression")
            else:
                granted = {r for v in verbs.values() for r in v}
                for role, expression in rf.items():
                    rwhere = f"{where}.rowFilter.{role}"
                    if role not in roles:
                        report.error(rwhere, f"role {role!r} is not declared in 'roles'")
                    elif role not in granted:
                        report.error(rwhere, f"role {role!r} has no access to this table, so the filter would never apply")
                    if not isinstance(expression, str) or not expression.strip():
                        report.error(rwhere, "must be a non-empty expression")
                        continue
                    if len(expression) > MAX_ROW_FILTER_LEN:
                        report.error(rwhere, f"at most {MAX_ROW_FILTER_LEN} characters")
                    terms, reason = parse_row_filter(expression)
                    if terms is None:
                        report.error(rwhere, f"not enforceable: {reason}. Supported: <field> <op> <literal> joined by 'and'")
                        continue
                    for field, _, _ in terms:
                        check_internal_name(report, rwhere, field)
                    row_filters[role] = terms
        model["tables"][table] = {"verbs": verbs, "fields": fields, "row_filters": row_filters}

    actions = manifest.get("actions")
    if actions is not None:
        if not isinstance(actions, dict):
            report.error("app.json.actions", "must be an object")
        else:
            if len(actions) > MAX_ACTIONS:
                report.error("app.json.actions", f"at most {MAX_ACTIONS} actions")
            for action, aspec in actions.items():
                awhere = f"app.json.actions.{action}"
                if not RE_ACTION_NAME.match(action):
                    report.error(awhere, "action names start with a letter and use letters, digits, '_'")
                if not isinstance(aspec, dict):
                    report.error(awhere, "must be an object")
                    continue
                for key in aspec:
                    if key not in ACTION_KEYS:
                        report.error(f"{awhere}.{key}", "unknown key (allowed: roles, runAs, script)")
                if not isinstance(aspec.get("script"), str) or not aspec.get("script"):
                    report.error(f"{awhere}.script", "required: the name of a module script in the target module")
                action_roles = check_role_list(report, f"{awhere}.roles", aspec.get("roles"), roles)
                if not action_roles:
                    report.error(f"{awhere}.roles", "required: at least one role")
                if "runAs" in aspec and aspec["runAs"] not in roles:
                    report.error(f"{awhere}.runAs", "must be a declared role")
            report.warn("app.json.actions", "actions cannot be called by an app yet; the deploy only checks that the module script exists. Remove 'actions'")
    return model


# ---------------------------------------------------------------------------------------------
# Folder, CSVs, dist/
# ---------------------------------------------------------------------------------------------


def is_junk(rel: str) -> bool:
    parts = rel.split("/")
    return parts[-1] in JUNK_NAMES or parts[-1].startswith("._") or any(p in JUNK_DIRS for p in parts)


def list_files(root: Path) -> list[str]:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files.append(path.relative_to(root).as_posix())
    return files


def csv_table_name(rel: str) -> str:
    return re.sub(r"[-_]import$", "", rel[:-4], flags=re.IGNORECASE)


def check_csvs(report: Report, root: Path, files: list[str], model: dict) -> tuple[dict, int]:
    """Returns ({table: csv path}, total characters of all seed CSVs)."""
    tables = model["tables"]
    by_lower = {t.lower(): t for t in tables}
    seeds: dict[str, str] = {}
    total_chars = 0
    for rel in files:
        if not rel.lower().endswith(".csv") or is_junk(rel):
            continue
        if "/" in rel:
            if not rel.startswith("dist/"):
                report.warn(rel, "CSVs are only read from the top level of the folder, next to app.json; this one is ignored")
            continue
        table = by_lower.get(csv_table_name(rel).lower())
        if table is None:
            report.warn(rel, f"matches no table in app.json (expected '<table>-import.csv'); it is not imported")
            continue
        if table in seeds:
            report.error(rel, f"second seed for table {table!r} (also {seeds[table]})")
            continue
        seeds[table] = rel
    if len(seeds) > MAX_SEEDS:
        report.error("seed CSVs", f"at most {MAX_SEEDS} seed CSVs per deploy")

    for table, rel in seeds.items():
        raw = (root / rel).read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            report.error(rel, "starts with a UTF-8 BOM, which corrupts the first column name; save as UTF-8 without BOM")
            raw = raw[3:]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            report.error(rel, "is not UTF-8; save it as UTF-8")
            continue
        total_chars += len(text)
        if len(text) > MAX_CSV_CHARS:
            report.error(rel, f"{len(text):,} characters; one seed may hold at most {MAX_CSV_CHARS:,}")
        reader = csv.reader(io.StringIO(text))
        rows = [r for r in reader if any(cell.strip() for cell in r)]
        if not rows:
            report.error(rel, "is empty; it needs a header row")
            continue
        header = [h.strip() for h in rows[0]]
        body = rows[1:]
        if len(header) == 1 and ";" in rows[0][0]:
            report.warn(rel, "looks semicolon-delimited; use commas")
        spec = tables[table]
        declared = spec["fields"]
        filter_fields = {f for terms in spec["row_filters"].values() for f, _, _ in terms}
        created = set(declared) | filter_fields

        dropped = [h for h in header if h not in created]
        if dropped:
            report.warn(rel, f"columns not declared in app.json are dropped: {', '.join(dropped)}")
        if len(set(header)) != len(header):
            report.error(rel, "repeats a column name")
        key = header[0]
        if key not in created:
            report.error(rel, f"first column {key!r} is not a declared field; later deploys match rows on it and fail")
        seen: set[str] = set()
        empty, padded, repeated, ragged = [], [], [], []
        for number, row in enumerate(body, start=2):
            value = row[0] if row else ""
            if value.strip() == "":
                empty.append(str(number))
            elif value != value.strip():
                padded.append(str(number))
            elif value in seen and value not in repeated:
                repeated.append(value)
            seen.add(value)
            if len(row) != len(rows[0]):
                ragged.append(str(number))
        merge_hint = ("the first deploy imports it, but every later deploy merges rows on the first column and "
                      "refuses this CSV. Put a unique key column first")
        if empty:
            report.error(rel, f"empty first column {key!r} on line(s) {', '.join(empty[:5])}: {merge_hint}")
        if padded:
            report.error(rel, f"first column {key!r} padded with spaces on line(s) {', '.join(padded[:5])}: {merge_hint}")
        if repeated:
            report.error(rel, f"first column {key!r} repeats {', '.join(repeated[:5])}{' …' if len(repeated) > 5 else ''}: {merge_hint}")
        if ragged:
            report.warn(rel, f"line(s) {', '.join(ragged[:5])} have a different number of cells than the header")
        if not body:
            report.warn(rel, "has a header but no rows; every field becomes Text")

        samples_by_field = {}
        for index, name in enumerate(header):
            samples_by_field[name] = [r[index] if index < len(r) else None for r in body[:SAMPLE_SIZE]]
        spec["seed_types"] = {}
        for field in sorted(created, key=lambda f: (f not in header, header.index(f) if f in header else 0, f)):
            if field in samples_by_field:
                inferred = infer_type(samples_by_field[field], field)
                samples = samples_by_field[field]
                if inferred == "boolean" and all(s in {"0", "1"} for s in samples):
                    report.warn(f"{rel} [{field}]", "only 0/1 in the first rows, so it becomes a boolean field. "
                                                    "Add a row with another number among the first 10 if it is a number")
                if inferred.startswith("text") and any(s is None or s.strip() == "" for s in samples):
                    rest = [s for s in samples if s is not None and s.strip() != ""]
                    if rest and infer_type(rest, field) in {"number", "boolean", "date", "timestamp"}:
                        report.warn(f"{rel} [{field}]", "an empty cell in the first 10 rows turns this column into Text; fill it or move the row down")
            else:
                inferred = "text (no CSV column)"
            spec["seed_types"][field] = inferred
    return seeds, total_chars


def check_filters_against_types(report: Report, model: dict) -> None:
    for table, spec in model["tables"].items():
        types = spec.get("seed_types")
        for role, terms in spec["row_filters"].items():
            for field, op, literal in terms:
                where = f"app.json.data.{table}.rowFilter.{role}"
                inferred = types.get(field) if types else "text (no seed CSV)"
                if isinstance(literal, bool) and inferred != "boolean":
                    report.warn(where, f"compares {field!r} with {str(literal).lower()}, but {field!r} will be {inferred}; "
                                       "the filter would match nothing. Seed it with true/false values, or fix the type in Ninox")
                elif isinstance(literal, (int, float)) and not isinstance(literal, bool) and inferred != "number":
                    report.warn(where, f"compares {field!r} with a number, but {field!r} will be {inferred}")


def check_dist(report: Report, root: Path, files: list[str], model: dict) -> list[str]:
    """Returns the dist-relative paths that will be uploaded."""
    dist = root / "dist"
    if not dist.is_dir():
        report.error("dist/", "missing: the dialog needs a dist folder next to app.json")
        return []
    uploads: list[str] = []
    total = 0
    for rel in files:
        parts = rel.split("/")
        if "dist" not in parts:
            continue
        if parts[0] != "dist":
            report.error(rel, "sits in a 'dist' folder outside the top-level dist/; the dialog uploads it too and it can "
                              "overwrite your files. Upload the clean folder that --package writes")
            continue
        sub = "/".join(parts[1:])
        if is_junk(rel):
            report.warn(rel, "junk file; --package leaves it out")
            continue
        path = root / rel
        size = path.stat().st_size
        total += size
        uploads.append(sub)
        ext = path.suffix.lower()
        if any(p.search(sub) for p in CREDENTIAL_PATTERNS):
            report.error(rel, "looks like a credential file; the deploy refuses it, and the app is public")
            continue
        if sub.split("/")[0] == "_ninox":
            report.error(rel, "the path /_ninox/ is reserved for the Ninox API")
        if "%" in sub or "\\" in sub or len(sub) > 1024:
            report.error(rel, "invalid upload path ('%', '\\', or longer than 1024 characters)")
        if size > MAX_FILE_BYTES:
            report.error(rel, f"{size:,} bytes; one file can be at most about {MAX_FILE_BYTES:,} bytes")
        if ext in KNOWN_BINARY_EXTENSIONS:
            report.error(rel, "binary file: the upload sends every file as text and corrupts it. "
                              "Use SVG, CSS, an emoji, or an external https:// URL instead")
            continue
        try:
            path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            report.error(rel, "not valid UTF-8 text: the upload would corrupt it")
            continue
        if ext not in TEXT_EXTENSIONS:
            report.warn(rel, f"'{ext or 'no extension'}' is served as a download (application/octet-stream)")
    if len(uploads) > MAX_RELEASE_FILES:
        report.error("dist/", f"{len(uploads)} files; at most {MAX_RELEASE_FILES}")
    if total > MAX_RELEASE_BYTES:
        report.error("dist/", f"{total:,} bytes; at most {MAX_RELEASE_BYTES:,}")
    if "index.html" not in uploads:
        report.error("dist/index.html", "missing: the app's root URL serves dist/index.html")
    entry = model.get("entry")
    if entry and entry not in uploads:
        report.error("app.json.entry", f"dist/{entry} does not exist")
    return uploads


RE_HTML_REF = re.compile(r"""<(?:script|link|img|source|iframe)\b[^>]*?\b(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
RE_JS_IMPORT = re.compile(r"""(?:\bimport\s*(?:[\w*{}\s,]+\s*from\s*)?|\bexport\s*[\w*{}\s,]+\s*from\s*|\bimport\s*\(\s*)["']([^"']+)["']""")


def check_links_and_sdk(report: Report, root: Path, uploads: list[str], model: dict) -> None:
    upload_set = set(uploads)
    sources: dict[str, str] = {}
    for sub in uploads:
        if Path(sub).suffix.lower() in {".html", ".htm", ".js", ".mjs"}:
            try:
                sources[sub] = (root / "dist" / sub).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                pass

    for sub, text in sources.items():
        is_html = sub.lower().endswith((".html", ".htm"))
        refs = [(ref, False) for ref in RE_HTML_REF.findall(text)] if is_html else []
        refs += [(ref, True) for ref in RE_JS_IMPORT.findall(text)]
        for ref, is_import in refs:
            if re.match(r"^(?:[a-z][a-z0-9+.-]*:|//|#|\$|\{)", ref, re.IGNORECASE):
                continue
            if is_import and not ref.startswith((".", "/")):
                if ref.startswith("@ninox/app-sdk"):
                    report.error(f"dist/{sub}", "imports '@ninox/app-sdk' by package name; browsers cannot resolve it. "
                                                "Import './ninox-app-sdk.js' (copied from the skill's assets) or bundle it")
                else:
                    report.error(f"dist/{sub}", f"bare import {ref!r} cannot load in the browser; bundle it or import a relative file")
                continue
            clean = ref.split("?")[0].split("#")[0]
            if clean.startswith("/"):
                target = clean.lstrip("/")
            else:
                target = os.path.normpath(os.path.join(os.path.dirname(sub), clean)).replace("\\", "/")
            if target.endswith("/") or target in {"", "."}:
                target = (target.rstrip("/") + "/index.html").lstrip("/")
            if target not in upload_set:
                report.error(f"dist/{sub}", f"references {ref!r}, which is not in dist/")

    all_code = "\n".join(sources.values())
    if "createNinoxApp" not in all_code:
        report.warn("dist/", "no createNinoxApp found; the app does not talk to Ninox (fine for a static page)")
    elif not any("NinoxAppError" in t and "session/anonymous" in t for t in sources.values()):
        report.error("dist/", "createNinoxApp is used but the SDK is not in dist/; copy assets/template-app/dist/ninox-app-sdk.js")

    scan_sdk_calls(report, sources, model)


RE_TABLE_CALL = re.compile(r"""\.table\(\s*(['"`])([^'"`]+)\1\s*\)(?:\s*\.\s*(list|get|create|attach)\s*\()?""")


def object_literal_keys(text: str, start: int) -> list[str] | None:
    """Keys of the object literal starting at text[start] == '{', top level only."""
    if start >= len(text) or text[start] != "{":
        return None
    depth, quote, segment, segments = 0, None, [], []
    i = start
    while i < len(text):
        c = text[i]
        if quote:
            segment.append(c)
            if c == "\\":
                segment.append(text[i + 1] if i + 1 < len(text) else "")
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
            segment.append(c)
        elif c in "{[(":
            depth += 1
            if depth > 1:
                segment.append(c)
        elif c in "}])":
            depth -= 1
            if depth == 0:
                segments.append("".join(segment))
                break
            segment.append(c)
        elif c == "," and depth == 1:
            segments.append("".join(segment))
            segment = []
        else:
            segment.append(c)
        i += 1
    keys = []
    for seg in segments:
        seg = seg.strip()
        if not seg or seg.startswith("...") or seg.startswith("["):
            continue
        m = re.match(r"""^(?:(['"])([^'"]+)\1|([A-Za-z_$][\w$]*))\s*(?::|$|\()""", seg)
        if m:
            keys.append(m.group(2) or m.group(3))
    return keys


def scan_sdk_calls(report: Report, sources: dict[str, str], model: dict) -> None:
    tables = model["tables"]
    needed = {"list": "read", "get": "read", "create": "create", "attach": "create"}
    for sub, text in sources.items():
        if "NinoxAppError" in text and "session/anonymous" in text:
            continue  # the SDK itself
        for m in RE_TABLE_CALL.finditer(text):
            table, method = m.group(2), m.group(3)
            where = f"dist/{sub} [table('{table}')]"
            if table not in tables:
                report.error(where, f"table {table!r} is not declared in app.json")
                continue
            spec = tables[table]
            if method and not spec["verbs"].get(needed[method]):
                report.error(where, f".{method}() needs '{needed[method]}' on {table!r} in app.json")
            if method == "get" and spec["row_filters"]:
                report.error(where, f".get() is always refused on {table!r} because it has a rowFilter; use .list() with a filter")
            if method == "create":
                keys = object_literal_keys(text, m.end() + len(text[m.end():]) - len(text[m.end():].lstrip()))
                for key in keys or []:
                    field = spec["fields"].get(key)
                    if field is None:
                        report.error(where, f"create() writes {key!r}, which is not declared in fields")
                    elif not field["writers"]:
                        report.error(where, f"create() writes {key!r}, which no role may write")


def check_subdomain(report: Report, handle: str) -> None:
    where = f"subdomain {handle!r}"
    if not 3 <= len(handle) <= 40:
        report.error(where, "must be 3 to 40 characters")
    if not re.match(r"^[a-z0-9-]+$", handle) or handle.startswith("-") or handle.endswith("-") or "--" in handle:
        report.error(where, "use lowercase letters, digits and single hyphens, not at the start or end")
    if handle.startswith("xn--"):
        report.error(where, "may not start with 'xn--'")
    if handle in RESERVED_HANDLES:
        report.error(where, "is reserved; pick a more specific name")


# ---------------------------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------------------------


def package(root: Path, out: Path, seeds: dict[str, str], uploads: list[str], make_zip: bool) -> None:
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"--package: {out} exists and is not empty; choose a new folder")
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / "app.json", out / "app.json")
    for rel in sorted(set(seeds.values())):
        shutil.copy2(root / rel, out / rel)
    for sub in uploads:
        target = out / "dist" / sub
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / "dist" / sub, target)
    print(f"\nPackaged upload folder: {out.resolve()}")
    if make_zip:
        zip_path = out.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(out.rglob("*")):
                if path.is_file():
                    zf.write(path, Path(out.name) / path.relative_to(out))
        print(f"Zip for sharing: {zip_path.resolve()} (unzip before uploading; the dialog takes a folder)")


# ---------------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Check (and package) a Ninox Custom App folder.")
    parser.add_argument("folder", type=Path, help="the app folder containing app.json and dist/")
    parser.add_argument("--subdomain", help="the subdomain the user will enter")
    parser.add_argument("--package", type=Path, metavar="OUT_DIR", help="write a clean upload folder (only with 0 errors)")
    parser.add_argument("--zip", action="store_true", help="with --package, also write OUT_DIR.zip")
    args = parser.parse_args()

    root: Path = args.folder
    report = Report()
    if not (root / "app.json").is_file():
        nested = [p for p in root.glob("*/app.json")]
        hint = f" (found {nested[0].parent}; select that folder)" if nested else ""
        print(f"ERROR {root}: no app.json at the top level{hint}")
        return 1

    raw = (root / "app.json").read_bytes()
    try:
        manifest = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"ERROR app.json: not valid JSON: {error}")
        return 1
    if raw.startswith(b"\xef\xbb\xbf"):
        report.warn("app.json", "starts with a UTF-8 BOM; save without BOM to be safe")

    model = check_manifest(report, manifest)
    files = list_files(root)
    seeds, csv_chars = check_csvs(report, root, files, model)
    check_filters_against_types(report, model)
    uploads = check_dist(report, root, files, model)
    check_links_and_sdk(report, root, uploads, model)
    if args.subdomain is not None:
        check_subdomain(report, args.subdomain)
    if len(raw) + csv_chars > MAX_SCHEMA_BODY:
        report.error("seed CSVs", f"app.json and the CSVs total {len(raw) + csv_chars:,} characters; the deploy sends them "
                                  "in one request of at most about 6 MB. Seed fewer rows and add the rest in Ninox")
    ignored = sorted({r.split('/')[0] for r in files
                      if not r.startswith("dist/") and r != "app.json" and r not in seeds.values() and not is_junk(r)})
    if ignored:
        report.note("folder", f"ignored by the upload: {', '.join(ignored)}")

    # Field types the first deploy will create
    print("Field types the first deploy creates (existing fields are never changed):")
    for table, spec in model["tables"].items():
        verbs = ", ".join(v for v, roles in spec["verbs"].items() if roles) or "-"
        seed = seeds.get(table)
        print(f"  {table}  [{verbs}]  seed: {seed or 'none'}")
        filter_fields = [f for terms in spec["row_filters"].values() for f, _, _ in terms]
        names = list(dict.fromkeys(list(spec["fields"]) + filter_fields))
        types = spec.get("seed_types", {})
        for field in names:
            access = []
            info = spec["fields"].get(field)
            if info is None:
                access.append("row filter only, not readable")
            elif spec["verbs"].get("read") and not info["readers"]:
                access.append("hidden")
            print(f"    {field:<28} {types.get(field, 'text (no seed CSV)'):<22} {', '.join(access)}")
        if not seed and any(spec["fields"]):
            report.note(f"table {table}", "no seed CSV: all fields are created as Text; fix types in Ninox after the "
                                          "first deploy, or create the table with typed fields beforehand")

    print()
    for label, items in (("ERROR", report.errors), ("WARN ", report.warnings), ("NOTE ", report.notes)):
        for item in items:
            print(f"{label} {item}")
    print(f"\n{len(report.errors)} error(s), {len(report.warnings)} warning(s), {len(report.notes)} note(s)")

    if args.package:
        if report.errors:
            print("Not packaged: fix the errors first.")
        else:
            package(root, args.package, seeds, uploads, args.zip)
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
