#!/usr/bin/env python3
"""Ninox workspace preflight: answer, in one run, everything a build plan depends on.

    python3 preflight.py [module_name]

Creates a throwaway module, probes each capability, deletes it, prints a report.
Run this BEFORE committing to a build plan — it costs ~15 seconds and replaces a
session's worth of guessing.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nxapi import Ninox

PROBE = sys.argv[1] if len(sys.argv) > 1 else 'zz_preflight'


def main():
    nx = Ninox()
    print(f'workspace {nx.ws} @ {nx.base}\n')
    results = {}

    st, b = nx.call('GET', '')
    results['auth'] = (st == 200, f'HTTP {st}')
    if st != 200:
        print('auth FAILED:', json.dumps(b, ensure_ascii=False)[:200]); return 1
    existing = [m['name'] for m in (b.get('data', {}).get('modules') or [])]
    print(f'existing modules: {existing or "(none)"}\n')

    nx.call('DELETE', f'/modules/{PROBE}')
    st, _ = nx.create_module(PROBE, 'Preflight')
    results['create module'] = (st < 300, f'HTTP {st}')
    nx.create_table(PROBE, 't', 'T')

    # choice options, incl. non-ASCII labels
    st, b = nx.create_fields(PROBE, 't', [
        {'name': 'num', 'labels': {'': 'Num'}, 'type': 'number'},
        {'name': 'pick', 'labels': {'': 'Pick'}, 'type': 'choice',
         'options': [{'name': 'Läuft'}, {'name': 'Störung'}]},
    ])
    opts = []
    if st < 300:
        for f in b.get('data', []):
            if f.get('name') == 'pick':
                opts = [o['name'] for o in f.get('options', [])]
    results['choice options'] = (bool(opts), str(opts))
    results['non-ASCII labels'] = ('Läuft' in opts, str(opts))

    # function field + does it actually compute?
    st, _, errs = nx.create_function(PROBE, 't', 'doubled', 'Doubled', 'num * 2')
    results['function field create'] = (st < 300 and not errs, f'HTTP {st} errors={errs}')

    nx.insert(PROBE, 't', [{'num': 21, 'pick': 'Störung'}])
    rows = nx.all_records(PROBE, 't')
    vals = rows[0]['values'] if rows else {}
    results['function computes'] = (vals.get('doubled') == 42, f"doubled={vals.get('doubled')}")
    results['choice round-trip'] = (vals.get('pick') == 'Störung', f"pick={vals.get('pick')!r}")

    # reverse field auto-creation (what aggregates must traverse)
    nx.create_table(PROBE, 'c', 'C')
    nx.create_fields(PROBE, 'c', [{'name': 'x', 'labels': {'': 'X'}, 'type': 'number'}])
    nx.create_reference(PROBE, 'c', 't_ref', 'T', 't')
    revs = [f['name'] for f in nx.fields(PROBE, 't') if f.get('type') == 'reverse']
    results['reverse auto-created'] = (bool(revs), f'on parent: {revs}')

    nx.call('DELETE', f'/modules/{PROBE}')

    width = max(len(k) for k in results)
    print('capability report')
    print('-' * (width + 30))
    ok = True
    for k, (passed, detail) in results.items():
        print(f'{k:<{width}}  {"OK  " if passed else "FAIL"}  {detail}')
        ok &= passed
    print('-' * (width + 30))
    print('probe module removed:', PROBE not in nx.call('GET', '')[1].get('data', {}).get('modules', []))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
