"""Explicit, bounded contracts for coupling, information and aggregation.

Size ceilings are named limits (contracts_max_rows, contracts_max_observations,
contracts_max_transfers, contracts_max_batch_transfers, contracts_max_accounts,
contracts_max_interfaces, contracts_max_payload_bytes, agent_max_payload_bytes);
raise them per call with ``limits={...}`` or through worldmodel.limits.
"""
from copy import deepcopy
from datetime import timedelta
from fractions import Fraction
import hashlib
import math
from .limits import resolve_limits
from .model import instant
from .util import canonical, digest


def finite(value):
    if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('Finite numeric quantity required')
    return value


def aggregate_quantity(rows, *, kind, unit, limits=None):
    """Extensive sum or measure-weighted intensive mean, including passive vectors."""
    limits=resolve_limits(limits)
    if kind not in ('extensive','intensive') or not isinstance(rows,list) or not rows:
        raise ValueError('Bounded numeric rows and extensive/intensive kind required')
    limits.check('contracts_max_rows',len(rows),'Bounded numeric rows and extensive/intensive kind required')
    vectors=isinstance(rows[0]['value'],list);width=len(rows[0]['value']) if vectors else 1
    if not 1<=width<=32:raise ValueError('Component bound exceeded')
    values=[];measures=[]
    for row in rows:
        if row['unit']!=unit or isinstance(row['value'],list)!=vectors:raise ValueError('Aggregation unit/shape mismatch')
        value=row['value'] if vectors else [row['value']]
        if len(value)!=width:raise ValueError('Component mismatch')
        if kind=='intensive' and 'measure' not in row:raise ValueError('Intensive aggregation requires explicit measures')
        measure=finite(row.get('measure',1))
        if measure<=0:raise ValueError('Positive measure required')
        measures.append(measure);values.append([finite(v) for v in value])
    try:
        total=finite(math.fsum(measures))
        summed=[finite(math.fsum(v[i]*(m if kind=='intensive' else 1) for v,m in zip(values,measures))) for i in range(width)]
        output=[finite(v/total) for v in summed] if kind=='intensive' else summed
    except OverflowError as error:raise ValueError('Aggregation exceeds finite range') from error
    return {'value':output if vectors else output[0],'unit':unit,'kind':kind,'measure':total,
            'information_loss':['Within-group distribution and member identities are not recoverable from this aggregate.']}


def select_timed_input(observations, contract, *, at, known_at, limits=None):
    """Latest eligible value: information_time+lag <= min(at,known_at), valid_time <= at.

    Missing policy error or omit; no implicit zero or carry beyond maximum age.
    Equal valid/information times with conflicting values require reconciliation.
    """
    limits=resolve_limits(limits)
    if not isinstance(observations,list):raise ValueError('Observation budget exceeded')
    limits.check('contracts_max_observations',len(observations),'Observation budget exceeded')
    if set(contract)!={'unit','lag_seconds','max_age_seconds','missing'} or contract['missing'] not in ('error','omit'):
        raise ValueError('Explicit temporal unit/lag/age/missing contract required')
    lag=finite(contract['lag_seconds']);age=finite(contract['max_age_seconds'])
    if lag<0 or age<0:raise ValueError('Lag and maximum age must be nonnegative')
    at=instant(at);known=instant(known_at);eligible=[]
    for item in observations:
        if item.get('unit')!=contract['unit']:raise ValueError('Timed input unit mismatch')
        info,valid=instant(item['information_time']),instant(item['valid_time'])
        if (min(at,known)-info).total_seconds()>=lag and valid<=at and (at-valid).total_seconds()<=age:
            canonical(item['value']);eligible.append((valid,info,item))
    if not eligible:
        if contract['missing']=='omit':return None
        raise ValueError('Required observation unavailable under information/valid time, lag or age contract')
    latest=max((v,i) for v,i,_ in eligible);winners=[r for v,i,r in eligible if (v,i)==latest]
    if len({digest(r['value']) for r in winners})!=1:raise ValueError('Conflicting timed observations require reconciliation')
    return deepcopy(winners[0])


class CouplingLedger:
    """Single authoritative conservative ledger; batches apply atomically once.

    Every interface names one quantity and allowed source/target accounts. Values
    are nonnegative; integer quantities use exact integer arithmetic (e.g. cents).
    Floating quantities use relative conservation tolerance 1e-12.

    A batch copies only the accounts it touches and updates exact running totals
    (integers, or Fractions of floats for real quantities, so reported totals equal
    math.fsum over all balances). Batch cost is O(batch), not O(accounts + receipts).
    """
    def __init__(self, quantities, balances, interfaces, *, max_transfers=10000, limits=None):
        self.limits=limits=resolve_limits(limits)
        if type(max_transfers) is not int or max_transfers<1:raise ValueError('Transfer budget invalid')
        limits.check('contracts_max_transfers',max_transfers,'Transfer budget invalid')
        if not quantities or not balances:raise ValueError('Coupling schema budget exceeded')
        limits.check('contracts_max_interfaces',len(quantities),'Coupling schema budget exceeded: quantities')
        limits.check('contracts_max_accounts',len(balances),'Coupling schema budget exceeded: accounts')
        limits.check('contracts_max_interfaces',len(interfaces),'Coupling schema budget exceeded: interfaces')
        limits.check('contracts_max_payload_bytes',len(canonical([quantities,balances,interfaces])),'Coupling schema budget exceeded: payload bytes')
        self.quantities=deepcopy(quantities);self.balances=deepcopy(balances);self.interfaces=deepcopy(interfaces)
        self.max_transfers=max_transfers;self.receipts=[];self.seen=set()
        for q,d in quantities.items():
            if set(d)!={'unit','integer'} or not isinstance(d['unit'],str) or type(d['integer']) is not bool:raise ValueError('Quantity requires unit and integer declaration')
        for account,values in balances.items():
            if not isinstance(account,str) or set(values)!=set(quantities):raise ValueError('Every account must declare every quantity')
            for q,value in values.items():self._amount(q,value)
        self._endpoints={}
        for name,interface in interfaces.items():
            if set(interface)!={'quantity','sources','targets'} or interface['quantity'] not in quantities:raise ValueError('Invalid coupling interface')
            for side in ('sources','targets'):
                if not isinstance(interface[side],list) or not interface[side] or any(a not in balances for a in interface[side]):raise ValueError('Interface endpoints must be declared accounts')
            self._endpoints[name]=(frozenset(interface['sources']),frozenset(interface['targets']))
        self.initial=self._totals(self.balances)
        self._exact={q:(sum(v[q] for v in self.balances.values()) if d['integer'] else sum((Fraction(v[q]) for v in self.balances.values()),Fraction(0)))
                     for q,d in self.quantities.items()}
        self._receipt_hash=hashlib.sha256(b'[')

    def _amount(self,q,value):
        finite(value)
        if value<0 or (self.quantities[q]['integer'] and type(value) is not int):raise ValueError('Quantity requires nonnegative values in declared integer/real type')

    def _totals(self,balances):
        return {q:finite(sum(v[q] for v in balances.values()) if d['integer'] else math.fsum(v[q] for v in balances.values())) for q,d in self.quantities.items()}

    def apply(self,transfers):
        limits=self.limits
        if not isinstance(transfers,list):raise ValueError('Transfer work/payload budget exceeded')
        limits.check('contracts_max_batch_transfers',len(transfers),'Transfer work/payload budget exceeded: batch transfers')
        if len(self.receipts)+len(transfers)>self.max_transfers:raise ValueError('Transfer work/payload budget exceeded')
        limits.check('contracts_max_payload_bytes',len(canonical(transfers)),'Transfer work/payload budget exceeded: payload bytes')
        candidate={};batch_seen=set();receipts=[];exact=dict(self._exact)
        for t in transfers:
            if set(t)!={'id','interface','source','target','quantity','unit','amount'} or not isinstance(t['id'],str) or not t['id']:raise ValueError('Explicit transfer identity and endpoints required')
            if t['id'] in batch_seen or t['id'] in self.seen:raise ValueError('Duplicate transfer application rejected')
            batch_seen.add(t['id']);interface=self.interfaces.get(t['interface'])
            q=t['quantity']
            if not interface or q!=interface['quantity'] or t['unit']!=self.quantities[q]['unit'] or t['source'] not in self._endpoints[t['interface']][0] or t['target'] not in self._endpoints[t['interface']][1] or t['source']==t['target']:raise ValueError('Transfer violates declared interface/unit/endpoints')
            self._amount(q,t['amount'])
            for account in (t['source'],t['target']):
                if account not in candidate:candidate[account]=dict(self.balances[account])
            source,target=candidate[t['source']],candidate[t['target']]
            if source[q]<t['amount']:raise ValueError('Transfer exceeds available balance')
            before=(source[q],target[q])
            source[q]-=t['amount'];target[q]+=t['amount']
            self._amount(q,target[q])
            if not self.quantities[q]['integer']:
                exact[q]+=Fraction(source[q])+Fraction(target[q])-Fraction(before[0])-Fraction(before[1])
            receipts.append(deepcopy(t))
        totals={}
        for q,d in self.quantities.items():
            try:totals[q]=finite(exact[q] if d['integer'] else float(exact[q]))
            except OverflowError as error:raise ValueError('Finite numeric quantity required') from error
            if (totals[q]!=self.initial[q] if d['integer'] else not math.isclose(totals[q],self.initial[q],rel_tol=1e-12,abs_tol=1e-12)):raise ValueError('Coupling conservation failure')
        encoded=[canonical(r) for r in receipts]
        self.balances.update(candidate);self.seen.update(batch_seen);self._exact=exact
        # Running SHA-256 of canonical(self.receipts): '[' + ','.join(items) + ']'.
        # Receipts are append-only; external mutation is unsupported.
        for item,receipt in zip(encoded,receipts):
            if self.receipts:self._receipt_hash.update(b',')
            self._receipt_hash.update(item);self.receipts.append(receipt)
        return {'receipts':receipts,'totals':totals,'receipt_hash':self._receipt_digest()}

    def _receipt_digest(self):
        final=self._receipt_hash.copy();final.update(b']')
        return final.hexdigest()

    def snapshot(self):
        return deepcopy({'quantities':self.quantities,'balances':self.balances,'interfaces':self.interfaces,
                         'receipts':self.receipts,'initial_totals':self.initial,'totals':self._totals(self.balances)})


def require_execution_eligible(request, entity, at, duration_seconds):
    """Reject actor predictions crossing a known inactive interval; never infer support=actor."""
    if not request.get('lifecycle'):return
    from .lifecycle import materialize_lifecycle, require_actor_eligible
    start=instant(at);end=start+timedelta(seconds=duration_seconds)
    times={start,max(start,end-timedelta(microseconds=1))}
    # Actor status is monotone not_started -> active -> ended. Reconstruction
    # validates all transitions, so both interval endpoints cover interior death
    # or merger without replaying once for every unrelated event.
    for time in sorted(times):
        lifecycle=materialize_lifecycle(request['lifecycle'],time.isoformat(),request['known_at'])
        if entity in lifecycle['entities']:require_actor_eligible(lifecycle,entity)


def validate_conserved_outputs(spec):
    declarations=spec.get('conserved_quantities',[])
    if not isinstance(declarations,list) or len(declarations)>100:raise ValueError('Conserved declarations must be bounded list')
    used=set()
    for d in declarations:
        if set(d)!={'id','unit','ports'} or not isinstance(d['ports'],list) or len(d['ports'])<2 or len(set(d['ports']))!=len(d['ports']):raise ValueError('Conserved quantity needs distinct numeric extensive output ports')
        for name in d['ports']:
            port=spec['outputs'].get(name)
            if name in used or not port or port['type']!='number' or port.get('unit')!=d['unit']:raise ValueError('Conserved output unit/type/overlap mismatch')
            used.add(name)


def audit_conserved_outputs(spec, old, new):
    for declaration in spec.get('conserved_quantities',[]):
        before=math.fsum(old[p] for p in declaration['ports']);after=math.fsum(new[p] for p in declaration['ports'])
        if not math.isfinite(after) or not math.isclose(before,after,rel_tol=1e-12,abs_tol=1e-12):raise ValueError('Declared conserved process output changed: '+declaration['id'])


class ContractAgentBackend:
    """Bind explicit model/config/prompt/tool policy before a journaled invocation.

    Wrap this object in JournaledBackend for retryable external execution. This
    adapter does not invoke tools, retry providers, or certify provider internals.
    """
    def __init__(self, provider, *, model, configuration, prompt, tool_policy, limits=None):
        self.limits=limits=resolve_limits(limits)
        if not callable(getattr(provider,'predict',None)) or not isinstance(getattr(provider,'identity',None),dict):raise ValueError('Provider requires explicit identity and predict')
        if not isinstance(model,str) or not model or not isinstance(prompt,str) or not prompt or not isinstance(configuration,dict) or not isinstance(tool_policy,dict):raise ValueError('Explicit model, configuration, prompt and tool policy required')
        self._contract=deepcopy({'model':model,'configuration':configuration,'prompt':prompt,'tool_policy':tool_policy})
        limits.check('agent_max_payload_bytes',len(canonical([self._contract,provider.identity])),'Agent configuration bound exceeded')
        self.provider=provider
        self.identity={'provider':deepcopy(provider.identity),'execution_contract':deepcopy(self._contract)}
        self.supports_idempotency=getattr(provider,'supports_idempotency',False) is True

    def predict(self, request, *, idempotency_key=None):
        if not isinstance(request,dict) or 'execution_contract' in request:raise ValueError('Provider request cannot override execution contract')
        request={**deepcopy(request),'execution_contract':deepcopy(self._contract)}
        self.limits.check('agent_max_payload_bytes',len(canonical(request)),'Agent invocation bound exceeded')
        result=self.provider.predict(request,idempotency_key=idempotency_key) if self.supports_idempotency else self.provider.predict(request)
        self.limits.check('agent_max_payload_bytes',len(canonical(result)),'Agent output bound exceeded')
        return deepcopy(result)
