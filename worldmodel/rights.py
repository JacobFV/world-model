"""Preserve source rights metadata through derivations without imposing execution gates."""
from .util import canonical


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
            'redistribution_review_required':any(r['terms_unspecified'] or r['metadata'].get('redistribution') in (False,'restricted','prohibited') for r in rows),
            'computation_policy':'metadata_only_no_local_execution_gate',
            'interpretation':'Source metadata inventory; compatibility and derivative-work obligations are not automatically adjudicated.'}
