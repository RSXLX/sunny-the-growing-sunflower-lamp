"""Tripo REST adapter with TLS verification, bounded downloads and no blind paid retries."""
from __future__ import annotations
import ipaddress, json, os, socket, urllib.error, urllib.parse, urllib.request
from pathlib import Path

class SubmissionUnknown(Exception): pass
class ProviderRejected(Exception): pass

API_BASES = {
    'https://openapi.tripo3d.ai/v3': 'v3',
    'https://api.tripo3d.ai/v2/openapi': 'v2',
    'https://api.tripo3d.com/v2/openapi': 'v2',
}
MODELS = {'v2.5-20250123', 'v3.0-20250812', 'v3.1-20260211'}

class Tripo:
    def __init__(self, config=None):
        self.key=os.getenv('TRIPO_API_KEY','').strip()
        self.config=dict(config) if config is not None else self.configuration()
        self.base=self.config['base']
        if self.base not in API_BASES or self.config.get('api_version')!=API_BASES[self.base]:
            raise ValueError('Tripo endpoint and API version do not match an allowed official endpoint')
        if self.config.get('model') not in MODELS:raise ValueError('Unsupported Tripo model')

    @staticmethod
    def configuration():
        base=os.getenv('TRIPO_API_BASE','https://openapi.tripo3d.ai/v3').rstrip('/')
        if base not in API_BASES:raise ValueError('TRIPO_API_BASE must be an allowed official endpoint')
        version=API_BASES[base]
        model=os.getenv('TRIPO_MODEL_VERSION','v3.1-20260211' if version=='v3' else 'v2.5-20250123')
        if model not in MODELS:raise ValueError('Unsupported TRIPO_MODEL_VERSION')
        return {'base':base,'api_version':version,'model':model,'texture':False,'pbr':False,'face_limit':20000}

    def request(self,method,path,payload=None,*,raw=None,content_type='application/json'):
        if not self.key:raise ValueError('TRIPO_API_KEY is not configured')
        request=urllib.request.Request(self.base+path,data=raw if raw is not None else json.dumps(payload).encode() if payload is not None else None,
            headers={'Authorization':'Bearer '+self.key,'Content-Type':content_type,'User-Agent':'Bloom-MVP/1.1'},method=method)
        # Never forward the bearer credential through redirects.
        with urllib.request.build_opener(NoRedirect).open(request,timeout=30) as response:
            body=response.read(2*1024*1024+1)
        if len(body)>2*1024*1024:raise ValueError('Tripo response exceeds limit')
        doc=json.loads(body)
        if not isinstance(doc,dict) or 'code' not in doc:raise ValueError('Invalid Tripo envelope')
        if doc['code']!=0:raise ProviderRejected('Tripo rejected request: code '+str(doc['code']))
        if not isinstance(doc.get('data'),dict):raise ValueError('Invalid Tripo data')
        return doc['data']

    def create(self,prompt,*,image_token=None,seed=None):
        if not self.key:raise ValueError('TRIPO_API_KEY is not configured')
        if image_token and self.config['api_version']!='v3':raise ValueError('Reference image generation requires v3')
        if not image_token and (not isinstance(prompt,str) or not 3<=len(prompt)<=1024):raise ValueError('Tripo prompt requires 3–1024 characters')
        payload={'texture':False,'pbr':False,'face_limit':20000}
        if self.config['api_version']=='v3':
            payload['model']=self.config['model']
            if seed is not None:payload['model_seed']=seed
            if image_token:payload['input']=image_token;path='/generation/image-to-model'
            else:payload['prompt']=prompt;path='/generation/text-to-model'
        else:
            payload.update({'type':'text_to_model','prompt':prompt,'model_version':self.config['model']});path='/task'
        try:
            task=self.request('POST',path,payload)['task_id']
            if not isinstance(task,str) or not task or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in task):
                raise ValueError('Missing or invalid task ID')
            return task
        except ProviderRejected:raise
        except urllib.error.HTTPError as exc:
            if 400<=exc.code<500:raise ProviderRejected(f'Tripo rejected request (HTTP {exc.code}); check key, model and balance') from exc
            raise SubmissionUnknown('提交结果未知，请核对 Tripo 控制台并填入已有任务 ID；不会自动重新生成。') from exc
        except Exception as exc:raise SubmissionUnknown('提交结果未知，请核对 Tripo 控制台并填入已有任务 ID；不会自动重新生成。') from exc

    def upload_image(self,data):
        if self.config['api_version']!='v3':raise ValueError('Image upload requires v3')
        import secrets
        boundary='bloom-'+secrets.token_hex(16)
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="reference.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
              +data+f'\r\n--{boundary}--\r\n'.encode())
        token=self.request('POST','/files',raw=body,content_type='multipart/form-data; boundary='+boundary).get('file_token')
        if not isinstance(token,str) or not token.startswith('file_') or len(token)>256:raise ValueError('Invalid Tripo file token')
        return token

    def poll(self,task):
        if not task or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in task):raise ValueError('Invalid task id')
        return self.request('GET',('/tasks/' if self.config['api_version']=='v3' else '/task/')+task)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def download_model(url: str,target: Path,max_bytes=50*1024*1024):
    opener=urllib.request.build_opener(NoRedirect)
    for _ in range(5):
        parsed=urllib.parse.urlparse(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:raise ValueError('Only HTTPS model files are accepted')
        for address in socket.getaddrinfo(parsed.hostname,parsed.port or 443,type=socket.SOCK_STREAM):
            if not ipaddress.ip_address(address[4][0]).is_global:raise ValueError('Private network asset URL rejected')
        try:
            with opener.open(urllib.request.Request(url,headers={'User-Agent':'Bloom-MVP/1.0'}),timeout=60) as response:
                if int(response.headers.get('Content-Length','0'))>max_bytes:raise ValueError('Model exceeds download limit')
                tmp=target.with_suffix('.partial'); total=0
                try:
                    with tmp.open('wb') as f:
                        while True:
                            chunk=response.read(65536)
                            if not chunk:break
                            total+=len(chunk)
                            if total>max_bytes:raise ValueError('Model exceeds download limit')
                            f.write(chunk)
                    tmp.replace(target)
                finally:tmp.unlink(missing_ok=True)
                return
        except urllib.error.HTTPError as exc:
            if exc.code in (301,302,303,307,308) and exc.headers.get('Location'):
                url=urllib.parse.urljoin(url,exc.headers['Location']);continue
            raise
    raise ValueError('Too many asset redirects')
