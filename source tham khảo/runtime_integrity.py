"""Signed file inventory for an authorized browser runtime; no binary patching."""
import base64
import hashlib
import json
from pathlib import Path,PurePosixPath

from commercial_client import LicenseRequired


def file_hash(path):
    h=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def verify_runtime(root,envelope,public_key):
    root=Path(root).resolve()
    try:
        encoded=envelope['payload'];signature=envelope['signature']
        if not isinstance(encoded,str) or len(encoded)>2_000_000:raise ValueError()
        public_key.verify(base64.urlsafe_b64decode(signature+'='*((-len(signature))%4)),encoded.encode('ascii'))
        manifest=json.loads(base64.urlsafe_b64decode(encoded+'='*((-len(encoded))%4)))
        files=manifest['files']
        if manifest['v']!=1 or not isinstance(files,dict) or not files or len(files)>10000:raise ValueError()
        if 'chrome.exe' not in files or not any(PurePosixPath(name).name=='chrome.dll' for name in files):raise ValueError()
        for name,expected in files.items():
            part=PurePosixPath(name)
            if part.is_absolute() or '..' in part.parts or '\\' in name or ':' in name:raise ValueError()
            target=(root/name).resolve()
            if not target.is_relative_to(root) or not target.is_file() or file_hash(target)!=expected:raise ValueError()
        for target in root.rglob('*'):
            if target.is_file() and target.suffix.lower() in ('.exe','.dll') and target.relative_to(root).as_posix() not in files:raise ValueError()
    except Exception as exc:raise LicenseRequired('NV: Bo ung dung khong dat kiem tra toan ven') from exc
    return True
