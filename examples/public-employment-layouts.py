"""Test the documented ASPEP column positions against every acquired vintage.

The 2014 and 2023 technical documentation give exact positions for the 206- and 213-character ID
files and the 94- and 80-character data files. Older archives ship no layout document, so the
hypothesis "positions 1-70 of the data file and 1-151 of the ID file are stable across vintages" is
tested here rather than assumed: every candidate column must parse, and the derived monthly wage and
the ASPEP-state-code to FIPS mapping must agree with a documented year.
"""
import io
import json
import os
import re
import zipfile
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
RECEIPT = next((PROJECT / 'data/census_aspep/artifacts/raw').glob('*/receipt.json'))
BASE = RECEIPT.parent
ID_MEMBER = re.compile(r'^\d\d c?empid\.(txt|dat|zip)$'.replace(' ', ''), re.I)
ST_MEMBER = re.compile(r'^\d\dc?empst\.(txt|dat|zip)$', re.I)


def members(path):
    """(id lines, data lines, id locator, data locator), descending one nested ZIP level if needed."""
    out = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            leaf = os.path.basename(name)
            role = 'id' if ID_MEMBER.match(leaf) else 'st' if ST_MEMBER.match(leaf) else None
            if role is None or role in out:
                continue
            raw = archive.read(name)
            locator = name
            if leaf.lower().endswith('.zip'):
                with zipfile.ZipFile(io.BytesIO(raw)) as inner:
                    inner_name = inner.namelist()[0]
                    raw = inner.read(inner_name)
                    locator = f'{name}!{inner_name}'
            out[role] = (raw.decode('latin-1').splitlines(), locator)
    return out


def main():
    receipt = json.load(open(RECEIPT))
    rows = []
    fips_map = {}
    for shard in sorted(receipt['shards'], key=lambda s: s['name']):
        year = int(shard['name'][6:10])
        path = shard['path'] if os.path.isabs(shard['path']) else str(BASE / shard['path'])
        found = members(path)
        if 'id' not in found or 'st' not in found:
            rows.append((year, 'MISSING MEMBER', found.keys()))
            continue
        id_lines, id_locator = found['id']
        st_lines, st_locator = found['st']
        id_len = Counter(len(line) for line in id_lines).most_common(1)[0][0]
        st_len = Counter(len(line) for line in st_lines).most_common(1)[0][0]

        # ID file: FIPS state 110-111, FIPS county 112-114 (1-based) per the 2014 and 2023 docs.
        pairs, bad_fips = {}, 0
        for line in id_lines:
            if len(line) < 114 or line[:2] == '00':
                continue
            state_code, fips = line[0:2], line[109:111]
            if fips.isdigit() and 1 <= int(fips) <= 78:
                pairs.setdefault(state_code, Counter())[fips] += 1
            else:
                bad_fips += 1
        mapping = {code: counter.most_common(1)[0][0] for code, counter in pairs.items()}
        consistent = sum(counter.most_common(1)[0][1] for counter in pairs.values())
        total_mapped = sum(sum(counter.values()) for counter in pairs.values())

        # Data file: FT employees 21-30, FT payroll 33-44, PT employees 47-56, PT payroll 59-70.
        parsed = wage_ok = considered = 0
        wages = []
        for line in st_lines:
            if len(line) < 70 or line[:2] == '00':
                continue
            considered += 1
            values = [line[a:b].strip() for a, b in ((20, 30), (32, 44), (46, 56), (58, 70))]
            if not all(v == '' or v.lstrip('-').isdigit() for v in values):
                continue
            parsed += 1
            employees = int(values[0] or 0)
            payroll = int(values[1] or 0)
            if employees >= 50 and payroll > 0:
                wage = payroll / employees
                wages.append(wage)
                if 700 <= wage <= 20000:
                    wage_ok += 1
        wages.sort()
        median = wages[len(wages) // 2] if wages else None
        rows.append((year, id_len, st_len, len(id_lines), len(st_lines), len(mapping),
                     consistent / max(total_mapped, 1), bad_fips,
                     parsed / max(considered, 1), wage_ok / max(len(wages), 1), median,
                     id_locator, st_locator))
        fips_map[year] = mapping

    print(f'{"year":>5} {"idlen":>6} {"stlen":>6} {"idrows":>8} {"strows":>8} {"states":>7} '
          f'{"fipsOK":>7} {"badfips":>8} {"colOK":>7} {"wageOK":>7} {"medwage":>9}')
    for row in rows:
        if row[1] == 'MISSING MEMBER':
            print(f'{row[0]:>5} MISSING MEMBER {row[2]}')
            continue
        (year, id_len, st_len, id_rows, st_rows, states, fips_ok, bad, col_ok, wage_ok, median,
         id_locator, st_locator) = row
        print(f'{year:>5} {id_len:>6} {st_len:>6} {id_rows:>8,} {st_rows:>8,} {states:>7} '
              f'{fips_ok:>7.4f} {bad:>8,} {col_ok:>7.4f} {wage_ok:>7.4f} '
              f'{(f"{median:,.0f}" if median else "-"):>9}')
    print()
    for row in rows:
        if row[1] != 'MISSING MEMBER':
            print(f'{row[0]}  id={row[11]}  st={row[12]}')
    print()
    reference = fips_map.get(2023) or fips_map.get(2014)
    for year, mapping in sorted(fips_map.items()):
        shared = set(mapping) & set(reference)
        agree = sum(1 for code in shared if mapping[code] == reference[code])
        print(f'{year}: ASPEP state code -> FIPS agrees with 2023 on {agree}/{len(shared)} codes')


if __name__ == '__main__':
    main()
