"""TPM-only Windows CNG RSA identity. No software-provider fallback or private export."""
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import struct

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization


class TpmUnavailable(ValueError):
    pass


class DeviceInfo(ctypes.Structure):
    _fields_=[('structVersion',wintypes.DWORD),('tpmVersion',wintypes.DWORD),('interfaceType',wintypes.DWORD),('revision',wintypes.DWORD)]


def tpm_status():
    if os.name!='nt':return {'available':False,'status':'windows_required'}
    dll=ctypes.WinDLL('tbs');function=dll.Tbsi_GetDeviceInfo
    function.argtypes=[wintypes.DWORD,ctypes.POINTER(DeviceInfo)];function.restype=wintypes.DWORD
    info=DeviceInfo();result=function(ctypes.sizeof(info),ctypes.byref(info))
    return {'available':result==0 and info.tpmVersion==2,'status':f'0x{result:08X}','version':info.tpmVersion}


class PssInfo(ctypes.Structure):
    _fields_=[('algorithm',wintypes.LPCWSTR),('salt_length',wintypes.DWORD)]


class TpmIdentity:
    def __init__(self,scope):
        if not tpm_status()['available']:raise TpmUnavailable('TPM 2.0 unavailable')
        self.api=ctypes.WinDLL('ncrypt');self.provider=ctypes.c_void_p();self.key=ctypes.c_void_p()
        handle=ctypes.c_void_p;ptr=ctypes.POINTER;DWORD=wintypes.DWORD;status=ctypes.c_long
        signatures={
            'NCryptOpenStorageProvider':[ptr(handle),wintypes.LPCWSTR,DWORD],
            'NCryptOpenKey':[handle,ptr(handle),wintypes.LPCWSTR,DWORD,DWORD],
            'NCryptCreatePersistedKey':[handle,ptr(handle),wintypes.LPCWSTR,wintypes.LPCWSTR,DWORD,DWORD],
            'NCryptSetProperty':[handle,wintypes.LPCWSTR,ctypes.c_void_p,DWORD,DWORD],
            'NCryptGetProperty':[handle,wintypes.LPCWSTR,ctypes.c_void_p,DWORD,ptr(DWORD),DWORD],
            'NCryptFinalizeKey':[handle,DWORD],
            'NCryptExportKey':[handle,handle,wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,DWORD,ptr(DWORD),DWORD],
            'NCryptSignHash':[handle,ctypes.c_void_p,ctypes.c_void_p,DWORD,ctypes.c_void_p,DWORD,ptr(DWORD),DWORD],
            'NCryptFreeObject':[handle]
        }
        for name,args in signatures.items():
            function=getattr(self.api,name);function.argtypes=args;function.restype=status
        # A dedicated key per server/application scope; not a copyable private-key file.
        self.name='NOVA.Profiles.TPM.'+hashlib.sha256(str(scope).encode()).hexdigest()[:32]
        try:
            self.check(self.api.NCryptOpenStorageProvider(ctypes.byref(self.provider),'Microsoft Platform Crypto Provider',0))
            if not self.property(self.provider,'Impl Type')&1:raise TpmUnavailable('Hardware provider required')
            result=self.api.NCryptOpenKey(self.provider,ctypes.byref(self.key),self.name,0,0x40)
            if result:
                # Only a truly missing key may be created; denied/broken identities are not reset.
                if result&0xffffffff!=0x80090016:raise TpmUnavailable('Cannot open TPM identity')
                self.check(self.api.NCryptCreatePersistedKey(self.provider,ctypes.byref(self.key),'RSA',self.name,0,0x40))
                for prop,value in [('Length',2048),('Export Policy',0),('Key Usage',2)]:
                    data=DWORD(value);self.check(self.api.NCryptSetProperty(self.key,prop,ctypes.byref(data),ctypes.sizeof(data),0))
                self.check(self.api.NCryptFinalizeKey(self.key,0x40))
            if self.property(self.key,'Export Policy')!=0:raise TpmUnavailable('Exportable identity rejected')
            size=DWORD();self.check(self.api.NCryptExportKey(self.key,None,'RSAPUBLICBLOB',None,None,0,ctypes.byref(size),0))
            if size.value>8192:raise TpmUnavailable('Invalid public key size')
            data=ctypes.create_string_buffer(size.value);self.check(self.api.NCryptExportKey(self.key,None,'RSAPUBLICBLOB',None,data,size.value,ctypes.byref(size),0))
            raw=data.raw[:size.value];magic,bits,e_len,n_len,p_len,q_len=struct.unpack('<6I',raw[:24])
            if magic!=0x31415352 or bits!=2048 or p_len or q_len or len(raw)!=24+e_len+n_len:raise TpmUnavailable('Invalid TPM public key')
            e=int.from_bytes(raw[24:24+e_len],'big');n=int.from_bytes(raw[24+e_len:],'big')
            self.public=rsa.RSAPublicNumbers(e,n).public_key()
            self.pem=self.public.public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        except Exception:
            self.close();raise

    @staticmethod
    def check(result):
        if result:raise TpmUnavailable('Windows TPM operation failed')

    def property(self,handle,name):
        value=wintypes.DWORD();length=wintypes.DWORD()
        self.check(self.api.NCryptGetProperty(handle,name,ctypes.byref(value),ctypes.sizeof(value),ctypes.byref(length),0))
        if length.value!=ctypes.sizeof(value):raise TpmUnavailable('Invalid TPM property')
        return value.value

    def public_key(self):return self.public

    def sign(self,data):
        hashed=ctypes.create_string_buffer(hashlib.sha256(data).digest());padding=PssInfo('SHA256',32);length=wintypes.DWORD()
        self.check(self.api.NCryptSignHash(self.key,ctypes.byref(padding),hashed,32,None,0,ctypes.byref(length),0x8|0x40))
        if length.value!=256:raise TpmUnavailable('Invalid signature size')
        output=ctypes.create_string_buffer(length.value)
        self.check(self.api.NCryptSignHash(self.key,ctypes.byref(padding),hashed,32,output,length.value,ctypes.byref(length),0x8|0x40))
        return output.raw[:length.value]

    def close(self):
        for name in ('key','provider'):
            handle=getattr(self,name,None)
            if handle and handle.value:self.api.NCryptFreeObject(handle);handle.value=None

    def __del__(self):
        try:self.close()
        except Exception:pass
