"""NOVA server-authorized operations; does not modify or hide the browser core."""
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from email.utils import parsedate_to_datetime

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


class LicenseRequired(ValueError):
    pass


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def unb64(value):
    return base64.urlsafe_b64decode(value+'='*((-len(value))%4))


class Blob(ctypes.Structure):
    _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]


def windows_protect(data,decrypt=False):
    """Current-user DPAPI. It is not TPM storage or protection against this user's code."""
    if os.name!='nt':raise LicenseRequired('Can Windows de bao ve khoa thiet bi')
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    buffer=ctypes.create_string_buffer(data);source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
    function=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise LicenseRequired('Khong mo duoc khoa bao ve Windows; khong tu dong tao lai danh tinh')
    try:return ctypes.string_at(target.pbData,target.cbData)
    finally:kernel.LocalFree(ctypes.cast(target.pbData,ctypes.c_void_p))


class LicenseRejected(LicenseRequired):
    def __init__(self,message,code=None):
        super().__init__(message)
        self.code=code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


class ServerBusy(LicenseRequired):
    """The server cannot take another call right now."""


class CommercialClient:
    # A busy server is asked again, so the tabs line up instead of all
    # failing at once. The wait grows, so they do not come back together.
    QUEUE_ATTEMPTS=8
    QUEUE_BASE_WAIT=1.0
    QUEUE_MAX_WAIT=15.0

    def __init__(self,config,root,*,transport=None,protect=windows_protect,clock=time.time,monotonic=time.monotonic):
        self.clock=clock;self.monotonic=monotonic;self.server_clock=None
        self.lock=threading.RLock();self.root=Path(root);self.protect=protect
        self.server=config['server_url'].rstrip('/');parsed=urlsplit(self.server)
        testing=config.get('local_test') is True and parsed.hostname in ('127.0.0.1','localhost')
        if (parsed.scheme!='https' and not (testing and parsed.scheme=='http')) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
            raise LicenseRequired('May chu cap phep phai co HTTPS hop le')
        self.public=serialization.load_pem_public_key(config['signing_public_key'].encode())
        if not isinstance(self.public,Ed25519PublicKey):raise LicenseRequired('Sai khoa may chu')
        self.transport=transport or self._post;self.token=None;self.claims=None
        self.pending_login=None;self.telegram_ticket=None;self.telegram_user_id=None
        self.core_manifest=config.get('core_manifest')
        provider=config.get('device_key_provider','dpapi')
        if provider not in ('dpapi','tpm'):raise LicenseRequired('Sai chinh sach khoa thiet bi')
        self.key_provider=provider
        if provider=='tpm':
            if (self.root/'device-key.dpapi').exists():raise LicenseRequired('Can quan tri phe duyet chuyen danh tinh thiet bi')
            from windows_tpm import TpmIdentity
            self.private=TpmIdentity('nova-profiles|'+self.server)
        else:
            self.root.mkdir(parents=True,exist_ok=True);target=self.root/'device-key.dpapi'
            if target.exists():
                try:self.private=serialization.load_pem_private_key(protect(target.read_bytes(),True),password=None)
                except Exception as exc:raise LicenseRequired('Khong doc duoc khoa thiet bi; lien he quan tri') from exc
            else:
                self.private=Ed25519PrivateKey.generate()
                pem=self.private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
                sealed=protect(pem);temp=target.with_name(target.name+'.'+secrets.token_hex(6)+'.tmp')
                try:
                    with temp.open('xb') as output:output.write(sealed)
                    # Do not overwrite a concurrently initialized device identity.
                    os.link(temp,target)
                except FileExistsError:
                    self.private=serialization.load_pem_private_key(protect(target.read_bytes(),True),password=None)
                finally:temp.unlink(missing_ok=True)
            if not isinstance(self.private,Ed25519PrivateKey):raise LicenseRequired('Sai loai khoa thiet bi')
        self.pem=self.private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.device=hashlib.sha256(self.private.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
        if not self.restore_session():self.restore_device()

    def license_time(self):
        if self.server_clock is None:return self.clock()
        epoch,started=self.server_clock
        return epoch+max(0,self.monotonic()-started)

    def sync_server_clock(self,date_header,elapsed=0):
        # HTTPS authenticates the server Date header. Keep the anchor in memory;
        # never restore a saved timestamp or alter the Windows system clock.
        try:
            stamp=parsedate_to_datetime(date_header)
            if stamp.tzinfo is None:return
            self.server_clock=(stamp.timestamp()+max(0,elapsed),self.monotonic())
        except (TypeError,ValueError,OverflowError):pass

    def _post(self,route,body):
        started=self.monotonic()
        request=Request(self.server+route,data=json.dumps(body,separators=(',',':')).encode(),headers={
            'Content-Type':'application/json', 'Accept':'application/json', 'User-Agent':'NOVA-Profiles/0.5'
        },method='POST')
        # Solving a captcha waits for the remote solver, so that call needs
        # more time and a bigger reply than the short license calls.
        captcha=route=='/v1/captcha/solve'
        try:
            with build_opener(NoRedirect()).open(request,timeout=150 if captcha else 12) as response:
                limit=2000000 if route=='/v1/catalog' else 16384
                raw=response.read(limit+1)
                if len(raw)>limit:raise LicenseRequired('Phan hoi cap phep qua lon')
                result=json.loads(raw)
                if not isinstance(result,dict):raise LicenseRequired('Phan hoi cap phep khong hop le')
                self.sync_server_clock(response.headers.get('Date'),self.monotonic()-started)
                return result
        except HTTPError as exc:
            # Never log the request body: it can contain key, ticket or lease.
            code=None
            reason=None
            try:
                value=json.loads(exc.read(4096))
                if value.get('error') in ('session_replaced','invalid_license','invalid_device','expired','invalid_token'):
                    code=value['error']
                elif value.get('error'):
                    # The server may answer with a plain reason, for example an
                    # invalid task or a missing solver key. Keep it visible.
                    reason=str(value['error'])[:120]
            except Exception:pass
            # The server is busy when too many tabs ask at the same time. The
            # call waits its turn and tries again, so a busy server does not
            # end a run.
            if exc.code==429:
                raise ServerBusy('Server NOVA đang bận, chờ đến lượt.') from exc
            if exc.code==503 and reason=='otp_unavailable':
                raise ServerBusy('Server NOVA chưa sẵn sàng, chờ đến lượt.') from exc
            # A proxy/firewall can return 403 HTML, and a challenge can expire.
            # Only an explicit license denial may erase the saved session: a
            # transient reason (a busy server, an expired challenge, a bad
            # request) must keep it, or the user has to sign in again.
            if exc.code in (400,401,403) and code is not None:
                raise LicenseRejected('May chu tu choi quyen su dung ('+code+'); kiem tra key, Telegram va thiet bi',code) from exc
            if exc.code in (400,401,403) and reason is not None:
                raise LicenseRequired('May chu tam thoi khong phan hoi ('+reason+'); vui long thu lai') from exc
            raise LicenseRequired('May chu tam thoi khong phan hoi; vui long thu lai') from exc
        except LicenseRequired:raise
        except ServerBusy:raise
        except Exception as exc:raise LicenseRequired('Khong ket noi duoc may chu cap phep; thao tac da bi chan') from exc

    def _post_with_queue(self,route,body):
        """Calls the server and waits its turn when the server is busy.

        Many tabs of one machine ask the server at the same time. When the
        server says that it is full, the call waits a little and tries
        again, so the tabs line up instead of all failing at once.
        """
        if self.transport is not self._post:
            # A test that brings its own transport has no busy answer to wait
            # for, so the call goes straight to it.
            return self.transport(route,body)
        attempt=0
        while True:
            attempt+=1
            try:
                return self._post(route,body)
            except ServerBusy:
                if attempt>=self.QUEUE_ATTEMPTS:
                    raise LicenseRequired('Server NOVA vẫn bận. Hãy thử lại sau.')
                # The wait grows, so the tabs do not come back at the same
                # moment and block each other again.
                wait=min(self.QUEUE_MAX_WAIT, self.QUEUE_BASE_WAIT*attempt)
                time.sleep(wait)

    def signed(self,action,binding):
        challenge=self.transport('/v1/challenge',{'public_key':self.pem});nonce=challenge['nonce']
        if not isinstance(nonce,str) or len(nonce)>100:raise LicenseRequired('Challenge khong hop le')
        proof=b64(self.private.sign(f'NOVA/1\n{action}\n{nonce}\n{binding}'.encode()))
        return {'public_key':self.pem,'nonce':nonce,'proof':proof}

    def accept(self,result):
        try:
            token=result['token']
            if not isinstance(token,str) or len(token)>4096:raise ValueError()
            encoded,signature=token.split('.')
            self.public.verify(unb64(signature),encoded.encode());claims=json.loads(unb64(encoded))
            now=self.license_time()
            if claims['v']!=1 or claims['aud']!='nova-profiles' or claims['device']!=self.device:raise ValueError()
            if not all(type(claims[k]) is int for k in ('exp','iat','license_exp')):raise ValueError()
            if not (claims['iat']<=now+30 and now<claims['exp']<=claims['license_exp'] and 0<claims['exp']-claims['iat']<=900):raise ValueError()
            if not str(claims['telegram_user_id']).isdigit():raise ValueError()
        except Exception as exc:raise LicenseRequired('Chu ky hoac thong tin giay phep khong hop le') from exc
        self.token=token;self.claims=claims
        self.save_session()
        return self.status()

    def save_session(self):
        # Only a signed lease is stored, never a key or Telegram login ticket.
        target=self.root/'session.dpapi'
        sealed=self.protect(json.dumps({'server':self.server,'device':self.device,'token':self.token}).encode())
        temporary=target.with_name(target.name+'.'+secrets.token_hex(6)+'.tmp')
        try:
            with temporary.open('xb') as output:output.write(sealed)
            os.replace(temporary,target)
        finally:temporary.unlink(missing_ok=True)

    def forget_session(self):
        self.token=None;self.claims=None
        (self.root/'session.dpapi').unlink(missing_ok=True)

    def restore_session(self):
        target=self.root/'session.dpapi'
        if not target.exists():return False
        try:
            if target.stat().st_size>16384:raise ValueError()
            saved=json.loads(self.protect(target.read_bytes(),True))
            token=saved['token']
            if saved['server']!=self.server or saved['device']!=self.device or not isinstance(token,str) or len(token)>4096:raise ValueError()
        except Exception:
            self.forget_session();return False
        try:
            self.accept(self.transport('/v1/resume',{'token':token,**self.signed('resume',digest(token))}))
            return True
        except LicenseRejected:
            self.forget_session();return False
        except Exception:
            # Network outage: keep the sealed session for the next attempt,
            # but never unlock the app without a fresh server authorization.
            self.token=None;self.claims=None;return False

    def restore_device(self):
        try:
            self.accept(self.transport('/v1/device-login',self.signed('device-login',digest('NOVA saved device login'))))
            return True
        except LicenseRejected:
            self.forget_session();return False
        except Exception:
            self.token=None;self.claims=None;return False

    def restore_login(self):
        with self.lock:
            if self.status()['active']:return self.status()
            if not self.restore_session():self.restore_device()
            return self.status()

    def start_login(self):
        with self.lock:
            result=self.transport('/v1/telegram/start',self.signed('login',digest('NOVA Telegram login')))
            import re
            if not re.fullmatch(r'https://t\.me/[A-Za-z0-9_]{5,32}\?start=nova_[a-f0-9]{32}',result.get('telegram_url','')):raise LicenseRequired('Phan hoi dang nhap khong hop le')
            self.pending_login=result;self.telegram_ticket=None;self.telegram_user_id=None
            return {**{k:result[k] for k in ('telegram_url','code','expires')}, 'remaining_seconds':max(0,min(300,result['expires']-self.license_time()))}

    def poll_login(self):
        with self.lock:
            if not self.pending_login:raise LicenseRequired('Chua tao yeu cau dang nhap')
            pending=self.pending_login;binding=digest(pending['login_id']+'\n'+pending['poll_secret'])
            result=self.transport('/v1/telegram/poll',{'login_id':pending['login_id'],'poll_secret':pending['poll_secret'],**self.signed('login-poll',binding)})
            if result.get('state')=='approved':
                self.telegram_ticket=result['telegram_ticket'];self.telegram_user_id=result['telegram_user_id']
                return {'state':'approved','telegram_user_id':self.telegram_user_id}
            return {'state':'pending'}

    def activate(self,key,telegram_ticket=None):
        with self.lock:
            telegram_ticket=telegram_ticket or self.telegram_ticket
            if not telegram_ticket:raise LicenseRequired('Can dang nhap Telegram truoc')
            request={'key':key,'telegram_ticket':telegram_ticket,**self.signed('activate',digest(key+'\n'+telegram_ticket))}
            result=self.accept(self.transport('/v1/activate',request));self.telegram_ticket=None;self.pending_login=None;return result

    def require(self):
        with self.lock:
            if not self.token and not self.restore_session() and not self.restore_device():raise LicenseRequired('Can dang nhap Telegram va kich hoat key')
            try:
                # The lease is checked on the server on every call, so a
                # revoked key stops the next operation right away. A lease
                # that is still good is only renewed, never kept.
                expires=self.claims.get('exp',0) if self.claims else 0
                # A renewal is a round trip to the server, and the panel asks
                # for several calls at a time. A lease that still has time is
                # used as it is and is renewed shortly before it runs out.
                if expires>self.license_time()+60:return
                # An expired lease cannot be renewed: the server only accepts
                # the token of a lapsed lease on `resume`, which is the call
                # that issues a new lease from the saved session.
                action='resume' if expires<=self.license_time() else 'renew'
                result=self.transport('/v1/'+action,{'token':self.token,**self.signed(action,digest(self.token))})
                self.accept(result)
            except LicenseRejected as exc:
                # A successful renewal response may be lost in transit. Recover
                # only through a fresh, signed online device authorization.
                # A lapsed lease and a replaced session both leave the machine
                # without a usable lease, and the device login is the only way
                # back that does not ask the owner to sign in again.
                if exc.code in ('session_replaced','invalid_token') and self.restore_device():return
                self.forget_session();raise
            except Exception:
                # Preserve the current signed lease on transient HTTP/network
                # failure. This operation still fails; no offline creation.
                raise

    def reserve(self,signature,pid,groups=None,legacy_signature=None):
        with self.lock:
            self.require()
            if groups is None:
                binding=digest(self.token+'\n'+signature+'\n'+pid)
                request={'token':self.token,'signature':signature,'profile_id':pid,**self.signed('reserve',binding)}
            else:
                groups_json=json.dumps(groups,sort_keys=True,separators=(',',':'))
                legacy_signature=legacy_signature or ''
                binding=digest(self.token+'\n'+signature+'\n'+pid+'\n'+groups_json+'\n'+legacy_signature)
                request={'token':self.token,'signature':signature,'profile_id':pid,'schema_version':'2','groups_json':groups_json,'legacy_signature':legacy_signature,**self.signed('reserve',binding)}
            result=self.transport('/v1/fingerprints/reserve',request)
            if type(result.get('accepted')) is not bool:raise LicenseRequired('Phan hoi chong trung khong hop le')
            return result['accepted']

    def device_catalog(self):
        with self.lock:
            self.require()
            return self.transport('/v1/catalog',{'token':self.token,**self.signed('catalog',digest(self.token))})

    def app_update(self):
        with self.lock:
            self.require()
            return self.transport('/v1/app-update',{'token':self.token,**self.signed('catalog',digest(self.token))})

    def site_links(self):
        with self.lock:
            self.require()
            return self.transport('/v1/site-links',{'token':self.token,**self.signed('catalog',digest(self.token))})

    def otp_request(self,route,fields=None):
        """Admin-only OTP panel call. The server rejects non-admin leases."""
        # The lock covers the call to the server as well, not only the state of
        # the session. The server keeps one live lease per key, so two calls
        # that renew at the same time would replace each other and the call
        # that was already on the wire would come back as a replaced session.
        with self.lock:
            self.require()
            token=self.token
            binding=digest(token+'\n'+route)
            request={'token':token,**self.signed('otp',binding)}
            request.update(fields or {})
            return self._post_with_queue(route,request)

    def captcha_solve(self,task,image,instruction='',site='',guide=''):
        """Admin-only captcha call. The solver key stays on the server."""
        # The lock covers the call to the server as well, for the same reason
        # as the OTP calls: one live lease per key means two renewals at the
        # same time replace each other.
        with self.lock:
            self.require()
            token=self.token
            route='/v1/captcha/solve'
            request={'token':token,**self.signed('otp',digest(token+'\n'+route)),
                     'task':task,'image':image,'instruction':instruction or '','site':site or ''}
            # The guide is a picture of its own, so it is only sent when there
            # is one. The server treats it as optional.
            if guide:request['guide']=guide
            return self._post_with_queue(route,request)

    def status(self):
        return {'required':True,'active':bool(self.token and self.claims and self.claims.get('license_exp',0)>self.license_time() and self.claims.get('exp',0)>self.license_time()),'device_id':self.device,'key_provider':self.key_provider,
                'telegram_user_id':self.claims.get('telegram_user_id') if self.claims else None,
                'owner_registration':self.claims.get('owner_registration') is True if self.claims else False,
                'expires':self.claims.get('license_exp') if self.claims else None}

    def verify_runtime(self,root):
        from runtime_integrity import verify_runtime
        return verify_runtime(root,self.core_manifest,self.public)


def configured_client(base,root,required=False):
    config_path=Path(base)/'commercial-policy.json'
    if not config_path.exists():
        if required:raise LicenseRequired('Ban thuong mai thieu cau hinh cap phep; khong khoi dong')
        return None
    return CommercialClient(json.loads(config_path.read_text(encoding='utf8')),Path(root)/'license')
