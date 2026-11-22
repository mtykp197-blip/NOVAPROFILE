"""Persistent history of stable, directly measured browser outputs."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime,timezone

# The signal families whose collision means the site groups two profiles as
# one device. The list is the same as the one of the server, so the profile
# is refused locally before the request is made.
BLOCKING_GROUPS=('identity','device','canvas','gpu','fonts','devices','core')


class FingerprintHistory:
    def __init__(self,path):
        self.path=str(path)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS fingerprints (signature TEXT PRIMARY KEY,profile_id TEXT NOT NULL,created_at TEXT NOT NULL,observed_json TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS collisions (id INTEGER PRIMARY KEY,signature TEXT NOT NULL,profile_id TEXT NOT NULL,created_at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS fingerprint_core (signature TEXT PRIMARY KEY,profile_id TEXT NOT NULL,created_at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS fingerprint_signals (group_name TEXT NOT NULL,signature TEXT NOT NULL,profile_id TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(group_name,signature,profile_id))')
            # The lookup table of the guard: the draw hash of every profile
            # that was handed out, with the GPU row it belongs to.
            db.execute('CREATE TABLE IF NOT EXISTS fingerprint_draws (draw TEXT NOT NULL,renderer TEXT NOT NULL,profile_id TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(draw,profile_id))')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30)
        try:
            with db:yield db
        finally:db.close()

    @staticmethod
    def _hash(value):
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def _device_signal(observed):
        """The device family of a profile: what a site groups it by.

        The lobby of sc88/cm88/c168 posts the physical resolution, the
        density, the number of cores, the model of the phone and the
        version of the system to `/api/netstat/attribution/submit`. The
        model and the version of the system come from the user agent and
        the UA-CH values, the panel from `screen`, the density from
        `devicePixelRatio` and the cores from `hardwareConcurrency`.
        """
        screen=observed.get('screen') or {}
        hints=observed.get('hints') or {}
        if not screen and not hints:return None
        return {'width':screen.get('width'),'height':screen.get('height'),
                'dpr':screen.get('dpr'),'cpu':observed.get('cpu'),
                'model':hints.get('model') or '', 'platform':hints.get('platform') or '',
                'platformVersion':hints.get('platformVersion') or ''}

    @classmethod
    def fingerprint_groups(cls,observed):
        if observed.get('canvas',{}).get('first')!=observed.get('canvas',{}).get('second'):
            raise ValueError('Canvas khong on dinh trong cung mot profile')
        # Keep volatile permission-dependent IDs out; group hashes allow the
        # server to compare stable signal families without receiving raw data.
        identity={k:observed[k] for k in ('ua','platform','vendor','ram','cpu','touch','coarse','screen','languages','timezone','hints','apis','contexts') if k in observed}
        canvas=observed.get('canvas')
        if canvas:
            canvas={k:v for k,v in canvas.items() if k not in ('first','second')}
            if 'png' not in canvas and observed.get('canvas',{}).get('first') is not None:canvas['png']=observed['canvas']['first']
        gpu={k:observed[k] for k in ('webgl','webgpu') if k in observed}
        # The storage quota, the network information and the media device
        # identifiers are read by the sites and are different in every
        # profile. They were not measured before, so two profiles with the
        # same quota and the same device identifiers looked different.
        devices={k:observed[k] for k in ('storage','connection','devices') if k in observed}
        # The device family of the profile: the panel, the density, the
        # number of cores, the model and the version of the system.
        #
        # Measured against the lobby of sc88/cm88/c168: the telemetry it
        # posts to `/api/netstat/attribution/submit` holds exactly these
        # values, so two profiles that agree on them are one device to the
        # site even when their canvas and their audio differ. The group is
        # the check that keeps the profiles apart.
        device=cls._device_signal(observed)
        groups={}
        for name,value in [('identity',identity),('device',device),('canvas',canvas),('audio',observed.get('audio')),('gpu',gpu or None),('fonts',observed.get('fonts')),('tls',observed.get('tls')),('devices',devices or None)]:
            if value is not None:groups[name]=cls._hash(value)
        if all(k in groups for k in ('canvas','audio','gpu')):
            groups['core']=cls._hash({k:groups[k] for k in ('canvas','audio','gpu')})
        groups['full']=cls._hash({'schema':'nova-fingerprint-v2','groups':groups})
        return groups

    @classmethod
    def legacy_signature(cls,observed):
        """Recreate the previous whole-object signature when its inputs exist."""
        measured=observed.get('legacy')
        if isinstance(measured,dict):
            canvas=measured.get('canvas',{});first=canvas.get('first')
            if first is None or first!=canvas.get('second'):return None
            signal=dict(measured);signal['canvas']=first
            return cls._hash(signal)
        required=('ua','platform','ram','cpu','touch','coarse','screen','canvas','audio','languages','hints')
        if any(k not in observed for k in required):return None
        canvas=observed['canvas'];first=canvas.get('first')
        if first is None:return None
        legacy={k:observed[k] for k in required}
        legacy['canvas']=first
        return cls._hash(legacy)

    @classmethod
    def signature(cls,observed):
        return cls.fingerprint_groups(observed)['full']

    @staticmethod
    def draw_signature(observed):
        """The draw hash of a profile, with the GPU row it belongs to.

        The draw hash is the hash of the pixels of a WebGL drawing, so it is
        the value a site groups the profiles by. The renderer is part of the
        key: two profiles on different GPU rows are different devices even
        when their pixels happen to be the same.
        """
        webgl=observed.get('webgl') or {}
        draw=webgl.get('draw')
        if not draw:return None
        return {'draw':str(draw),'renderer':str(webgl.get('renderer') or '')}

    def draw_owner(self,observed,pid):
        """The profile that already uses this draw hash on another GPU row.

        The guard asks this before a profile is handed out: a draw hash that
        is already in use by another profile means the site can group the
        two as one person.
        """
        key=self.draw_signature(observed)
        if not key:return None
        with self.connect() as db:
            rows=db.execute('SELECT profile_id,renderer FROM fingerprint_draws WHERE draw=? AND profile_id!=?',(key['draw'],pid)).fetchall()
        for row in rows:
            if row[1]!=key['renderer']:return {'profile_id':row[0],'renderer':row[1],'draw':key['draw']}
        return None

    def reserve_draw(self,observed,pid):
        """Write the draw hash of the profile into the lookup table."""
        key=self.draw_signature(observed)
        if not key:return None
        now=datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            # The profile owns one draw hash at a time: the old one is
            # dropped, so a profile that was edited does not keep a hash it
            # no longer paints with.
            db.execute('DELETE FROM fingerprint_draws WHERE profile_id=?',(pid,))
            db.execute('INSERT OR IGNORE INTO fingerprint_draws VALUES(?,?,?,?)',(key['draw'],key['renderer'],pid,now))
        return key

    def reserve(self,observed,pid):
        groups=self.fingerprint_groups(observed);signature=groups['full'];legacy=self.legacy_signature(observed);now=datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            matches=db.execute('SELECT signature,profile_id FROM fingerprints WHERE signature=? OR (? IS NOT NULL AND signature=?)',(signature,legacy,legacy)).fetchall()
            existing=next((row for row in matches if row[0]==signature),matches[0] if matches else None)
            core=db.execute('SELECT profile_id FROM fingerprint_core WHERE signature=?',(groups.get('core'),)).fetchone() if groups.get('core') else None
            if (any(row[1]!=pid for row in matches)) or (core and core[0]!=pid):
                db.execute('INSERT INTO collisions(signature,profile_id,created_at) VALUES(?,?,?)',(signature,pid,now));return None
            # A signal family that another profile already uses means the
            # site groups the two profiles as one device. The check is the
            # same as the one of the server, so the profile is refused here
            # before the request is made.
            for name in BLOCKING_GROUPS:
                value=groups.get(name)
                if not value:continue
                hit=db.execute('SELECT profile_id FROM fingerprint_signals WHERE group_name=? AND signature=? AND profile_id!=?',(name,value,pid)).fetchone()
                if hit:
                    db.execute('INSERT INTO collisions(signature,profile_id,created_at) VALUES(?,?,?)',(signature,pid,now));return None
            if existing and existing[0]==signature:return signature
            db.execute('INSERT OR IGNORE INTO fingerprints VALUES(?,?,?,?)',(signature,pid,now,json.dumps(observed,ensure_ascii=False)))
            if groups.get('core'):db.execute('INSERT OR IGNORE INTO fingerprint_core VALUES(?,?,?)',(groups['core'],pid,now))
            for name,value in groups.items():
                if name!='full':db.execute('INSERT OR IGNORE INTO fingerprint_signals VALUES(?,?,?,?)',(name,value,pid,now))
            # The draw hash goes into the lookup table of the guard, so the
            # next profile can be checked against it. The old hash of this
            # profile is dropped: a profile that was edited does not keep a
            # hash it no longer paints with.
            draw=self.draw_signature(observed)
            if draw:
                db.execute('DELETE FROM fingerprint_draws WHERE profile_id=?',(pid,))
                db.execute('INSERT OR IGNORE INTO fingerprint_draws VALUES(?,?,?,?)',(draw['draw'],draw['renderer'],pid,now))
        return signature

    def would_collide(self,observed,pid):
        groups=self.fingerprint_groups(observed);signature=groups['full'];legacy=self.legacy_signature(observed)
        with self.connect() as db:
            exact=db.execute('SELECT profile_id FROM fingerprints WHERE signature=? OR (? IS NOT NULL AND signature=?)',(signature,legacy,legacy)).fetchall()
            core=db.execute('SELECT profile_id FROM fingerprint_core WHERE signature=?',(groups.get('core'),)).fetchone() if groups.get('core') else None
            signals=[]
            for name in BLOCKING_GROUPS:
                value=groups.get(name)
                if not value:continue
                hit=db.execute('SELECT profile_id FROM fingerprint_signals WHERE group_name=? AND signature=? AND profile_id!=?',(name,value,pid)).fetchone()
                if hit:signals.append((name,hit[0]))
        return bool(any(row[0]!=pid for row in exact) or (core and core[0]!=pid) or signals)

    def stats(self):
        with self.connect() as db:return {'remembered':db.execute('SELECT count(*) FROM fingerprints').fetchone()[0],'collisions':db.execute('SELECT count(*) FROM collisions').fetchone()[0]}

    def recent(self):
        with self.connect() as db:return [dict(zip(['signature','profile_id','created_at'],row)) for row in db.execute('SELECT signature,profile_id,created_at FROM fingerprints ORDER BY created_at DESC LIMIT 500')]
