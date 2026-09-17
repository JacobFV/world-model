"""Grounded cognitive agents: entities from the unified graph that perceive, believe and decide.

The design contract is ``docs/agents-design.md``. The cognitive substrate is **tensacode**
(``Store`` of claims with evidence, ``Fragment``/``integrate``, ``Rule``/``think``, ``explain``,
``choose`` with constraints and ``Unknown``), which is an **optional** dependency::

    pip install "worldmodel-substrate[agents]"

The core of this package imports without it, exactly like the numpy extra in
``worldmodel.backends``: ``tensacode_available()`` answers whether the substrate is installed
and ``load_tensacode()`` either returns it or raises with an actionable message. The modules
that actually need it (``grounding``, ``person``, ``agents_cli``) call ``load_tensacode()`` at
import time, so ``import worldmodel.agents`` is always safe and ``import
worldmodel.agents.person`` fails loudly and clearly when the extra is missing.

``affect`` is pure Python and needs neither tensacode nor numpy.
"""
INSTALL_HINT = ('Grounded cognitive agents require the optional substrate: '
                'pip install "worldmodel-substrate[agents]" (tensacode>=0.0.0.dev0). '
                'For a local checkout: pip install -e <tensacode>/tensacode/python, or run with '
                'PYTHONPATH=<tensacode>/tensacode/python/src.')

_tensacode = None


def load_tensacode(required=True):
    """Return the ``tensacode`` module, or ``None`` when it is absent and not required."""
    global _tensacode
    if _tensacode is None:
        try:
            import tensacode
        except ImportError:
            tensacode = False
        _tensacode = tensacode
    if _tensacode is False:
        if required:
            raise ImportError(INSTALL_HINT)
        return None
    return _tensacode


def tensacode_available():
    return load_tensacode(required=False) is not None


#: Names this package exposes lazily, so that a missing substrate costs an ImportError only
#: when something that needs it is actually touched. ``firm`` and ``institution`` are the
#: sibling agent kinds from the design contract; entries for names their modules do not define
#: simply raise ``AttributeError``.
_LAZY = {
    'EvidenceIndex': 'grounding', 'Horizon': 'grounding', 'Percept': 'grounding',
    'Grounding': 'grounding', 'SeedReport': 'grounding', 'seed_store': 'grounding',
    'perceive': 'grounding', 'LEGISLATOR': 'grounding', 'default_index_path': 'grounding',
    'Person': 'person', 'Episode': 'person', 'Intention': 'person', 'legislator': 'person',
    'Firm': 'firm', 'Institution': 'institution',
    'Role': 'roles', 'Charter': 'roles', 'Authority': 'roles', 'Procedure': 'roles',
    'InstitutionalAct': 'roles', 'CorporateReading': 'corporate_affect',
}


def __getattr__(name):
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError('module %r has no attribute %r' % (__name__, name))
    import importlib
    try:
        module = importlib.import_module('.' + module_name, __name__)
    except ImportError as error:
        # A sibling agent kind whose module is not in this checkout yet is simply absent, not
        # a broken install; a missing *substrate* still raises with the install hint.
        if module_name in str(error) and 'tensacode' not in str(error):
            raise AttributeError('module %r has no attribute %r' % (__name__, name)) from error
        raise
    try:
        return getattr(module, name)
    except AttributeError:
        raise AttributeError('module %r has no attribute %r' % (__name__, name)) from None


def __dir__():
    return sorted(set(list(globals()) + list(_LAZY)))


__all__ = ['INSTALL_HINT', 'load_tensacode', 'tensacode_available', *sorted(_LAZY)]
