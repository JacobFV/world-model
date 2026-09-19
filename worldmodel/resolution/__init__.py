"""Entity resolution: deterministic identifier links and auditable probabilistic matching.

- :mod:`.normalize`: legal-suffix, transliteration, person-name and address normalization.
- :mod:`.similarity`: Jaro-Winkler, Jaccard, TF-IDF cosine.
- :mod:`.fellegi_sunter`: Fellegi-Sunter scoring with EM-estimated m/u probabilities.
- :mod:`.deterministic`: published-crosswalk links (LEI-CIK, FEC, bioguide-ICPSR, OFAC-OpenSanctions...).
- :mod:`.bridges`: published identifiers carried in fields that are not identifier assertions
  (GLEIF registration-authority entity IDs, the CUSIP inside a US ISIN).
- :mod:`.wikidata`: the Wikidata external-identifier statements those bridges read, what each
  property is, the check digits each value is held to, and the properties that are refused.
- :mod:`.engine`: SQLite-backed blocking, comparison, estimation, constrained clustering and resolved views.

Nothing here rewrites source records. Outputs are explicit assertions with method,
score, evidence and reviewer status; the resolved view is regenerable and digest-audited.
"""
from .bridges import BRIDGES, BRIDGE_TAGS, cusip_from_isin, gleif_registration_authority_claim, record_claims
from .deterministic import MAPPING_SPECS, UNIQUE_NAMESPACES, link_mapping, shared_identifier_links
from .engine import ResolutionEngine, inputs_from_records
from .fellegi_sunter import FellegiSunter
from .normalize import normalize_address, normalize_name, normalize_organization, normalize_person, soundex, transliterate
from .similarity import TfIdf, jaro, jaro_winkler, token_jaccard
from .wikidata import PROPERTIES as WIKIDATA_PROPERTIES, REFUSED as WIKIDATA_REFUSED, geo_entity_id_claim, statement_claim

__all__ = ['MAPPING_SPECS', 'UNIQUE_NAMESPACES', 'BRIDGES', 'BRIDGE_TAGS', 'cusip_from_isin',
           'gleif_registration_authority_claim', 'record_claims', 'link_mapping', 'shared_identifier_links',
           'ResolutionEngine', 'inputs_from_records', 'FellegiSunter', 'normalize_address', 'normalize_name',
           'normalize_organization', 'normalize_person', 'soundex', 'transliterate', 'TfIdf', 'jaro',
           'jaro_winkler', 'token_jaccard', 'WIKIDATA_PROPERTIES', 'WIKIDATA_REFUSED',
           'geo_entity_id_claim', 'statement_claim']
