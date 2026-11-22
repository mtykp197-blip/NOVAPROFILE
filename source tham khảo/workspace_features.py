import copy
import ctypes
from ctypes import wintypes
import json
import math
import re
import secrets
import threading
import time
from urllib.parse import unquote,urlsplit

import websocket
from browser_sync import BrowserSync,page_target
from runtime_probe import call
from public_errors import record_error


def parse_proxies(text):
    result=[]
    for raw in text.splitlines():
        raw=raw.strip()
        if not raw:continue
        if '://' in raw:
            uri=urlsplit(raw);mode=uri.scheme.upper()
            p={'type':mode,'ip':uri.hostname,'port':uri.port,'user':unquote(uri.username or ''),'pass':unquote(uri.password or '')}
        else:
            pieces=raw.split(':',3)
            if len(pieces) not in (2,4):raise ValueError('Proxy phai la host:port[:user:pass] hoac URL HTTP/SOCKS5')
            p={'type':'HTTP','ip':pieces[0],'port':int(pieces[1]),'user':pieces[2] if len(pieces)==4 else '',
               'pass':pieces[3] if len(pieces)==4 else ''}
        if p['type'] not in ('HTTP','SOCKS5') or not p['ip'] or not p['port'] or not 1<=p['port']<=65535:
            raise ValueError('Proxy khong hop le')
        result.append(p)
    if not result:raise ValueError('Chua nhap proxy')
    return result


class WorkspaceFeatures:
    def init_workspace(self):
        self.jobs={};self.sync=None

    def groups(self):
        path=self.root/'groups.json'
        return json.loads(path.read_text(encoding='utf8')) if path.exists() else []

    def create_group(self,name):
        from desktop_backend import atomic
        name=str(name).strip()[:100]
        if not name:raise ValueError('Thieu ten list')
        with self.lock:
            groups=self.groups();row={'id':'g_'+secrets.token_hex(8),'name':name};groups.append(row)
            atomic(self.root/'groups.json',groups)
            return row

    def validate_group(self,gid):
        if gid and gid not in {g['id'] for g in self.groups()}:raise ValueError('Khong tim thay list')

    def start_bulk(self,body):
        ids=list(dict.fromkeys(body.get('ids',[])));action=body.get('action')
        if action=='create':
            count=int(body.get('count',1))
            if not 1<=count<=1000:raise ValueError('Moi dot tao tu 1 den 1000 profile')
            ids=list(range(count))
        elif not ids:raise ValueError('Chua chon profile')
        if action!='create':
            for pid in ids:self.get(pid)
        if action not in ('create','launch','stop','delete','group','proxy','rotate'):raise ValueError('Thao tac hang loat khong hop le')
        proxies=None
        if action=='proxy':
            proxies=parse_proxies(body.get('proxies',''))
            if len(proxies)<len(ids) and not body.get('repeat'):raise ValueError('So proxy it hon so profile; bat lap lai neu muon dung chung')
        if action=='group':self.validate_group(body.get('group_id'))
        job_id='j_'+secrets.token_hex(10)
        job={'id':job_id,'action':action,'total':len(ids),'done':0,'ok':[],'errors':[],'finished':False,'cancelled':False}
        self.jobs[job_id]=job
        def run():
            from desktop_backend import atomic
            try:
                for index,pid in enumerate(ids):
                    if job['cancelled']:break
                    try:
                        if action=='create':
                            item=copy.deepcopy(body);item['name']=str(body.get('name') or 'Android')+' '+str(index+1)
                            pid=self.create(item)['id']
                        elif action=='launch':self.launch(pid)
                        elif action=='stop':self.stop(pid)
                        elif action=='delete':self.delete(pid)
                        elif action=='rotate':self.rotate(pid)
                        else:
                            with self.lock:
                                if self.status(pid)['running']:raise ValueError('Dung profile truoc khi doi list/proxy')
                                p=self.get(pid)
                                if action=='group':p['group_id']=body.get('group_id') or None
                                if action=='proxy':p['proxy']=copy.deepcopy(proxies[index%len(proxies)]);self.validate(p)
                                atomic(self.path(pid),p)
                        job['ok'].append(pid)
                    except Exception as e:job['errors'].append({'id':pid,**record_error(self.root,e)})
                    job['done']+=1
                    if action=='launch':time.sleep(.2)
            finally:job['finished']=True
        threading.Thread(target=run,daemon=True).start()
        return copy.deepcopy(job)

    def arrange(self,ids):
        rect=wintypes.RECT()
        if not ctypes.windll.user32.SystemParametersInfoW(0x30,0,ctypes.byref(rect),0):
            raise ValueError('Khong doc duoc kich thuoc man hinh')
        ids=[pid for pid in ids if self.status(pid)['running']]
        if not ids:raise ValueError('Chua co profile dang chay')
        width=rect.right-rect.left;height=rect.bottom-rect.top
        columns=max(1,math.ceil(math.sqrt(len(ids)*width/height*.55)));rows=math.ceil(len(ids)/columns)
        cell_width=width//columns;cell_height=height//rows;result=[]
        for index,pid in enumerate(ids):
            target=page_target(self.root/'user-data'/pid)
            ws=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=4,suppress_origin=True)
            try:
                window_id=call(ws,'Browser.getWindowForTarget',{'targetId':target['id']})['windowId']
                bounds={'windowState':'normal'}
                call(ws,'Browser.setWindowBounds',{'windowId':window_id,'bounds':bounds})
                bounds={'left':rect.left+(index%columns)*cell_width,'top':rect.top+(index//columns)*cell_height,
                        'width':cell_width,'height':cell_height}
                call(ws,'Browser.setWindowBounds',{'windowId':window_id,'bounds':bounds});result.append({'id':pid,'bounds':bounds})
                controller=self.sessions[pid].get('touch')
                if controller:controller.set_display_size(cell_width,cell_height)
            finally:ws.close()
        return {'arranged':result}

    def start_sync(self,ids,master=None):
        ids=list(dict.fromkeys(ids))
        if len(ids)<2:raise ValueError('Chon it nhat 2 profile dang chay')
        for pid in ids:
            self.get(pid)
            if not self.status(pid)['running']:raise ValueError('Mo tat ca profile truoc khi dong bo')
        master=master or ids[0]
        if master not in ids:raise ValueError('Cua so chinh phai nam trong nhom da chon')
        self.stop_sync();self.sync=BrowserSync(master,[pid for pid in ids if pid!=master],self.root/'user-data')
        return self.sync.state

    def stop_sync(self):
        if self.sync:self.sync.close();self.sync=None
        return {'running':False}
