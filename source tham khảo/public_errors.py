"""Customer-safe errors with bounded diagnostics for local runtime probes."""
from datetime import datetime,timezone
import json
import re
import secrets
import threading
import traceback

MESSAGE='Không thể hoàn tất thao tác. Vui lòng thử lại hoặc liên hệ hỗ trợ.'
_lock=threading.Lock()
_LOG_FILE=re.compile(r'^probe-failure-v_[a-f0-9]{24}\.log$')


def _diagnostic(exc):
    value=getattr(exc,'safe_diagnostic',None)
    if not isinstance(value,dict):return None
    stage=value.get('stage')
    if stage not in ('javascript','probe','startup','devtools'):return None
    result={'stage':stage}
    detail=value.get('exception')
    if isinstance(detail,str):result['exception']=detail[:400]
    filename=value.get('log_file')
    if isinstance(filename,str) and _LOG_FILE.fullmatch(filename):result['log_file']=filename
    return result


def record_error(root,exc):
    reference='NV-'+secrets.token_hex(4).upper()
    frames=[{'file':frame.filename.replace('\\','/').rsplit('/',1)[-1],
             'function':frame.name,'line':frame.lineno} for frame in traceback.extract_tb(exc.__traceback__)]
    record={'reference':reference,'at':datetime.now(timezone.utc).isoformat(),
            'type':type(exc).__name__,'frames':frames}
    # The reason the server gave is kept: without it a lost connection and a
    # revoked key look the same from the outside.
    code=getattr(exc,'code',None)
    if isinstance(code,str) and len(code)<=64:record['code']=code
    diagnostic=_diagnostic(exc)
    if diagnostic:record['diagnostic']=diagnostic
    try:
        with _lock:
            path=root/'support-errors.jsonl'
            if path.exists() and path.stat().st_size>2_000_000:
                path.replace(root/'support-errors.previous.jsonl')
            with path.open('a',encoding='utf8') as output:output.write(json.dumps(record,ensure_ascii=False)+'\n')
    except OSError:pass
    return {'error':MESSAGE,'reference':reference}


def scrub_errors(value):
    if isinstance(value,list):return [scrub_errors(item) for item in value]
    if isinstance(value,dict):return {key:(MESSAGE if key=='error' and item else scrub_errors(item)) for key,item in value.items()}
    return value
