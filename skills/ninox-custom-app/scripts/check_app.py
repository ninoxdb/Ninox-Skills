#!/usr/bin/env python3
"""Check a Ninox Custom App folder offline, and optionally package it for upload.

Replays the validation the "Deploy custom app" dialog and the server run, so problems show up
before the user uploads anything:

  * app.json: allowed keys, roles, auth, table verbs, fields, row filter grammar, actions
  * names Ninox has to create (lowercase snake_case)
  * seed CSVs: file naming, first-column key, columns that would be dropped, and the field
    types the first deploy will infer from the first 10 rows
  * dist/: index.html at the root, binary files the upload would corrupt, credential-shaped
    files, broken relative links and imports, size and count limits, SDK presence
  * SDK calls in dist/ against tables, verbs and fields app.json does not grant (static scan of
    literal table names; computed names are reported as unverified)
  * the subdomain rules (with --subdomain)

Usage:
  python3 check_app.py <app-folder> [--subdomain NAME] [--package OUT_DIR [--zip] [--overwrite]]

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
ALLOWED_TOP_LEVEL = {"actions", "auth", "data", "entry", "name", "roles", "routing"}
ALLOWED_VISITOR_OPTIONS = {"anonymous", "providers", "signInRedirect"}
SIGN_IN_PROVIDERS = {"app-user", "ninox"}
ALLOWED_TABLE_OPTIONS = {"read", "create", "write", "delete", "fields", "rowFilter"}
VERBS = ("read", "create", "write", "delete")
ALLOWED_FIELD_OPTIONS = {"read", "write"}
ALLOWED_ACTION_OPTIONS = {"roles", "runAs", "script"}
MAX_NAME = 64
MAX_ROLES = 32
MAX_TABLES = 200
MAX_FIELDS = 500
MAX_ACTIONS = 100
MAX_ROW_FILTER_LEN = 2000
MAX_ROW_FILTER_TERMS = 20
RE_ROLE_NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
RE_ACTION_NAME = re.compile(r"[a-zA-Z]\w{0,63}", re.ASCII)
RE_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
RE_INTERNAL_NAME = re.compile(r"[a-z0-9_]+")
RE_ROLE_ID = re.compile(r"[0-9a-f]{24}")
RE_HANDLE = re.compile(r"[a-z0-9-]+")

MAX_FILE_BYTES = 5_500_000  # 6 MB JSON body limit per upload request, minus escaping overhead
MAX_RELEASE_BYTES = 250 * 1024 * 1024
MAX_RELEASE_FILES = 5000
MAX_APP_JSON_CHARS = 1_000_000
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
SDK_FILE_NAME = "ninox-app-sdk.js"
PACKAGE_MARKER = ".packaged-by-check-app"
CREDENTIAL_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(^|/)\.env($|\.|/)", r"(^|/)\.npmrc$", r"(^|/)\.netrc$", r"(^|/)\.aws/",
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
# Field-name tokens that suggest personal data; readable fields are public to anyone with the URL.
PERSONAL_TOKENS = {"email", "mail", "phone", "tel", "mobile", "address", "street", "birthday",
                   "birthdate", "dob", "iban", "passport", "licence", "license", "ssn", "salary"}


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


def is_text(value: object) -> bool:
    return isinstance(value, str)


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
    re.VERBOSE | re.ASCII,
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
            hint = " (a field used in a row filter must start with a letter or '_')" if k1 == "number" else ""
            return None, f"expected a field name, got {field!r}{hint}"
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

RE_US_NUMBER = re.compile(r"-?\d{1,3}(,\d{3})*(\.\d+)?", re.ASCII)
RE_EU_NUMBER = re.compile(r"-?\d+(\.\d{3})*(,\d+)?", re.ASCII)
RE_PLAIN_NUMBER = re.compile(r"-?\d{1,100}(?:\.\d{1,100})?(?:[eE][+-]?\d{1,100})?", re.ASCII)
RE_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
RE_SEP_DATE = re.compile(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", re.ASCII)
RE_TIME = re.compile(r"\d{1,2}:\d{2}", re.ASCII)
RE_EPOCH = re.compile(r"\d{10,13}", re.ASCII)
RE_RANGE_SEP = re.compile(r"\s-\s")
RE_HEX_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")
RE_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
RE_URL = re.compile(r"https?://")
RE_PHONE = re.compile(r"[\d\s\-+()]{7,}", re.ASCII)


def _date_like(value: str) -> bool:
    return bool(RE_ISO_DATE.match(value) or RE_SEP_DATE.match(value))


def _is_number(sample: str | None) -> bool:
    if sample is None or sample.strip() == "":
        return False
    text = sample.strip()
    if "," in text:
        return bool(RE_US_NUMBER.fullmatch(text) or RE_EU_NUMBER.fullmatch(text))
    cleaned = re.sub(r"\s", "", text)
    return len(cleaned) <= 100 and bool(RE_PLAIN_NUMBER.fullmatch(cleaned))


def infer_type(samples: list[str | None], field_name: str) -> str:
    if samples and all(is_text(s) and s.lower() in {"0", "1", "false", "no", "true", "yes"} for s in samples):
        return "boolean"
    if samples:
        ranges = 0
        for s in samples:
            if is_text(s):
                parts = RE_RANGE_SEP.split(s.strip())
                if len(parts) == 2 and _date_like(parts[0]) and _date_like(parts[1]):
                    ranges += 1
        if ranges / len(samples) > 0.8:
            return "appointment"
        dates, has_time = 0, False
        for s in samples:
            if not is_text(s):
                continue
            t = s.strip()
            if _date_like(t):
                dates += 1
                has_time = has_time or bool(RE_TIME.search(t))
            elif RE_EPOCH.fullmatch(t):
                dates += 1
                has_time = True
        if dates / len(samples) > 0.8:
            return "timestamp" if has_time else "date"
    if samples and all(_is_number(s) for s in samples):
        return "number"
    if samples and sum(1 for s in samples if is_text(s) and RE_HEX_COLOR.fullmatch(s.strip())) / len(samples) > 0.7:
        return "color"
    tokens = [t for t in re.split(r"[^a-z0-9]+", field_name.lower()) if t]
    if any(t in {"email", "mail"} for t in tokens):
        return "text (email)"
    if any(t in {"phone", "tel"} for t in tokens):
        return "text (phone)"
    if any(t in {"url", "website", "link"} for t in tokens):
        return "text (url)"
    size = len(samples) or 1
    strs = [s.strip() for s in samples if is_text(s)]
    if sum(1 for s in strs if RE_EMAIL.fullmatch(s)) / size > 0.7:
        return "text (email)"
    if sum(1 for s in strs if RE_URL.match(s)) / size > 0.7:
        return "text (url)"
    if sum(1 for s in strs if RE_PHONE.fullmatch(s)) / size > 0.7:
        return "text (phone)"
    return "text"


# ---------------------------------------------------------------------------------------------
# app.json
# ---------------------------------------------------------------------------------------------


def valid_internal_name(name: str) -> bool:
    return (1 <= len(name) <= 100 and bool(RE_INTERNAL_NAME.fullmatch(name)) and not name.startswith("_")
            and not name.endswith("_") and "__" not in name)


def check_internal_name(report: Report, where: str, name: str) -> None:
    if not valid_internal_name(name):
        report.error(where, f"{name!r} is not a valid internal name: use lowercase letters, digits and "
                            "single underscores, not starting or ending with '_' (Ninox cannot create it)")


def check_role_list(report: Report, where: str, value: object, roles: dict) -> list[str]:
    if not isinstance(value, list) or not all(is_text(r) for r in value):
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
    model: dict = {"tables": {}, "roles": {}}
    if not isinstance(manifest, dict):
        report.error("app.json", "must be a JSON object")
        return model
    for key in manifest:
        if key not in ALLOWED_TOP_LEVEL:
            report.error(f"app.json.{key}", f"unknown key (allowed: {', '.join(sorted(ALLOWED_TOP_LEVEL))})")

    name = manifest.get("name")
    if not is_text(name) or not (1 <= len(name.strip()) <= MAX_NAME) or RE_CONTROL.search(name):
        report.error("app.json.name", f"required: text of 1-{MAX_NAME} characters")

    entry = manifest.get("entry", "dist/index.html")
    segments = entry.split("/") if is_text(entry) else []
    if (not is_text(entry) or not entry or entry.startswith("/") or "\\" in entry
            or RE_CONTROL.search(entry) or any(s in {"", ".", ".."} for s in segments)
            or not entry.lower().endswith(".html")):
        report.error("app.json.entry", "must be a relative path ending in .html, like 'dist/index.html'")
    else:
        model["entry"] = entry[len("dist/"):] if entry.startswith("dist/") else entry
        if model["entry"] != "index.html":
            report.warn("app.json.entry", "the app's root URL always serves dist/index.html; keep entry 'dist/index.html'")

    routing = manifest.get("routing", "spa")
    if not is_text(routing) or routing not in {"spa", "static"}:
        report.error("app.json.routing", "must be 'spa' or 'static'")

    roles = manifest.get("roles")
    if not isinstance(roles, dict) or not roles:
        report.error("app.json.roles", "required: an object mapping app role names to workspace role IDs")
        roles = {}
    if len(roles) > MAX_ROLES:
        report.error("app.json.roles", f"at most {MAX_ROLES} roles")
    for role, ref in roles.items():
        where = f"app.json.roles.{role}"
        if not RE_ROLE_NAME.fullmatch(role):
            report.error(where, "role names are lowercase, start with a letter, and use letters, digits, '-'")
        if not is_text(ref) or not ref:
            report.error(where, "value must be a workspace role ID (text)")
        elif ref == ADMIN_ROLE_ID:
            report.error(where, "an app role may not map to the admin role")
        elif not RE_ROLE_ID.fullmatch(ref):
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
        if key not in ALLOWED_VISITOR_OPTIONS:
            report.error(f"app.json.auth.{key}", f"unknown key (allowed: {', '.join(sorted(ALLOWED_VISITOR_OPTIONS))})")
    anonymous = auth.get("anonymous")
    if anonymous is not None and not isinstance(anonymous, bool):
        report.error("app.json.auth.anonymous", "must be true or false")
    providers = auth.get("providers", [])
    if not isinstance(providers, list) or any(not is_text(p) or p not in SIGN_IN_PROVIDERS for p in providers):
        report.error("app.json.auth.providers", f"allowed values: {', '.join(sorted(SIGN_IN_PROVIDERS))}")
        providers = []
    elif providers:
        report.warn("app.json.auth.providers", "sign-in providers are not available yet; visitors are anonymous. Remove 'providers'")
    redirect = auth.get("signInRedirect")
    if redirect is not None and (not is_text(redirect) or not redirect.startswith("/") or redirect.startswith("//")):
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
            if key not in ALLOWED_TABLE_OPTIONS:
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
                    if key not in ALLOWED_FIELD_OPTIONS:
                        report.error(f"{fwhere}.{key}", "unknown key (allowed: read, write)")
                readers = check_role_list(report, f"{fwhere}.read", fspec["read"], roles) if "read" in fspec else verbs.get("read", [])
                writers = check_role_list(report, f"{fwhere}.write", fspec["write"], roles) if "write" in fspec else verbs.get("create", [])
                fields[field] = {"readers": set(readers), "writers": set(writers)}
            if verbs.get("read") and not any(f["readers"] for f in fields.values()):
                report.error(f"{where}.fields", "every field is hidden, so reading the table fails with 403")
            exposed = sorted(f for f, info in fields.items()
                             if info["readers"] and set(re.split(r"[^a-z0-9]+", f)) & PERSONAL_TOKENS)
            if exposed and "anonymous" in {r for f in exposed for r in fields[f]["readers"]}:
                report.warn(f"{where}.fields", f"{', '.join(exposed)} look like personal data and are readable by anyone "
                                               "with the URL. Unless the operator means to publish them, hide them with {\"read\": []} "
                                               "or keep them in a create-only table")

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
                    if not is_text(expression) or not expression.strip():
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
                if not RE_ACTION_NAME.fullmatch(action):
                    report.error(awhere, "action names start with a letter and use letters, digits, '_'")
                if not isinstance(aspec, dict):
                    report.error(awhere, "must be an object")
                    continue
                for key in aspec:
                    if key not in ALLOWED_ACTION_OPTIONS:
                        report.error(f"{awhere}.{key}", "unknown key (allowed: roles, runAs, script)")
                if not is_text(aspec.get("script")) or not aspec.get("script"):
                    report.error(f"{awhere}.script", "required: the name of a module script in the target module")
                action_roles = check_role_list(report, f"{awhere}.roles", aspec.get("roles"), roles)
                if not action_roles:
                    report.error(f"{awhere}.roles", "required: at least one role")
                run_as = aspec.get("runAs")
                if "runAs" in aspec and (not is_text(run_as) or run_as not in roles):
                    report.error(f"{awhere}.runAs", "must be a declared role")
            report.warn("app.json.actions", "actions cannot be called by an app yet; the deploy only checks that the module script exists. Remove 'actions'")
    return model


def note_role_effects(report: Report, model: dict) -> None:
    """What each deploy does to the declared tables' permissions inside Ninox."""
    roles = model["roles"]
    for table, spec in model["tables"].items():
        parts = []
        for verb, label in (("read", "read"), ("create", "create rows")):
            mapped = sorted({BUILTIN_ROLE_IDS.get(roles.get(r, ""), "custom") for r in spec["verbs"].get(verb, [])})
            if not mapped:
                continue
            effects = []
            if verb == "create" and "viewer" in mapped:
                effects.append("workspace viewers gain this right")
            if "editor" not in mapped:
                effects.append("editors lose it")
            parts.append(f"{label}: admins and the {'/'.join(mapped)} role only"
                         + (f" ({', '.join(effects)})" if effects else ""))
        if parts:
            report.note(f"table {table}", f"each deploy is expected to set its permissions inside Ninox (not yet confirmed on a live deploy). {'; '.join(parts)}")


# ---------------------------------------------------------------------------------------------
# Folder and seed CSVs
# ---------------------------------------------------------------------------------------------


def is_junk(rel: str) -> bool:
    parts = rel.split("/")
    return (parts[-1] in JUNK_NAMES or parts[-1] == PACKAGE_MARKER or parts[-1].startswith("._")
            or any(p in JUNK_DIRS for p in parts))


def list_files(root: Path) -> list[str]:
    return [p.relative_to(root).as_posix() for p in sorted(root.rglob("*")) if p.is_file()]


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
            report.warn(rel, "matches no table in app.json (name it '<table>.csv' or '<table>-import.csv'); it is not imported")
            continue
        if table in seeds:
            report.error(rel, f"second seed for table {table!r} (also {seeds[table]})")
            continue
        seeds[table] = rel
    if len(seeds) > MAX_SEEDS:
        report.error("seed CSVs", f"at most {MAX_SEEDS} seed CSVs per deploy")

    csv.field_size_limit(MAX_CSV_CHARS)
    for table, rel in seeds.items():
        raw = (root / rel).read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]  # the upload decodes the file as UTF-8, which drops a BOM
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            report.error(rel, "is not UTF-8; save it as UTF-8")
            continue
        total_chars += len(text)
        if len(text) > MAX_CSV_CHARS:
            report.error(rel, f"{len(text):,} characters; one seed may hold at most {MAX_CSV_CHARS:,}")
        try:
            rows = [r for r in csv.reader(io.StringIO(text, newline="")) if any(cell.strip() for cell in r)]
        except csv.Error as error:
            report.error(rel, f"cannot be read as CSV: {error}")
            continue
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
        repeated: dict[str, None] = {}
        empty, padded, ragged = [], [], []
        for number, row in enumerate(body, start=2):
            value = row[0] if row else ""
            if value.strip() == "":
                empty.append(str(number))
            elif value != value.strip():
                padded.append(str(number))
            elif value in seen:
                repeated[value] = None
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
            shown = list(repeated)[:5]
            report.error(rel, f"first column {key!r} repeats {', '.join(shown)}{' …' if len(repeated) > 5 else ''}: {merge_hint}")
        if ragged:
            report.warn(rel, f"line(s) {', '.join(ragged[:5])} have a different number of cells than the header")
        if not body:
            report.warn(rel, "has a header but no rows; every field becomes Text")

        samples_by_field = {name: [r[i] if i < len(r) else None for r in body[:SAMPLE_SIZE]]
                            for i, name in enumerate(header)}
        spec["seed_types"] = {}
        for field in sorted(created, key=lambda f: (f not in header, header.index(f) if f in header else 0, f)):
            if field not in samples_by_field:
                spec["seed_types"][field] = "text (no CSV column)"
                continue
            samples = samples_by_field[field]
            inferred = infer_type(samples, field)
            spec["seed_types"][field] = inferred
            values = [s.strip() for s in samples if s is not None and s.strip() != ""]
            where = f"{rel} [{field}]"
            if inferred == "boolean" and all(s in {"0", "1"} for s in values):
                report.warn(where, "only 0/1 in the first rows, so it becomes a yes/no field. "
                                   "Add a row with another number among the first 10 if it is a number")
            if inferred == "timestamp" and values and all(RE_EPOCH.fullmatch(s) for s in values):
                report.warn(where, "10-13 digit numbers (EANs, phone numbers, IDs) become a timestamp field. "
                                   "Add a letter or prefix to the values if they are codes")
            if inferred.startswith("text") and len(values) < len(samples) and values:
                if infer_type(values, field) in {"number", "boolean", "date", "timestamp"}:
                    report.warn(where, "an empty cell in the first 10 rows turns this column into Text; fill it or move the row down")
    return seeds, total_chars


def check_filters_against_types(report: Report, model: dict) -> None:
    for table, spec in model["tables"].items():
        types = spec.get("seed_types")
        for role, terms in spec["row_filters"].items():
            for field, op, literal in terms:
                where = f"app.json.data.{table}.rowFilter.{role}"
                inferred = types.get(field) if types else "text (no seed CSV)"
                if (isinstance(literal, (int, float)) and not isinstance(literal, bool)
                        and op in {">", ">=", "<", "<="} and inferred != "number"):
                    report.warn(where, f"compares {field!r} with {op} {literal}, but {field!r} will be {inferred}, "
                                       "which compares as text ('100' < '20'). Seed it with numbers, or retype it in Ninox")


# ---------------------------------------------------------------------------------------------
# dist/: files, links, SDK calls
# ---------------------------------------------------------------------------------------------


def check_dist(report: Report, root: Path, files: list[str], model: dict) -> list[str]:
    """Returns the dist-relative paths that will be uploaded."""
    names = [p.name for p in root.iterdir() if p.is_dir() and p.name.lower() == "dist"]
    if "dist" not in names:
        hint = f" (found {names[0]!r}; rename it to 'dist', the upload matches the name exactly)" if names else ""
        report.error("dist/", f"missing: the dialog needs a folder named 'dist' next to app.json{hint}")
        return []
    uploads: list[str] = []
    total = 0
    junk: list[str] = []
    stray: list[str] = []
    for rel in files:
        parts = rel.split("/")
        if "dist" not in parts:
            continue
        if parts[0] != "dist":
            stray.append(rel)
            continue
        sub = "/".join(parts[1:])
        if is_junk(rel):
            junk.append(rel)
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
    if stray:
        report.warn("folder", f"{len(stray)} file(s) sit in a 'dist' folder outside the top-level dist/ (first: {stray[0]}). "
                              "The dialog uploads them too, where they can overwrite your files. Upload the folder --package writes")
    if junk:
        report.warn("dist/", f"{len(junk)} junk file(s) such as {junk[0]}; --package leaves them out")
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


def mask_js(src: str) -> tuple[str, list[str]]:
    """Blanks JS comments and regex literals and replaces each string literal with a placeholder
    "\\x01<n>\\x01", so patterns only ever match code. Returns (masked code, string literals);
    template literals keep their raw text, including any ${...}."""
    out: list[str] = []
    literals: list[str] = []
    n, i = len(src), 0
    prev_char, prev_word, spaced = "", "", False
    regex_after_words = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void",
                         "throw", "yield", "await", "instanceof"}
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            end = src.find("\n", i)
            i = n if end < 0 else end
            continue
        if c == "/" and nxt == "*":
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
            continue
        if c in "'\"`":
            j, depth = i + 1, 0
            while j < n:
                d = src[j]
                if d == "\\":
                    j += 2
                    continue
                if c == "`" and d == "$" and j + 1 < n and src[j + 1] == "{":
                    depth += 1
                    j += 2
                    continue
                if c == "`" and depth and d == "}":
                    depth -= 1
                elif d == c and not depth:
                    break
                elif d == "\n" and c != "`":
                    break
                j += 1
            literals.append(src[i + 1:j])
            out.append(f"\x01{len(literals) - 1}\x01")
            i = j + 1
            prev_char, prev_word = "0", ""
            continue
        if c == "/" and (prev_char == "" or prev_char in "(,=:[!&|?{};+-*%<>~^" or prev_word in regex_after_words):
            j, in_class = i + 1, False
            while j < n and src[j] != "\n":
                d = src[j]
                if d == "\\":
                    j += 2
                    continue
                if d == "[":
                    in_class = True
                elif d == "]":
                    in_class = False
                elif d == "/" and not in_class:
                    break
                j += 1
            j += 1
            while j < n and (src[j].isalnum()):
                j += 1
            out.append(" 0 ")
            i = j
            prev_char, prev_word = "0", ""
            continue
        out.append(c)
        if c.isalnum() or c in "_$":
            word_char = prev_char.isalnum() or prev_char in "_$"
            prev_word = prev_word + c if word_char and not spaced else c
            prev_char, spaced = c, False
        elif c.isspace():
            spaced = True
        else:
            prev_char, prev_word, spaced = c, "", False
        i += 1
    return "".join(out), literals


RE_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\b[^>]*>", re.IGNORECASE | re.DOTALL)
RE_HTML_COMMENT = re.compile(r"<!--.*?--!?>", re.DOTALL)
RE_HTML_REF = re.compile(r"""<(?:script|link|img|source|iframe)\b[^>]*?\b(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
RE_IMPORT = re.compile(r"(?:(?:^|[;{}\n])\s*(?:import|export)\b[^\x01;]*?\bfrom\s*|(?:^|[;{}\n(=,:]|\bawait|\breturn)\s*import\s*(?:\(\s*)?)\x01(\d+)\x01")
RE_TABLE_LITERAL = re.compile(r"\.\s*table\s*\(\s*\x01(\d+)\x01\s*\)")
RE_TABLE_COMPUTED = re.compile(r"\.\s*table\s*\(\s*(?!\x01)[^)\s]")
RE_TABLE_BINDING = re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:await\s+)?[\w$.]*\.\s*table\s*\(\s*\x01(\d+)\x01\s*\)\s*[;\n]")
RE_METHOD = re.compile(r"\s*\.\s*(list|get|create|attach)\s*\(")


def js_sources(root: Path, uploads: list[str]) -> dict[str, list[tuple[str, list[str]]]]:
    """Masked JS per uploaded file: a .js file is one unit, an HTML file one unit per inline script."""
    units: dict[str, list[tuple[str, list[str]]]] = {}
    for sub in uploads:
        ext = Path(sub).suffix.lower()
        if ext not in {".html", ".htm", ".js", ".mjs"}:
            continue
        try:
            text = (root / "dist" / sub).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if ext in {".js", ".mjs"}:
            units[sub] = [mask_js(text)]
        else:
            html = RE_HTML_COMMENT.sub("", text)
            units[sub] = [mask_js(body) for attrs, body in RE_SCRIPT.findall(html) if "src=" not in attrs.lower()]
    return units


def resolve(sub: str, ref: str) -> str:
    clean = ref.split("?")[0].split("#")[0]
    target = clean.lstrip("/") if clean.startswith("/") else os.path.normpath(os.path.join(os.path.dirname(sub), clean)).replace("\\", "/")
    if clean.endswith("/") or target in {"", "."}:
        target = (target.rstrip("/").lstrip(".") + "/index.html").lstrip("/")
    return target


def check_links_and_sdk(report: Report, root: Path, uploads: list[str], model: dict) -> None:
    upload_set = set(uploads)
    units = js_sources(root, uploads)
    external = re.compile(r"(?:[a-z][a-z0-9+.-]*:|//|#|\$|\{)", re.IGNORECASE)

    for sub in uploads:
        if Path(sub).suffix.lower() not in {".html", ".htm"}:
            continue
        html = RE_HTML_COMMENT.sub("", (root / "dist" / sub).read_text(encoding="utf-8"))
        if "example.com/privacy" in html:
            report.warn(f"dist/{sub}", "still links the template's placeholder privacy policy (example.com/privacy); "
                                       "replace the notice with the operator's own text and link")
        markup = RE_SCRIPT.sub(lambda m: f"<script{m.group(1)}></script>", html)
        for ref in RE_HTML_REF.findall(markup):
            if not external.match(ref) and resolve(sub, ref) not in upload_set:
                report.error(f"dist/{sub}", f"references {ref!r}, which is not in dist/")

    for sub, file_units in units.items():
        for code, literals in file_units:
            for match in RE_IMPORT.finditer(code):
                ref = literals[int(match.group(1))]
                if "${" in ref or external.match(ref):
                    continue
                if not ref.startswith((".", "/")):
                    if ref.startswith("@ninox/app-sdk"):
                        report.error(f"dist/{sub}", "imports '@ninox/app-sdk' by package name; browsers cannot resolve it, and the "
                                                    f"npm package is a different SDK. Import './{SDK_FILE_NAME}' or bundle the local copy")
                    else:
                        report.error(f"dist/{sub}", f"bare import {ref!r} cannot load in the browser; bundle it or import a relative file")
                elif resolve(sub, ref) not in upload_set:
                    report.error(f"dist/{sub}", f"imports {ref!r}, which is not in dist/")

    all_code = "\n".join(code for file_units in units.values() for code, _ in file_units)
    all_literals = [lit for file_units in units.values() for _, lits in file_units for lit in lits]
    has_sdk = any(Path(s).name == SDK_FILE_NAME for s in uploads) or any(
        "session/anonymous" in lit for lit in all_literals)
    if "createNinoxApp" not in all_code:
        report.warn("dist/", "no createNinoxApp found; the app does not talk to Ninox (fine for a static page)")
    elif not has_sdk:
        report.error("dist/", f"createNinoxApp is used but the SDK is not in dist/; copy assets/template-app/dist/{SDK_FILE_NAME}")

    scan_sdk_calls(report, units, model)


def object_literal_keys(code: str, start: int, literals: list[str]) -> list[str] | None:
    """Top-level keys of the object literal at code[start] == '{' (strings are already masked)."""
    if start >= len(code) or code[start] != "{":
        return None
    depth, segment, segments = 0, [], []
    for i in range(start, len(code)):
        c = code[i]
        if c in "{[(":
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
    keys = []
    for seg in segments:
        seg = seg.strip()
        if not seg or seg.startswith("...") or seg.startswith("["):
            continue
        m = re.match(r"\x01(\d+)\x01\s*:|([A-Za-z_$][\w$]*)\s*(?::|$|\()", seg)
        if m:
            keys.append(literals[int(m.group(1))] if m.group(1) else m.group(2))
    return keys


def scan_sdk_calls(report: Report, units: dict, model: dict) -> None:
    tables = model["tables"]
    needed = {"list": "read", "get": "read", "create": "create", "attach": "create"}

    def check_call(where: str, table: str, method: str, code: str, end: int, literals: list[str]) -> None:
        spec = tables[table]
        if not spec["verbs"].get(needed[method]):
            report.error(where, f".{method}() needs '{needed[method]}' on {table!r} in app.json")
        if method == "get" and spec["row_filters"]:
            report.error(where, f".get() is always refused on {table!r} because it has a rowFilter; use .list() with a filter")
        if method == "create":
            start = end
            while start < len(code) and code[start].isspace():
                start += 1
            for key in object_literal_keys(code, start, literals) or []:
                field = spec["fields"].get(key)
                if field is None:
                    report.error(where, f"create() writes {key!r}, which is not declared in fields")
                elif not field["writers"]:
                    report.error(where, f"create() writes {key!r}, which no role may write")

    for sub, file_units in units.items():
        if Path(sub).name == SDK_FILE_NAME:
            continue
        computed = 0
        for code, literals in file_units:
            for m in RE_TABLE_LITERAL.finditer(code):
                table = literals[int(m.group(1))]
                where = f"dist/{sub} [table('{table}')]"
                if "${" in table:
                    computed += 1
                    continue
                if table not in tables:
                    report.error(where, f"table {table!r} is not declared in app.json")
                    continue
                call = RE_METHOD.match(code, m.end())
                if call:
                    check_call(where, table, call.group(1), code, call.end(), literals)
            for b in RE_TABLE_BINDING.finditer(code):
                handle, table = b.group(1), literals[int(b.group(2))]
                if table not in tables:
                    continue
                for call in re.finditer(rf"(?<![\w$.]){re.escape(handle)}\s*\.\s*(list|get|create|attach)\s*\(", code):
                    check_call(f"dist/{sub} [{handle} = table('{table}')]", table, call.group(1), code, call.end(), literals)
            computed += len(RE_TABLE_COMPUTED.findall(code))
        if computed:
            report.warn(f"dist/{sub}", f"{computed} .table(...) call(s) use a computed name; the check cannot verify their "
                                       "table, verbs or fields. Pass literal names, e.g. app.table('orders')")


def check_subdomain(report: Report, handle: str) -> None:
    where = f"subdomain {handle!r}"
    if not 3 <= len(handle) <= 40:
        report.error(where, "must be 3 to 40 characters")
    if not RE_HANDLE.fullmatch(handle) or handle.startswith("-") or handle.endswith("-") or "--" in handle:
        report.error(where, "use lowercase letters, digits and single hyphens, not at the start or end")
    if handle.startswith("xn--"):
        report.error(where, "may not start with 'xn--'")
    if handle in RESERVED_HANDLES:
        report.error(where, "is reserved; pick a more specific name")


# ---------------------------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------------------------


def package_target_problem(root: Path, out: Path, overwrite: bool) -> str | None:
    root_r, out_r = root.resolve(), out.resolve()
    if out_r == root_r or root_r in out_r.parents or out_r in root_r.parents:
        return f"{out} overlaps the app folder; package next to it, not inside it"
    if out.is_file():
        return f"{out} is a file; choose a folder name"
    if out.exists() and any(out.iterdir()):
        if not overwrite:
            return f"{out} exists and is not empty; add --overwrite to replace it"
        if not (out / PACKAGE_MARKER).is_file():
            return f"{out} was not written by --package; refusing to overwrite it"
    zip_path = out.parent / (out.name + ".zip")
    if zip_path.exists() and not overwrite:
        return f"{zip_path} exists; add --overwrite to replace it"
    return None


def package(root: Path, out: Path, seeds: dict[str, str], uploads: list[str], make_zip: bool) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copy2(root / "app.json", out / "app.json")
    (out / PACKAGE_MARKER).write_text("Written by check_app.py --package. The upload ignores this file.\n", encoding="utf-8")
    for rel in sorted(set(seeds.values())):
        shutil.copy2(root / rel, out / rel)
    for sub in uploads:
        target = out / "dist" / sub
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / "dist" / sub, target)
    print(f"\nPackaged upload folder: {out.resolve()}")
    print("  In 'Choose folder', select this folder: it directly contains app.json and dist/.")
    if make_zip:
        zip_path = out.parent / (out.name + ".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(out.rglob("*")):
                if path.is_file() and path.name != PACKAGE_MARKER:
                    zf.write(path, path.relative_to(out).as_posix())
        print(f"Zip for sharing: {zip_path.resolve()}")
        print("  Its files sit at the zip root. Extract it into a new folder and select that folder.")


# ---------------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Check (and package) a Ninox Custom App folder.")
    parser.add_argument("folder", type=Path, help="the app folder containing app.json and dist/")
    parser.add_argument("--subdomain", help="the subdomain the user will enter")
    parser.add_argument("--package", type=Path, metavar="OUT_DIR", help="write a clean upload folder (only with 0 errors)")
    parser.add_argument("--zip", action="store_true", help="with --package, also write OUT_DIR.zip")
    parser.add_argument("--overwrite", action="store_true", help="with --package, replace an earlier package")
    args = parser.parse_args()

    root: Path = args.folder
    if not (root / "app.json").is_file():
        nested = list(root.glob("*/app.json"))
        hint = f" (found {nested[0].parent}; select that folder)" if nested else ""
        print(f"ERROR {root}: no app.json at the top level{hint}")
        return 1
    if args.package:
        problem = package_target_problem(root, args.package, args.overwrite)
        if problem:
            print(f"ERROR --package: {problem}")
            return 1

    report = Report()
    raw = (root / "app.json").read_bytes()
    try:
        manifest = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        print(f"ERROR app.json: not valid JSON: {str(error)[:200]}")
        return 1
    if raw.startswith(b"\xef\xbb\xbf"):
        report.warn("app.json", "starts with a UTF-8 BOM; save without BOM to be safe")
    if len(raw) > MAX_APP_JSON_CHARS:
        report.error("app.json", f"larger than {MAX_APP_JSON_CHARS:,} characters")

    model = check_manifest(report, manifest)
    files = list_files(root)
    seeds, csv_chars = check_csvs(report, root, files, model)
    check_filters_against_types(report, model)
    uploads = check_dist(report, root, files, model)
    check_links_and_sdk(report, root, uploads, model)
    note_role_effects(report, model)
    if args.subdomain is not None:
        check_subdomain(report, args.subdomain)
    if len(raw) + csv_chars > MAX_SCHEMA_BODY:
        report.error("seed CSVs", f"app.json and the CSVs total {len(raw) + csv_chars:,} characters; the deploy sends them "
                                  "in one request of at most about 6 MB. Seed fewer rows and add the rest in Ninox")
    ignored = sorted({r.split("/")[0] for r in files
                      if not r.startswith("dist/") and r != "app.json" and r not in seeds.values() and not is_junk(r)})
    if ignored:
        report.note("folder", f"ignored by the upload: {', '.join(ignored)}")

    print("Field types the first deploy creates (existing fields are never changed):")
    for table, spec in model["tables"].items():
        verbs = ", ".join(v for v, roles in spec["verbs"].items() if roles) or "-"
        seed = seeds.get(table)
        print(f"  {table}  [{verbs}]  seed: {seed or 'none'}")
        filter_fields = [f for terms in spec["row_filters"].values() for f, _, _ in terms]
        types = spec.get("seed_types", {})
        for field in dict.fromkeys(list(spec["fields"]) + filter_fields):
            info = spec["fields"].get(field)
            if info is None:
                access = "row filter only, not readable"
            elif spec["verbs"].get("read") and not info["readers"]:
                access = "hidden"
            else:
                access = ""
            print(f"    {field:<28} {types.get(field, 'text (no seed CSV)'):<22} {access}")
        if not seed and spec["fields"]:
            report.note(f"table {table}", "no seed CSV: all fields are created as Text. Retype them in Ninox after the "
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
