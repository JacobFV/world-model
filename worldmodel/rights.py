"""Preserve source rights metadata through derivations without imposing execution gates.

One restriction *is* enforced, because it decides what a pipeline writes rather than how a
published artifact may be used: whether identified natural persons may be retained. Sources
differ in why they restrict, so a single global switch would be wrong. Each dataset declares
its own rule under ``source.person_level_records`` and the operator declares the deployment's
purpose once, in ``WM_COMMERCIAL_USE``.
"""
import os

from .util import canonical

COMMERCIAL_USE_ENV='WM_COMMERCIAL_USE'
#: ``source.person_level_records.policy`` values, from most to least restrictive.
PERSON_POLICIES=('prohibited','conditional','permitted')
_FALSE={'0','false','no','off','non_commercial','noncommercial'}
_TRUE={'1','true','yes','on','commercial'}


def commercial_use(environ=None):
    """Whether this deployment operates for a commercial purpose.

    Defaults to ``True``. A restriction that binds only commercial users has to bind until
    someone actually declares otherwise, so a fresh clone gets the conservative path and the
    permissive one is always an explicit act by the operator.
    """
    environ=os.environ if environ is None else environ
    declared=environ.get(COMMERCIAL_USE_ENV)
    if declared is None:return True
    value=declared.strip().lower()
    if value in _FALSE:return False
    if value in _TRUE:return True
    raise ValueError(f'{COMMERCIAL_USE_ENV} must be one of {sorted(_TRUE|_FALSE)}; got {declared!r}')


def person_level_rule(source):
    """Read a dataset's declared rule for retaining identified natural persons.

    An undeclared source is treated as ``prohibited``: the aggregate-only behaviour every
    dataset had before this rule existed stays the default for every dataset that has not
    thought about it.
    """
    rule=(source or {}).get('person_level_records') or {}
    if not isinstance(rule,dict):raise ValueError(f'source.person_level_records must be an object; got {type(rule).__name__}')
    policy=rule.get('policy','prohibited')
    if policy not in PERSON_POLICIES:raise ValueError(f'Unknown person_level_records policy {policy!r}; expected one of {PERSON_POLICIES}')
    if policy=='conditional' and rule.get('condition')!='non_commercial_use':
        raise ValueError("A conditional person_level_records rule must declare condition='non_commercial_use'; "
                         f'got {rule.get("condition")!r}')
    return {'policy':policy,'condition':rule.get('condition'),'authority':rule.get('authority'),'note':rule.get('note')}


def retain_identified_persons(source,environ=None):
    """Decide, and explain, whether a pipeline may emit identified natural persons.

    Returns ``(allowed, decision)``. The decision travels into the artifact so a filtered and
    an unfiltered build of the same source are told apart by their provenance rather than by
    inspecting their rows.
    """
    rule=person_level_rule(source)
    purpose='commercial' if commercial_use(environ) else 'non_commercial'
    allowed=rule['policy']=='permitted' or (rule['policy']=='conditional' and purpose=='non_commercial')
    reason=({'permitted':'the source places no condition on identified persons',
             'prohibited':'the source prohibits retaining identified persons'}.get(rule['policy'])
            or f'the source permits identified persons for non-commercial use only, and this deployment declared {purpose}')
    return allowed,{**rule,'declared_purpose':purpose,'identified_persons_retained':allowed,'reason':reason}


def inherited_rights(store,inputs=(),raw_inputs=()):
    pending=[('derived',ref) for ref in inputs]+[('raw',ref) for ref in raw_inputs]
    visited=set();sources={}
    while pending:
        kind,ref=pending.pop();key=canonical(ref)
        if (kind,key) in visited:continue
        visited.add((kind,key))
        if len(visited)>10000:raise ValueError('Rights lineage exceeds 10000 inputs')
        if kind=='derived':
            manifest=store.manifest(ref)
            pending.extend(('derived',r) for r in manifest['inputs'])
            pending.extend(('raw',r) for r in manifest['raw_inputs'])
            continue
        receipt=store.artifact(ref);source=receipt.get('source',{})
        parent=source.get('sampling',{}).get('input_version')
        if parent is not None:pending.append(('derived',parent))
        fields=('publisher','license','license_id','license_status','terms_url','url','attribution','redistribution','rights')
        metadata={k:source[k] for k in fields if k in source}
        declared=metadata.get('license_id',metadata.get('license'))
        unknown=not isinstance(declared,str) or declared.strip().lower() in ('','unknown','verify','unspecified')
        sources[key]={'input':ref,'metadata':metadata,'terms_unspecified':unknown}
    rows=[sources[k] for k in sorted(sources)]
    return {'code_license':'MIT','sources':rows,'combination_policy':'preserve_all_source_terms_without_relicensing',
            'declared_purpose':'commercial' if commercial_use() else 'non_commercial',
            'redistribution_review_required':any(r['terms_unspecified'] or r['metadata'].get('redistribution') in (False,'restricted','prohibited') for r in rows),
            'computation_policy':'metadata_only_no_local_execution_gate',
            'interpretation':'Source metadata inventory; compatibility and derivative-work obligations are not automatically adjudicated.'}
