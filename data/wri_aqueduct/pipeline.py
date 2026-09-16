"""WRI Aqueduct 4.0 CSV tables -> sub-basin/admin water-risk observations.

Reads only the three CSV members of the published ZIP (the file geodatabase is ignored):
* baseline annual (one row per Aqueduct unit = HydroBASINS level-6 sub-basin x GADM admin-1 x groundwater unit):
  basin indicators (bws, bwd, iav, sev, gtd, rfr, cfr, drr) are emitted once per sub-basin (pfaf_id),
  admin/country indicators (ucw, cep, udw, usa, rri) once per admin-1 unit, and the default-weighted overall
  water risk (w_awr_def_tot) once per Aqueduct unit;
* baseline monthly: bws, bwd, iav per sub-basin and calendar month;
* future annual: water stress (ws), depletion (wd), interannual (iv) and seasonal (sv) variability raw values and
  scores, plus available blue water (ba) and withdrawals (ww) for 2030/2050/2080 under three scenarios.
Baseline values describe the 1979-2019 reference climate. -9999 = no data (skipped); raw 9999 is Aqueduct's
sentinel for arid/low-use basins (kept as a missing raw value with its score).
"""
import re

READER = {'format': 'csv', 'encoding': 'utf-8-sig'}
BASELINE = ('1979-01-01', '2020-01-01')
BASIN = {'bws': ('baseline_water_stress', 'ratio'), 'bwd': ('baseline_water_depletion', 'ratio'),
         'iav': ('interannual_variability', 'coefficient_of_variation'), 'sev': ('seasonal_variability', 'coefficient_of_variation'),
         'gtd': ('groundwater_table_decline', 'cm/year'), 'rfr': ('riverine_flood_risk', 'fraction_population_per_year'),
         'cfr': ('coastal_flood_risk', 'fraction_population_per_year'), 'drr': ('drought_risk', 'index_0_1')}
ADMIN = {'ucw': ('untreated_connected_wastewater', 'fraction'), 'cep': ('coastal_eutrophication_potential', 'index'),
         'udw': ('unimproved_drinking_water', 'fraction'), 'usa': ('unimproved_sanitation', 'fraction'),
         'rri': ('peak_reprisk_country_esg_risk', 'index')}
FUTURE = {'ws': ('water_stress', 'ratio'), 'wd': ('water_depletion', 'ratio'), 'iv': ('interannual_variability', 'coefficient_of_variation'),
          'sv': ('seasonal_variability', 'coefficient_of_variation'), 'ba': ('available_blue_water', 'cm/year'),
          'ww': ('water_withdrawal', 'cm/year')}
SCENARIOS = {'bau': 'business_as_usual_ssp3_rcp70', 'opt': 'optimistic_ssp1_rcp26', 'pes': 'pessimistic_ssp5_rcp85'}
FUTURE_COLUMN = re.compile(r'^(bau|opt|pes)(30|50|80)_(ws|wd|iv|sv|ba|ww)_x_(r|s)$')


def _value(text):
    text = (text or '').strip()
    if not text:
        return None
    value = float(text)
    if value in (-9999.0, -9999):
        return None
    return int(value) if value.is_integer() else round(value, 8)


def _member_kind(locator):
    name = locator.split('/member:', 1)[1].rsplit('/line:', 1)[0].lower() if '/member:' in locator else ''
    if 'future_annual' in name:
        return 'future'
    if 'baseline_monthly' in name:
        return 'monthly'
    if 'baseline_annual' in name:
        return 'annual'
    return None


class _Emit:
    def __init__(self, context, index):
        self.context, self.index = context, index
        self.observed = context.raw_receipt(index)['retrieved_at']

    def entity(self, entity_id, typ, label, locator, **attrs):
        return {'kind': 'entity', 'id': 'aqueduct:entity:' + entity_id, 'entity_id': entity_id, 'entity_type': typ,
                'label': label, 'observed_at': self.observed, 'evidence': self.context.raw_evidence(locator, self.index),
                'attributes': attrs}

    def within(self, subject, parent, locator):
        return {'kind': 'assertion', 'id': f'aqueduct:within:{subject}:{parent}', 'subject': subject, 'predicate': 'within',
                'object': parent, 'observed_at': self.observed, 'evidence': self.context.raw_evidence(locator, self.index),
                'attributes': {}}

    def obs(self, rid, subject, metric, value, unit, locator, dims, valid=BASELINE, missing=None, **attrs):
        record = {'kind': 'observation', 'id': rid, 'subject': subject, 'metric': metric, 'value': value, 'unit': unit,
                  'valid_from': valid[0], 'valid_to': valid[1], 'observed_at': self.observed,
                  'evidence': self.context.raw_evidence(locator, self.index), 'dimensions': dims, 'attributes': attrs}
        if value is None:
            record['missing_reason'] = missing or 'source_missing'
        return record


def _indicator(out, rid, subject, metric, unit, row, code, locator, dims, valid=BASELINE, suffix=''):
    raw, score = row.get(f'{code}{suffix}_raw'), row.get(f'{code}{suffix}_score')
    label = (row.get(f'{code}{suffix}_label') or '').strip() or None
    raw_value, score_value = _value(raw), _value(score)
    if raw_value is None and score_value is None:
        return
    if raw_value == 9999:
        yield out.obs(rid, subject, metric, None, unit, locator, dims, valid, 'aqueduct_sentinel_9999_arid_or_low_use', label=label)
    elif raw_value is not None:
        yield out.obs(rid, subject, metric, raw_value, unit, locator, dims, valid, label=label)
    if score_value is not None:
        yield out.obs(rid + ':score', subject, metric + '_score', score_value, 'score_0_5', locator, dims, valid,
                      category=_value(row.get(f'{code}{suffix}_cat')), label=label)


def run(context):
    if not context.raw_inputs:
        raise ValueError('wri_aqueduct: raw acquisition required')
    for index, _ in enumerate(context.raw_inputs):
        out = _Emit(context, index)
        basins, admins = set(), set()  # ~16k sub-basins, ~3.6k admin-1 units
        rows = context.raw_rows(index, format='csv', encoding='utf-8-sig', members=['*/CVS/*.csv', 'Aqueduct40_*.csv'])
        for locator, row in rows:
            kind = _member_kind(locator)
            pfaf = (row.get('pfaf_id') or '').strip()
            if kind is None or not pfaf or pfaf == '-9999':
                continue
            basin = 'aqueduct:pfaf:' + pfaf
            if basin not in basins:
                basins.add(basin)
                yield out.entity(basin, 'watershed', f'HydroBASINS level 6 sub-basin {pfaf}', locator, pfaf_id=int(pfaf))
            if kind in ('monthly', 'future'):
                if (kind, pfaf) in basins:
                    continue  # a few sub-basins are listed twice with identical basin-level values
                basins.add((kind, pfaf))
            if kind == 'annual':
                yield from _annual(out, row, basin, pfaf, locator, basins, admins)
            elif kind == 'monthly':
                for month in range(1, 13):
                    for code in ('bws', 'bwd', 'iav'):
                        metric, unit = BASIN[code]
                        yield from _indicator(out, f'aq40:{pfaf}:{code}:m{month:02d}', basin, metric, unit, row, code, locator,
                                              {'reference_period': '1979-2019', 'month': month}, suffix=f'_{month:02d}')
            else:
                for column, text in row.items():
                    match = FUTURE_COLUMN.match(column)
                    if not match:
                        continue
                    scenario, year, code, measure = match.groups()
                    value = _value(text)
                    if value is None:
                        continue
                    metric, unit = FUTURE[code]
                    if measure == 's':
                        metric, unit = metric + '_score', 'score_0_5'
                    year = 2000 + int(year)
                    yield out.obs(f'aq40:{pfaf}:{scenario}{year}:{code}:{measure}', basin, metric, value, unit, locator,
                                  {'scenario': SCENARIOS[scenario], 'projection_year': year},
                                  (f'{year}-01-01', f'{year + 1}-01-01'), projection=True,
                                  label=(row.get(f'{scenario}{year % 100}_{code}_x_l') or '').strip() or None)


def _annual(out, row, basin, pfaf, locator, basins, admins):
    dims = {'reference_period': '1979-2019'}
    first_for_basin = ('annual', pfaf) not in basins
    if first_for_basin:
        basins.add(('annual', pfaf))
        for code, (metric, unit) in BASIN.items():
            yield from _indicator(out, f'aq40:{pfaf}:{code}', basin, metric, unit, row, code, locator, dims)
        area = _value(row.get('area_km2'))
    gid0, gid1 = (row.get('gid_0') or '').strip(), (row.get('gid_1') or '').strip()
    admin = None
    if gid1 and gid1 != '-9999':
        admin = 'gadm36:' + gid1
        if admin not in admins:
            admins.add(admin)
            yield out.entity(admin, 'location', (row.get('name_1') or gid1).strip(), locator, gadm_level=1, gid_1=gid1)
            if gid0 and gid0 != '-9999':
                country = 'iso3:' + gid0
                if country not in admins:
                    admins.add(country)
                    yield out.entity(country, 'country', (row.get('name_0') or gid0).strip(), locator)
                yield out.within(admin, country, locator)
            for code, (metric, unit) in ADMIN.items():
                yield from _indicator(out, f'aq40:{gid1}:{code}', admin, metric, unit, row, code, locator, dims)
    string_id = (row.get('string_id') or '').strip()
    if string_id and ('unit', string_id) not in basins:  # a few string_ids repeat (units without admin-1)
        basins.add(('unit', string_id))
        unit_id = 'aqueduct:unit:' + string_id
        yield out.entity(unit_id, 'location', f'Aqueduct unit {string_id}', locator, aqid=_value(row.get('aqid')),
                         area_km2=_value(row.get('area_km2')))
        yield out.within(unit_id, basin, locator)
        if admin:
            yield out.within(unit_id, admin, locator)
        for group in ('def',):
            yield from _indicator(out, f'aq40:{string_id}:awr_{group}_tot', unit_id, 'overall_water_risk', 'score_0_5_weighted',
                                  row, f'w_awr_{group}_tot', locator, {**dims, 'weighting': 'default'})
