"""Incremental, transactional temporal evaluation with JSON checkpoints.

Checkpoints are integrity checked, not authenticated: accept them only from trusted
sources. Live backend effects cannot be rolled back. A failed backend transition
blocks retries and live-backend checkpoint restore is deliberately unsupported.
"""
import array
from copy import deepcopy
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys
import uuid
import zlib

from . import materialize as temporal
from .model import instant
from .provenance import capture_code
from .processes import combine_pressures, _typed, _pressure
from .process_library import default_registry
from .limits import resolve_limits
from .util import canonical, digest, file_hash
from .view_state import EvidenceState, collect_evidence


def _json_native(value, limits=None):
    """Reject Python values that JSON serialization would change semantically."""
    maximum=resolve_limits(limits).checkpoint_max_items
    finite=math.isfinite
    def reject():
        raise ValueError('Checkpoint values must be finite JSON-native values; tuples and Python objects are unsupported')
    kind=type(value)
    if kind is not dict and kind is not list:
        if kind is float and not finite(value):reject()
        if value is not None and kind not in (str,bool,int,float):reject()
        return
    pending=[(value,0)];visited=1
    while pending:
        item,depth=pending.pop()
        if depth>256:
            raise ValueError('Checkpoint JSON-native nesting or item budget exceeded')
        if type(item) is dict:
            for key in item:
                if type(key) is not str:
                    raise ValueError('Checkpoint values must be JSON-native: object keys must be strings')
            children=item.values()
        else:
            children=item
        visited+=len(children)
        if visited>maximum:
            from .limits import LimitExceeded
            raise LimitExceeded('checkpoint_max_items',visited,maximum,'Checkpoint JSON-native nesting or item budget exceeded')
        for child in children:
            kind=type(child)
            if kind is float:
                if not finite(child):reject()
            elif kind is dict or kind is list:
                pending.append((child,depth+1))
            elif not (kind is str or kind is int or kind is bool or child is None):
                reject()


_ARRAY_TYPECODES = {'<i8': 'q', '<f8': 'd', '|b1': 'B', '<i4': 'i', '<u1': 'B'}


def _iter_canonical(value, depth=0, block=256):
    """Yield util.canonical(value) text in bounded pieces using the C JSON encoder.

    Dicts are expanded to depth 3 and long lists are emitted in C-encoded blocks,
    so pieces stay small while the concatenation is byte-identical to canonical().
    """
    dumps = json.dumps
    if isinstance(value, dict) and value and depth < 3:
        yield '{'
        for index, key in enumerate(sorted(value)):
            yield (',' if index else '') + dumps(key, ensure_ascii=False) + ':'
            yield from _iter_canonical(value[key], depth + 1, block)
        yield '}'
    elif isinstance(value, list) and len(value) > block:
        yield '['
        for start in range(0, len(value), block):
            piece = dumps(value[start:start + block], sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)[1:-1]
            yield (',' if start else '') + piece
        yield ']'
    else:
        yield dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def stream_digest(value):
    """util.digest(value) without materializing the whole canonical document."""
    checksum = hashlib.sha256()
    for piece in _iter_canonical(value):
        checksum.update(piece.encode('utf-8'))
    return checksum.hexdigest()


def _safe_name(name):
    if not isinstance(name, str) or not name or len(name) > 200 or any(c in name for c in '/\\\0'):
        raise ValueError('Checkpoint array names must be short plain strings')
    return name


def _fsync_directory(path):
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _publish_directory(temporary, directory, overwrite):
    """Atomically move a fully written temporary directory into place."""
    if directory.exists():
        if not overwrite:
            raise ValueError('Checkpoint destination already exists')
        retired = directory.with_name('.' + directory.name + '.retired-' + uuid.uuid4().hex)
        os.rename(directory, retired)
        try:
            os.rename(temporary, directory)
        except BaseException:
            os.rename(retired, directory)
            raise
        shutil.rmtree(retired, ignore_errors=True)
    else:
        os.rename(temporary, directory)
    _fsync_directory(directory.parent)


def _write_manifest(temporary, manifest):
    manifest = {**manifest, 'checksum': digest(manifest)}
    with (temporary / 'manifest.json').open('wb') as stream:
        stream.write(canonical(manifest)); stream.flush(); os.fsync(stream.fileno())
    return manifest


def _read_manifest(directory, expected_format):
    directory = Path(directory)
    try:
        manifest = json.loads((directory / 'manifest.json').read_bytes())
    except (OSError, ValueError) as error:
        raise ValueError('Checkpoint manifest missing or unreadable') from error
    if not isinstance(manifest, dict) or manifest.get('format') != expected_format or manifest.get('schema_version') != 1:
        raise ValueError('Unsupported checkpoint manifest')
    checksum = manifest.pop('checksum', None)
    if checksum != digest(manifest):
        raise ValueError('Checkpoint manifest integrity failure')
    return directory, manifest


def _verified_bytes(directory, entry):
    name = entry.get('file')
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError('Unsafe checkpoint chunk path')
    path = directory / name
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ValueError('Checkpoint chunk missing') from error
    if len(data) != entry.get('bytes') or hashlib.sha256(data).hexdigest() != entry.get('sha256'):
        raise ValueError('Checkpoint chunk integrity failure')
    return data


def write_array_checkpoint(arrays, metadata, directory, *, overwrite=False):
    """Atomically write named arrays as raw little-endian buffers plus a manifest.

    arrays maps names to numpy arrays (any shape; int64/float64/bool/int32/uint8)
    or flat Python lists of ints/floats/bools. metadata must be JSON-native. Each
    buffer has a sha256 in manifest.json and the manifest has its own checksum;
    files are fsynced in a temporary sibling directory that is renamed into place
    only when complete, so readers never observe a partial checkpoint.
    """
    if not isinstance(arrays, dict):
        raise ValueError('arrays must map names to arrays')
    _json_native(metadata)
    directory = Path(directory)
    directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = directory.with_name('.' + directory.name + '.writing-' + uuid.uuid4().hex)
    temporary.mkdir()
    try:
        entries = {}; total = 0
        for index, name in enumerate(sorted(arrays)):
            _safe_name(name)
            value = arrays[name]
            if hasattr(value, 'dtype') and hasattr(value, 'tobytes'):
                import numpy as np
                contiguous = np.ascontiguousarray(value)
                if contiguous.dtype.itemsize > 1:
                    contiguous = contiguous.astype(contiguous.dtype.newbyteorder('<'), copy=False)
                code = contiguous.dtype.str.replace('=', '<')
                if code not in _ARRAY_TYPECODES:
                    raise ValueError(f'Unsupported checkpoint array dtype {contiguous.dtype}')
                shape = list(contiguous.shape); data = memoryview(contiguous).cast('B')
            else:
                if not isinstance(value, (list, tuple)):
                    raise ValueError('Checkpoint arrays must be numpy arrays or flat lists')
                kinds = {type(item) for item in value}
                code = '|b1' if kinds == {bool} else '<f8' if float in kinds and kinds <= {int, float} else '<i8'
                if not kinds <= {int, float, bool} or (code == '<i8' and kinds - {int}):
                    raise ValueError('Checkpoint lists must contain only ints, floats or bools')
                buffer = array.array(_ARRAY_TYPECODES[code], value)
                if sys.byteorder != 'little' and buffer.itemsize > 1: buffer.byteswap()
                shape = [len(value)]; data = memoryview(buffer).cast('B')
            file_name = f'{index:05d}.bin'
            checksum = hashlib.sha256()
            with (temporary / file_name).open('wb') as stream:
                step = 64 * 1024 * 1024
                for offset in range(0, len(data), step):
                    piece = data[offset:offset + step]; checksum.update(piece); stream.write(piece)
                stream.flush(); os.fsync(stream.fileno())
            entries[name] = {'file': file_name, 'dtype': code, 'shape': shape, 'bytes': len(data), 'sha256': checksum.hexdigest()}
            total += len(data)
        manifest = _write_manifest(temporary, {'schema_version': 1, 'format': 'worldmodel-array-checkpoint',
                                               'arrays': entries, 'metadata': metadata, 'bytes': total})
        _fsync_directory(temporary)
        _publish_directory(temporary, directory, overwrite)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
    return {'directory': str(directory), 'bytes': total, 'checksum': manifest['checksum'], 'arrays': len(entries)}


def read_array_checkpoint(directory):
    """Verify every buffer, then return (arrays, metadata); numpy arrays when installed."""
    directory, manifest = _read_manifest(directory, 'worldmodel-array-checkpoint')
    try:
        import numpy as np
    except ImportError:
        np = None
    arrays = {}
    for name, entry in manifest['arrays'].items():
        _safe_name(name)
        code = entry.get('dtype')
        if code not in _ARRAY_TYPECODES or not isinstance(entry.get('shape'), list):
            raise ValueError('Invalid checkpoint array descriptor')
        data = _verified_bytes(directory, entry)
        if np is not None:
            value = np.frombuffer(data, dtype=np.dtype(code)).reshape(entry['shape']).copy()
        else:
            buffer = array.array(_ARRAY_TYPECODES[code]); buffer.frombytes(data)
            if sys.byteorder != 'little' and buffer.itemsize > 1: buffer.byteswap()
            value = [bool(v) for v in buffer] if code == '|b1' else buffer.tolist()
        arrays[name] = value
    return arrays, manifest['metadata']


def write_checkpoint(checkpoint, directory, *, chunk_bytes=None, overwrite=False, limits=None):
    """Chunked form of a JSON checkpoint with identical canonical bytes and checksum.

    Canonical JSON is streamed into zlib-compressed chunks (limit
    checkpoint_chunk_bytes by default); the manifest stores each chunk's sha256,
    the sha256 of the complete canonical document (equal to util.digest of the
    checkpoint) and the envelope checksum. Publication is atomic.
    """
    limits = resolve_limits(limits)
    _json_native(checkpoint, limits=limits)
    chunk_bytes = limits.checkpoint_chunk_bytes if chunk_bytes is None else chunk_bytes
    if type(chunk_bytes) is not int or chunk_bytes < 1024:
        raise ValueError('chunk_bytes must be an integer >= 1024')
    directory = Path(directory)
    directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = directory.with_name('.' + directory.name + '.writing-' + uuid.uuid4().hex)
    temporary.mkdir()
    try:
        chunks = []; whole = hashlib.sha256(); pending = []; pending_size = 0; total = 0

        def flush():
            nonlocal pending, pending_size
            if not pending:
                return
            raw = ''.join(pending).encode('utf-8')
            compressed = zlib.compress(raw, 1)
            name = f'{len(chunks):06d}.jsonz'
            with (temporary / name).open('wb') as stream:
                stream.write(compressed); stream.flush(); os.fsync(stream.fileno())
            chunks.append({'file': name, 'bytes': len(compressed), 'sha256': hashlib.sha256(compressed).hexdigest(), 'raw_bytes': len(raw)})
            pending = []; pending_size = 0

        for piece in _iter_canonical(checkpoint):
            encoded = piece.encode('utf-8'); whole.update(encoded); total += len(encoded)
            pending.append(piece); pending_size += len(encoded)
            if pending_size >= chunk_bytes:
                flush()
        flush()
        manifest = _write_manifest(temporary, {'schema_version': 1, 'format': 'worldmodel-chunked-json-checkpoint',
            'chunks': chunks, 'document_sha256': whole.hexdigest(), 'document_bytes': total,
            'envelope_checksum': checkpoint.get('checksum') if isinstance(checkpoint, dict) else None})
        _fsync_directory(temporary)
        _publish_directory(temporary, directory, overwrite)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
    return {'directory': str(directory), 'document_sha256': manifest['document_sha256'], 'bytes': total,
            'compressed_bytes': sum(c['bytes'] for c in chunks), 'chunks': len(chunks)}


def read_checkpoint(directory, *, limits=None):
    """Verify all chunks and the whole-document hash, then return the checkpoint dict."""
    limits = resolve_limits(limits)
    directory, manifest = _read_manifest(directory, 'worldmodel-chunked-json-checkpoint')
    whole = hashlib.sha256(); parts = []
    for entry in manifest['chunks']:
        raw = zlib.decompress(_verified_bytes(directory, entry))
        if len(raw) != entry.get('raw_bytes'):
            raise ValueError('Checkpoint chunk length mismatch')
        whole.update(raw); parts.append(raw)
    document = b''.join(parts)
    if whole.hexdigest() != manifest['document_sha256'] or len(document) != manifest['document_bytes']:
        raise ValueError('Checkpoint document integrity failure')
    def constant(name):
        raise ValueError('Checkpoint contains non-finite JSON constant ' + name)
    value = json.loads(document, parse_constant=constant)  # json.loads yields only JSON-native values otherwise.
    if isinstance(value, dict) and value.get('checksum') != manifest.get('envelope_checksum'):
        raise ValueError('Checkpoint envelope checksum mismatch')
    return value


class CheckpointEvaluator:
    """Environment evaluator; one new history item advances one observation step.

    reset(seed), checkpoint(format='json'|'chunked', directory=...), restore(JSON-dict or
    chunked checkpoint directory), and publish() are explicit APIs.
    The request's full horizon pins implementation selection and prediction dt;
    observation boundaries never shorten a pending end-of-step prediction.
    """
    # Every new prediction is JSON-validated and budgets bound the trajectory, so
    # Environment need not re-encode the whole growing result on each step.
    bounded_output = True

    def __init__(self, store, graph_ref, request, step_seconds, registry=None,
                 max_total_calls=10000, agent_backend=None):
        _json_native(request);_json_native(graph_ref)
        resolve_limits().integer('materialize_max_calls', max_total_calls, 'Invalid incremental call budget')
        if request.get('interventions'):
            raise ValueError('Environment history owns intervention schedule')
        self.store=store; self.graph_ref=deepcopy(graph_ref)
        self.step_seconds=temporal.seconds(step_seconds,'environment step_seconds')
        self.request,self.start,self.end,self.samples,self.targets=temporal._request(
            {**deepcopy(request),'step_seconds':self.step_seconds})
        self.duration=round((self.end-self.start).total_seconds(),6)
        self.registry=deepcopy(registry) if registry is not None else default_registry()
        self.backend=agent_backend; self.max_total_calls=max_total_calls
        _json_native(self.registry.describe());_json_native(getattr(agent_backend,'identity',None))
        self.bindings,self.keys,self.plan,self.specs=temporal._plan(
            self.registry,self.request,self.targets,self.duration,self.backend)
        self.live=any(b['implementation']['fidelity']=='agent' for b in self.bindings)
        if self.request.get('lifecycle'):
            from .lifecycle import materialize_lifecycle, require_actor_eligible
            for at in (self.request['start'],self.request['end']):
                lifecycle=materialize_lifecycle(self.request['lifecycle'],at,self.request['known_at'])
                for entity in {k[0] for k in self.keys} & set(lifecycle['entities']):
                    require_actor_eligible(lifecycle,entity)
        self.code=capture_code(temporal.PROJECT,'worldmodel.checkpoints:CheckpointEvaluator')
        self.identity=digest({'graph':self.graph_ref,'request':self.request,
            'registry':self.registry.describe(),'code':self.code['files'],
            'backend':getattr(self.backend,'identity',None),'max_total_calls':max_total_calls})
        self.evidence=EvidenceState(store,graph_ref,self.keys)
        self._data=None; self.audit=[]; self.blocked=False; self.attempted_calls=0

    @property
    def total_calls(self):
        return self._data['calls'] if self._data else 0

    def _verify(self):
        self.store.verify(self.graph_ref)
        if any(file_hash(temporal.PROJECT/name)!=checksum for name,checksum in self.code['files'].items()):
            raise ValueError('Code changed during checkpoint session; restart with stable code')

    def reset(self,seed=0):
        if type(seed) is not int:raise ValueError('Seed must be integer')
        self._verify()
        state,reconciliation=self.evidence.select(self.start,instant(self.request['known_at']),
            self.request.get('initial_state',[]),self.request.get('reconciliation','error'))
        for item in state.values():_json_native(item)
        temporal._compatible(state,self.bindings,self.specs)
        snapshots=temporal._snapshots(state,self.targets,self.start,self.request)
        if len(snapshots)*len(self.samples)>self.request['max_points']:
            raise ValueError('Group snapshots exceed max_points budget')
        data={'seed':seed,'state':state,'elapsed':0.0,'history':[],'memories':{},'held':{},
            'next_due':{b['id']:0.0 for b in self.bindings},
            'rngs':{b['id']:random.Random(int(digest([seed,b['id']]),16)) for b in self.bindings},
            'literals':{b['id']:{p:deepcopy(v) for p,v in b['inputs_resolved'].items() if isinstance(v,dict)} for b in self.bindings},
            'snapshots':snapshots,'trace':[],'interventions':[],'reconciliation':reconciliation,'calls':0,'cost':0.0,'work':[]}
        data['work']=self._preflight_work(data,self.duration)
        self._data=data;self.audit=[];self.blocked=False;self.attempted_calls=0
        return self.result()

    def _preflight_work(self,data,end):
        work=[]
        for b in self.bindings:
            impl=b['implementation'];name=b['id']
            calls=max(0,math.ceil(round((end-data['next_due'][name])*1000000)/round(b['cadence_seconds']*1000000)))
            if impl.get('work_estimator'):
                if impl['work_estimator']=='worldmodel.economy_processes:estimate_process_work':
                    from .economy_processes import estimate_process_work
                elif impl['work_estimator']=='worldmodel.fields:estimate_process_work':
                    from functools import partial
                    from .fields import estimate_process_work as field_work
                    estimate_process_work=partial(field_work,parameters=b.get('parameters',{}))
                else:raise ValueError('Unsupported process work estimator')
                item=b['inputs_resolved'][impl['work_input']]
                value=data['state'][item]['value'] if isinstance(item,tuple) else data['literals'][name][impl['work_input']]['value']
                work.append({'binding':name,'unit':impl['work_unit'],**estimate_process_work(value,calls)})
        return work

    def __call__(self,history,seed):
        _json_native(history)
        if not history:return self.reset(seed)
        if self._data is None:raise ValueError('Reset required before incremental evaluation')
        if self.blocked:raise ValueError('Backend transition failed; explicit reset required; inspect audit before retry')
        old=self._data
        if seed!=old['seed'] or len(history)!=len(old['history'])+1 or history[:-1]!=old['history']:
            raise ValueError('Incremental evaluator requires exactly one appended history item and unchanged seed')
        if not isinstance(history[-1],dict) or set(history[-1])!={'inputs'}:
            raise ValueError('History item requires inputs')
        end=round(len(history)*self.step_seconds,6)
        if end>self.duration:raise ValueError('Environment exceeds materialization horizon')
        self._verify()
        # Copy-on-write transition: the committed data is never mutated, so accumulated
        # trace/snapshot/history entries are shared instead of deep-copied every step.
        data=dict(old)
        for key in ('interventions','trace','snapshots','work','reconciliation'):data[key]=list(old[key])
        for key in ('held','next_due'):data[key]=dict(old[key])
        data['literals']=deepcopy(old['literals']);data['memories']=deepcopy(old['memories']);data['rngs']=deepcopy(old['rngs'])
        changes=[{'time':(self.start+timedelta(seconds=old['elapsed'])).isoformat(),**p} for p in history[-1]['inputs']]
        schedule=temporal._interventions({**self.request,'interventions':changes},self.start,self.end,self.bindings,self.specs)
        for change in schedule.get(old['elapsed'],[]):
            data['literals'][change['binding']][change['port']]={'value':deepcopy(change['value']),'unit':change['unit']}
            data['interventions'].append(change)
        due_counts={b['id']:max(0,math.ceil(round((end-data['next_due'][b['id']])*1000000)/round(b['cadence_seconds']*1000000))) for b in self.bindings}
        calls=sum(due_counts.values());cost=sum(due_counts[b['id']]*b['implementation']['cost_per_call'] for b in self.bindings)
        from .environments import BudgetExceeded
        if self.attempted_calls+calls>self.max_total_calls or data['calls']+calls>self.request['max_calls'] or data['cost']+cost>self.request['budget']:
            raise BudgetExceeded('Incremental process budget exhausted before handler calls')
        data['work']+=self._preflight_work(data,end)
        audit_start=len(self.audit)
        try:
            if self.request['mode']=='observed':
                data['state'],decisions=self.evidence.select(self.start+timedelta(seconds=end),instant(self.request['known_at']),policy=self.request.get('reconciliation','error'))
                data['reconciliation']+=decisions;data['elapsed']=end
            else:
                self._advance(data,end)
            data['history']=old['history']+[deepcopy(history[-1])]
            data['snapshots']+=temporal._snapshots(data['state'],self.targets,self.start+timedelta(seconds=end),self.request)
            self._validate_data(data)
            self._verify()
        except Exception:
            if len(self.audit)>audit_start:self.blocked=True
            raise
        self._data=data
        return self.result()

    def _advance(self,data,end):
        while data['elapsed']<end:
            elapsed=data['elapsed'];state=data['state'];pending=[]
            for b in self.bindings:
                name=b['id']
                if data['next_due'][name]>elapsed:continue
                from .process_contracts import require_execution_eligible
                require_execution_eligible(self.request,b['entity_id'],(self.start+timedelta(seconds=elapsed)).isoformat(),min(b['cadence_seconds'],self.duration-elapsed))
                inputs={p:{'value':deepcopy(state[v]['value']),'unit':state[v]['unit']} if isinstance(v,tuple) else deepcopy(data['literals'][name][p]) for p,v in b['inputs_resolved'].items()}
                memory=deepcopy(data['memories'].get(b['entity_id'],{}))
                context={'dt_seconds':min(b['cadence_seconds'],self.duration-elapsed),
                    'time':(self.start+timedelta(seconds=elapsed)).isoformat(),'known_at':self.request['known_at'],'rng':data['rngs'][name],
                    'state':{p:deepcopy(state[k]['value']) for p,k in b['outputs_resolved'].items()},
                    'entity_id':b['entity_id'],'memory':memory,'agent_backend':self.backend,
                    'budget':self.request['budget'],'remaining_budget':self.request['budget']-data['cost']-sum(x[0]['implementation']['cost_per_call'] for x in pending)}
                entry=None
                if b['implementation']['fidelity']=='agent':
                    entry={'sequence':len(self.audit),'binding':name,'time':context['time'],
                           'inputs_digest':digest(inputs),'status':'attempted'}
                    self.audit.append(entry)
                self.attempted_calls+=1
                prediction=self.registry.predict(b['implementation']['id'],inputs,deepcopy(b.get('parameters',{})),context)
                _json_native(prediction)
                if entry is not None:entry.update(status='returned',prediction_digest=digest(prediction))
                pending.append((b,inputs,memory,prediction))
            for b,inputs,memory,prediction in pending:
                name=b['id'];data['held'][name]=prediction['pressures']
                if b['implementation']['fidelity']=='agent':data['memories'][b['entity_id']]=deepcopy(prediction.get('memory',memory))
                data['next_due'][name]=round(min(self.duration,elapsed+b['cadence_seconds']),6)
                data['calls']+=1;data['cost']+=b['implementation']['cost_per_call']
                data['trace'].append({'time':(self.start+timedelta(seconds=elapsed)).isoformat(),
                    'binding':name,'implementation':b['implementation']['id'],'inputs':inputs,
                    'prediction':prediction,'memory_before':memory,'memory_after':deepcopy(data['memories'].get(b['entity_id'],{}))})
            boundary=min([end]+list(data['next_due'].values()))
            dt=boundary-elapsed
            if dt<=0:raise ValueError('Temporal scheduling made no progress')
            influences={}
            for b in self.bindings:
                if b['implementation'].get('output_timing')=='end_of_step' and boundary<data['next_due'][b['id']]:continue
                for pressure in data['held'].get(b['id'],[]):
                    influences.setdefault(b['outputs_resolved'][pressure['port']],[]).append(pressure)
            new=deepcopy(state);provenance=collect_evidence(state)
            for item in new.values():
                if item['origin']=='observed':item['origin']='persistence_assumption'
            for key,pressures in influences.items():
                new[key]['value']=combine_pressures(state[key]['value'],pressures,dt,value_type=state[key]['type'])
                new[key].update(origin='forecast',evidence=provenance)
            for b in self.bindings:
                for port,key in b['outputs_resolved'].items():
                    spec=self.specs[b['process_id']]['outputs'][port];value=new[key]['value']
                    if type(value) in (int,float) and not spec.get('minimum',-math.inf)<=value<=spec.get('maximum',math.inf):
                        raise ValueError('Forecast violates declared output bounds')
            from .process_contracts import audit_conserved_outputs
            for b in self.bindings:
                audit_conserved_outputs(self.specs[b['process_id']],{p:state[k]['value'] for p,k in b['outputs_resolved'].items()},{p:new[k]['value'] for p,k in b['outputs_resolved'].items()})
            data['state']=new;data['elapsed']=boundary

    def result(self):
        if self._data is None:raise ValueError('Reset required')
        d=self._data
        return deepcopy({'schema_version':1,'graph':self.graph_ref,'request':{**self.request,'seed':d['seed'],
            'end':(self.start+timedelta(seconds=d['elapsed'])).isoformat()},'plan':{**self.plan,'work':d['work']},
            'snapshots':d['snapshots'],'trace':d['trace'],'input_interventions':d['interventions'],
            'reconciliation':d['reconciliation'],'execution':{'calls':d['calls'],'cost':d['cost'],'agent_memories':d['memories']},
            'backend_identity':getattr(self.backend,'identity',None),'backend_audit':self.audit,
            'limitations':['Process predictions are illustrative and uncalibrated.',
                'Forecast inputs persist as explicit assumptions; temporal sampling adds no observational precision.',
                'Backend side effects cannot be rolled back; failed live transitions block retry and live checkpoint restore is unsupported.',
                'Checkpoint checksums detect corruption, not malicious modification.']})

    def _validate_data(self,data):
        required={'seed','state','elapsed','history','memories','held','next_due','rngs',
                  'literals','snapshots','trace','interventions','reconciliation','calls','cost','work'}
        if set(data)!=required or type(data['seed']) is not int:
            raise ValueError('Checkpoint schema mismatch')
        if not isinstance(data['history'],list) or any(not isinstance(h,dict) or set(h)!={'inputs'} or not isinstance(h['inputs'],list) for h in data['history']):
            raise ValueError('Checkpoint history invalid')
        if not isinstance(data['trace'],list) or len(data['trace'])!=data['calls']:
            raise ValueError('Checkpoint trace accounting mismatch')
        by_name={b['id']:b for b in self.bindings}
        if any(t.get('binding') not in by_name or t.get('implementation')!=by_name[t['binding']]['implementation']['id'] for t in data['trace']):
            raise ValueError('Checkpoint trace binding mismatch')
        expected_cost=sum(by_name[t['binding']]['implementation']['cost_per_call'] for t in data['trace'])
        if not math.isclose(data['cost'],expected_cost,rel_tol=1e-12,abs_tol=1e-12):
            raise ValueError('Checkpoint cost accounting mismatch')
        for b in self.bindings:
            literals=data['literals'][b['id']]
            if set(literals)!={p for p,v in b['inputs_resolved'].items() if isinstance(v,dict)}:
                raise ValueError('Checkpoint literal ports mismatch')
            for port,item in literals.items():
                spec=self.specs[b['process_id']]['inputs'][port]
                if not isinstance(item,dict) or set(item)!={'value','unit'} or item['unit']!=spec.get('unit'):
                    raise ValueError('Checkpoint literal unit mismatch')
                try:_typed(item['value'],'array' if spec.get('temporal') else spec['type'])
                except ValueError as exc:raise ValueError('Checkpoint literal type mismatch') from exc
        if set(data['state'])!=set(self.keys):raise ValueError('Checkpoint state keys mismatch')
        temporal._compatible(data['state'],self.bindings,self.specs)
        for item in data['state'].values():_typed(item['value'],item['type'])
        if data['elapsed']!=round(len(data['history'])*self.step_seconds,6) or not 0<=data['elapsed']<=self.duration:
            raise ValueError('Checkpoint elapsed/history mismatch')
        names={b['id'] for b in self.bindings}
        if set(data['next_due'])!=names or set(data['rngs'])!=names or set(data['literals'])!=names:
            raise ValueError('Checkpoint scheduler keys mismatch')
        if not set(data['held'])<=names:raise ValueError('Checkpoint held pressure mismatch')
        last_predictions={};scheduled={name:0.0 for name in names}
        for trace in data['trace']:
            binding=by_name[trace['binding']];name=binding['id']
            at=round((instant(trace['time'])-self.start).total_seconds(),6)
            if at!=scheduled[name] or not 0<=at<data['elapsed']:
                raise ValueError('Checkpoint trace scheduling mismatch')
            scheduled[name]=round(min(self.duration,at+binding['cadence_seconds']),6)
            prediction=trace['prediction']
            for field,kind in (('pressures',list),('events',list),('memory',dict),('diagnostics',dict)):
                if type(prediction.get(field)) is not kind:raise ValueError('Checkpoint prediction schema mismatch')
            last_predictions[name]=prediction['pressures']
        if data['next_due']!=scheduled or data['held']!=last_predictions:
            raise ValueError('Checkpoint held pressures or next-due schedule disagree with trace')
        for name,pressures in data['held'].items():
            binding=by_name[name];output_specs=self.specs[binding['process_id']]['outputs']
            for pressure in pressures:
                if not isinstance(pressure,dict) or pressure.get('port') not in output_specs:
                    raise ValueError('Checkpoint held pressure output port mismatch')
                expected=output_specs[pressure['port']]
                _pressure(pressure,expected['type'])
                if pressure['unit']!=expected.get('unit'):
                    raise ValueError('Checkpoint held pressure output unit mismatch')
                if binding['implementation'].get('output_timing')=='end_of_step' and pressure['mode']!='set':
                    raise ValueError('Checkpoint end_of_step pressure must use set mode')
                state_value=data['state'][binding['outputs_resolved'][pressure['port']]]['value']
                if expected['type']=='vector' and len(pressure['value'])!=len(state_value):
                    raise ValueError('Checkpoint held pressure vector dimension mismatch')
        if any(not data['elapsed']<=v<=self.duration for v in data['next_due'].values()):raise ValueError('Checkpoint due time invalid')
        if type(data['calls']) is not int or not 0<=data['calls']<=self.request['max_calls'] or not 0<=data['cost']<=self.request['budget']:
            raise ValueError('Checkpoint budget invalid')

    def checkpoint(self, *, format='json', directory=None, chunk_bytes=None, overwrite=False):
        """JSON checkpoint dict (default) or an atomic chunked checkpoint directory.

        Both forms carry the same identity and checksum; format='chunked' streams
        canonical JSON into verified compressed chunks and returns write metadata.
        """
        if self._data is None:raise ValueError('Reset required')
        if format not in ('json','chunked') or (format=='chunked')!=(directory is not None):
            raise ValueError("format='chunked' requires directory; format='json' does not accept one")
        payload=deepcopy(self._data)
        payload['state']=[{'key':list(k),'item':v} for k,v in sorted(payload['state'].items())]
        payload['rngs']={k:[v.getstate()[0],list(v.getstate()[1]),v.getstate()[2]] for k,v in payload['rngs'].items()}
        _json_native(payload)
        checkpoint={'schema_version':1,'identity':self.identity,'payload':payload}
        if format=='chunked':
            checkpoint['checksum']=stream_digest(checkpoint)
            info=write_checkpoint(checkpoint,directory,chunk_bytes=chunk_bytes,overwrite=overwrite)
            return {**info,'format':'chunked','identity':self.identity,'checksum':checkpoint['checksum']}
        # canonical JSON normalizes RNG tuples without unsafe pickle deserialization.
        checkpoint=json.loads(canonical(checkpoint))
        return {**checkpoint,'checksum':digest(checkpoint)}

    def restore(self,checkpoint,*,journal_capability=None):
        chunked=isinstance(checkpoint,(str,os.PathLike))
        if chunked:checkpoint=read_checkpoint(checkpoint)
        _json_native(checkpoint)
        authorized=False
        if self.live and journal_capability is not None:
            from .journaled_environment import JournaledBackend
            if type(self.backend) is not JournaledBackend:raise ValueError('Journal capability requires a journaled backend')
            authorized=self.backend.authorize_restore(journal_capability,self)
        if self.live and not authorized:raise ValueError('Live backend checkpoint restore cannot guarantee exactly-once external effects')
        if self.blocked and not authorized:raise ValueError('Blocked backend session cannot restore')
        if not chunked:resolve_limits().check('checkpoint_max_json_bytes',len(canonical(checkpoint)),'Checkpoint exceeds JSON size limit; use a chunked checkpoint')
        candidate=checkpoint if chunked else deepcopy(checkpoint)
        checksum=candidate.pop('checksum',None) if isinstance(candidate,dict) else None
        if not isinstance(candidate,dict) or set(candidate)!={'schema_version','identity','payload'} or candidate['schema_version']!=1 or candidate['identity']!=self.identity or stream_digest(candidate)!=checksum:
            raise ValueError('Checkpoint integrity or identity mismatch')
        try:
            data=candidate['payload']
            rows=data['state'];data['state']={tuple(row['key']):row['item'] for row in rows}
            if len(data['state'])!=len(rows):raise ValueError('Duplicate checkpoint state keys')
            rngs={}
            for name,value in data['rngs'].items():
                rng=random.Random();rng.setstate((value[0],tuple(value[1]),value[2]));rngs[name]=rng
            data['rngs']=rngs
            self._validate_data(data)
        except (KeyError,TypeError,IndexError,OverflowError) as exc:
            raise ValueError('Invalid checkpoint structure') from exc
        self._verify();self._data=data;self.attempted_calls=max(self.attempted_calls,data['calls'])
        if authorized:self.blocked=False
        return self.result()

    @contextmanager
    def transaction(self):
        """Also roll back when an Environment rejects outputs or reward selectors."""
        before=self._data;audit_start=len(self.audit)  # Transitions never mutate committed data.
        try:
            yield
        except Exception:
            self._data=before
            if len(self.audit)>audit_start:self.blocked=True
            raise

    def publish(self):
        """Publish the committed trajectory with source and graph provenance."""
        self._verify()
        result=self.result()
        return temporal._publish(self.store,result,self.code,self.registry.describe())
