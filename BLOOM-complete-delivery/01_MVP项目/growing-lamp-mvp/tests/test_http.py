"""Actual localhost HTTP contracts, authentication, same-origin and CSRF."""
import base64,io,http.cookiejar,http.client,json,os,tempfile,threading,unittest,urllib.request,urllib.error
from PIL import Image
from app.server import BloomServer
from app.service import Service

class HttpTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  os.environ['QUIET']='1';cls.tmp=tempfile.TemporaryDirectory();cls.service=Service(cls.tmp.name,True);cls.server=BloomServer(('127.0.0.1',0),cls.service);cls.url='http://127.0.0.1:'+str(cls.server.server_port);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
  cls.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()));status,data,headers=cls.call('/api/auth/login','POST',{'email':'alice@bloom.local','password':'BloomDemo!2026'});cls.csrf=data['csrf'];cls.cookie=headers['Set-Cookie']
 @classmethod
 def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.service.close();cls.tmp.cleanup()
 @classmethod
 def call(cls,path,method='GET',body=None,headers=None,anonymous=False):
  req=urllib.request.Request(cls.url+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json',**(headers or {})},method=method)
  try:res=(urllib.request.urlopen(req,timeout=5) if anonymous else cls.opener.open(req,timeout=5))
  except urllib.error.HTTPError as e:res=e
  raw=res.read();data=json.loads(raw) if 'application/json' in res.headers.get('Content-Type','') else raw
  return res.code,data,dict(res.headers)
 def test_01_root_and_assets(self):
  for path in ['/','/app.js','/viewer.js','/style.css','/wiring.svg','/favicon.svg']:self.assertEqual(self.call(path)[0],200)
 def test_02_requires_authentication(self):self.assertEqual(self.call('/api/state',anonymous=True)[0],401)
 def test_03_cookie_security(self):self.assertIn('HttpOnly',self.cookie);self.assertIn('SameSite=Strict',self.cookie)
 def test_04_csrf_required(self):self.assertEqual(self.call('/api/memories','POST',{'title':'t','body':'b','theme':'forest'})[0],403)
 def test_05_memory_write_read_delete(self):
  status,m,_=self.call('/api/memories','POST',{'title':'HTTP memory','body':'Saved through real HTTP','theme':'forest'},{'X-CSRF-Token':self.csrf});self.assertEqual(status,201);self.assertTrue(any(x['id']==m['id'] for x in self.call('/api/state')[1]['memories']));self.assertEqual(self.call('/api/memories/'+m['id'],'DELETE',{}, {'X-CSRF-Token':self.csrf})[0],200)
 def test_06_cross_origin_write(self):self.assertEqual(self.call('/api/memories','POST',{}, {'Origin':'https://evil.example','X-CSRF-Token':self.csrf})[0],403)
 def test_07_host_rebinding_rejected(self):self.assertEqual(self.call('/api/health',headers={'Host':'evil.example'})[0],403)
 def test_08_traversal_rejected(self):self.assertEqual(self.call('/%2e%2e/app/store.py')[0],404)
 def test_09_device_bearer_required(self):self.assertEqual(self.call('/api/device/poll','POST',{},anonymous=True)[0],401)
 def test_10_create_real_device_protocol(self):
  code,d,_=self.call('/api/devices','POST',{'name':'HTTP test device'},{'X-CSRF-Token':self.csrf});self.assertEqual(code,201)
  code,data,_=self.call('/api/device/poll','POST',{'reported':{'power':False,'brightness':0,'projection':0,'theme':'forest','temperature_c':25.0}},{'Authorization':'Bearer '+d['token']},True);self.assertEqual(code,200);self.assertEqual(data['protocol'],'bloom-device-v1')
 def test_11_security_headers(self):
  _,_,h=self.call('/');self.assertEqual(h['X-Content-Type-Options'],'nosniff');self.assertIn("script-src 'self'",h['Content-Security-Policy'])
 def test_12_bad_login_rejected(self):self.assertEqual(self.call('/api/auth/login','POST',{'email':'alice@bloom.local','password':'not-correct'},anonymous=True)[0],401)
 def test_13_logout_then_login_on_same_connection(self):
  conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
  try:
   headers={'Content-Type':'application/json'}
   conn.request('POST','/api/auth/login',json.dumps({'email':'bob@bloom.local','password':'BloomDemo!2026'}),headers)
   response=conn.getresponse();cookie=response.getheader('Set-Cookie').split(';')[0];csrf=json.loads(response.read())['csrf'];sock=conn.sock
   conn.request('POST','/api/auth/logout','{}',{**headers,'Cookie':cookie,'X-CSRF-Token':csrf})
   response=conn.getresponse();self.assertEqual(response.status,200);response.read()
   self.assertIs(conn.sock,sock)
   conn.request('POST','/api/auth/login',json.dumps({'email':'bob@bloom.local','password':'BloomDemo!2026'}),headers)
   response=conn.getresponse();self.assertEqual(response.status,200);self.assertEqual(json.loads(response.read())['user']['email'],'bob@bloom.local')
  finally:conn.close()
 def test_14_invalid_body_closes_connection(self):
  conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
  try:
   conn.request('POST','/api/auth/login','unread body',{'Content-Type':'text/plain'})
   response=conn.getresponse();self.assertEqual(response.status,415);self.assertEqual(response.getheader('Connection'),'close');response.read()
  finally:conn.close()
 def test_15_manufacturing_http_routes_and_access(self):
  headers={'X-CSRF-Token':self.csrf}
  design=self.call('/api/state')[1]['designs'][0]
  code,data,_=self.call('/api/designs/'+design['id']+'/versions');self.assertEqual(code,200);version=data['versions'][0]
  path='/api/versions/'+version['id']
  self.assertEqual(self.call(path,anonymous=True)[0],401)
  self.assertEqual(self.call(path+'/review','POST',{'action':'approve'})[0],403)
  self.assertEqual(self.call('/api/designs/'+design['id']+'/versions','POST',{'units':'mm','stl_base64':'invalid','note':'HTTP 无效 STL 上传测试。'},headers)[0],400)
  code,archive,_=self.call(path+'/package');self.assertEqual(code,200);self.assertTrue(archive.startswith(b'PK'))
 def test_16_reference_upload_over_default_body_limit_and_private_content(self):
  buffer=io.BytesIO()
  with Image.frombytes('RGB',(320,320),os.urandom(320*320*3)) as image:image.save(buffer,'PNG')
  payload={'name':'HTTP private reference','data_base64':base64.b64encode(buffer.getvalue()).decode()}
  self.assertGreater(len(json.dumps(payload)),256*1024)
  self.assertEqual(self.call('/api/reference-assets','POST',payload)[0],403)
  code,asset,_=self.call('/api/reference-assets','POST',payload,{'X-CSRF-Token':self.csrf});self.assertEqual(code,201)
  self.assertEqual(self.call(asset['url'])[0],200)
  self.assertEqual(self.call(asset['url'],anonymous=True)[0],401)
  code,_,headers=self.call('/api/auth/login','POST',{'email':'bob@bloom.local','password':'BloomDemo!2026'},anonymous=True);self.assertEqual(code,200)
  self.assertEqual(self.call(asset['url'],headers={'Cookie':headers['Set-Cookie'].split(';')[0]},anonymous=True)[0],404)
  self.assertEqual(self.call('/api/reference-assets/'+asset['id'],'DELETE',{}, {'X-CSRF-Token':self.csrf})[0],200)
  self.assertEqual(self.call(asset['url'])[0],404)

if __name__=='__main__':unittest.main()
