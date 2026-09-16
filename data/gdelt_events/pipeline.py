"""GDELT 1.0 daily event exports: compact machine-coded events and daily dyad signals.

GDELT is automated news coding (duplicates, misclassification and media-volume
bias are common); treat outputs as weak signals next to UCDP/ACLED.
"""
from datetime import date, timedelta

from worldmodel.raw_readers import iter_rows

from .evidence import Evidence, is_full, num

ACTOR = ['Code', 'Name', 'CountryCode', 'KnownGroupCode', 'EthnicCode', 'Religion1Code', 'Religion2Code', 'Type1Code', 'Type2Code', 'Type3Code']
GEO = ['Type', 'FullName', 'CountryCode', 'ADM1Code', 'Lat', 'Long', 'FeatureID']
FIELDS = (['GLOBALEVENTID', 'SQLDATE', 'MonthYear', 'Year', 'FractionDate'] + ['Actor1' + f for f in ACTOR] + ['Actor2' + f for f in ACTOR]
          + ['IsRootEvent', 'EventCode', 'EventBaseCode', 'EventRootCode', 'QuadClass', 'GoldsteinScale', 'NumMentions', 'NumSources',
             'NumArticles', 'AvgTone'] + ['Actor1Geo_' + f for f in GEO] + ['Actor2Geo_' + f for f in GEO] + ['ActionGeo_' + f for f in GEO]
          + ['DATEADDED', 'SOURCEURL'])
READER = {'format': 'tsv', 'members': ['*.CSV'], 'quoting': 'none', 'compression': 'zip', 'fieldnames': FIELDS, 'strict': False}
# CAMEO regional / non-state country codes; every other 3-letter code is an ISO 3166-1 alpha-3 code.
REGIONS = {'AFR': 'Africa', 'ASA': 'Asia', 'BLK': 'Balkans', 'CAS': 'Central Asia', 'CAU': 'Caucasus', 'CFR': 'Central Africa',
           'CRB': 'Caribbean', 'EAF': 'Eastern Africa', 'EEU': 'Eastern Europe', 'EIN': 'East Indies', 'EUR': 'Europe',
           'LAM': 'Latin America', 'MEA': 'Middle East', 'MDE': 'Middle East', 'NAF': 'North Africa', 'NMR': 'North America',
           'PGS': 'Persian Gulf', 'SAF': 'Southern Africa', 'SAM': 'South America', 'SAS': 'South Asia', 'SCN': 'Scandinavia',
           'SEA': 'Southeast Asia', 'WAF': 'West Africa', 'WST': 'The West', 'NAM': None}
QUAD = {'1': 'verbal_cooperation', '2': 'material_cooperation', '3': 'verbal_conflict', '4': 'material_conflict'}


def iso_day(value):
    value = (value or '').strip()
    if len(value) != 8 or not value.isdigit():
        return None
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:])).isoformat()
    except ValueError:
        return None


class Places:
    def __init__(self, ev):
        self.ev = ev

    def key(self, code, loc):
        """Return (entity key or None, new entity record or None)."""
        code = (code or '').strip().upper()
        if len(code) != 3 or not code.isalpha():
            return None, None
        if code in REGIONS and REGIONS[code]:
            key = 'cameo:region:' + code
            return key, self.ev.entity(key, 'location', 'CAMEO region ' + REGIONS[code], loc, cameo_code=code)
        key = 'iso3:' + code
        return key, self.ev.entity(key, 'country', code, loc, iso3=code, label_source='CAMEO country code; label is the code')


def _shards(context):
    if not is_full(context):
        raise ValueError('gdelt_events: requires the full daily export acquisition (no sample adapter)')
    return list(context.raw_shards())


def run(context):
    ev = Evidence(context, 'gdelt')
    places = Places(ev)
    for shard in _shards(context):
        for loc, row in iter_rows([shard], READER):
            if row.get('SOURCEURL') is None or not row.get('GLOBALEVENTID'):
                continue  # malformed/short line; physical locator is still auditable in raw
            added = iso_day(row['DATEADDED'])
            occurred = iso_day(row['SQLDATE']) or added
            if occurred is None:
                continue
            participants = []
            for side in ('Actor1', 'Actor2'):
                key, record = places.key(row[side + 'CountryCode'], loc)
                if record is not None:
                    yield record
                if key and key not in participants:
                    participants.append(key)
            actors = {}
            for side, name in (('Actor1', 'actor1'), ('Actor2', 'actor2')):
                if row[side + 'Code']:
                    actors[name] = {'code': row[side + 'Code'], 'country': row[side + 'CountryCode'] or None,
                                    'type': row[side + 'Type1Code'] or None, 'name': row[side + 'Name'] or None}
            geo = None
            if row['ActionGeo_Type'] not in ('', '0'):
                geo = {'type': num(row['ActionGeo_Type']), 'fips': row['ActionGeo_CountryCode'] or None,
                       'adm1': row['ActionGeo_ADM1Code'] or None, 'lat': num(row['ActionGeo_Lat']),
                       'lon': num(row['ActionGeo_Long']), 'name': row['ActionGeo_FullName'] or None}
            yield ev.event('cameo:' + row['EventCode'], occurred, participants, loc, [row['GLOBALEVENTID'], row['DATEADDED']],
                           attributes={'gdelt_id': num(row['GLOBALEVENTID']), 'date_added': added, 'root': row['IsRootEvent'] == '1',
                                       'base_code': row['EventBaseCode'], 'root_code': row['EventRootCode'],
                                       'quad_class': QUAD.get(row['QuadClass']), 'goldstein': num(row['GoldsteinScale']),
                                       'mentions': num(row['NumMentions']), 'sources': num(row['NumSources']),
                                       'articles': num(row['NumArticles']), 'tone': num(row['AvgTone']),
                                       **actors, **({'action_geo': geo} if geo else {})})
    ev.close()


def daily_dyads(context):
    """Per publication day (DATEADDED), actor1 place x actor2 place x QuadClass: counts, Goldstein and tone means."""
    ev = Evidence(context, 'gdelt_daily')
    places = Places(ev)
    for shard in _shards(context):
        buckets = {}
        locator = None
        for loc, row in iter_rows([shard], READER):
            if row.get('SOURCEURL') is None or not row.get('GLOBALEVENTID'):
                continue
            added = iso_day(row['DATEADDED'])
            subject, record = places.key(row['Actor1CountryCode'], loc)
            if record is not None:
                yield record
            other, record = places.key(row['Actor2CountryCode'], loc)
            if record is not None:
                yield record
            if added is None or subject is None or row['QuadClass'] not in QUAD:
                continue
            locator = locator or loc.rsplit('/line:', 1)[0]
            bucket = buckets.setdefault((added, subject, other or 'none', QUAD[row['QuadClass']]), [0, 0, 0.0, 0.0, 0])
            goldstein, tone, mentions = num(row['GoldsteinScale']), num(row['AvgTone']), num(row['NumMentions']) or 0
            bucket[0] += 1
            bucket[1] += row['IsRootEvent'] == '1'
            bucket[2] += goldstein or 0
            bucket[3] += tone or 0
            bucket[4] += mentions
        for (day, subject, other, quad), (count, roots, goldstein, tone, mentions) in sorted(buckets.items()):
            end = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
            common = dict(valid_from=day, valid_to=end, aggregate=True,
                          dimensions={'frequency': 'daily', 'counterpart': other, 'quad_class': quad, 'date_basis': 'dateadded'},
                          method='GDELT 1.0 daily export events with Actor1CountryCode=subject; machine-coded weak signal')
            yield ev.observation(subject, 'gdelt_event_count', count, 'events', locator, **common)
            yield ev.observation(subject, 'gdelt_root_event_count', roots, 'events', locator, **common)
            yield ev.observation(subject, 'gdelt_mentions', mentions, 'mentions', locator, **common)
            yield ev.observation(subject, 'gdelt_goldstein_mean', round(goldstein / count, 4), 'goldstein_scale_-10_10', locator, **common)
            yield ev.observation(subject, 'gdelt_avg_tone_mean', round(tone / count, 4), 'tone_score', locator, **common)
    ev.close()
