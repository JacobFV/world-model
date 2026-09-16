"""Entity resolution: deterministic identifier links and auditable probabilistic matching.

- :mod:`.normalize`: legal-suffix, transliteration, person-name and address normalization.
- :mod:`.similarity`: Jaro-Winkler, Jaccard, TF-IDF cosine.
- :mod:`.fellegi_sunter`: Fellegi-Sunter scoring with EM-estimated m/u probabilities.
- :mod:`.deterministic`: published-crosswalk links (LEI-CIK, FEC, bioguide-ICPSR, OFAC-OpenSanctions...).
- :mod:`.engine`: SQLite-backed blocking, comparison, estimation, constrained clustering and resolved views.

Nothing here rewrites source records. Outputs are explicit assertions with method,
score, evidence and reviewer status; the resolved view is regenerable and digest-audited.
"""
from .deterministic import MAPPING_SPECS, UNIQUE_NAMESPACES, link_mapping, shared_identifier_links
from .engine import ResolutionEngine, inputs_from_records
from .fellegi_sunter import FellegiSunter
from .normalize import normalize_address, normalize_name, normalize_organization, normalize_person, soundex, transliterate
from .similarity import TfIdf, jaro, jaro_winkler, token_jaccard

__all__ = ['MAPPING_SPECS', 'UNIQUE_NAMESPACES', 'link_mapping', 'shared_identifier_links', 'ResolutionEngine',
           'inputs_from_records', 'FellegiSunter', 'normalize_address', 'normalize_name', 'normalize_organization',
           'normalize_person', 'soundex', 'transliterate', 'TfIdf', 'jaro', 'jaro_winkler', 'token_jaccard']
