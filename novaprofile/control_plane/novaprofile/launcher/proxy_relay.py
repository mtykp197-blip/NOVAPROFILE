"""A relay that carries the name and the password of a proxy.

The browser is told to use the relay, and the relay is told to use the
real proxy.  The relay adds the ``Proxy-Authorization`` header itself,
or answers the SOCKS5 authentication, so the browser never asks the
site for a name and a password.

The relay listens on the loopback only.  It is closed with the
browser.

This is the relay of the reference engine, brought along as it is.
"""

"""Loopback-only HTTP proxy adapter for HTTP/SOCKS5 upstream authentication."""
import base64
import select
import socket
import socketserver
import struct
import threading
from urllib.parse import urlsplit


def _exact(sock, count):
    data=b''
    while len(data)<count:
        part=sock.recv(count-len(data))
        if not part:raise ConnectionError('Proxy closed connection')
        data+=part
    return data


def _headers(sock):
    data=b''
    while b'\r\n\r\n' not in data:
        part=sock.recv(4096)
        if not part:raise ValueError('Incomplete proxy headers')
        data+=part
        if len(data)>65536:raise ValueError('Invalid proxy headers')
    return data.split(b'\r\n\r\n',1)


def _tunnel(a,b):
    while True:
        ready,_,_=select.select([a,b],[],[],60)
        if not ready:return
        for source in ready:
            data=source.recv(65536)
            if not data:return
            (b if source is a else a).sendall(data)


class ProxyRelay:
    def __init__(self,proxy):
        self.proxy=dict(proxy)
        mode=str(proxy.get('type','HTTP')).lower()
        if mode not in ('http','socks5'):
            raise ValueError('Proxy relay supports HTTP and SOCKS5')
        self.proxy['type']=mode
        self.proxy['port']=int(proxy['port'])
        if not 1<=self.proxy['port']<=65535:raise ValueError('Invalid proxy port')
        self.connections=set()
        relay=self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                upstream=None
                try:
                    self.request.settimeout(15)
                    headers,tail=_headers(self.request)
                    lines=headers.decode('iso-8859-1').split('\r\n')
                    method,target,version=lines[0].split(' ',2)
                    connect=method.upper()=='CONNECT'
                    if connect:
                        parsed=urlsplit('//'+target)
                    else:
                        parsed=urlsplit(target)
                    host=parsed.hostname
                    port=parsed.port or (443 if connect else 80)
                    if not host:raise ValueError('Invalid proxy target')
                    upstream=socket.create_connection((relay.proxy['ip'],relay.proxy['port']),timeout=15)
                    relay.connections.add(upstream)
                    relay.connections.add(self.request)
                    if mode=='socks5':
                        user=str(proxy.get('user','')).encode()
                        password=str(proxy.get('pass','')).encode()
                        methods=b'\x00\x02' if user or password else b'\x00'
                        upstream.sendall(b'\x05'+bytes([len(methods)])+methods)
                        answer=_exact(upstream,2)
                        if answer==b'\x05\x02':
                            if max(len(user),len(password))>255:raise ValueError('SOCKS credentials too long')
                            upstream.sendall(b'\x01'+bytes([len(user)])+user+bytes([len(password)])+password)
                            if _exact(upstream,2)!=b'\x01\x00':raise ConnectionError('SOCKS authentication rejected')
                        elif answer!=b'\x05\x00':raise ConnectionError('SOCKS authentication method rejected')
                        domain=host.encode('idna')
                        if len(domain)>255:raise ValueError('Proxy hostname too long')
                        upstream.sendall(b'\x05\x01\x00\x03'+bytes([len(domain)])+domain+struct.pack('!H',port))
                        reply=_exact(upstream,4)
                        if reply[:2]!=b'\x05\x00':raise ConnectionError('SOCKS connection rejected')
                        length={1:4,4:16}.get(reply[3])
                        if reply[3]==3:length=_exact(upstream,1)[0]
                        if length is None:raise ConnectionError('Invalid SOCKS address')
                        _exact(upstream,length+2)
                        if connect:self.request.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                        else:
                            path=parsed.path or '/'
                            if parsed.query:path+='?'+parsed.query
                            kept=[x for x in lines[1:] if not x.lower().startswith(('proxy-authorization:','proxy-connection:','connection:'))]
                            upstream.sendall((f'{method} {path} {version}\r\n'+'\r\n'.join(kept)+'\r\nConnection: close\r\n\r\n').encode('iso-8859-1'))
                    else:
                        kept=[x for x in lines[1:] if not x.lower().startswith(('proxy-authorization:','proxy-connection:','connection:'))]
                        user,password=proxy.get('user',''),proxy.get('pass','')
                        if user or password:
                            auth=base64.b64encode(f'{user}:{password}'.encode()).decode()
                            kept.append('Proxy-Authorization: Basic '+auth)
                        kept.append('Connection: close')
                        upstream.sendall((lines[0]+'\r\n'+'\r\n'.join(kept)+'\r\n\r\n').encode('iso-8859-1'))
                        if connect:
                            response,remaining=_headers(upstream)
                            self.request.sendall(response+b'\r\n\r\n'+remaining)
                            if response.split(b' ',2)[1]!=b'200':return
                    if tail:upstream.sendall(tail)
                    self.request.settimeout(None)
                    upstream.settimeout(None)
                    _tunnel(self.request,upstream)
                except (OSError,ValueError,ConnectionError):
                    try:self.request.sendall(b'HTTP/1.1 502 Proxy connection failed\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                    except OSError:pass
                finally:
                    relay.connections.discard(self.request)
                    if upstream:
                        relay.connections.discard(upstream)
                        upstream.close()
        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address=True
            daemon_threads=True
        self.server=Server(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    @property
    def url(self):return f'http://127.0.0.1:{self.server.server_address[1]}'

    def close(self):
        for sock in list(self.connections):
            try:sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        self.server.shutdown()
        self.server.server_close()
