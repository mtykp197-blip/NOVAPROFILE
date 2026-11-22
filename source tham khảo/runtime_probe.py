"""Measure actual outputs of the bundled Hidemium core in disposable profiles."""
import json
import hashlib
import ctypes
import ctypes.wintypes
from pathlib import Path
import re
import socket
import socketserver
import subprocess
import threading
import time
import html as html_module
from urllib.request import urlopen

import websocket
from hidemium_native_config import encode_config

MEASURE='''(async()=>{
async function hash(s){const d=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s));return [...new Uint8Array(d)].map(x=>x.toString(16).padStart(2,'0')).join('')}
async function canvas(){const c=document.createElement('canvas');c.width=301;c.height=83;const x=c.getContext('2d',{willReadFrequently:true});x.fillStyle='#fe9021';x.fillRect(3,4,140,55);x.font='18px Arial';x.fillStyle='#126';x.fillText('Android profile 012345',12,37);x.globalCompositeOperation='multiply';x.fillStyle='rgba(30,90,210,.61)';x.beginPath();x.arc(117,39,23,0,Math.PI*2);x.fill();return {png:await hash(c.toDataURL()),pixels:await hash(String.fromCharCode(...x.getImageData(0,0,c.width,c.height).data.slice(0,12000))),textWidth:x.measureText('Android profile 012345').width}}
async function webgl(){const c=document.createElement('canvas'),g=c.getContext('webgl2')||c.getContext('webgl');if(!g)return null;const d=g.getExtension('WEBGL_debug_renderer_info');const v=g.createShader(g.VERTEX_SHADER);g.shaderSource(v,'attribute vec2 a;varying vec2 t;void main(){t=a*0.5+0.5;gl_Position=vec4(a,0.,1.);}');g.compileShader(v);const f=g.createShader(g.FRAGMENT_SHADER);g.shaderSource(f,'precision mediump float;varying vec2 t;void main(){float r=sin(t.x*12.9898+t.y*78.233)*43758.5453;r=fract(sin(r)*43758.5453);gl_FragColor=vec4(fract(r),fract(r*1.7),fract(r*3.3),1.0);}');g.compileShader(f);const p=g.createProgram();g.attachShader(p,v);g.attachShader(p,f);g.linkProgram(p);g.useProgram(p);const b=g.createBuffer();g.bindBuffer(g.ARRAY_BUFFER,b);g.bufferData(g.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,0,1]),g.STATIC_DRAW);const a=g.getAttribLocation(p,'a');g.enableVertexAttribArray(a);g.vertexAttribPointer(a,2,g.FLOAT,false,0,0);g.viewport(0,0,32,32);g.drawArrays(g.TRIANGLES,0,3);const pixels=new Uint8Array(4096);g.readPixels(0,0,32,32,g.RGBA,g.UNSIGNED_BYTE,pixels);return {version:g.getParameter(g.VERSION),shading:g.getParameter(g.SHADING_LANGUAGE_VERSION),vendor:d?g.getParameter(d.UNMASKED_VENDOR_WEBGL):g.getParameter(g.VENDOR),renderer:d?g.getParameter(d.UNMASKED_RENDERER_WEBGL):g.getParameter(g.RENDERER),limits:[g.getParameter(g.MAX_TEXTURE_SIZE),g.getParameter(g.MAX_VERTEX_ATTRIBS),g.getParameter(g.MAX_COMBINED_TEXTURE_IMAGE_UNITS)],draw:await hash(String.fromCharCode(...pixels))}}
async function audio(){const a=new OfflineAudioContext(1,8192,44100),o=a.createOscillator(),c=a.createDynamicsCompressor();o.type='triangle';o.frequency.value=10000;c.threshold.value=-50;c.knee.value=40;c.ratio.value=12;c.attack.value=0;c.release.value=.25;o.connect(c);c.connect(a.destination);o.start(0);const b=await Promise.race([a.startRendering(),new Promise((_,reject)=>setTimeout(()=>reject(Error('audio timeout')),4000))]),s=b.getChannelData(0);let sum=0,sq=0,peak=0;for(const q of s){sum+=Math.abs(q);sq+=q*q;peak=Math.max(peak,Math.abs(q))}return {length:s.length,meanAbs:sum/s.length,rms:Math.sqrt(sq/s.length),peak,head:await hash(String.fromCharCode(...new Uint8Array(b.copyFromChannel?(()=>{const q=new Float32Array(512);b.copyFromChannel(q,0);return new Uint8Array(q.buffer)})():new Uint8Array(0))))}}
async function legacy(){const c=document.createElement('canvas');c.width=300;c.height=80;const x=c.getContext('2d');x.fillStyle='#fe9021';x.fillRect(3,4,140,55);x.font='18px Arial';x.fillStyle='#126';x.fillText('Android profile 012345',12,37);const first=await hash(c.toDataURL()),second=await hash(c.toDataURL());const a=new OfflineAudioContext(1,4410,44100),o=a.createOscillator(),d=a.createDynamicsCompressor();o.type='triangle';o.frequency.value=10000;o.connect(d);d.connect(a.destination);o.start(0);const b=await Promise.race([a.startRendering(),new Promise((_,reject)=>setTimeout(()=>reject(Error('legacy audio timeout')),4000))]);let sum=0;for(const q of b.getChannelData(0))sum+=Math.abs(q);const hints=navigator.userAgentData?await navigator.userAgentData.getHighEntropyValues(['architecture','bitness','model','platformVersion']):null;return {ua:navigator.userAgent,platform:navigator.platform,ram:navigator.deviceMemory,cpu:navigator.hardwareConcurrency,touch:navigator.maxTouchPoints,coarse:matchMedia('(pointer:coarse)').matches,screen:{width:screen.width,height:screen.height,dpr:devicePixelRatio},canvas:{first,second},audio:sum,languages:navigator.languages,hints}}
let webgpu=null;try{if(navigator.gpu){const a=await Promise.race([navigator.gpu.requestAdapter(),new Promise((_,reject)=>setTimeout(()=>reject(Error('webgpu timeout')),3000))]);webgpu=a?{features:[...a.features].sort(),limits:{texture2d:a.limits.maxTextureDimension2D,bindGroups:a.limits.maxBindGroups}}:{available:false}}}catch(e){webgpu={error:e.name}}
const fonts=[];for(const name of ['Roboto','Noto Sans','Segoe UI','Arial','Calibri','Times New Roman','Droid Sans']){const c=document.createElement('canvas');c.width=240;c.height=42;const x=c.getContext('2d',{willReadFrequently:true});x.font='20px "'+name+'"';x.fillText('Fingerprint glyph 0123',1,28);fonts.push({name,width:x.measureText('Fingerprint glyph 0123').width,bitmap:await hash(String.fromCharCode(...x.getImageData(0,0,c.width,c.height).data))})}let devices=null;try{devices=(await Promise.race([navigator.mediaDevices.enumerateDevices(),new Promise((_,reject)=>setTimeout(()=>reject(Error('media timeout')),6000))])).map(x=>({kind:x.kind,label:x.label||'',id:x.deviceId||'',group:x.groupId||''}))}catch(e){devices={error:e.name}}
let storage=null;try{const e=await navigator.storage.estimate();storage={quota:e.quota||0,usage:e.usage||0}}catch(e){storage={error:e.name}}
let connection=null;try{const c=navigator.connection||navigator.mozConnection||navigator.webkitConnection;if(c)connection={type:c.type||'',effectiveType:c.effectiveType||'',rtt:c.rtt||0,downlink:c.downlink||0,saveData:!!c.saveData}}catch(e){connection={error:e.name}}
return {ua:navigator.userAgent,platform:navigator.platform,vendor:navigator.vendor,ram:navigator.deviceMemory,cpu:navigator.hardwareConcurrency,touch:navigator.maxTouchPoints,coarse:matchMedia('(pointer:coarse)').matches,screen:{width:screen.width,height:screen.height,availWidth:screen.availWidth,availHeight:screen.availHeight,dpr:devicePixelRatio,colorDepth:screen.colorDepth},canvas:await canvas(),webgl:await webgl(),webgpu,audio:await audio(),fonts,devices,storage,connection,languages:navigator.languages,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,apis:{webdriver:navigator.webdriver,cookieEnabled:navigator.cookieEnabled,pdfViewerEnabled:navigator.pdfViewerEnabled,plugins:navigator.plugins?.length,mimeTypes:navigator.mimeTypes?.length,doNotTrack:navigator.doNotTrack},hints:await navigator.userAgentData?.getHighEntropyValues(['architecture','bitness','model','platformVersion','fullVersionList','wow64'])||null,legacy:await legacy()}
})()'''


STABILITY_DIFF = r'''function novaDiffPaths(a,b,path=''){
  if(Object.is(a,b))return [];
  const ao=a!==null&&typeof a==='object',bo=b!==null&&typeof b==='object';
  const aa=Array.isArray(a),ba=Array.isArray(b);
  if(ao&&bo&&aa&&ba){
    if(a.length!==b.length)return [path||'<root>'];
    return a.flatMap((value,index)=>novaDiffPaths(value,b[index],path+'['+index+']'));
  }
  if(ao&&bo&&!aa&&!ba){
    const keys=[...new Set([...Object.keys(a),...Object.keys(b)])].sort();
    return keys.flatMap(key=>novaDiffPaths(a[key],b[key],path?path+'.'+key:key));
  }
  return [path||'<root>'];
}'''


def call(ws,method,params=None,request_id=1):
    ws.send(json.dumps({'id':request_id,'method':method,'params':params or {}}))
    while True:
        message=json.loads(ws.recv())
        if message.get('id')==request_id:
            if 'error' in message:raise RuntimeError(message['error']['message'])
            return message.get('result',{})


def _measure(ws):
    result=call(ws,'Runtime.evaluate',{'expression':MEASURE,'awaitPromise':True,'returnByValue':True})
    if 'exceptionDetails' in result:raise RuntimeError('Fingerprint measurement failed: '+str(result['exceptionDetails'].get('text','')))
    return result['result']['value']


def _read_exact(sock,size):
    data=b''
    while len(data)<size:
        chunk=sock.recv(size-len(data))
        if not chunk:raise ValueError('incomplete TLS record')
        data+=chunk
    return data


def _hide_probe_window(pid):
    """Hide the temporary headed Hidemium window while its probe runs."""
    if not pid or not hasattr(ctypes, 'windll'):
        return
    user32=ctypes.windll.user32
    callback_type=ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL,ctypes.wintypes.HWND,ctypes.wintypes.LPARAM)
    def visit(hwnd,_):
        owner=ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
        if owner.value==pid and user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd,0)
        return True
    callback=callback_type(visit)
    user32.EnumWindows(callback,0)


def _client_hello(data):
    if len(data)<4 or data[0]!=1:raise ValueError('not a TLS ClientHello')
    pos=4+2+32
    session=data[pos];pos+=1+session
    cipher_len=int.from_bytes(data[pos:pos+2],'big');pos+=2
    ciphers=[int.from_bytes(data[i:i+2],'big') for i in range(pos,pos+cipher_len,2)];pos+=cipher_len
    compression=data[pos];compressions=list(data[pos+1:pos+1+compression]);pos+=1+compression
    if pos==len(data):extensions=[]
    else:
        total=int.from_bytes(data[pos:pos+2],'big');pos+=2;end=pos+total;extensions=[]
        while pos+4<=end:
            kind=int.from_bytes(data[pos:pos+2],'big');length=int.from_bytes(data[pos+2:pos+4],'big');pos+=4
            ext=data[pos:pos+length];pos+=length
            if kind&0x0f0f==0x0a0a:continue
            value={'id':kind}
            if kind in (10,13,43):
                offset=1 if kind==43 else 2
                if len(ext)>=offset:
                    width=ext[0] if offset==1 else int.from_bytes(ext[:2],'big')
                    value['values']=[v for v in (int.from_bytes(ext[i:i+2],'big') for i in range(offset,min(offset+width,len(ext)),2)) if v&0x0f0f!=0x0a0a]
            elif kind==16 and len(ext)>=2:
                length=int.from_bytes(ext[:2],'big');cur=2;items=[]
                while cur+1<=min(len(ext),length+2):
                    n=ext[cur];cur+=1;items.append(ext[cur:cur+n].decode('ascii','replace'));cur+=n
                value['values']=items
            extensions.append(value)
    filtered=[v for v in ciphers if v&0x0f0f!=0x0a0a]
    # Chromium randomizes extension order between handshakes. Preserve the
    # advertised set and values, but canonicalize that unstable order.
    summary={'legacy_version':int.from_bytes(data[4:6],'big'),'ciphers':filtered,'compressions':compressions,'extensions':sorted(extensions,key=lambda item:item['id'])}
    return {'summary':summary,'signature':hashlib.sha256(json.dumps(summary,sort_keys=True,separators=(',',':')).encode()).hexdigest()}


class _TLSHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.request.settimeout(3);header=_read_exact(self.request,5)
            if header[0]!=22:return
            record=_read_exact(self.request,int.from_bytes(header[3:5],'big'))
            if len(record)>=4 and record[0]==1:
                parsed=_client_hello(record)
                if len(self.server.samples)<4:
                    self.server.samples.append(parsed)
                    if not self.server.capture:self.server.capture.update(parsed)
                    self.server.captured.set()
        except (OSError,ValueError,IndexError):pass


class _TLSServer(socketserver.ThreadingTCPServer):
    allow_reuse_address=True;daemon_threads=True
    def __init__(self):
        self.capture={};self.samples=[];self.captured=threading.Event();super().__init__(('127.0.0.1',0),_TLSHandler)


def _measure_contexts(ws,main):
    token='nova-'+str(time.time_ns())
    child=f"void(async()=>{{parent.postMessage({{token:{json.dumps(token)},kind:'frame',value:await ({MEASURE})}},'*')}})()"
    html='<!doctype html><script>'+child.replace('</script>','<\\/script>')+'</script>'
    worker="""self.onmessage=async()=>{let hints=null;try{hints=await navigator.userAgentData?.getHighEntropyValues(['architecture','bitness','model','platformVersion'])||null}catch{};let canvas=null;try{const c=new OffscreenCanvas(32,32),g=c.getContext('webgl2')||c.getContext('webgl');canvas=!!g}catch{};postMessage({ua:navigator.userAgent,platform:navigator.platform,hints,offscreenWebGL:canvas})}"""
    expression="""(async()=>{const token=TOKEN;const values={};let finish;const done=new Promise(r=>finish=r);const listener=e=>{if(e.data?.token===token){values[e.data.kind]=e.data.value;if(values.frame)finish()}};addEventListener('message',listener);const frame=document.createElement('iframe');frame.srcdoc=HTML;document.body.append(frame);const timer=setTimeout(()=>finish(),8000);await done;clearTimeout(timer);removeEventListener('message',listener);let worker=null;try{const w=new Worker(URL.createObjectURL(new Blob([WORKER],{type:'text/javascript'})));worker=await new Promise(resolve=>{w.onmessage=e=>{w.terminate();resolve(e.data)};w.onerror=()=>{w.terminate();resolve(null)};setTimeout(()=>{w.terminate();resolve(null)},5000);w.postMessage('measure')})}catch{}return {frame:values.frame||null,worker}})()"""
    expression=expression.replace('TOKEN',json.dumps(token)).replace('HTML',json.dumps(html)).replace('WORKER',json.dumps(worker))
    result=call(ws,'Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True,'timeout':15000})
    if 'exceptionDetails' in result:raise RuntimeError('Context probe failed: '+str(result['exceptionDetails'].get('text','')))
    contexts=result['result']['value'] or {}
    frame=contexts.get('frame');worker_result=contexts.get('worker')
    if frame and (frame.get('ua')!=main.get('ua') or frame.get('hints')!=main.get('hints')):raise ValueError('UA/UA-CH khong nhat quan giua trang va iframe')
    if worker_result and (worker_result.get('ua')!=main.get('ua') or worker_result.get('platform')!=main.get('platform')):raise ValueError('UA/platform khong nhat quan giua trang va worker')
    if worker_result and worker_result.get('hints') is not None and worker_result.get('hints')!=main.get('legacy',{}).get('hints'):raise ValueError('UA-CH khong nhat quan giua trang va worker')
    return contexts


def _probe_document(tls_port):
    token='nova-'+str(time.time_ns())
    frame_measure="""(async()=>{const hints=await navigator.userAgentData?.getHighEntropyValues(['architecture','bitness','model','platformVersion','fullVersionList','wow64'])||null;const c=document.createElement('canvas');c.width=96;c.height=32;const x=c.getContext('2d');x.font='16px Arial';x.fillText('NOVA iframe probe',1,22);const data=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(c.toDataURL()));return {ua:navigator.userAgent,platform:navigator.platform,hints,screen:[screen.width,screen.height,devicePixelRatio],canvas:[...new Uint8Array(data)].map(v=>v.toString(16).padStart(2,'0')).join('')}})()"""
    frame_script=f"void(async()=>{{parent.postMessage({{token:{json.dumps(token)},kind:'frame',value:await ({frame_measure})}},'*')}})()"
    frame_html='<!doctype html><script>'+frame_script+'</script>'
    frame_literal=json.dumps(frame_html).replace('</script>','<\\/script>')
    worker="self.onmessage=async()=>{let hints=null;try{hints=await navigator.userAgentData?.getHighEntropyValues(['architecture','bitness','model','platformVersion'])||null}catch{};let webgl=false;try{const c=new OffscreenCanvas(32,32);webgl=!!(c.getContext('webgl2')||c.getContext('webgl'))}catch{};postMessage({ua:navigator.userAgent,platform:navigator.platform,hints,offscreenWebGL:webgl})}"
    script=f"""window.novaProbePromise=(async()=>{{{STABILITY_DIFF}const main=await ({MEASURE});const repeat=await ({MEASURE});const changed=novaDiffPaths(main,repeat).filter(path=>path!=='devices'&&!path.startsWith('devices.')&&path!=='storage.usage');if(changed.length)throw Error('unstable runtime probe fields: '+changed.slice(0,24).join(','));const token={json.dumps(token)};let frame=null;try{{frame=await new Promise(resolve=>{{const listener=e=>{{if(e.data?.token===token){{removeEventListener('message',listener);resolve(e.data.value)}}}};addEventListener('message',listener);const f=document.createElement('iframe');f.srcdoc={frame_literal};document.body.append(f);setTimeout(()=>{{removeEventListener('message',listener);resolve(null)}},8000)}})}}catch{{}}
let workerResult=null;try{{const w=new Worker(URL.createObjectURL(new Blob([{json.dumps(worker)}],{{type:'text/javascript'}})));workerResult=await new Promise(resolve=>{{w.onmessage=e=>{{w.terminate();resolve(e.data)}};w.onerror=()=>{{w.terminate();resolve(null)}};setTimeout(()=>{{w.terminate();resolve(null)}},5000);w.postMessage('probe')}})}}catch{{}}
if(frame&&(frame.ua!==main.ua||JSON.stringify(frame.hints)!==JSON.stringify(main.hints)))throw Error('UA/UA-CH mismatch in iframe');if(workerResult&&(workerResult.ua!==main.ua||workerResult.platform!==main.platform))throw Error('UA/platform mismatch in worker');if(workerResult?.hints&&JSON.stringify(workerResult.hints)!==JSON.stringify(main.legacy?.hints))throw Error('UA-CH mismatch in worker');
main.contexts={{frame,worker:workerResult}};for(let i=0;i<4;i++){{try{{await Promise.race([fetch('https://localhost:{tls_port}/'+i,{{mode:'no-cors'}}).catch(()=>null),new Promise(r=>setTimeout(r,1000))])}}catch{{}}}}document.getElementById('nova-result').textContent=JSON.stringify(main);return main}})();window.novaProbePromise.catch(e=>document.getElementById('nova-result').textContent=JSON.stringify({{probe_error:String(e?.message||e)}}));"""
    return '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><pre id="nova-result"></pre><script>'+script+'</script>'


def probe(executable,config,directory,mobile_commands=()):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    (directory/'config.hidemium').write_text(encode_config(config),encoding='utf8')
    log_path=directory/'probe.log';log=log_path.open('wb');tls_server=_TLSServer();tls_thread=threading.Thread(target=tls_server.serve_forever,daemon=True);tls_thread.start()
    process=None;ws=None
    try:
        page=directory/'probe.html';page.write_text(_probe_document(tls_server.server_address[1]),encoding='utf8')
        # The bundled Hidemium core may create a headed browser without writing
        # DevToolsActivePort for port=0. Reserve a loopback port and poll the
        # documented HTTP discovery endpoint directly instead.
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as reservation:
            reservation.bind(('127.0.0.1',0));debug_port=reservation.getsockname()[1]
        process=subprocess.Popen([executable,'--no-sandbox','--enable-hidemium-config','--user-data-dir='+str(directory.resolve()),'--remote-debugging-port='+str(debug_port),'--remote-debugging-address=127.0.0.1','--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-component-update','--disable-sync','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1','--no-proxy-server','--window-position=-32000,-32000','--window-size=1280,720','about:blank'],stdout=log,stderr=log)
        deadline=time.monotonic()+20;pages=[]
        while True:
            _hide_probe_window(process.pid)
            if time.monotonic()>deadline:
                error=RuntimeError('Browser DevTools discovery timed out')
                error.safe_diagnostic={'stage':'browser_startup','exit_code':process.poll(),'devtools_port':debug_port,'devtools_http_ready':False,'mode':'headed_hidden','timeout_seconds':20}
                raise error
            try:
                with urlopen(f'http://127.0.0.1:{debug_port}/json/list',timeout=1) as response:targets=json.load(response)
                pages=[t for t in targets if t['type']=='page']
                if pages:break
            except (OSError,ValueError,TimeoutError):pass
            time.sleep(.1)
        _hide_probe_window(process.pid)
        ws=websocket.create_connection(pages[0]['webSocketDebuggerUrl'],timeout=30,suppress_origin=True)
        for method,params in mobile_commands:call(ws,method,params)
        call(ws,'Page.navigate',{'url':page.resolve().as_uri()})
        # Read the explicit completion record written by the probe page. Some
        # Hidemium CDP builds return a Promise remote object even when
        # awaitPromise is requested, so relying on Runtime.evaluate's Promise
        # result can reject a successful measurement as an invalid value.
        observed=None;probe_error=None;deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            response=call(ws,'Runtime.evaluate',{'expression':'document.getElementById("nova-result")?.textContent||""','returnByValue':True})
            text=response.get('result',{}).get('value')
            if isinstance(text,str) and text:
                try:measurement=json.loads(text)
                except (TypeError,ValueError):measurement=None
                if isinstance(measurement,dict):
                    if isinstance(measurement.get('probe_error'),str):
                        probe_error=measurement['probe_error'][:300]
                    else:observed=measurement
                    break
            time.sleep(.15)
        if probe_error:
            error=RuntimeError('Runtime JavaScript probe failed')
            error.safe_diagnostic={'stage':'javascript','probe_error':probe_error}
            raise error
        if not isinstance(observed,dict):
            error=RuntimeError('Browser probe did not publish measurements')
            error.safe_diagnostic={'stage':'measurement_result','dom_result_ready':False,'timeout_seconds':35}
            raise error
        tls_server.captured.wait(4)
        samples=tls_server.samples
        observed['tls']=tls_server.capture if len(samples)>=2 and len({x['signature'] for x in samples})==1 else None
        return observed
    except Exception as exc:
        try:
            log.flush()
            with log_path.open('rb') as stream:
                stream.seek(max(0,log_path.stat().st_size-12000));tail=stream.read()
            safe=getattr(exc,'safe_diagnostic',None)
            header=('probe_failure='+json.dumps(safe,ensure_ascii=False)+'\n' if isinstance(safe,dict) else
                    'probe_failure='+type(exc).__name__+'\n')
            if process is not None:header+='browser_exit_code='+str(process.poll())+'\n'
            failure=directory.parent/('probe-failure-'+directory.name+'.log')
            failure.write_bytes(header.encode('utf8')+tail)
            setattr(exc,'safe_diagnostic',{**(safe if isinstance(safe,dict) else {'stage':'probe'}),
                    'log_file':failure.name})
            previous=sorted(directory.parent.glob('probe-failure-v_*.log'),key=lambda p:p.stat().st_mtime,reverse=True)
            for old in previous[5:]:old.unlink(missing_ok=True)
        except (OSError,ValueError):pass
        raise
    finally:
        if ws:ws.close()
        if process and process.poll() is None:
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
            if process.poll() is None:process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:pass
        log.close();tls_server.shutdown();tls_server.server_close();tls_thread.join(timeout=2)
