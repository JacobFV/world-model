"""Q2. One US county across employment, population, weather/hazard exposure and disaster assistance.

Every leg comes from a different publisher, joined only on the Census GEOID:

  census_geography        county <-> state containment, land/water area
  census_business         CBP establishment counts, employment and payroll
  census_population       Population Estimates and components of change
  acs_5yr_tables          ACS 5-year demographic and income estimates
  fema_nri                National Risk Index expected annual loss and risk ratings
  noaa_climdiv            nClimDiv monthly county temperature and precipitation
  noaa_storm_events       NCEI Storm Events episodes and damages
  openfema                FEMA public assistance and individual assistance
"""
import common

LEGS = {
    'geography': ('census_geography', None),
    'employment': ('census_business', None),
    'jobs': ('lehd_lodes', None),
    'population': ('census_population', None),
    'demographics': ('acs_5yr_tables', None),
    'agriculture': ('usda_agriculture', None),
    'hazard_risk': ('fema_nri', None),
    'climate': ('noaa_climdiv', None),
}
# Datasets that describe counties but do not anchor a record on the GEOID, so a GEOID join does
# not reach them: their subject is the event, not the place.
UNREACHABLE_BY_GEOID = {
    'noaa_storm_events': 'observations and events are anchored on noaa:storm_event:<id>; the county travels '
                         'in dimensions and participants, not as a subject or an edge',
    'openfema': 'records are anchored on fema:disaster:<id> and FEMA project identifiers; county names without '
                'a FIPS stay dimensions',
    'epa_aqs_daily': 'published after this index was built (the catalog is live)',
}


def candidate_counties(connection, limit=60):
    """Counties that appear as the subject of a containment edge. The edges table is small and
    indexed, so this costs nothing next to scanning 51M observation rows."""
    low, high = common.prefix_range('geo:US:county:')
    return [row['subject'] for row in common.rows(
        connection, 'SELECT DISTINCT subject FROM edges WHERE subject >= ? AND subject < ? '
                    "AND predicate = 'within' LIMIT ?", (low, high, limit))]


def richest_county(connection, wanted, limit=60):
    """The candidate county described by the most of the datasets above.

    Each check is a point lookup on the subject index, so this stays cheap on a 125 GB index;
    a GROUP BY over every county observation is not.
    """
    best = None
    for county in candidate_counties(connection, limit):
        sources = {row['dataset'] for row in common.rows(
            connection, "SELECT DISTINCT dataset FROM records WHERE subject = ? AND kind = 'observation'",
            (county,))} & set(wanted)
        if best is None or len(sources) > best['sources']:
            best = {'subject': county, 'sources': len(sources), 'datasets': sorted(sources)}
        if best['sources'] == len(wanted):
            break
    return best


def main():
    options = common.parser(__doc__)
    options.add_argument('--county', help='GEOID5, e.g. 06001')
    args = options.parse_args()
    graph, connection = common.open_index(args.index)
    wanted = sorted({name for name, _ in LEGS.values()})
    if args.county:
        county = 'geo:US:county:' + args.county
    else:
        best = richest_county(connection, wanted)
        if not best:
            return common.emit('q2_county_economy_hazard_assistance',
                               {'answer': None, 'reason': 'no county observations in this index scope'},
                               save=args.save)
        county = best['subject']
    containment = common.described(connection,
                                  graph.neighborhood(county, hops=2, predicates=['within'], limit=20)['edges'])
    observations, by_dataset = [], {}
    for dataset in wanted:  # one query per publisher, so no dataset can crowd out the others
        for row in common.observations_for(connection, county, limit=60, dataset=dataset):
            observations.append(row)
            by_dataset.setdefault(row['from_dataset'], {}).setdefault(row['metric'], []).append(row)
    answer = {'county': common.label(connection, county), 'containment': containment[:4]}
    for leg, (dataset, _) in LEGS.items():
        metrics = by_dataset.get(dataset, {})
        answer[leg] = {'dataset': dataset,
                       'metrics': {metric: {'observations': len(values),
                                            'example': {k: values[0][k] for k in
                                                        ('value', 'unit', 'valid_from', 'valid_to',
                                                         'missing_reason')}}
                                   for metric, values in sorted(metrics.items())[:12]}}
        if not metrics:
            answer[leg]['note'] = 'no observation for this county in the selected scope'
    migration = common.described(connection, graph.neighborhood(
        county, hops=1, predicates=['flow_source', 'flow_destination'], direction='in', limit=200)['edges'])
    answer['migration_flows'] = {'dataset': 'irs_soi_migration', 'edges': len(migration),
                                 'examples': migration[:4]}
    result = {
        'answer': answer,
        'counts': {'datasets_describing_this_county': len({r['from_dataset'] for r in observations}),
                   'observations_inspected': len(observations), 'migration_flow_edges': len(migration)},
        'which_dataset_supplied_which_edge': {
            'county -> state -> nation containment, and tract -> county': 'census_geography / acs_5yr_tables '
                                                                          '(within assertions)',
            **{leg: dataset for leg, (dataset, _) in LEGS.items()},
            'county-to-county migration flows': 'irs_soi_migration (flow_source / flow_destination edges)'},
        'datasets_that_describe_counties_but_cannot_be_reached_by_a_geoid_join': UNREACHABLE_BY_GEOID,
        'what_this_does_not_establish': [
            'Joining on a GEOID is a join on a geography vintage, not on a stable place: Connecticut replaced its '
            'counties with planning regions from 2022, and older vintages in these datasets still use the legacy '
            'codes. Cross-vintage totals need worldmodel.crosswalks, not a string match.',
            'No causal claim. Hazard risk, employment, climate and migration are separate measurements of the '
            'same polygon.',
            'Periods differ: CBP employment is a March pay period, Population Estimates are 1 July stocks, ACS is '
            'a five-year average, nClimDiv is monthly, NRI is a single 2025 vintage, LODES is an annual snapshot.',
            'Suppressed CBP cells are null with a missing_reason and are not zero.',
            'The hazard leg is expected annual loss and a risk rating, not a forecast and not an insurance price.',
            'Storm damage and disaster assistance are missing by construction, not by absence: NOAA Storm Events '
            'and OpenFEMA anchor their records on event and disaster identifiers, so a GEOID join never reaches '
            'them. Traversing dimensions and event participants is a different query.'],
    }
    return common.emit('q2_county_economy_hazard_assistance', result, save=args.save)


if __name__ == '__main__':
    main()
