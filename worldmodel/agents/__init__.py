"""Grounded cognitive agents: entities from the unified graph that perceive, believe and decide.

The design contract is ``docs/agents-design.md``. The cognitive substrate is **tensorcode**
(``Store`` of claims with evidence, ``Fragment``/``integrate``, ``Rule``/``think``, ``explain``,
``choose`` with constraints and ``Unknown``), which is an **optional** dependency::

    pip install "worldmodel-substrate[agents]"

The core of this package imports without it, exactly like the numpy extra in
``worldmodel.backends``: ``tensorcode_available()`` answers whether the substrate is installed
and ``load_tensorcode()`` either returns it or raises with an actionable message. The modules
that actually need it (``grounding``, ``person``, ``agents_cli``) call ``load_tensorcode()`` at
import time, so ``import worldmodel.agents`` is always safe and ``import
worldmodel.agents.person`` fails loudly and clearly when the extra is missing.

``affect`` is pure Python and needs neither tensorcode nor numpy.
"""
INSTALL_HINT = ('Grounded cognitive agents require the optional substrate: '
                'pip install "worldmodel-substrate[agents]", or install it directly '
                'with: pip install "tensorcode>=0.1.0a1".')

_tensorcode = None


def load_tensorcode(required=True):
    """Return the ``tensorcode`` module, or ``None`` when it is absent and not required."""
    global _tensorcode
    if _tensorcode is None:
        try:
            import tensorcode
        except ImportError:
            tensorcode = False
        _tensorcode = tensorcode
    if _tensorcode is False:
        if required:
            raise ImportError(INSTALL_HINT)
        return None
    return _tensorcode


def tensorcode_available():
    return load_tensorcode(required=False) is not None


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
        if module_name in str(error) and 'tensorcode' not in str(error):
            raise AttributeError('module %r has no attribute %r' % (__name__, name)) from error
        raise
    try:
        return getattr(module, name)
    except AttributeError:
        raise AttributeError('module %r has no attribute %r' % (__name__, name)) from None


def __dir__():
    return sorted(set(list(globals()) + list(_LAZY)))


__all__ = ['INSTALL_HINT', 'load_tensorcode', 'tensorcode_available', *sorted(_LAZY)]
