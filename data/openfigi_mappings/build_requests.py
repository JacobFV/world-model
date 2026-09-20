"""Generate this dataset's acquisition request list from the pinned catalog artifacts.

The OpenFIGI mapping API is a POST-only service: the thing being asked for travels in the
request body, so the declaration has to name every identifier it will send. That list is not
written by hand. It is derived here, once, from two pinned normalized artifacts, and the
declaration records the versions it came from:

* **CUSIPs** - every distinct CUSIP that appears on a 13F information table in
  ``sec_13f_history`` and ``sec_ownership_datasets``, kept only where the nine-character value
  carries a valid modulus-10 double-add-double check digit. The failures are overwhelmingly the
  option pseudo-CUSIPs filers construct by putting 9 in the seventh position
  (``037833900`` for an Apple call); they are not CUSIPs and are not sent.
* **Tickers** - every symbol ``sec_issuer_reference`` publishes in an ``issuer_listing``
  assertion, asked for twice: once scoped to the ISO 10383 MIC the SEC snapshot names
  (``XNYS``/``XNAS``), which is what makes OpenFIGI's answer a MIC-scoped listing rather than
  a guess about Bloomberg's exchange codes, and once at OpenFIGI's own ``US`` composite scope,
  which is the only scope available for the symbols whose SEC exchange value (``OTC``,
  ``CBOE``, blank) is not a MIC.

Run it with the data root set, from the repository root::

    WORLD_MODEL_DATA=... python3 data/openfigi_mappings/build_requests.py

It rewrites ``acquisition.combinations`` and ``parameters`` in ``dataset.json`` in place and
prints the counts. Re-running it against the same pinned versions is a no-op.
"""
import gzip
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
JOBS_PER_REQUEST = 10          # the published unkeyed ceiling; 11 jobs is answered with HTTP 413

# The pinned normalized artifacts the request list is derived from.
HOLDINGS = [('sec_13f_history', '94be7bc75fe7bd35c8b212eb9bc64e31832c8616a5061b6c0471037986d32cdc'),
            ('sec_ownership_datasets', '6d7bf0fcb22b6ab319ef6bef277a3b893c3a1fb968dbca3edc3734687c740c0b')]
LISTINGS = ('sec_issuer_reference', 'b5923f5238ffd3758933cb2e797c6c9df27e35915e55bdecd13f3a04bb594562')
# Scopes ``sec_issuer_reference`` uses that are ISO 10383 MICs. Its other scope values - ``OTC``,
# ``CBOE`` and ``SEC_UNSPECIFIED`` - are the SEC's own exchange labels, not MICs, so no micCode is
# sent for them and those symbols are asked for at the US composite scope only.
MIC_SCOPES = ('XNAS', 'XNYS')

_CUSIP_SHAPE = re.compile(r'[0-9A-Z]{9}')
_VALUES = {character: index for index, character in enumerate('0123456789')}
_VALUES.update({character: 10 + index for index, character in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZ')})
_VALUES.update({'*': 36, '@': 37, '#': 38})


def cusip_check_digit(body):
    """The CUSIP modulus-10 double-add-double check digit for the first eight characters."""
    total = 0
    for position, character in enumerate(body):
        value = _VALUES[character]
        if position % 2:
            value *= 2
        total += value // 10 + value % 10
    return str((10 - total % 10) % 10)


def valid_cusip(value):
    """``value`` when it is a nine-character CUSIP whose check digit recomputes, else ``None``."""
    value = (value or '').strip().upper()
    if not _CUSIP_SHAPE.fullmatch(value):
        return None
    return value if cusip_check_digit(value[:8]) == value[8] else None


def _records(data_root, dataset, version):
    path = Path(data_root) / dataset / 'artifacts' / 'normalized' / version / 'records.jsonl.gz'
    if not path.exists():
        raise SystemExit(f'{path} is missing; the pinned artifact has to be present to rebuild the request list')
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        yield from handle


def collect_cusips(data_root):
    """Distinct check-digit-valid CUSIPs on 13F information tables, with what was refused."""
    kept, refused, seen = set(), {}, 0
    for dataset, version in HOLDINGS:
        for line in _records(data_root, dataset, version):
            if '"security"' not in line or '"cusip:' not in line:
                continue
            record = json.loads(line)
            if record.get('kind') != 'entity' or record.get('entity_type') != 'security':
                continue
            entity_id = record.get('entity_id') or ''
            if not entity_id.startswith('cusip:'):
                continue
            seen += 1
            value = entity_id[6:]
            if valid_cusip(value):
                kept.add(value)
            else:
                refused[value] = (record.get('attributes') or {}).get('issuer_name') or ''
    return sorted(kept), refused, seen


def collect_tickers(data_root):
    """``{symbol: [scope, ...]}`` from the issuer_listing assertions SEC submissions publish."""
    symbols = {}
    for line in _records(data_root, *LISTINGS):
        if '"issuer_listing"' not in line:
            continue
        record = json.loads(line)
        if record.get('predicate') != 'issuer_listing':
            continue
        obj = record.get('object') or ''
        parts = obj.split(':', 2)
        if len(parts) != 3 or parts[0] != 'ticker':
            continue
        symbols.setdefault(parts[2], set()).add(parts[1])
    return {symbol: sorted(scopes) for symbol, scopes in sorted(symbols.items())}


def _batches(jobs, group, extra=None):
    out = []
    for start in range(0, len(jobs), JOBS_PER_REQUEST):
        out.append({'group': group, 'batch': len(out), **(extra or {}),
                    'jobs': jobs[start:start + JOBS_PER_REQUEST]})
    return out


def build(data_root):
    cusips, refused, seen = collect_cusips(data_root)
    tickers = collect_tickers(data_root)
    combinations = _batches([{'idType': 'ID_CUSIP', 'idValue': value} for value in cusips], 'cusip')
    for mic in MIC_SCOPES:
        scoped = [symbol for symbol, scopes in tickers.items() if mic in scopes]
        combinations += _batches([{'idType': 'TICKER', 'idValue': symbol, 'micCode': mic} for symbol in scoped],
                                 'ticker_mic', {'mic': mic})
    combinations += _batches([{'idType': 'TICKER', 'idValue': symbol, 'exchCode': 'US'} for symbol in tickers],
                             'ticker_us')
    parameters = {
        'jobs_per_request': JOBS_PER_REQUEST,
        'derived_from': ([{'dataset': dataset, 'stage': 'normalized', 'version': version} for dataset, version in HOLDINGS]
                         + [{'dataset': LISTINGS[0], 'stage': 'normalized', 'version': LISTINGS[1]}]),
        'cusips': {'requested': len(cusips),
                   'security_entities_read': seen,
                   'refused_check_digit': len(refused),
                   'sha256': hashlib.sha256('\n'.join(cusips).encode()).hexdigest()},
        'tickers': {'symbols': len(tickers),
                    'mic_scoped': {mic: sum(1 for scopes in tickers.values() if mic in scopes) for mic in MIC_SCOPES},
                    'other_scopes': sorted({scope for scopes in tickers.values() for scope in scopes} - set(MIC_SCOPES)),
                    'sha256': hashlib.sha256('\n'.join(tickers).encode()).hexdigest()},
        'requests': {group: sum(1 for c in combinations if c['group'] == group)
                     for group in ('cusip', 'ticker_mic', 'ticker_us')},
    }
    return combinations, parameters


def render(definition, combinations, parameters):
    """dataset.json text: pretty-printed, with one compact line per acquisition request.

    Eleven thousand fully pretty-printed request objects would be half a million lines, so each
    combination is dumped on its own line. The file stays ordinary JSON and a re-generation's
    diff stays on the requests that actually changed.
    """
    definition = json.loads(json.dumps(definition))
    definition['parameters'] = parameters
    tokens = {f'\u0000combination:{index}\u0000': json.dumps(combination, sort_keys=True)
              for index, combination in enumerate(combinations)}
    definition['acquisition']['combinations'] = list(tokens)
    text = json.dumps(definition, indent=2, sort_keys=True)
    for token, compact in tokens.items():
        text = text.replace(json.dumps(token), compact, 1)
    json.loads(text)  # the substitution has to leave valid JSON behind
    return text + '\n'


def main():
    data_root = os.environ.get('WORLD_MODEL_DATA')
    if not data_root:
        raise SystemExit('Set WORLD_MODEL_DATA to the data root')
    combinations, parameters = build(data_root)
    definition = json.loads((HERE / 'dataset.json').read_text())
    (HERE / 'dataset.json').write_text(render(definition, combinations, parameters))
    json.dump(parameters, sys.stdout, indent=1)
    print()
    print(f'{len(combinations)} requests', file=sys.stderr)


if __name__ == '__main__':
    main()
