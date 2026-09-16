"""Fictional record generator with known truth for resolution tests and throughput benchmarks."""
import random

_SYLLABLES = ('ka lo mi ra ten vor sul bel dan fir gar hol jin kel lum mor nex pal quin ros sta tor ul ven wex yor zan '
              'bri cor dra eld fen gul hav isk jor kri lan mar nor oss pri qua ril sen tal uth vil wyn xan yel zor').split()
_INDUSTRY = ('foods energy systems capital motors logistics health pharma metals textiles software analytics '
             'shipping mining farms airlines bank insurance realty media').split()
_SUFFIXES = (('Inc', 'Incorporated', 'Inc.'), ('LLC', 'L.L.C.', 'Limited Liability Company'), ('Corp', 'Corporation'),
             ('Ltd', 'Limited'), ('Co', 'Company'), ('GmbH',), ('S.A.', 'SA'), ('PLC',))


def _word(rng):
    return ''.join(rng.choice(_SYLLABLES) for _ in range(rng.choice((2, 2, 3)))).capitalize()


def _typo(rng, text):
    if len(text) < 5:
        return text
    i = rng.randrange(1, len(text) - 2)
    kind = rng.random()
    if kind < 0.4:
        return text[:i] + text[i + 1] + text[i] + text[i + 2:]
    if kind < 0.7:
        return text[:i] + text[i + 1:]
    return text[:i] + rng.choice('aeiourstnl') + text[i + 1:]


def organizations(n, *, seed=7, duplicate_rate=0.2, identifier_rate=0.3, sources=('alpha', 'beta', 'gamma')):
    """Yield n fictional organization inputs; ``truth`` holds the true entity key (not used by the engine)."""
    rng = random.Random(seed)
    cities = [_word(rng) for _ in range(800)]
    produced, entity = 0, 0
    while produced < n:
        entity += 1
        words = [_word(rng) for _ in range(rng.choice((1, 2, 2, 3)))]
        if rng.random() < 0.6:
            words.append(rng.choice(_INDUSTRY).capitalize())
        suffix_group = rng.choice(_SUFFIXES)
        postal = f'{rng.randrange(1000, 99999):05d}'
        city = rng.choice(cities)
        lei = f'FICT{entity:016d}' if rng.random() < identifier_rate else None
        copies = 1 + (rng.random() < duplicate_rate) + (rng.random() < duplicate_rate / 4)
        for copy in range(copies):
            if produced >= n:
                return
            name_words = list(words)
            if copy:
                roll = rng.random()
                if roll < 0.5:
                    j = rng.randrange(len(name_words))
                    name_words[j] = _typo(rng, name_words[j])
                elif roll < 0.6 and len(name_words) > 1:
                    name_words = name_words[:-1]
            item = {'entity_id': f'{sources[copy % len(sources)]}:{entity}-{copy}', 'source': sources[copy % len(sources)],
                    'name': ' '.join(name_words) + ' ' + rng.choice(suffix_group), 'country': 'US',
                    'postal': postal if not copy or rng.random() > 0.15 else None,
                    'city': city if rng.random() > 0.2 else None,
                    'identifiers': {'lei': lei} if lei and (not copy or rng.random() < 0.5) else {},
                    'truth': f'org:{entity}'}
            produced += 1
            yield item


def true_pairs(items):
    groups = {}
    for item in items:
        groups.setdefault(item['truth'], []).append(item['entity_id'])
    return {(a, b) for members in groups.values() for a in members for b in members if a < b}
