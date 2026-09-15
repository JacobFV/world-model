"""Durable episode transitions and explicit provider-effect journaling.

One committed action outcome and checkpoint form a SQLite transaction. Failed
attempts remain charged. Explicit resume may supersede a crashed numerical writer;
external effects must be resolved before recovery. This is local coordination.
"""
from copy import deepcopy
import math
import tempfile
from pathlib import Path

from .checkpoints import CheckpointEvaluator, _json_native
from .execution_journal import ExecutionJournal, EffectUnresolved, _name, _integer
from .util import canonical, digest, read_json


class JournaledBackend:
    """Wrap an explicitly configured provider; never automatically retry an effect."""
    def __init__(self,provider,journal,session):
        if not isinstance(journal,ExecutionJournal) or not isinstance(getattr(provider,'identity',None),dict) or not callable(getattr(provider,'predict',None)):
            raise ValueError('Journaled backend requires provider identity and predict')
        _name(session);self.provider=provider;self.journal=journal;self.session=session
        self.identity={'provider':deepcopy(provider.identity),'journaled_session':session,
                       'idempotency_supported':getattr(provider,'supports_idempotency',False) is True}
        _json_native(self.identity);self.prefix='provider:'+digest(session)[:20]+':'
        self._action=None;self._counter=0;self._owner=None;self._claim=None

    def begin_action(self,action_id):
        self._action=action_id;self._counter=0
        self._claim=None

    def _begin_claim(self,action):
        self.begin_action(action['id']);self._claim=deepcopy(action)

    def _check_claim(self):
        if self._owner is None:return # Standalone explicitly journaled provider.
        _,current=self._owner._action(self._action)
        latest=self.journal.db.execute('SELECT MAX(sequence) FROM checkpoints WHERE session=?',
                                       (self._owner.stream,)).fetchone()[0]
        if not self._claim or current!=self._claim or current['status']!='pending' or latest!=current['before_sequence']:
            raise EffectUnresolved('Provider action was superseded; stale writer cannot reserve an effect')

    def unresolved(self):
        for row in self.journal.db.execute('SELECT * FROM effects WHERE key LIKE ?',(self.prefix+'%',)):
            if self.journal._decode(row)['status']!='completed':return True
        return False

    def authorize_restore(self,capability,evaluator):
        if self._owner is None or capability is not self._owner._capability or evaluator is not self._owner.evaluate:
            raise ValueError('Journal restore capability/session mismatch')
        if self.unresolved():raise EffectUnresolved('Provider effects require explicit reconciliation before restore')
        return True

    def predict(self,request):
        if self._action is None:raise ValueError('Journaled provider requires an active episode action')
        key=self.prefix+digest([self._action,self._counter]);self._counter+=1
        with self.journal._transaction():
            self._check_claim()
            effect=self.journal._begin_effect(key,{'provider':self.identity,'request':request})
        if not effect['execute']:return deepcopy(effect['result'])
        try:
            response=(self.provider.predict(deepcopy(request),idempotency_key=key)
                      if self.identity['idempotency_supported'] else self.provider.predict(deepcopy(request)))
            _json_native(response)
            self.journal.complete_effect(key,response,provider_receipt={'idempotency_key':key,'identity':self.provider.identity})
            return response
        except BaseException:
            try:self.journal.mark_uncertain(key,'Provider invocation or completion persistence failed')
            except Exception:pass # Durable pending still prohibits a retry.
            raise


class JournaledEnvironment:
    def __init__(self,env,journal,session,*,seed=0,resume=False,max_process_calls=None):
        if not isinstance(env.evaluate,CheckpointEvaluator) or not isinstance(journal,ExecutionJournal):
            raise ValueError('Journaled episodes require CheckpointEvaluator and ExecutionJournal')
        _name(session)
        if len(session)>100 or type(seed) is not int:raise ValueError('Bounded session ID and integer seed required')
        self.env=env;self.evaluate=env.evaluate;self.journal=journal;self.session=session;self.seed=seed
        self.spec=env.spec;self.stream='episode:'+session;self.quota_name=self.stream;self._capability=object();self._usable=True
        self.limit=self.evaluate.max_total_calls if max_process_calls is None else _integer(max_process_calls,100000,zero=True)
        self.binding={'evaluator_identity':self.evaluate.identity,'graph':self.evaluate.graph_ref,
                      'request':self.evaluate.request,'environment':self.spec,'seed':seed,'quota':self.limit}
        _json_native(self.binding);self.identity=digest(self.binding)
        if self.evaluate.live:
            backend=self.evaluate.backend
            if not isinstance(backend,JournaledBackend) or backend.journal is not journal or backend.session!=session:
                raise ValueError('Live journal episode requires matching JournaledBackend')
            backend._owner=self
        journal.db.execute('''CREATE TABLE IF NOT EXISTS episode_actions(
            session TEXT NOT NULL,action_id TEXT NOT NULL,payload TEXT NOT NULL,checksum TEXT NOT NULL,bytes INTEGER NOT NULL,
            PRIMARY KEY(session,action_id))''')
        prior=journal.db.execute('SELECT * FROM entries WHERE stream=? AND key=?',(self.stream,'binding')).fetchone()
        if prior and digest(journal._decode(prior))!=self.identity:raise ValueError('Journal session configuration mismatch')
        if prior and not resume:raise ValueError('Session exists; explicit resume required')
        if not prior and resume:raise ValueError('Cannot resume missing session')
        journal.configure_quota(self.quota_name,self.limit)
        journal._encode(self.binding)
        with journal._transaction():journal._append(self.stream,self.binding,'binding')
        self.sequence=journal.db.execute('SELECT COALESCE(MAX(sequence),0) FROM checkpoints WHERE session=?',(self.stream,)).fetchone()[0]
        if self.sequence:
            self._restore(journal.load_checkpoint(self.stream))
            with journal._transaction():
                # Fence takeover against effects reserved since restore checked.
                if self.evaluate.live and self.evaluate.backend.unresolved():
                    raise EffectUnresolved('Provider effects require reconciliation before takeover')
                for row in journal.db.execute('SELECT * FROM episode_actions WHERE session=?',(session,)).fetchall():
                    action=journal._decode(row,checkpoint=True)
                    if action['status']=='pending':
                        action['status']='failed';action['failure']='Explicit resume superseded pending attempt'
                        self._write_action(action,row)
        else:
            self.reset_outcome=env.reset(seed=seed);self.last_outcome=self.reset_outcome
            saved=journal.save_checkpoint(self.stream,self._checkpoint(),expected_sequence=0)
            self.sequence=saved['sequence']

    @property
    def history(self):return self.env.history
    @property
    def materialization(self):return self.env.materialization

    def reset(self,seed=None):
        if seed is not None and seed!=self.seed:raise ValueError('Journal session seed cannot change')
        if len(self.last_outcome)==2:return deepcopy(self.last_outcome)
        observation,_,terminated,truncated,info=self.last_outcome
        return deepcopy(observation),{**deepcopy(info),'terminated':terminated,'truncated':truncated}

    def _checkpoint(self):
        value={'schema_version':1,'identity':self.identity,'payload':{
            'evaluator':self.evaluate.checkpoint(),'environment':{'history':self.env.history,
                'evaluations':self.env._evaluations,'done':self.env._done,'seed':self.seed},
            'reset_outcome':list(self.reset_outcome),'last_outcome':list(self.last_outcome)}}
        return {**value,'checksum':digest(value)}

    def _restore(self,checkpoint):
        self.journal._checkpoint(checkpoint)
        if checkpoint['identity']!=self.identity:raise ValueError('Episode checkpoint binding mismatch')
        payload=checkpoint['payload']
        if set(payload)!={'evaluator','environment','reset_outcome','last_outcome'}:raise ValueError('Malformed stored episode state')
        state=payload['environment']
        if set(state)!={'history','evaluations','done','seed'} or type(state['done']) is not bool or state['seed']!=self.seed:
            raise ValueError('Malformed stored environment state')
        _integer(state['evaluations'],self.spec['max_evaluations'])
        if type(state['history']) is not list or state['evaluations']<len(state['history'])+1:
            raise ValueError('Stored evaluation count/history mismatch')
        if state['history']!=payload['evaluator']['payload']['history'] or len(state['history'])>self.spec['max_steps']:
            raise ValueError('Stored environment history mismatch')
        if type(payload['reset_outcome']) is not list or type(payload['last_outcome']) is not list or len(payload['reset_outcome'])!=2 or len(payload['last_outcome']) not in (2,5):
            raise ValueError('Invalid stored outcome')
        with self.evaluate.transaction():
            if self.evaluate.live:self.evaluate.restore(payload['evaluator'],journal_capability=self._capability)
            else:self.evaluate.restore(payload['evaluator'])
            result=self.evaluate.result();self.env._outputs(result);self.env._reward(result,result)
            expected=self.env._terminated(result) or len(state['history'])>=self.spec['max_steps'] or state['evaluations']>=self.spec['max_evaluations']
            if state['done']!=expected and not (len(payload['last_outcome'])==5 and payload['last_outcome'][3] is True):
                raise ValueError('Stored termination state mismatch')
            last=payload['last_outcome']
            if canonical(last[0])!=canonical(self.env._outputs(result)) or type(last[-1]) is not dict or last[-1].get('step')!=len(state['history']):
                raise ValueError('Stored outcome does not match checkpoint observations/history')
            first_time=result['snapshots'][0]['time'];initial={'snapshots':[r for r in result['snapshots'] if r['time']==first_time]}
            initial_info={'step':0,'terminated':self.env._terminated(initial),'truncated':self.spec['max_evaluations']==1,'reward_unit':'normalized_score'}
            if canonical(payload['reset_outcome'])!=canonical([self.env._outputs(initial),initial_info]):
                raise ValueError('Stored reset outcome mismatch')
            if len(last)==5:
                if type(last[2]) is not bool or type(last[3]) is not bool or state['done']!=(last[2] or last[3]):
                    raise ValueError('Invalid stored done flags')
                previous={'snapshots':[r for r in result['snapshots'] if r['time']!=result['snapshots'][-1]['time']]}
                reward=0.0 if not state['history'] or last[-1].get('truncation_reason') else self.env._reward(previous,result)
                if type(last[1]) not in (int,float) or not math.isfinite(last[1]) or last[1]!=reward:
                    raise ValueError('Stored reward mismatch')
            elif state['history']:raise ValueError('Missing transition outcome')
        self.env._history=deepcopy(state['history']);self.env._evaluations=state['evaluations'];self.env._done=state['done']
        self.env._seed=self.seed;self.env._result=result
        self.reset_outcome=tuple(payload['reset_outcome']);self.last_outcome=tuple(payload['last_outcome'])

    def _action(self,action_id):
        row=self.journal.db.execute('SELECT * FROM episode_actions WHERE session=? AND action_id=?',(self.session,action_id)).fetchone()
        return row,self.journal._decode(row,checkpoint=True) if row else None

    def _write_action(self,action,prior=None):
        encoded,checksum,size=self.journal._encode(action,checkpoint=True)
        self.journal._charge(size-(prior['bytes'] if prior else 0),0 if prior else 1)
        if prior:self.journal.db.execute('UPDATE episode_actions SET payload=?,checksum=?,bytes=? WHERE session=? AND action_id=?',
                    (encoded,checksum,size,self.session,action['id']))
        else:self.journal.db.execute('INSERT INTO episode_actions VALUES(?,?,?,?,?)',(self.session,action['id'],encoded,checksum,size))

    def _work(self):
        end=round((len(self.history)+1)*self.evaluate.step_seconds,6)
        return sum(max(0,math.ceil(round((end-self.evaluate._data['next_due'][b['id']])*1000000)/round(b['cadence_seconds']*1000000)))
                   for b in self.evaluate.bindings)

    def _claim(self,action_id,actions,work):
        with self.journal._transaction():
            row,prior=self._action(action_id)
            if prior:
                if prior['content_hash']!=digest(actions):raise ValueError('Action ID has different content')
                if prior['status']=='completed':return prior
                if prior['status']!='failed':raise EffectUnresolved('Action pending; explicit resume/reconciliation required')
            latest=self.journal.db.execute('SELECT MAX(sequence) FROM checkpoints WHERE session=?',(self.stream,)).fetchone()[0]
            if latest!=self.sequence:raise ValueError('Stale episode writer; explicit resume required')
            for other in self.journal.db.execute('SELECT * FROM episode_actions WHERE session=?',(self.session,)):
                if self.journal._decode(other,checkpoint=True)['status']=='pending':raise EffectUnresolved('Another episode action is pending')
            attempt=prior['attempt']+1 if prior else 1
            if work:self.journal._reserve(self.quota_name,work,digest([action_id,attempt]))
            action={'id':action_id,'actions':deepcopy(actions),'content_hash':digest(actions),'attempt':attempt,
                    'status':'pending','before_sequence':self.sequence,'reserved_calls':work}
            self._write_action(action,row)
            return action

    def _commit(self,action,outcome):
        checkpoint=self._checkpoint();self.journal._checkpoint(checkpoint)
        encoded,checksum,size=self.journal._encode(checkpoint,checkpoint=True)
        with self.journal._transaction():
            row,current=self._action(action['id'])
            latest=self.journal.db.execute('SELECT MAX(sequence) FROM checkpoints WHERE session=?',(self.stream,)).fetchone()[0]
            if current!=action or latest!=self.sequence:raise ValueError('Stale action commit rejected')
            self.journal._charge(size);sequence=self.sequence+1
            self.journal.db.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?)',(self.stream,sequence,self.identity,encoded,checksum,size))
            completed={**action,'status':'completed','outcome':list(outcome),'checkpoint_sequence':sequence}
            self._write_action(completed,row)
            self.journal._append(self.stream,{'action_id':action['id'],'content_hash':action['content_hash'],
                                             'checkpoint_sequence':sequence,'outcome':list(outcome)})
        self.sequence=sequence

    def step(self,actions,*,action_id):
        _name(action_id);_json_native(actions)
        if len(canonical(actions))>65536:raise ValueError('Action exceeds 64 KiB')
        if not self._usable:raise ValueError('Session unusable; explicit resume/reconciliation required')
        row,prior=self._action(action_id)
        if prior and prior['status']=='completed':
            if prior['content_hash']!=digest(actions):raise ValueError('Action ID has different content')
            return tuple(deepcopy(prior['outcome']))
        if self.env._done:raise ValueError('Episode has ended')
        before=self._checkpoint();action=self._claim(action_id,actions,self._work())
        if action['status']=='completed':return tuple(deepcopy(action['outcome']))
        if self.evaluate.live:self.evaluate.backend._begin_claim(action)
        try:
            outcome=self.env.step(actions);self.last_outcome=outcome
            self._commit(action,outcome)
            return deepcopy(outcome)
        except BaseException:
            try:
                row,current=self._action(action_id)
                if current['status']=='completed':self._usable=False # Lost commit response; recover committed checkpoint.
                else:
                    self._restore(before)
                    with self.journal._transaction():
                        row,current=self._action(action_id)
                        if current==action:self._write_action({**current,'status':'failed'},row)
            except BaseException:self._usable=False
            raise
        finally:
            if self.evaluate.live:self.evaluate.backend._action=None

    def publish(self,store,dataset,*,request=None,raw_inputs=()):
        """Publish a verified immutable SQLite export and an episode report."""
        if not self._usable:raise RuntimeError("Session requires explicit resume before publication")
        if self.journal.db.execute('SELECT MAX(sequence) FROM checkpoints WHERE session=?',(self.stream,)).fetchone()[0]!=self.sequence:raise RuntimeError("Session changed; resume before publication")
        from .artifacts import publish_report
        with tempfile.TemporaryDirectory(dir=store.scratch_dir(dataset),prefix='journal-export-') as temporary:
            exported=self.journal.backup(Path(temporary)/'journal.sqlite')
            raw=store.import_file(dataset,exported['database'],{'publisher':'local execution journal',
                'license_id':'MIT','backup_manifest':read_json(exported['manifest'])},update_latest=False)
        view=self.evaluate.publish()
        completed=[self.journal._decode(row,checkpoint=True) for row in self.journal.db.execute('SELECT * FROM episode_actions WHERE session=?',(self.session,))]
        frames=[{'action_id':a['id'],'outcome':a['outcome']} for a in sorted((a for a in completed if a['status']=='completed'),key=lambda a:a['checkpoint_sequence'])]
        report={'frames':frames,'session':self.session,'binding':self.binding,'request':request if request is not None else self.binding,
                'journal_snapshot':raw,'checkpoint_sequence':self.sequence,'input_history':self.history,
                'snapshots':self.materialization['snapshots'],'last_outcome':list(self.last_outcome),
                'quota':self.journal.quota(self.quota_name),'materialization_ref':view,
                'limitations':['Local cooperating SQLite writers only; provider cooperation is required for exactly-once external effects.']}
        return publish_report(store,dataset,report,{'binding':self.binding,'request':request},
                inputs=[self.evaluate.graph_ref,view],raw_inputs=[*raw_inputs,raw],entrypoint='worldmodel.journaled_environment:JournaledEnvironment')
