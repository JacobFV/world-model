"""Q3. One commodity from field production to traded quantity to price.

Four publishers describe the same physical crop with four different code systems, and only the
published concordances let them meet:

  usda_agriculture   USDA NASS production, area harvested and prices received, keyed by
                     dimensions.commodity = nass:commodity:<name>, subject = a US geography
  usda_fas_psd       USDA FAS country supply/distribution balances, keyed by
                     dimensions.commodity = usda_psd:commodity:<code>, subject = iso3:<ISO3>
  un_comtrade        reported bilateral trade, keyed by dimensions.product = hs:<code>
  classifications    the HS <-> Schedule B <-> NAICS maps_to edges
  wits_trains_tariffs / usitc_hts_tariffs   the tariff lines on the same HS code
"""
import common

NASS_METRICS = ('production', 'area_harvested', 'average_price', 'yield')
PSD_METRICS = ('production', 'exports', 'imports', 'ending_stocks', 'domestic_consumption')
TRADE_METRICS = ('trade_value', 'trade_net_weight')


def labelled_entities(connection, prefix, term, limit=20000):
    """Entities in one ID namespace whose published label mentions the commodity term."""
    low, high = common.prefix_range(prefix)
    found = []
    for row in common.stream(connection, 'SELECT entity_id, dataset, body FROM records WHERE entity_id >= ? '
                                         "AND entity_id < ? AND kind='entity' LIMIT ?", (low, high, limit)):
        body = common.body_of(row)
        label = body.get('label') or ''
        if term in label.lower():
            found.append({'entity_id': row['entity_id'], 'label': label, 'from_dataset': row['dataset']})
    return found


def series_for(connection, subject, key, wanted, *, dataset=None, keep=2, metrics=None):
    """Observations recorded against one subject whose dimension ``key`` is one of ``wanted``.

    Anchoring on the subject means one point lookup on the subject index; scanning a metric across
    the whole 51M-observation table does not scale.
    """
    wanted, out = set(wanted), {}
    query = "SELECT dataset, body FROM records WHERE subject=? AND kind='observation'"
    args = [subject]
    if dataset:
        query += ' AND dataset=?'
        args.append(dataset)
    for row in common.stream(connection, query, args):
        body = common.body_of(row)
        dimensions = body.get('dimensions') or {}
        if str(dimensions.get(key) or '') not in wanted:
            continue
        if metrics and body.get('metric') not in metrics:
            continue
        bucket = out.setdefault((row['dataset'], body.get('metric'), body.get('unit')), [])
        if len(bucket) < keep:
            bucket.append({'subject': body.get('subject'), 'value': body.get('value'),
                           'valid_from': body.get('valid_from'), 'valid_to': body.get('valid_to'),
                           'code': str(dimensions.get(key)),
                           'dimensions': {k: v for k, v in dimensions.items() if k != 'series'}})
    return [{'from_dataset': dataset, 'metric': metric, 'unit': unit, 'examples': examples}
            for (dataset, metric, unit), examples in sorted(out.items(), key=str)]


def main():
    options = common.parser(__doc__)
    options.add_argument('--commodity', default='wheat', help='a term matched against published commodity labels')
    options.add_argument('--country', default='USA', help='ISO3 for the balance and trade legs')
    args = options.parse_args()
    graph, connection = common.open_index(args.index)
    term = args.commodity.lower()
    nass = labelled_entities(connection, 'nass:commodity:', term)
    psd = labelled_entities(connection, 'usda_psd:commodity:', term)
    # Only the classification datasets publish English labels for trade codes: the bare hs: entities
    # that census_intl_trade emits are labelled "HS 100111". Start from Schedule B and NAICS, then
    # follow the published maps_to edges to the HS codes Comtrade reports.
    schedule_b = labelled_entities(connection, 'scheduleb2025:', term, limit=12000)
    naics = labelled_entities(connection, 'naics2022:', term, limit=12000)
    codes = sorted({e['entity_id'] for e in schedule_b})
    bridge = []
    for code in codes[:8]:
        bridge.extend(common.described(connection, graph.neighborhood(
            code, hops=1, predicates=['maps_to', 'classified_as'], limit=40)['edges']))
    mapped = {edge['object'] for edge in bridge if (edge['object'] or '').startswith('hs')}
    mapped |= {edge['subject'] for edge in bridge if (edge['subject'] or '').startswith('hs')}
    # The published classified_as edges reach NAICS, SITC and end-use, but not HS. A Schedule B code's
    # first six digits *are* its HS6 code by Census construction, so the HS codes below are DERIVED by
    # truncation, not read from a published edge. Comtrade reports at chapter, heading and subheading.
    derived = {'hs:' + code.split(':', 1)[1][:n] for code in codes for n in (2, 4, 6)}
    chapters = sorted(derived | {'hs:' + code.split(':', 1)[1][:n] for code in mapped for n in (2, 4, 6)})
    production = series_for(connection, 'geo:US', 'commodity', [e['entity_id'] for e in nass],
                            dataset='usda_agriculture', metrics=NASS_METRICS)
    balances = series_for(connection, 'iso3:' + args.country, 'commodity', [e['entity_id'] for e in psd],
                          dataset='usda_fas_psd', metrics=PSD_METRICS)
    trade = series_for(connection, 'iso3:' + args.country, 'product', chapters,
                       dataset='un_comtrade', metrics=TRADE_METRICS)
    result = {
        'answer': {
            'commodity_term': term,
            'the_same_crop_under_four_code_systems': {'nass': nass[:6], 'usda_psd': psd[:6],
                                                      'schedule_b': schedule_b[:6], 'naics': naics[:6],
                                                      'hs_codes_used_for_the_trade_leg': chapters,
                                                      'hs_code_basis': 'DERIVED by truncating the Schedule B '
                                                                       'code (its first six digits are the HS6 '
                                                                       'code); the published classified_as '
                                                                       'edges reach NAICS, SITC and end-use, '
                                                                       'not HS'},
            'physical_production_and_prices_received': production,
            'country_supply_and_distribution_balances': balances,
            'reported_bilateral_trade': trade,
            'published_code_bridge': bridge[:args.limit]},
        'counts': {'nass_codes': len(nass), 'psd_codes': len(psd), 'schedule_b_codes': len(schedule_b),
                   'naics_codes': len(naics), 'hs_codes_reached': len(chapters),
                   'production_series': len(production), 'balance_series': len(balances),
                   'trade_series': len(trade), 'code_bridge_edges': len(bridge)},
        'which_dataset_supplied_which_edge': {
            'physical production, area harvested and prices received by farmers': 'usda_agriculture (USDA NASS)',
            'country supply and distribution balances': 'usda_fas_psd (USDA FAS PSD)',
            'reported bilateral trade values and weights': 'un_comtrade',
            'Schedule B -> NAICS / SITC / end-use code bridge': 'classifications (classified_as assertions)',
            'Schedule B -> HS': 'DERIVED by truncation, not published as an edge in this catalog',
            'identity of the crop across the three code systems': 'NOT published as a crosswalk. The three code '
                                                                 'sets are matched here on their published '
                                                                 'English labels - INFERRED, not asserted.'},
        'what_this_does_not_establish': [
            'The crop identity across NASS, PSD and HS is a label match, not a published crosswalk. Treat it as a '
            'hypothesis to check, not as a join key.',
            'This is not a commodity balance. NASS bushels, PSD metric tonnes and Comtrade kilograms use different '
            'commodity definitions, marketing years and coverage; nothing here reconciles them. '
            'worldmodel.units.convert refuses bushels-to-mass without an explicit commodity.',
            'Comtrade is reported trade: importer and exporter reports of the same flow disagree, and aggregate '
            'rows are flagged is_aggregate. The harmonised mirror-flow dataset (cepii_baci, 89.2M rows) is '
            'outside the default profile.',
            'A price received by farmers is not a futures or spot price, and marketing-year averages are not '
            'daily quotes.',
            'maps_to edges carry no split weights, so quantities must not be summed across a mapping without '
            'worldmodel.crosswalks apportionment.'],
    }
    return common.emit('q3_commodity_production_trade_price', result, save=args.save)


if __name__ == '__main__':
    main()
