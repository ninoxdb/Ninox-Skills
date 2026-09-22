#!/usr/bin/env python3
"""Minimal Ninox Public API client for agent-driven builds.

Why this exists: a multi-table build is 30-60 API calls. Hand-rolling curl per call
costs an agent time, tokens, and a class of quoting bugs. Import this instead.

    from nxapi import Ninox
    nx = Ninox()                      # reads env / ~/.ninox/.env / ~/.env
    nx.create_module('printops', 'Druckbetrieb')
    nx.create_table('printops', 'sites', 'Standorte')

Every method returns (status, body). Nothing raises on HTTP errors: an agent needs to
read the error body to fix the cause, not catch an exception.
"""
import json, os, time, urllib.request, urllib.error

ENV_FILES = ['~/.ninox/.env', '~/.env', './.env']


def _load_env():
    """Env vars win; then the first readable file that defines a key."""
    out = {k: v for k, v in os.environ.items() if k.startswith('NINOX_')}
    for path in ENV_FILES:
        p = os.path.expanduser(path)
        if not os.path.isfile(p):
            continue
        try:
            for line in open(p, encoding='utf-8'):
                line = line.strip()
                if line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                out.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except OSError:
            continue
    return out


class Ninox:
    def __init__(self, workspace=None, key=None, base=None):
        env = _load_env()
        self.base = (base or env.get('NINOX_API_BASE') or 'https://go.ninox.com').rstrip('/')
        self.ws = workspace or env.get('NINOX_WORKSPACE_ID')
        self.key = key or env.get('NINOX_API_KEY')
        if not self.key:
            raise SystemExit(
                'No NINOX_API_KEY. Set it in the environment or in one of: '
                + ', '.join(ENV_FILES)
                + '. Note: an `export` in the user\'s own terminal does not reach a '
                  'tool-spawned shell — ask them to write the file instead.')
        if not self.ws:
            raise SystemExit('No NINOX_WORKSPACE_ID. It is the 2nd path segment of the '
                             'go.ninox.com app URL, not the organization id.')
        self.root = f'{self.base}/api/v1/workspace/{self.ws}'

    def call(self, method, path, body=None, retries=3):
        """Retries transient failures (429, 5xx, gateway resets) with backoff.
        A long build must not abort because one call hit a blip."""
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(retries):
            req = urllib.request.Request(self.root + path, data=data, method=method)
            req.add_header('Authorization', 'Bearer ' + self.key)
            if data:
                req.add_header('Content-Type', 'application/json')
            try:
                with urllib.request.urlopen(req) as r:
                    raw = r.read().decode()
                    return r.status, (json.loads(raw) if raw else None)
            except urllib.error.HTTPError as e:
                raw = e.read().decode()
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = {'raw': raw}
                transient = e.code == 429 or e.code >= 500
                if transient and attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return e.code, parsed
            except urllib.error.URLError as e:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return 0, {'raw': f'connection failed: {e.reason}'}

    # --- schema ---------------------------------------------------------
    def create_module(self, name, label, roles=('admin', 'editor')):
        return self.call('POST', '/modules',
                         {'name': name, 'labels': {'': label}, 'openRoles': list(roles)})

    def create_table(self, module, name, label, **flags):
        body = {'name': name, 'labels': {'': label},
                'hasGlobalSearch': True, 'hasHistory': True}
        body.update(flags)
        return self.call('POST', f'/modules/{module}/tables', body)

    def create_fields(self, module, table, fields):
        """Batch field create. One bad member rolls back the whole batch."""
        return self.call('POST', f'/modules/{module}/tables/{table}/fields/batch', fields)

    def create_reference(self, module, table, name, label, target):
        return self.call('POST', f'/modules/{module}/tables/{table}/fields',
                         {'name': name, 'labels': {'': label},
                          'type': 'reference', 'refTableName': target})

    def create_function(self, module, table, name, label, expression):
        """Returns (status, body, expression_errors). Always check the third value:
        a broken expression can still create the field."""
        st, body = self.call('POST', f'/modules/{module}/tables/{table}/fields',
                             {'name': name, 'labels': {'': label},
                              'type': 'function', 'expression': expression})
        errs = (body or {}).get('data', {}).get('expressionErrors')
        return st, body, errs

    def fields(self, module, table):
        st, b = self.call('GET', f'/modules/{module}/tables/{table}/fields')
        return b.get('data', []) if st < 300 else []

    def tables(self, module):
        st, b = self.call('GET', f'/modules/{module}/tables')
        return [t['name'] for t in b.get('data', [])] if st < 300 else []

    # --- records --------------------------------------------------------
    def insert(self, module, table, rows, chunk=100):
        """Create rows, return ids in source order. Asserts the count matches so a
        short return cannot silently under-link dependent rows."""
        ids = []
        for i in range(0, len(rows), chunk):
            st, b = self.call('POST', f'/modules/{module}/tables/{table}/records',
                              {'records': rows[i:i + chunk]})
            if st >= 300:
                raise SystemExit(f'{table} insert failed at row {i}: {st} '
                                 + json.dumps(b, ensure_ascii=False)[:400])
            ids += b['data']['ids']
        assert len(ids) == len(rows), f'{table}: got {len(ids)} ids for {len(rows)} rows'
        return ids

    def all_records(self, module, table, fields=None):
        """Paginate a whole table (the API caps limit at 100)."""
        out, off = [], 0
        q = f'&fields={",".join(fields)}' if fields else ''
        while True:
            st, b = self.call(
                'GET', f'/modules/{module}/tables/{table}/records?limit=100&offset={off}{q}')
            if st >= 300:
                return out
            out += b.get('data', [])
            if not b.get('page_info', {}).get('has_more'):
                return out
            off += 100

    def count(self, module, table):
        return len(self.all_records(module, table))
