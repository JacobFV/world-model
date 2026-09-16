"""Optional vectorized backend selection. The pure-Python path is the reference.

Install the optional extra with ``pip install worldmodel-substrate[fast]`` (numpy).
``backend`` arguments accept ``'python'`` (reference), ``'numpy'`` (required,
fails if unavailable) or ``'auto'`` (numpy when installed and the work is at
least ``AUTO_THRESHOLD`` elements, otherwise python). ``WORLD_MODEL_BACKEND``
sets the default for calls that do not pass a backend.
"""
import os

BACKENDS = ('python', 'numpy', 'auto')
AUTO_THRESHOLD = 50_000
_numpy = None


def numpy_available():
    return load_numpy(required=False) is not None


def load_numpy(required=True):
    global _numpy
    if _numpy is None:
        try:
            import numpy
        except ImportError:
            numpy = False
        _numpy = numpy
    if _numpy is False:
        if required:
            raise ImportError('The numpy backend requires the optional extra: pip install "worldmodel-substrate[fast]" (numpy>=1.26)')
        return None
    return _numpy


def resolve_backend(backend=None, size=0, threshold=AUTO_THRESHOLD):
    """Return 'python' or 'numpy' for an explicit, environment or auto choice."""
    if backend is None:
        backend = os.environ.get('WORLD_MODEL_BACKEND', 'auto') or 'auto'
    if backend not in BACKENDS:
        raise ValueError(f'backend must be one of {BACKENDS}')
    if backend == 'python':
        return 'python'
    if backend == 'numpy':
        load_numpy(required=True)
        return 'numpy'
    return 'numpy' if size >= threshold and numpy_available() else 'python'
