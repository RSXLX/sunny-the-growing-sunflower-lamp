"""Single-machine MVP HTTP server using Python's standard-library HTTP stack.
Run behind a maintained HTTPS reverse proxy for any non-local exposure; this is
not a hardened public SaaS deployment. Authentication and object ownership are real.
"""
from __future__ import annotations
import argparse, http.cookies, ipaddress, json, mimetypes, os, re, secrets, sqlite3, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from .service import APIError, Service, ROOT, text, theme
from .store import uid, stamp, hash_secret, verify_password

MAX_BODY=256*1024

def load_env(path):
    if path.exists():
        for line in path.read_text().splitlines():
            line=line.strip()
            if not line or line.startswith('#') or '=' not in line:continue
            key,value=line.split('=',1);os.environ.setdefault(key.strip(),value.strip().strip('"').strip("'"))

class BloomServer(ThreadingHTTPServer):
    daemon_threads=True;allow_reuse_address=True
    def __init__(self,address,service):
        self.service=service;self.limits={};self.limit_lock=threading.Lock()
        super().__init__(address,Handler)

class Handler(BaseHTTPRequestHandler):
    server_version='BloomMVP/1.0'
    protocol_version='HTTP/1.1'
    def setup(self):
        super().setup();self.connection.settimeout(20)
    @property
    def service(self):return self.server.service
    def log_message(self,format,*args):
        # Do not log tokens, body, or full URLs that could contain user information.
        if os.getenv('QUIET','0')!='1':sys.stderr.write(f'{self.client_address[0]} {self.command} {self.path.split("?")[0][:130]}\n')
    def send(self,status=200,data=None,body=None,kind='application/json; charset=utf-8',headers=None):
        if body is None:body=json.dumps(data if data is not None else {},ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Cache-Control','no-store' if self.path.startswith('/api/') else 'no-cache')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers()
        if self.command!='HEAD':self.wfile.write(body)
    def body(self,max_bytes=MAX_BODY):
        if hasattr(self,'_parsed_body'):return self._parsed_body
        try:n=int(self.headers.get('Content-Length','0'))
        except ValueError:raise APIError(400,'无效的请求长度')
        if not 0<=n<=max_bytes:
            self.close_connection=True
            raise APIError(413,'请求太大')
        if self.headers.get('Transfer-Encoding'):raise APIError(400,'不支持 chunked 请求')
        if n and 'application/json' not in self.headers.get('Content-Type',''):raise APIError(415,'API 请求必须使用 JSON')
        try:result=json.loads(self.rfile.read(n) or b'{}')
        except (json.JSONDecodeError,UnicodeDecodeError):raise APIError(400,'JSON 格式无效')
        if not isinstance(result,dict):raise APIError(400,'请求体必须为 JSON 对象')
        return result
    def security(self):
        host=self.headers.get('Host','')
        try:hostname=urlparse('http://'+host).hostname or ''
        except ValueError:raise APIError(400,'Host 无效')
        allowed=hostname in {'localhost','127.0.0.1','::1'}
        if not allowed and os.getenv('ALLOW_LAN','0')=='1':
            try:allowed=ipaddress.ip_address(hostname).is_private
            except ValueError:allowed=hostname in os.getenv('ALLOWED_HOSTS','').split(',')
        if not allowed:raise APIError(403,'Host 未授权；局域网调试需显式启用 ALLOW_LAN')
        origin=self.headers.get('Origin')
        if self.command in {'POST','PUT','PATCH','DELETE'} and origin and urlparse(origin).netloc!=host:raise APIError(403,'跨站写入被拒绝')
    def auth(self,write=False):
        cookies=http.cookies.SimpleCookie()
        try:cookies.load(self.headers.get('Cookie',''))
        except http.cookies.CookieError:raise APIError(401,'请重新登录')
        token=cookies.get('bloom_session')
        if not token:raise APIError(401,'请先登录')
        row=self.service.store.one('SELECT s.*,u.name,u.email FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?',(hash_secret(token.value),stamp()))
        if not row:raise APIError(401,'登录已失效')
        if write and not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),row['csrf']):raise APIError(403,'CSRF 校验失败，请刷新后重试')
        return row
    def rate_limit(self):
        key=self.client_address[0]
        with self.server.limit_lock:
            recent=[t for t in self.server.limits.get(key,[]) if t>time.monotonic()-60]
            if len(recent)>=12:raise APIError(429,'登录请求过于频繁，请稍后重试')
            recent.append(time.monotonic());self.server.limits[key]=recent
    def dispatch(self):
        self.security();path=unquote(urlparse(self.path).path).rstrip('/') or '/';method=self.command
        # Consume even nominally empty mutation bodies before reusing HTTP/1.1.
        if method in {'POST','PATCH','DELETE','PUT'}:
            limit=17*1024*1024 if re.fullmatch(r'/api/designs/[a-f0-9]{32}/versions',path) and method=='POST' else MAX_BODY
            if re.fullmatch(r'/api/instances/[a-f0-9]{32}/(records|stage)',path) and method=='POST':limit=9*1024*1024
            if path=='/api/reference-assets' and method=='POST':limit=12*1024*1024
            self._parsed_body=self.body(limit)
        s=self.service
        if path=='/api/health' and method=='GET':return self.send(data={'ok':True,'version':'1.0.0','mode':'local-demo' if s.demo else 'local-real'})
        if path=='/api/auth/login' and method=='POST':
            self.rate_limit();p=self.body();email=text(p.get('email'),'邮箱',3,160).lower();password=text(p.get('password'),'密码',1,200)
            user=s.store.one('SELECT * FROM users WHERE email=?',(email,))
            if not user or not verify_password(password,user['password']):raise APIError(401,'邮箱或密码错误')
            token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(24)
            s.store.execute('DELETE FROM sessions WHERE expires<=?',(stamp(),))
            s.store.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hash_secret(token),user['id'],csrf,stamp()+7*86400))
            cookie=f'bloom_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800'
            if os.getenv('COOKIE_SECURE')=='1':cookie+='; Secure'
            return self.send(data={'user':{'id':user['id'],'name':user['name'],'email':user['email']},'csrf':csrf},headers={'Set-Cookie':cookie})
        if path=='/api/auth/register' and method=='POST':
            self.rate_limit();p=self.body();email=text(p.get('email'),'邮箱',5,160).lower();name=text(p.get('name'),'名字',1,40);password=text(p.get('password'),'密码',10,200)
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise APIError(400,'邮箱格式错误')
            if s.store.one('SELECT id FROM users WHERE email=?',(email,)):raise APIError(409,'邮箱已存在')
            owner=s.store.user(email,name,password)
            if s.demo:s.store.device(owner,name+'的灯','simulator')
            return self.send(201,{'id':owner})
        if path=='/api/device/poll' and method=='POST':
            token=self.headers.get('Authorization','')
            if not token.startswith('Bearer '):raise APIError(401,'需要设备 Bearer token')
            device=s.device_auth(token[7:]);return self.send(data=s.poll_device(device,self.body()))
        if path.startswith('/api/'):
            session=self.auth(write=method not in {'GET','HEAD'});owner=session['user_id']
            if path=='/api/auth/me' and method=='GET':return self.send(data={'user':{'id':owner,'name':session['name'],'email':session['email']},'csrf':session['csrf']})
            if path=='/api/auth/logout' and method=='POST':
                s.store.execute('DELETE FROM sessions WHERE token=?',(session['token'],));return self.send(data={'ok':True},headers={'Set-Cookie':'bloom_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/'})
            if path=='/api/state' and method=='GET':return self.send(data=s.snapshot(owner))
            if path=='/api/reference-assets':
                if method=='GET':return self.send(data={'assets':s.references.list(owner)})
                if method=='POST':return self.send(201,s.references.create(owner,self.body()))
            match=re.fullmatch(r'/api/reference-assets/([a-f0-9]{32})(/content)?',path)
            if match:
                if method=='GET' and match[2]:return self.send(body=s.references.content(owner,match[1]),kind='image/png')
                if method=='GET':return self.send(data=s.references.get(owner,match[1]))
                if method=='DELETE' and not match[2]:return self.send(data=s.references.delete(owner,match[1]))
            if path=='/api/memories' and method=='POST':return self.send(201,s.create_memory(owner,self.body()))
            match=re.fullmatch(r'/api/memories/([a-f0-9]{32})',path)
            if match:
                id=match[1];s.own('memories',id,owner)
                if method=='DELETE':s.store.execute('DELETE FROM memories WHERE id=?',(id,));return self.send(data={'ok':True})
                if method=='PATCH':return self.send(data=s.edit_memory(owner,id,self.body()))
            if path=='/api/designs' and method=='POST':return self.send(202,s.create_design(owner,self.body(),self.headers.get('Idempotency-Key','')))
            match=re.fullmatch(r'/api/designs/([a-f0-9]{32})/versions',path)
            if match:
                if method=='GET':return self.send(data={'versions':s.manufacturing.list(owner,match[1])})
                if method=='POST':return self.send(201,s.manufacturing.create(owner,match[1],self.body(17*1024*1024)))
            match=re.fullmatch(r'/api/versions/([a-f0-9]{32})(?:/(submit|review|package|asset/[^/]+))?',path)
            if match:
                version_id,action=match.groups()
                if action is None and method=='GET':return self.send(data=s.manufacturing.get(owner,version_id))
                if action in {'submit','review'} and method=='POST':
                    payload=self.body()
                    if action=='submit':payload['action']='submit'
                    return self.send(data=s.manufacturing.review(owner,version_id,payload))
                if action=='package' and method=='GET':return self.send(body=s.manufacturing.package(owner,version_id),kind='application/zip',headers={'Content-Disposition':f'attachment; filename="bloom-version-{version_id[:8]}.zip"'})
                if action and action.startswith('asset/') and method=='GET':
                    name=action.split('/')[1]
                    return self.send(body=s.manufacturing.file(owner,version_id,name),kind=mimetypes.guess_type(name)[0] or 'application/octet-stream')
            match=re.fullmatch(r'/api/designs/([a-f0-9]{32})(?:/(review|publish|recover|favorite|package|asset/[^/]+))?',path)
            if match:
                id,action=match[1],match[2]
                if not action and method=='GET':return self.send(data=s.design(id,owner))
                if action=='review' and method=='POST':return self.send(data=s.review_design(owner,id,self.body()))
                if action=='publish' and method=='POST':return self.send(data=s.publish_design(owner,id,self.body()))
                if action=='recover' and method=='POST':return self.send(data=s.recover_task(owner,id,self.body()))
                if action=='favorite' and method=='POST':
                    s.design(id,owner);p=self.body()
                    if p.get('favorite') is True:s.store.execute('INSERT OR IGNORE INTO favorites VALUES(?,?)',(owner,id))
                    else:s.store.execute('DELETE FROM favorites WHERE owner=? AND design_id=?',(owner,id))
                    return self.send(data={'ok':True})
                if action=='package' and method=='GET':return self.send(body=s.manufacture_package(owner,id),kind='application/zip',headers={'Content-Disposition':f'attachment; filename="bloom-manufacture-{id[:8]}.zip"'})
                if action and action.startswith('asset/') and method=='GET':
                    target=s.asset_file(owner,id,action.split('/')[1]);return self.send(body=target.read_bytes(),kind=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
            match=re.fullmatch(r'/api/fabrication-photos/([a-f0-9]{32})/content',path)
            if match and method=='GET':return self.send(body=s.fabrication.content(owner,match[1]),kind='image/png')
            if path=='/api/instances' and method=='POST':return self.send(201,s.create_instance(owner,self.body()))
            match=re.fullmatch(r'/api/instances/([a-f0-9]{32})/records',path)
            if match:
                if method=='GET':return self.send(data=s.fabrication_records(owner,match[1]))
                if method=='POST':return self.send(data=s.stage_instance(owner,match[1],self.body()))
            match=re.fullmatch(r'/api/instances/([a-f0-9]{32})/(stage|bind)',path)
            if match and method=='POST':return self.send(data=s.stage_instance(owner,match[1],self.body()) if match[2]=='stage' else s.bind_instance(owner,match[1],self.body()))
            if path=='/api/devices' and method=='POST':
                p=self.body();id,token=s.store.device(owner,text(p.get('name'),'设备名',1,60),'esp32');return self.send(201,{'id':id,'token':token,'notice':'只展示一次。将 token 写入固件 config.local.h；不要提交至版本库。'})
            match=re.fullmatch(r'/api/devices/([a-f0-9]{32})/(light|rotate-token)',path)
            if match and method=='POST':
                if match[2]=='light':return self.send(202,s.command(owner,match[1],'light',self.body()))
                s.own('devices',match[1],owner);token=secrets.token_urlsafe(32);s.store.execute('UPDATE devices SET token=? WHERE id=?',(hash_secret(token),match[1]));return self.send(data={'token':token})
            if path=='/api/sim/install' and method=='POST':
                p=self.body();return self.send(data=s.simulator_install(owner,p.get('device_id'),p.get('instance_id')))
            if path=='/api/hardware/files' and method=='GET':
                return self.send(data={'files':[{'name':p.name,'url':'/api/hardware/file/'+p.name,'bytes':p.stat().st_size} for p in sorted((ROOT/'hardware/exports').iterdir()) if p.is_file()]})
            match=re.fullmatch(r'/api/hardware/file/([a-zA-Z0-9_.-]+)',path)
            if match and method=='GET':
                target=ROOT/'hardware/exports'/match[1]
                if not target.is_file():raise APIError(404,'文件不存在')
                return self.send(body=target.read_bytes(),kind=mimetypes.guess_type(target.name)[0] or 'application/octet-stream',headers={'Content-Disposition':f'attachment; filename="{target.name}"'})
            raise APIError(404,'API 不存在')
        if method not in {'GET','HEAD'}:raise APIError(405,'方法不允许')
        web=ROOT/'web';target=(web/('index.html' if path=='/' else path.lstrip('/'))).resolve()
        if not target.is_relative_to(web.resolve()) or not target.is_file():raise APIError(404,'页面不存在')
        return self.send(body=target.read_bytes(),kind={'.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.html':'text/html; charset=utf-8','.svg':'image/svg+xml'}.get(target.suffix,mimetypes.guess_type(target.name)[0] or 'application/octet-stream'))
    def handle_request(self):
        if hasattr(self,'_parsed_body'):del self._parsed_body
        try:
            with self.service.request_scope():self.dispatch()
        except APIError as exc:
            self.close_connection=True
            self.send(exc.status,{'error':exc.message},headers={'Connection':'close'})
        except (BrokenPipeError,ConnectionResetError,TimeoutError):pass
        except sqlite3.IntegrityError:self.send(409,{'error':'记录冲突或关联对象无效'})
        except Exception as exc:
            sys.stderr.write(f'Internal error: {type(exc).__name__}: {str(exc)[:150]}\n')
            self.send(500,{'error':'服务内部错误，请检查终端日志；请求未被标记成功'})
    do_GET=handle_request;do_HEAD=handle_request;do_POST=handle_request;do_PATCH=handle_request;do_DELETE=handle_request

def main():
    load_env(ROOT/'.env');parser=argparse.ArgumentParser(description='BLOOM growing lamp MVP')
    parser.add_argument('--host',default=os.getenv('HOST','127.0.0.1'));parser.add_argument('--port',type=int,default=int(os.getenv('PORT','8787')))
    parser.add_argument('--data',type=Path,default=ROOT/'data');parser.add_argument('--no-demo',action='store_true');args=parser.parse_args()
    demo=not args.no_demo and os.getenv('DEMO_MODE','1')=='1'
    if args.host not in {'127.0.0.1','localhost','::1'} and os.getenv('ALLOW_LAN','0')!='1':parser.error('局域网绑定需要 ALLOW_LAN=1；不要直接暴露公网')
    service=Service(args.data,demo);server=BloomServer((args.host,args.port),service);service.start()
    print(f'\nBLOOM · 会长大的台灯\nhttp://{args.host}:{args.port}\nData: {args.data}\nMode: {"explicit local demo" if demo else "real devices; no seeded demo"}\n',flush=True)
    if demo:print('Demo accounts: alice@bloom.local / bob@bloom.local | BloomDemo!2026\n',flush=True)
    try:server.serve_forever(poll_interval=.3)
    except KeyboardInterrupt:pass
    finally:service.close();server.server_close()

if __name__=='__main__':main()
