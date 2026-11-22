"""User-enabled synchronization of the first page in selected local profiles."""
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit
from urllib.request import urlopen

import websocket
from runtime_probe import call

CAPTURE=r'''(()=>{if(window.__localProfileSync)return;window.__localProfileSync=true;
const send=data=>{try{window.localProfilesSync(JSON.stringify(data))}catch{}};
let down=false,lastMove=0,lastScroll=0;
for(const type of ['pointerdown','pointermove','pointerup','pointercancel'])addEventListener(type,e=>{
if(!e.isTrusted)return;if(type==='pointerdown')down=true;if(type==='pointermove'&&(!down||Date.now()-lastMove<24))return;
lastMove=Date.now();send({kind:'pointer',phase:type,x:e.clientX/innerWidth,y:e.clientY/innerHeight});
if(type==='pointerup'||type==='pointercancel')down=false;},true);
addEventListener('input',e=>{if(!e.isTrusted)return;const t=e.target;
if(typeof t.value==='string'&&t.value.length<65536)send({kind:'input',value:t.value,start:t.selectionStart,end:t.selectionEnd});
else if(t.isContentEditable&&t.textContent.length<65536)send({kind:'editable',value:t.textContent});},true);
for(const type of ['keydown','keyup'])addEventListener(type,e=>{if(!e.isTrusted||e.isComposing)return;
if(e.key.length>1&&!['Shift','Control','Alt','Meta','Backspace','Delete'].includes(e.key))send({kind:'key',phase:type,key:e.key,code:e.code,keyCode:e.keyCode,modifiers:(e.altKey?1:0)|(e.ctrlKey?2:0)|(e.metaKey?4:0)|(e.shiftKey?8:0)});},true);
addEventListener('scroll',()=>{if(Date.now()-lastScroll<50)return;lastScroll=Date.now();const e=document.scrollingElement;
send({kind:'scroll',x:e.scrollLeft/Math.max(1,e.scrollWidth-e.clientWidth),y:e.scrollTop/Math.max(1,e.scrollHeight-e.clientHeight)});},true);
})()'''


def page_target(directory):
    port=int((Path(directory)/'DevToolsActivePort').read_text().splitlines()[0])
    with urlopen(f'http://127.0.0.1:{port}/json/list',timeout=4) as response:targets=json.load(response)
    pages=[t for t in targets if t['type']=='page']
    if not pages:raise ValueError('Profile chua co tab de dong bo')
    return pages[0]


class BrowserSync:
    def __init__(self,master,followers,root):
        self.master=master;self.followers=list(followers);self.root=Path(root)
        self.stopped=threading.Event();self.sockets=[]
        self.state={'running':False,'master':master,'followers':followers,'events':0,'error':None}
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()

    def _run(self):
        try:
            target=page_target(self.root/self.master)
            master=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=3,suppress_origin=True)
            self.sockets.append(master)
            for pid in self.followers:
                other=page_target(self.root/pid)
                ws=websocket.create_connection(other['webSocketDebuggerUrl'],timeout=3,suppress_origin=True)
                self.sockets.append(ws)
                if target['url']!=other['url'] and urlsplit(target['url']).scheme in ('http','https'):
                    call(ws,'Page.navigate',{'url':target['url']})
            call(master,'Runtime.addBinding',{'name':'localProfilesSync'})
            call(master,'Page.addScriptToEvaluateOnNewDocument',{'source':CAPTURE})
            call(master,'Page.enable');call(master,'Runtime.enable')
            call(master,'Runtime.evaluate',{'expression':CAPTURE})
            self.state['running']=True;master.settimeout(.5)
            while not self.stopped.is_set():
                try:event=json.loads(master.recv())
                except websocket.WebSocketTimeoutException:continue
                method=event.get('method');params=event.get('params',{})
                if method=='Runtime.bindingCalled' and params.get('name')=='localProfilesSync':
                    data=json.loads(params['payload'])
                    for ws in self.sockets[1:]:self.forward(ws,data)
                    self.state['events']+=1
                elif method=='Page.frameNavigated' and not params.get('frame',{}).get('parentId'):
                    url=params['frame'].get('url','')
                    if urlsplit(url).scheme in ('http','https'):
                        for ws in self.sockets[1:]:call(ws,'Page.navigate',{'url':url})
        except Exception as e:
            if not self.stopped.is_set():self.state['error']=str(e)
        finally:
            self.state['running']=False
            for ws in self.sockets:
                try:ws.close()
                except Exception:pass

    @staticmethod
    def forward(ws,data):
        kind=data.get('kind')
        if kind=='pointer':
            size=call(ws,'Runtime.evaluate',{'expression':'({w:innerWidth,h:innerHeight})','returnByValue':True})['result']['value']
            phase={'pointerdown':'touchStart','pointermove':'touchMove','pointerup':'touchEnd','pointercancel':'touchCancel'}[data['phase']]
            points=[] if phase in ('touchEnd','touchCancel') else [{'x':max(0,min(.999,float(data['x'])))*size['w'],
                'y':max(0,min(.999,float(data['y'])))*size['h'],'id':0,'radiusX':1,'radiusY':1,'force':1}]
            call(ws,'Input.dispatchTouchEvent',{'type':phase,'touchPoints':points})
        elif kind in ('input','editable'):
            value=str(data.get('value',''))[:65536]
            expression='''(()=>{const e=document.activeElement;if(!e)return;const v=VALUE;
if('value' in e){const proto=e instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
const set=Object.getOwnPropertyDescriptor(proto,'value')?.set;if(set)set.call(e,v);else e.value=v;
try{e.setSelectionRange(START,END)}catch{};e.dispatchEvent(new InputEvent('input',{bubbles:true,composed:true,inputType:'insertText',data:v}));
}else if(e.isContentEditable){e.textContent=v;e.dispatchEvent(new InputEvent('input',{bubbles:true,composed:true}));}})()'''
            expression=expression.replace('START',json.dumps(data.get('start'))).replace('END',json.dumps(data.get('end'))).replace('VALUE',json.dumps(value))
            call(ws,'Runtime.evaluate',{'expression':expression})
        elif kind=='key':
            call(ws,'Input.dispatchKeyEvent',{'type':'keyDown' if data['phase']=='keydown' else 'keyUp',
                'key':str(data['key'])[:40],'code':str(data.get('code',''))[:40],
                'windowsVirtualKeyCode':int(data.get('keyCode',0)),'modifiers':int(data.get('modifiers',0))})
        elif kind=='scroll':
            x=max(0,min(1,float(data['x'])));y=max(0,min(1,float(data['y'])))
            call(ws,'Runtime.evaluate',{'expression':f'(()=>{{const e=document.scrollingElement;e.scrollTo({x}*(e.scrollWidth-e.clientWidth),{y}*(e.scrollHeight-e.clientHeight))}})()'})

    def close(self):
        self.stopped.set();self.thread.join(timeout=6)
        for ws in self.sockets:
            try:ws.close()
            except Exception:pass
