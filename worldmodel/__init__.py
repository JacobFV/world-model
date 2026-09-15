"""Immutable evidence pipelines; no network activity on import."""
import hashlib
from pathlib import Path

__version__ = '0.3.0'
_PACKAGE_ROOT = Path(__file__).resolve().parent
# Long-lived SDK processes must restart after editing implementation files. This
# prevents importlib's module cache from running old code under new source hashes.
_LOADED_SOURCE_HASHES = {
    str(path.relative_to(_PACKAGE_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in _PACKAGE_ROOT.rglob('*.py')
}
