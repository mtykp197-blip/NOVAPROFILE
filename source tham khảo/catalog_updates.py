"""Signed, data-only catalog updates. Never loads downloaded executable code."""
import base64
import json
import os
import secrets
from pathlib import Path


def validate(data):
    if not isinstance(data,dict) or data.get('aud')!='nova-device-catalog' or type(data.get('version')) is not int or data['version']<1:
        raise ValueError('Invalid device catalog')
    rows=data.get('presets')
    if not isinstance(rows,list) or not 1<=len(rows)<=2000:raise ValueError('Invalid catalog size')
    ids=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('Invalid preset')
        for name in ['id','name','brand','model','android','gpu_vendor','gpu_renderer']:
            if not isinstance(row.get(name),str) or not 1<=len(row[name])<=200:raise ValueError('Invalid preset field')
        if row['id'] in ids:raise ValueError('Duplicate preset')
        ids.add(row['id'])
        if row.get('orientation') not in ['portrait','landscape'] or row.get('kind') not in ['phone','tablet','foldable'] or type(row.get('mobile')) is not bool:raise ValueError('Invalid preset type')
        for name in ['cpu_threads','device_memory']:
            if type(row.get(name)) is not int or not 1<=row[name]<=64:raise ValueError('Invalid hardware')
        screen=row.get('screen',{})
        for name in ['width','height']:
            if type(screen.get(name)) is not int or not 100<=screen[name]<=8000:raise ValueError('Invalid screen')
        if type(screen.get('pixelRatio')) not in (int,float) or not 0.5<=screen['pixelRatio']<=8:raise ValueError('Invalid DPR')
    return data


def verify(envelope,public):
    if not isinstance(envelope,dict) or set(envelope)!={'payload','signature'}:raise ValueError('Invalid envelope')
    def decode(value):
        if not isinstance(value,str):raise ValueError('Invalid encoding')
        return base64.urlsafe_b64decode(value+'='*((-len(value))%4))
    raw=decode(envelope['payload'])
    if len(raw)>1500000:raise ValueError('Catalog too large')
    public.verify(decode(envelope['signature']),raw)
    return validate(json.loads(raw))


class CatalogCache:
    def __init__(self,base,root,public):
        self.file=Path(root)/'updates'/'device-catalog.signed.json';self.public=public
        self.data=validate(json.loads((Path(base)/'device-catalog.json').read_text(encoding='utf-8')))
        if self.file.exists():
            try:
                data=verify(json.loads(self.file.read_text(encoding='utf-8')),public)
                if data['version']>=self.data['version']:self.data=data
            except Exception:pass # Keep the trusted bundled catalog if local cache is invalid.

    def apply(self,envelope):
        data=verify(envelope,self.public)
        if data['version']<self.data['version']:raise ValueError('Catalog downgrade refused')
        if data['version']==self.data['version']:
            if data!=self.data:raise ValueError('Catalog version conflict')
            return False
        self.file.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.file.with_name('catalog-'+secrets.token_hex(8)+'.tmp')
        try:
            with temporary.open('x',encoding='utf-8') as output:json.dump(envelope,output)
            os.replace(temporary,self.file)
        finally:temporary.unlink(missing_ok=True)
        self.data=data
        return True
