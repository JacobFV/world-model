"""Local SQLite runtime journal, shared quotas and explicit external-effect states.

SQLite transactions coordinate cooperating processes on one local filesystem.
Payload checksums detect corruption, not malicious edits. Pending/uncertain effects
never authorize a retry; provider cooperation is needed for exactly-once effects.
"""
from contextlib import contextmanager
import json
import sqlite3
import hashlib
import hmac
import os
import shutil
from pathlib import Path
import tempfile

from .checkpoints import _json_native
from .limits import resolve_limits
from .util import canonical, digest, atomic_json, file_hash, read_json


class QuotaExceeded(ValueError):
    """A durable shared reservation cannot fit its immutable quota."""


class EffectUnresolved(ValueError):
    """A previous attempt may already have performed its external effect."""


_DEFAULTS={'max_payload_bytes':1024*1024,'max_checkpoint_bytes':32*1024*1024,
           'max_storage_bytes':64*1024*1024,'max_records':100000}
# Persisted per-journal defaults stay small; their configurable ceilings are named limits.
_CEILINGS={'max_payload_bytes':'journal_max_payload_bytes','max_checkpoint_bytes':'journal_max_checkpoint_bytes',
           'max_storage_bytes':'journal_max_storage_bytes','max_records':'journal_max_records'}


def _ceiling(key,value,limits):
    _integer(value)
    return limits.check(_CEILINGS[key],value,'Journal limit '+key)


def _name(value):
    if type(value) is not str or not 1<=len(value)<=200:
        raise ValueError('Journal names/keys must be strings of 1..200 characters')
    return value


def _integer(value,maximum=2**53-1,zero=False):
    if type(value) is not int or not (0 if zero else 1)<=value<=maximum:
        raise ValueError('Integer outside journal bounds')
    return value


class ExecutionJournal:
    """Use one connection per thread/process; close explicitly or as a context manager.

    Limits are persisted on creation. Reopening without limits adopts those values;
    explicit different limits are rejected. Logical storage limits exclude SQLite
    indexes, pages and WAL overhead; this is not a physical disk quota.
    """
    def __init__(self,path,*,limits=None,signing_key=None,work_limits=None):
        if signing_key is not None and (type(signing_key) is not bytes or len(signing_key)<32):
            raise ValueError("Journal signing key requires at least 32 bytes")
        self.path=Path(path).resolve();self._signing_key=signing_key
        if limits is not None and (not isinstance(limits,dict) or set(limits)-set(_DEFAULTS)):
            raise ValueError('Unknown journal limits')
        requested={**_DEFAULTS,**(limits or {})}
        self._work_limits=work_limits;ceilings=resolve_limits(work_limits)
        for key,value in requested.items():_ceiling(key,value,ceilings)
        self.db=sqlite3.connect(str(path),timeout=10,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        try:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY CHECK(id=1),bytes INTEGER NOT NULL,records INTEGER NOT NULL);
                INSERT OR IGNORE INTO usage VALUES(1,0,0);
                CREATE TABLE IF NOT EXISTS entries (stream TEXT NOT NULL,sequence INTEGER NOT NULL,key TEXT,
                    payload TEXT NOT NULL,checksum TEXT NOT NULL,bytes INTEGER NOT NULL,
                    PRIMARY KEY(stream,sequence),UNIQUE(stream,key));
                CREATE TABLE IF NOT EXISTS checkpoints (session TEXT NOT NULL,sequence INTEGER NOT NULL,identity TEXT NOT NULL,
                    payload TEXT NOT NULL,checksum TEXT NOT NULL,bytes INTEGER NOT NULL,PRIMARY KEY(session,sequence));
                CREATE TABLE IF NOT EXISTS quotas (name TEXT PRIMARY KEY,ceiling INTEGER NOT NULL,used INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS reservations (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    quota TEXT NOT NULL REFERENCES quotas(name),key TEXT NOT NULL,units INTEGER NOT NULL,UNIQUE(quota,key));
                CREATE TABLE IF NOT EXISTS effects (sequence INTEGER PRIMARY KEY AUTOINCREMENT,key TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL,checksum TEXT NOT NULL,bytes INTEGER NOT NULL);
            ''')
            with self._transaction():
                row=self.db.execute("SELECT payload FROM metadata WHERE key='limits'").fetchone()
                if row:
                    self.limits=json.loads(row['payload'])
                    if set(self.limits)!=set(_DEFAULTS):raise ValueError('Invalid stored journal limits')
                    for key,value in self.limits.items():_ceiling(key,value,ceilings)
                    if limits is not None and requested!=self.limits:raise ValueError('Journal limits are immutable')
                else:
                    self.limits=requested
                    self.db.execute("INSERT INTO metadata VALUES('limits',?)",(canonical(requested).decode(),))
                    self.db.execute("INSERT INTO metadata VALUES('schema_version','1')")
                auth=self.db.execute("SELECT payload FROM metadata WHERE key='authentication'").fetchone()
                expected_auth=self._checksum({'purpose':'journal-key-check'}) if signing_key else 'sha256'
                if auth and auth[0]!=expected_auth:raise ValueError('Journal authentication key/mode mismatch')
                if not auth:
                    if signing_key and self.db.execute('SELECT records FROM usage WHERE id=1').fetchone()[0]:
                        raise ValueError('Cannot enable authentication on existing unsigned journal')
                    self.db.execute("INSERT INTO metadata VALUES('authentication',?)",(expected_auth,))
                schema=self.db.execute("SELECT payload FROM metadata WHERE key='schema_version'").fetchone()
                if not schema or schema[0]!='1':raise ValueError('Unsupported journal schema')
        except Exception:
            self.db.close();raise

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def close(self):
        self.db.close()
        if getattr(self,'_temporary_restore',False):
            for suffix in ('','-wal','-shm'):Path(str(self.path)+suffix).unlink(missing_ok=True)

    @contextmanager
    def _transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK');raise

    def _checksum(self,value):
        return hmac.new(self._signing_key,canonical(value),hashlib.sha256).hexdigest() if self._signing_key else digest(value)

    def _encode(self,value,checkpoint=False):
        _json_native(value);encoded=canonical(value)
        maximum=self.limits['max_checkpoint_bytes' if checkpoint else 'max_payload_bytes']
        if len(encoded)>maximum:raise ValueError('Journal payload exceeds configured bound')
        return encoded.decode('utf-8'),self._checksum(value),len(encoded)

    def _decode(self,row,checkpoint=False):
        try:
            if len(row['payload'].encode())>self.limits['max_checkpoint_bytes' if checkpoint else 'max_payload_bytes']:
                raise ValueError('oversized')
            value=json.loads(row['payload']);_json_native(value)
            if not hmac.compare_digest(self._checksum(value),row['checksum']) or len(row['payload'].encode())!=row['bytes']:raise ValueError('checksum')
            return value
        except (ValueError,TypeError) as exc:raise ValueError('Journal payload integrity failure') from exc

    def _charge(self,byte_count,records=1):
        usage=self.db.execute('SELECT bytes,records FROM usage WHERE id=1').fetchone()
        if usage['bytes']+byte_count>self.limits['max_storage_bytes'] or usage['records']+records>self.limits['max_records']:
            raise ValueError('Journal logical storage/record budget exhausted')
        self.db.execute('UPDATE usage SET bytes=bytes+?,records=records+? WHERE id=1',(byte_count,records))

    def _page(self,limit,max_bytes=None):
        limits=resolve_limits(self._work_limits)
        _integer(limit);limits.check('journal_max_page_items',limit,'Journal page limit')
        if max_bytes is not None:
            _integer(max_bytes);limits.check('journal_max_page_bytes',max_bytes,'Journal page bytes')

    def usage(self):return dict(self.db.execute('SELECT bytes,records FROM usage WHERE id=1').fetchone())

    def _append(self,stream,value,key=None):
        encoded,checksum,size=self._encode(value)
        if key is not None:
            row=self.db.execute('SELECT * FROM entries WHERE stream=? AND key=?',(stream,key)).fetchone()
            if row:
                if self._checksum(self._decode(row))!=checksum:raise ValueError('Append idempotency key has different payload')
                return row['sequence']
        sequence=self.db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM entries WHERE stream=?',(stream,)).fetchone()[0]
        self._charge(size)
        self.db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?)',(stream,sequence,key,encoded,checksum,size))
        return sequence

    def append(self,stream,value,*,key=None):
        _name(stream)
        if stream.startswith('effect:'):raise ValueError('Effect audit streams are reserved')
        if key is not None:_name(key)
        with self._transaction():return self._append(stream,value,key)

    def history(self,stream,*,after=0,limit=100,max_bytes=8*1024*1024):
        _name(stream);_integer(after,zero=True);self._page(limit,max_bytes)
        rows=self.db.execute('SELECT * FROM entries WHERE stream=? AND sequence>? ORDER BY sequence LIMIT ?',
                             (stream,after,limit+1))
        items=[];size=0;more=False
        for row in rows:
            if len(items)==limit or size+row['bytes']>max_bytes:
                if not items:raise ValueError('Page byte budget cannot fit next history item')
                more=True;break
            value=self._decode(row);size+=row['bytes']
            items.append({'sequence':row['sequence'],'key':row['key'],'value':value})
        return {'items':items,'next_after':items[-1]['sequence'] if items else after,'has_more':more,'payload_bytes':size}

    def configure_quota(self,name,limit):
        _name(name);_integer(limit,zero=True)
        with self._transaction():
            row=self.db.execute('SELECT ceiling FROM quotas WHERE name=?',(name,)).fetchone()
            if row and row[0]!=limit:raise ValueError('Shared quota ceiling is immutable')
            if not row:
                self._charge(0);self.db.execute('INSERT INTO quotas VALUES(?,?,0)',(name,limit))
        return self.quota(name)

    def quota(self,name):
        _name(name);row=self.db.execute('SELECT * FROM quotas WHERE name=?',(name,)).fetchone()
        if not row:raise ValueError('Unknown shared quota')
        return {'name':name,'limit':row['ceiling'],'used':row['used'],'remaining':row['ceiling']-row['used']}

    def _reserve(self,name,units,key):
        prior=self.db.execute('SELECT * FROM reservations WHERE quota=? AND key=?',(name,key)).fetchone()
        if prior:
            if prior['units']!=units:raise ValueError('Reservation key has different units')
            return {'sequence':prior['sequence'],'key':key,'units':units,'already_reserved':True}
        row=self.db.execute('SELECT * FROM quotas WHERE name=?',(name,)).fetchone()
        if not row:raise ValueError('Unknown shared quota')
        if units>row['ceiling']-row['used']:raise QuotaExceeded('Shared quota exhausted before reservation')
        self._charge(0)
        self.db.execute('UPDATE quotas SET used=used+? WHERE name=?',(units,name))
        cursor=self.db.execute('INSERT INTO reservations(quota,key,units) VALUES(?,?,?)',(name,key,units))
        return {'sequence':cursor.lastrowid,'key':key,'units':units,'already_reserved':False}

    def reserve(self,name,units,key):
        _name(name);_name(key);_integer(units)
        with self._transaction():return self._reserve(name,units,key)

    def reservations(self,name,*,after=0,limit=100):
        _name(name);_integer(after,zero=True);self._page(limit)
        rows=self.db.execute('SELECT sequence,key,units FROM reservations WHERE quota=? AND sequence>? ORDER BY sequence LIMIT ?',
                             (name,after,limit+1)).fetchall()
        items=[dict(row) for row in rows[:limit]]
        return {'items':items,'next_after':items[-1]['sequence'] if items else after,'has_more':len(rows)>limit}

    def _checkpoint(self,value):
        _json_native(value)
        if type(value) is not dict or set(value)!={'schema_version','identity','payload','checksum'} or type(value['schema_version']) is not int or value['schema_version']!=1:
            raise ValueError('Invalid checkpoint envelope')
        if not isinstance(value['identity'],str) or len(value['identity'])!=64 or any(c not in '0123456789abcdef' for c in value['identity']) or not isinstance(value['payload'],dict):
            raise ValueError('Invalid checkpoint identity/payload')
        if digest({k:v for k,v in value.items() if k!='checksum'})!=value['checksum']:
            raise ValueError('Checkpoint integrity failure')
        self._encode(value,checkpoint=True)

    def save_checkpoint(self,session,checkpoint,*,history_events=(),expected_sequence=None):
        _name(session);self._checkpoint(checkpoint)
        if session.startswith('effect:'):raise ValueError('Effect audit streams are reserved')
        if not isinstance(history_events,(list,tuple)) or len(history_events)>100:raise ValueError('At most 100 checkpoint history events')
        for event in history_events:self._encode(event)
        if expected_sequence is not None:_integer(expected_sequence,zero=True)
        encoded,checksum,size=self._encode(checkpoint,checkpoint=True)
        with self._transaction():
            prior=self.db.execute('SELECT * FROM checkpoints WHERE session=? ORDER BY sequence DESC LIMIT 1',(session,)).fetchone()
            current=prior['sequence'] if prior else 0
            if expected_sequence is not None and current!=expected_sequence:raise ValueError('Checkpoint sequence conflict')
            if prior:
                self._decode(prior,checkpoint=True)
                if prior['identity']!=checkpoint['identity']:raise ValueError('Checkpoint session identity cannot change')
            sequence=current+1;self._charge(size)
            self.db.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?)',(session,sequence,checkpoint['identity'],encoded,checksum,size))
            for event in history_events:self._append(session,event)
        return {'session':session,'sequence':sequence,'checksum':checksum,'identity':checkpoint['identity']}

    def load_checkpoint(self,session,*,sequence=None,expected_identity=None):
        _name(session)
        if sequence is not None:_integer(sequence)
        row=self.db.execute('SELECT * FROM checkpoints WHERE session=? '+('AND sequence=? ' if sequence is not None else '')+
            'ORDER BY sequence DESC LIMIT 1',(session,sequence) if sequence is not None else (session,)).fetchone()
        if not row:raise ValueError('Checkpoint does not exist')
        value=self._decode(row,checkpoint=True);self._checkpoint(value)
        if value['identity']!=row['identity'] or (expected_identity is not None and value['identity']!=expected_identity):
            raise ValueError('Checkpoint identity mismatch')
        return value

    def save_evaluator(self,session,evaluator,**kwargs):
        return self.save_checkpoint(session,evaluator.checkpoint(),**kwargs)

    def load_evaluator(self,session,evaluator,**kwargs):
        """Use the evaluator's own restore validation; live restore stays unsupported."""
        return evaluator.restore(self.load_checkpoint(session,expected_identity=evaluator.identity,**kwargs))

    def _effect(self,key):
        row=self.db.execute('SELECT * FROM effects WHERE key=?',(key,)).fetchone()
        return row,self._decode(row) if row else None

    def _write_effect(self,key,value,prior=None):
        encoded,checksum,size=self._encode(value)
        self._charge(size-(prior['bytes'] if prior else 0),0 if prior else 1)
        if prior:self.db.execute('UPDATE effects SET payload=?,checksum=?,bytes=? WHERE key=?',(encoded,checksum,size,key))
        else:self.db.execute('INSERT INTO effects(key,payload,checksum,bytes) VALUES(?,?,?,?)',(key,encoded,checksum,size))
        self._append('effect:'+key,{'status':value['status'],'reason':value.get('reason'),
                     'result_digest':digest(value['result']) if 'result' in value else None,'provider_receipt':value.get('provider_receipt')})

    def _begin_effect(self,key,request,*,quota=None,units=1):
        """Reserve inside the caller's transaction, after any ownership fence."""
        _name(key);_integer(units);self._encode(request)
        if len(key)>193:raise ValueError('Effect keys must fit the 200-character audit stream bound')
        if quota is not None:_name(quota)
        row,prior=self._effect(key)
        if prior:
            if digest(prior['request'])!=digest(request) or prior['quota']!=quota or prior['units']!=units:
                raise ValueError('Effect idempotency key has different request or reservation')
            if prior['status']!='completed':raise EffectUnresolved('Effect is '+prior['status']+'; reconcile with provider before further action')
            return {**prior,'execute':False}
        if quota is not None:self._reserve(quota,units,'effect:'+key)
        value={'key':key,'request':request,'quota':quota,'units':units,'status':'pending'}
        self._write_effect(key,value)
        return {**value,'execute':True}

    def begin_effect(self,key,request,*,quota=None,units=1):
        """Only execute when execute=True, returned after durable pending commit."""
        with self._transaction():return self._begin_effect(key,request,quota=quota,units=units)

    def mark_uncertain(self,key,reason):
        _name(key);_name(reason)
        with self._transaction():
            row,value=self._effect(key)
            if not row:raise ValueError('Unknown effect')
            if value['status']=='completed':raise ValueError('Completed effects cannot become uncertain')
            value.update(status='uncertain',reason=reason);self._write_effect(key,value,row)
        return value

    def complete_effect(self,key,result,*,provider_receipt=None):
        _name(key);self._encode(result)
        if provider_receipt is not None:self._encode(provider_receipt)
        with self._transaction():
            row,value=self._effect(key)
            if not row:raise ValueError('Unknown effect')
            if value['status']=='completed':
                if digest(value['result'])!=digest(result) or digest(value.get('provider_receipt'))!=digest(provider_receipt):raise ValueError('Completed effect differs from recorded result/receipt')
                return value
            if value['status']=='uncertain' and (not isinstance(provider_receipt,dict) or not provider_receipt):
                raise ValueError('Uncertain effect requires explicit provider reconciliation receipt')
            value.update(status='completed',result=result,provider_receipt=provider_receipt)
            self._write_effect(key,value,row)
        return value

    def effects(self,*,after=0,limit=100,max_bytes=8*1024*1024):
        _integer(after,zero=True);self._page(limit,max_bytes)
        rows=self.db.execute('SELECT * FROM effects WHERE sequence>? ORDER BY sequence LIMIT ?',(after,limit+1))
        items=[];size=0;more=False
        for row in rows:
            if len(items)==limit or size+row['bytes']>max_bytes:
                if not items:raise ValueError('Page byte budget cannot fit next effect')
                more=True;break
            items.append({'sequence':row['sequence'],**self._decode(row)});size+=row['bytes']
        return {'items':items,'next_after':items[-1]['sequence'] if items else after,'has_more':more,'payload_bytes':size}


    def storage_usage(self):
        """Logical charged payload and observed file sizes, not a disk quota."""
        return {**self.usage(),'database_bytes':self.path.stat().st_size if self.path.exists() else 0,
                'wal_bytes':Path(str(self.path)+'-wal').stat().st_size if Path(str(self.path)+'-wal').exists() else 0}

    def verify(self):
        """Validate stored payloads and recompute logical accounting in a read snapshot."""
        with self._transaction():
            if self.db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Journal SQLite integrity failure')
            size=0;count=0
            tables=[('entries',False),('checkpoints',True),('effects',False)]
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='episode_actions'").fetchone():tables.append(('episode_actions',True))
            for table,is_checkpoint in tables:
                for row in self.db.execute('SELECT * FROM '+table):
                    self._decode(row,checkpoint=is_checkpoint);size+=row['bytes'];count+=1
            for table in ('quotas','reservations'):
                count+=self.db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
            for quota in self.db.execute('SELECT * FROM quotas'):
                used=self.db.execute('SELECT COALESCE(SUM(units),0) FROM reservations WHERE quota=?',(quota['name'],)).fetchone()[0]
                if used!=quota['used'] or not 0<=used<=quota['ceiling']:raise ValueError('Journal quota accounting mismatch')
            if {'bytes':size,'records':count}!=self.usage():raise ValueError('Journal storage accounting mismatch')
        return True

    def prune(self,stream,*,kind='history',keep_last=1,limit=100):
        """Prune only unkeyed user history or superseded checkpoints; keep authority."""
        _name(stream);_integer(keep_last);resolve_limits(self._work_limits).check('journal_max_records',keep_last,'Prune keep_last');self._page(limit)
        if stream.startswith(('effect:','episode:')):raise ValueError('Authority/audit streams cannot be pruned')
        if kind not in ('history','checkpoints'):raise ValueError('Unknown retention kind')
        table,column=('entries','stream') if kind=='history' else ('checkpoints','session')
        with self._transaction():
            cutoff=self.db.execute('SELECT sequence FROM '+table+' WHERE '+column+'=? ORDER BY sequence DESC LIMIT 1 OFFSET ?',
                                   (stream,keep_last-1)).fetchone()
            rows=[] if not cutoff else self.db.execute('SELECT * FROM '+table+' WHERE '+column+'=? AND sequence<?'+
                    (' AND key IS NULL' if kind=='history' else '')+' ORDER BY sequence LIMIT ?',
                    (stream,cutoff[0],limit)).fetchall()
            for row in rows:
                self._decode(row,checkpoint=kind=='checkpoints')
                self.db.execute('DELETE FROM '+table+' WHERE '+column+'=? AND sequence=?',(stream,row['sequence']))
            self._charge(-sum(row['bytes'] for row in rows),-len(rows))
        return {'removed':len(rows),'payload_bytes_freed':sum(row['bytes'] for row in rows)}

    def backup(self,path):
        """Create a standalone consistent SQLite snapshot and checksum/HMAC manifest."""
        path=Path(path).resolve();manifest=path.with_suffix(path.suffix+'.manifest.json')
        if path==self.path or path.exists() or manifest.exists():raise ValueError('Backup destination must be new')
        path.parent.mkdir(parents=True,exist_ok=True)
        self.verify()
        fd,temporary=tempfile.mkstemp(prefix='.journal-backup-',dir=path.parent);os.close(fd)
        temporary=Path(temporary)
        try:
            target=sqlite3.connect(temporary)
            try:
                self.db.backup(target,pages=128);target.execute('PRAGMA journal_mode=DELETE')
            finally:target.close()
            with ExecutionJournal(temporary,signing_key=self._signing_key) as checked:checked.verify();usage=checked.usage()
            # Closing the last connection checkpoints any reopened WAL.
            descriptor={'schema_version':1,'database':path.name,'sha256':file_hash(temporary),
                        'bytes':temporary.stat().st_size,'logical_usage':usage,'authentication':'hmac-sha256' if self._signing_key else 'sha256'}
            if self._signing_key:descriptor['hmac_sha256']=self._checksum(descriptor)
            os.rename(temporary,path);atomic_json(manifest,descriptor)
        finally:
            temporary.unlink(missing_ok=True)
        return {**descriptor,'database':str(path),'manifest':str(manifest)}

    @classmethod
    def from_backup(cls,manifest,*,signing_key=None,destination=None):
        manifest=Path(manifest);descriptor=read_json(manifest)
        if descriptor.get('authentication')=='hmac-sha256':
            if type(signing_key) is not bytes or len(signing_key)<32:raise ValueError('Authenticated backup requires key')
            expected=hmac.new(signing_key,canonical({k:v for k,v in descriptor.items() if k!='hmac_sha256'}),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected,descriptor.get('hmac_sha256','')):raise ValueError('Backup authentication failure')
        elif signing_key is not None:raise ValueError('Backup is unsigned')
        name=descriptor.get('database')
        if not isinstance(name,str) or Path(name).name!=name:raise ValueError('Unsafe backup path')
        path=manifest.parent/name
        if file_hash(path)!=descriptor['sha256'] or path.stat().st_size!=descriptor['bytes']:raise ValueError('Backup checksum mismatch')
        temporary=destination is None
        if temporary:
            fd,destination=tempfile.mkstemp(prefix='journal-restored-',suffix='.sqlite');os.close(fd)
        else:
            destination=Path(destination)
            if destination.exists():raise ValueError('Restore destination must be new')
        shutil.copyfile(path,destination)
        journal=cls(destination,signing_key=signing_key);journal._temporary_restore=temporary
        try:journal.verify()
        except BaseException:journal.close();raise
        return journal
