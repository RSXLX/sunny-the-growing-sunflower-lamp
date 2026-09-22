"""Application service: permission boundaries, durable commands and generation jobs."""
from __future__ import annotations
import hashlib, io, json, os, re, secrets, shutil, sqlite3, subprocess, sys, threading, time, zipfile
from pathlib import Path
from contextlib import contextmanager
from .data_lock import DataLock
from .geometry import make_crown, INTERFACE, glb_triangles, write_stl, inspect_mesh
from .providers import Tripo, SubmissionUnknown, ProviderRejected, download_model
from .assets import ReferenceAssets
from .fabrication import Fabrication
from .errors import APIError
from .manufacturing import Manufacturing, note_text
from .store import Store, uid, stamp, hash_secret

THEMES={'sunflower','forest','stars'}
ROOT=Path(__file__).resolve().parent.parent

def text(value,label='text',minimum=1,maximum=1000):
    if not isinstance(value,str) or not minimum<=len(value.strip())<=maximum:raise APIError(400,f'{label} 长度须为 {minimum}–{maximum} 字符')
    return value.strip()

def theme(value):
    if value not in THEMES:raise APIError(400,'未知主题')
    return value

def integer(value,minimum,maximum,label):
    if isinstance(value,bool) or not isinstance(value,int) or not minimum<=value<=maximum:raise APIError(400,f'{label} 超出范围')
    return value

class Service:
    def __init__(self,directory,demo=True):
        self.data_lock=DataLock(directory)
        self.lifecycle=threading.Condition();self.active_requests=0;self.closing=False
        try:
            if (self.data_lock.directory/'restore-incomplete').exists():raise RuntimeError('此目录的恢复未完成；请在新的目录重新恢复备份')
            self._initialize(directory,demo)
        except BaseException:self.data_lock.close();raise

    def _initialize(self,directory,demo):
        self.store=Store(directory); self.data=self.store.directory; self.demo=demo
        (self.data/'assets').mkdir(exist_ok=True)
        self.stop_event=threading.Event(); self.job_lock=threading.Lock(); self.worker=None; self.sim_worker=None
        self.manufacturing=Manufacturing(self.store, self.data)
        self.references=ReferenceAssets(self.store)
        self.fabrication=Fabrication(self.store,self.manufacturing)
        if demo:self.seed()
        self.manufacturing.bootstrap()
        # Old tasks were created with v2. Record the assumption, never repoint them
        # to a new endpoint just because current generation settings changed.
        legacy={'base':'https://api.tripo3d.ai/v2/openapi','api_version':'v2','model':'v2.5-20250123','legacy_assumed':True,'input_mode':'text'}
        self.store.execute("UPDATE designs SET request_snapshot=? WHERE provider='tripo' AND request_snapshot IS NULL",(json.dumps(legacy),))
        self.store.execute("UPDATE designs SET status='queued' WHERE status='uploading' AND task_id IS NULL")
        # A process may have crashed after the provider accepted a paid POST. Never repeat it blindly.
        self.store.execute("UPDATE designs SET status='submission_unknown',error='进程在提交期间中断，请从 Tripo 控制台核对并恢复任务 ID' WHERE status='submitting' AND task_id IS NULL")
        self.store.execute("UPDATE designs SET status='polling' WHERE task_id IS NOT NULL AND status IN ('submitting','generating','downloading')")
        self.store.execute("UPDATE designs SET status='queued' WHERE provider='parametric' AND status='generating'")
    def seed(self):
        alice=self.store.user('alice@bloom.local','嘉一','BloomDemo!2026')
        bob=self.store.user('bob@bloom.local','林间来信','BloomDemo!2026')
        if self.store.one('SELECT id FROM designs LIMIT 1'):return
        created=stamp()
        seeds=[(alice,'初见 · 向日葵','刚搬进来的房间，需要一束温暖而安静的光。','sunflower',11,0),
               (alice,'第一晚 · 森林','搬来这里的第一晚，窗外有一棵很大的树。我喜欢那片树影。','forest',21,1),
               (bob,'来信 · 星夜','把散步时抬头看见的星星，留在自己的房间里。','stars',32,1)]
        for i,(owner,title,prompt,th,seed,public) in enumerate(seeds):
            mid=uid();self.store.execute('INSERT INTO memories VALUES(?,?,?,?,?,?)',(mid,owner,title,prompt,th,created-(3-i)*86400))
            did=uid(); asset=f'assets/{did}'; make_crown(self.data/asset,prompt,th,seed)
            self.store.execute('INSERT INTO designs(id,owner,title,prompt,theme,seed,memory_id,provider,status,progress,asset,public,review_note,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (did,owner,title,prompt,th,seed,mid,'parametric','reviewed',100,asset,public,'团队参数化样例，数字网格检查完成；没有实物验证。',created-(3-i)*86400))
            if owner==alice:
                iid=uid();self.store.execute('INSERT INTO instances(id,owner,design_id,name,tag_payload,stage,simulated,created) VALUES(?,?,?,?,?,?,?,?)',
                    (iid,owner,did,title+' / 演示件','BLM1:'+iid,'bound',1,created))
        for owner,name in [(alice,'我的向日葵'),(bob,'林间的灯')]:
            did,_=self.store.device(owner,name,'simulator')
            instance=self.store.one('SELECT i.*,d.theme FROM instances i JOIN designs d ON d.id=i.design_id WHERE i.owner=? ORDER BY i.created LIMIT 1',(owner,))
            state={'power':True,'brightness':45,'projection':20,'theme':instance['theme'] if instance else 'stars','instance_id':instance['id'] if instance else None,'temperature_c':27.0}
            self.store.execute('UPDATE devices SET reported=?,seen=? WHERE id=?',(json.dumps(state),stamp(),did))
            device=self.store.one('SELECT * FROM devices WHERE id=?',(did,))
            self.store.event(device,'demo_seed',{'note':'预置演示数据，并非真实长期使用记录'})
    def own(self,table,id,owner):
        if table not in {'memories','designs','instances','devices'}:raise ValueError('Invalid table')
        row=self.store.one(f'SELECT * FROM {table} WHERE id=? AND owner=?',(id,owner))
        if not row:raise APIError(404,'记录不存在或无权访问')
        return row
    def design(self,id,owner):
        row=self.store.one('SELECT d.*,u.name AS author FROM designs d JOIN users u ON u.id=d.owner WHERE d.id=? AND (d.owner=? OR d.public=1)',(id,owner))
        if not row:raise APIError(404,'设计不存在或未公开')
        return self.safe_design(row,owner)
    def safe_design(self,row,owner):
        row=dict(row); row['is_owner']=row['owner']==owner
        row['asset_url']=f"/api/designs/{row['id']}/asset/crown.stl" if row['asset'] and (self.data/row['asset']/'crown.stl').exists() else None
        row['raw_url']=f"/api/designs/{row['id']}/asset/raw.glb" if row['asset'] and (self.data/row['asset']/'raw.glb').exists() else None
        row['parameters']=None
        if row['asset'] and (self.data/row['asset']/'parameters.json').exists():row['parameters']=json.loads((self.data/row['asset']/'parameters.json').read_text())
        row['generation']=json.loads(row.pop('request_snapshot') or '{}')
        row.pop('upload_token',None)
        row.pop('raw_sha256',None)
        row.pop('paid_reserved',None)
        row['source_label']='参数化演示 · 非 Tripo 生成' if row['provider']=='parametric' else 'Tripo API · 已关联供应商任务' if row['task_id'] else 'Tripo API · 本地任务待提交或核对'
        if row['is_owner']:
            row['versions']=self.manufacturing.list(owner,row['id'])
            if row['versions']:
                latest=row['versions'][0]
                row['asset_url']=latest['asset_url']
                row['raw_url']=latest.get('original_preview_url')
                row['preview_error']=latest['mesh_report'].get('original_preview',{}).get('error')
        else:
            row['versions']=[self.manufacturing.get(owner,row['published_version_id'])] if row.get('published_version_id') else []
            public_version=row['versions'][0] if row['versions'] else None
            row['asset_url']=public_version['asset_url'] if public_version else None
            row['raw_url']=None
            row['parameters']=None
        # Share design instructions, never the source memory or private operational details.
        for key in ['asset','idempotency_key','next_run','attempts']:row.pop(key,None)
        if not row['is_owner']:
            for key in ['memory_id','task_id','error','review_note','reference_asset_id','generation']:row.pop(key,None)
        return row
    def snapshot(self,owner):
        designs=[self.safe_design(r,owner) for r in self.store.all('SELECT d.*,u.name AS author FROM designs d JOIN users u ON u.id=d.owner WHERE d.owner=? ORDER BY d.created DESC',(owner,))]
        public=[self.safe_design(r,owner) for r in self.store.all('SELECT d.*,u.name AS author FROM designs d JOIN users u ON u.id=d.owner WHERE d.public=1 ORDER BY d.created DESC LIMIT 100')]
        devices=self.store.all('SELECT id,name,kind,reported,seen,created FROM devices WHERE owner=? ORDER BY created',(owner,))
        for d in devices:
            d['reported']=json.loads(d['reported']);d['online']=stamp()-d['seen']<15
            d['commands']=self.store.all("SELECT id,kind,state,error,created FROM commands WHERE device_id=? ORDER BY created DESC LIMIT 5",(d['id'],))
        events=self.store.all('SELECT e.* FROM events e JOIN devices d ON d.id=e.device_id WHERE d.owner=? ORDER BY e.created DESC,e.rowid DESC LIMIT 50',(owner,))
        for e in events:e['payload']=json.loads(e['payload'])
        instances=self.store.all('SELECT i.*,d.title AS design_title,d.theme,d.provider,v.revision AS version_revision,v.status AS version_status FROM instances i JOIN designs d ON d.id=i.design_id LEFT JOIN manufacturing_versions v ON v.id=i.manufacturing_version_id WHERE i.owner=? ORDER BY i.created DESC',(owner,))
        for instance in instances:instance.pop('creation_key',None);instance.pop('creation_hash',None)
        return {'reference_assets':self.references.list(owner),'memories':self.store.all('SELECT * FROM memories WHERE owner=? ORDER BY created DESC',(owner,)),
                'designs':designs,'garden':public,'instances':instances,
                'devices':devices,'events':events,'favorites':[r['design_id'] for r in self.store.all('SELECT design_id FROM favorites WHERE owner=?',(owner,))],
                'capabilities':{'demo':self.demo,'tripo_configured':bool(os.getenv('TRIPO_API_KEY')),'image_upload':self.references.available(),'generation_config':self.generation_config(),'tripo_allowance':self.tripo_allowance(),'openscad':bool(shutil.which('openscad')),'interface':INTERFACE},'server_time':stamp()}
    def create_memory(self,owner,p):
        id=uid();self.store.execute('INSERT INTO memories VALUES(?,?,?,?,?,?)',(id,owner,text(p.get('title'),'标题',1,80),text(p.get('body'),'记忆',1,4000),theme(p.get('theme','forest')),stamp()));return {'id':id}
    def edit_memory(self,owner,id,p):
        self.own('memories',id,owner);self.store.execute('UPDATE memories SET title=?,body=?,theme=? WHERE id=?',(text(p.get('title'),'标题',1,80),text(p.get('body'),'记忆',1,4000),theme(p.get('theme')),id));return {'ok':True}
    def generation_config(self):
        try:return Tripo.configuration()
        except ValueError as exc:return {'error':str(exc)}

    def tripo_allowance(self):
        try:limit=max(0,int(os.getenv('TRIPO_MAX_SUBMISSIONS','0')))
        except ValueError:limit=0
        used=self.store.one('SELECT COUNT(*) AS n FROM designs WHERE paid_reserved=1')['n']
        return {'limit':limit,'reserved':used,'remaining':max(0,limit-used),'scope':'local_database_lifetime','unit':'generation_submissions'}

    def create_design(self,owner,p,key):
        key=text(key,'幂等键',8,100)
        previous=self.store.one('SELECT id FROM designs WHERE owner=? AND idempotency_key=?',(owner,key))
        if previous:return {'id':previous['id'],'reused':True}
        title=text(p.get('title'),'标题',1,80); prompt=text(p.get('prompt'),'视觉描述',3,2000); th=theme(p.get('theme','forest'))
        provider=p.get('provider','parametric');reference=p.get('reference_asset_id') or None
        if provider not in {'parametric','tripo'}:raise APIError(400,'未知生成器')
        config=None
        if provider=='tripo':
            if not os.getenv('TRIPO_API_KEY') or p.get('accept_charges') is not True:raise APIError(400,'先配置 Tripo API Key，并确认可能产生的 API 费用')
            try:config=Tripo.configuration()
            except ValueError as exc:raise APIError(400,str(exc)) from exc
            if p.get('confirm_provider_config')!=config:raise APIError(409,'生成配置已改变或未确认，请刷新后重新核对模型与输入')
            if not reference and len(prompt)>1024:raise APIError(400,'Tripo 文字生成的视觉描述最多 1024 字符')
            config.update({'input_mode':'image' if reference else 'text','confirmed_at':stamp(),'prompt':None if reference else prompt})
        if provider=='parametric':
            if not self.demo:raise APIError(400,'参数化演示模式未启用')
            if reference:raise APIError(400,'参数化演示不识别图片，请选择 Tripo 图片生成')
        mid=p.get('memory_id') or None; source=p.get('source_id') or None
        if mid:self.own('memories',mid,owner)
        if source:self.design(source,owner)
        id=uid();seed=secrets.randbelow(2**31)
        # Reserve generation allowance and asset reference atomically with the job.
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT id FROM designs WHERE owner=? AND idempotency_key=?',(owner,key)).fetchone()
            if previous:return {'id':previous['id'],'reused':True}
            if reference:
                if not isinstance(reference,str):raise APIError(400,'参考图 ID 无效')
                if config['api_version']!='v3':raise APIError(400,'图片生成需要 Tripo v3 接口')
                asset=self.references._row(owner,reference,db)
                if p.get('confirm_reference_sha256')!=asset['sha256']:raise APIError(400,'请预览并确认实际发送的参考图')
                self.references.content(owner,reference)
                config.update({'reference_sha256':asset['sha256'],'reference_asset_id':reference})
            active=db.execute("SELECT COUNT(*) FROM designs WHERE owner=? AND status IN ('queued','uploading','submitting','generating','polling','downloading','preparing')",(owner,)).fetchone()[0]
            if active>=3:raise APIError(429,'最多同时运行 3 个生成任务')
            if provider=='tripo':
                allowance=self.tripo_allowance()
                if allowance['remaining']<1:raise APIError(409,'Tripo 本地提交额度不足；请配置 TRIPO_MAX_SUBMISSIONS 后再创建任务')
            db.execute('INSERT INTO designs(id,owner,title,prompt,theme,seed,source_id,memory_id,provider,status,created,idempotency_key,reference_asset_id,request_snapshot,paid_reserved) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (id,owner,title,prompt,th,seed,source,mid,provider,'queued',stamp(),key,reference,json.dumps(config) if config else None,int(provider=='tripo')))
        return {'id':id}
    def review_design(self,owner,id,p):
        row=self.own('designs',id,owner)
        if row['status'] not in {'needs_review','reviewed'} or not row['asset']:raise APIError(409,'请等待生成和文件准备完成')
        if p.get('acknowledge') is not True:raise APIError(400,'请确认这不是实物安全认证')
        note=text(p.get('note'),'检查记录',8,2000)
        self.store.execute("UPDATE designs SET status='reviewed',review_note=? WHERE id=?",(note,id));return {'ok':True}
    def publish_design(self,owner,id,p):
        row=self.own('designs',id,owner)
        version_id=None
        if p.get('public') is True:
            if row['status']!='reviewed':raise APIError(409,'先完成人工设计检查记录')
            if p.get('confirm_rights') is not True:raise APIError(400,'请确认你有权公开设计，并同意 CC BY 4.0；私人记忆不会发布')
            versions=self.manufacturing.list(owner,id)
            version_id=p.get('version_id') or (versions[0]['id'] if versions else None)
            if not version_id:raise APIError(409,'先建立一个可公开的模型版本')
            version=self.manufacturing._row(owner,version_id)
            if version['design_id']!=id or version['status']=='revoked':raise APIError(409,'所选公开版本无效')
            self.manufacturing._verified_files(version)
        self.store.execute('UPDATE designs SET public=?,published_version_id=? WHERE id=?',
                           (int(p.get('public') is True),version_id,id));return {'ok':True}
    def recover_task(self,owner,id,p):
        with self.job_lock:
            row=self.own('designs',id,owner)
            if row['provider']!='tripo' or row['status'] not in {'submission_unknown','failed','configuration_failed','upload_failed','download_failed','poll_failed','preparation_failed'}:raise APIError(409,'只有异常 Tripo 任务可恢复')
            if row['status'] in {'upload_failed','configuration_failed'}:
                self.store.execute("UPDATE designs SET status='queued',error=NULL,attempts=0,next_run=0 WHERE id=?",(id,));return {'ok':True}
            if row['status']=='preparation_failed':
                self.store.execute("UPDATE designs SET status='preparing',error=NULL,attempts=0,next_run=0 WHERE id=?",(id,));return {'ok':True}
            task=row['task_id'] or text(p.get('task_id'),'Tripo 任务 ID',8,100)
            if p.get('task_id') and p['task_id']!=task:raise APIError(409,'任务已关联原 Tripo ID，不能替换成另一个任务')
            if not re.fullmatch(r'[A-Za-z0-9_-]+',task):raise APIError(400,'任务 ID 格式错误')
            self.store.execute("UPDATE designs SET task_id=?,status='polling',next_run=0,error=NULL,attempts=0 WHERE id=?",(task,id));return {'ok':True}
    def create_instance(self,owner,p):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            key=p.get('request_key')
            if key is not None:key=text(key,'提交标识',1,100)
            try:payload_hash=hashlib.sha256(json.dumps(p,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
            except (ValueError,TypeError):raise APIError(400,'制作档案请求格式无效')
            existing=db.execute('SELECT id,manufacturing_version_id,creation_hash FROM instances WHERE owner=? AND creation_key=?',(owner,key)).fetchone() if key else None
            if existing:
                if existing['creation_hash']!=payload_hash:raise APIError(409,'提交标识已用于不同的制作档案')
                return {'id':existing['id'],'manufacturing_version_id':existing['manufacturing_version_id'],'reused':True}
            version_id=p.get('manufacturing_version_id')
            if not version_id:
                chosen=db.execute("SELECT v.id FROM manufacturing_versions v JOIN designs d ON d.id=v.design_id WHERE d.id=? AND d.owner=? AND v.status='approved' ORDER BY v.revision DESC LIMIT 1",(p.get('design_id'),owner)).fetchone()
                if not chosen:raise APIError(409,'先批准一个制造版本，再创建对应的实物档案')
                version_id=chosen['id']
            version=self.manufacturing.require_approved(owner,version_id,db)
            if p.get('design_id') and p['design_id']!=version['design_id']:raise APIError(409,'制造版本不属于所选设计')
            source=p.get('source_instance_id') or None
            if source:
                previous=db.execute('SELECT * FROM instances WHERE id=? AND owner=?',(source,owner)).fetchone()
                if not previous:raise APIError(404,'原制作档案不存在')
                if previous['stage']!='failed' or previous['design_id']!=version['design_id']:raise APIError(409,'重制须关联同一设计的失败档案')
                note_text(p.get('note'))
            id=uid()
            db.execute('INSERT INTO instances(id,owner,design_id,name,tag_payload,created,manufacturing_version_id) VALUES(?,?,?,?,?,?,?)',
                       (id,owner,version['design_id'],text(p.get('name',version['title']),'名称',1,100),'BLM1:'+id,stamp(),version_id))
            db.execute('UPDATE instances SET creation_key=?,creation_hash=? WHERE id=?',(key,payload_hash,id))
            if source:
                db.execute('UPDATE instances SET source_instance_id=? WHERE id=?',(source,id))
                db.execute('INSERT INTO fabrication_records(id,instance_id,actor,stage,note,details,created) VALUES(?,?,?,?,?,?,?)',
                    (uid(),id,owner,'planned',note_text(p.get('note')),json.dumps({'source_instance_id':source,'source_version_id':previous['manufacturing_version_id'],'manufacturing_version_id':version_id}),stamp()))
        return {'id':id,'manufacturing_version_id':version_id}

    def stage_instance(self,owner,id,p):
        return self.fabrication.record(owner,id,p)

    def fabrication_records(self,owner,id):
        return self.fabrication.records(owner,id)
    def command(self,owner,device_id,kind,payload,ttl=60):
        d=self.own('devices',device_id,owner);id=uid()
        if kind=='light':
            if not isinstance(payload.get('power'),bool):raise APIError(400,'power 须为布尔值')
            payload={'power':payload['power'],'brightness':integer(payload.get('brightness'),0,100,'亮度'),
                     'projection':integer(payload.get('projection',0),0,35,'投影亮度'),'theme':theme(payload.get('theme','sunflower'))}
            # Latest desired light state supersedes unacknowledged older commands.
            self.store.execute("UPDATE commands SET state='superseded' WHERE device_id=? AND kind='light' AND state='queued'",(device_id,))
        self.store.execute('INSERT INTO commands(id,device_id,kind,payload,created,expires) VALUES(?,?,?,?,?,?)',(id,d['id'],kind,json.dumps(payload),stamp(),stamp()+ttl))
        return {'id':id,'state':'queued','message':'等待设备执行回执；网页未提前宣称成功'}
    def bind_instance(self,owner,id,p):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM instances WHERE id=? AND owner=?',(id,owner)).fetchone()
            d=db.execute('SELECT * FROM devices WHERE id=? AND owner=?',(p.get('device_id'),owner)).fetchone()
            if not row or not d:raise APIError(404,'设备或实物不存在或无权访问')
            if d['kind']=='esp32':
                if row['stage'] not in {'verified','bound'} or row['simulated'] or row['legacy_unverified']:
                    raise APIError(409,'真实绑定需要可追溯的实际装配检查；模拟绑定不能转换为真实验证')
                self.manufacturing.require_approved(owner,row['manufacturing_version_id'],db)
            elif row['manufacturing_version_id']:
                self.manufacturing.require_approved(owner,row['manufacturing_version_id'],db)
            if db.execute("SELECT id FROM commands WHERE device_id=? AND kind='bind' AND state='queued' AND expires>?",(d['id'],stamp())).fetchone():raise APIError(409,'已有绑定等待完成')
            des=db.execute('SELECT theme FROM designs WHERE id=?',(row['design_id'],)).fetchone()
            command_id=uid();payload={'instance_id':id,'tag_payload':row['tag_payload'],'theme':des['theme']}
            db.execute('INSERT INTO commands(id,device_id,kind,payload,created,expires) VALUES(?,?,?,?,?,?)',
                       (command_id,d['id'],'bind',json.dumps(payload),stamp(),stamp()+90))
        return {'id':command_id,'state':'queued','message':'等待设备写入与读回'}
    def device_auth(self,token):
        d=self.store.one('SELECT * FROM devices WHERE token=?',(hash_secret(token),))
        if not d:raise APIError(401,'设备凭据无效')
        return d
    def _device_instance(self,db,device,instance_id):
        row=db.execute('SELECT * FROM instances WHERE id=? AND owner=?',(instance_id,device['owner'])).fetchone()
        if not row:raise APIError(404,'实物不存在或无权访问')
        return row

    def _handle_device_events(self,db,device,events):
        if not isinstance(events,list) or len(events)>16:raise APIError(400,'事件批次无效')
        for e in events:
            if not isinstance(e,dict):raise APIError(400,'事件须为对象')
            eid=text(e.get('id'),'事件 ID',8,100);kind=e.get('kind');payload=e.get('payload',{})
            if not isinstance(payload,dict):raise APIError(400,'事件格式错误')
            existing=db.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
            if existing:
                if existing['device_id']!=device['id'] or existing['kind']!=kind or json.loads(existing['payload'])!=payload:
                    raise APIError(409,'事件 ID 与已保存内容冲突')
                continue
            if kind=='tag_bound':
                instance=self._device_instance(db,device,payload.get('instance_id'))
                pending=db.execute("SELECT * FROM commands WHERE device_id=? AND kind='bind' AND state='queued' AND expires>? ORDER BY created DESC LIMIT 1",(device['id'],stamp())).fetchone()
                if not pending or json.loads(pending['payload']).get('instance_id')!=instance['id'] or payload.get('verified') is not True:
                    raise APIError(409,'绑定事件缺少对应的有效命令或读回校验')
                if device['kind']=='esp32':
                    if instance['simulated'] or instance['legacy_unverified'] or instance['stage'] not in {'verified','bound'}:
                        raise APIError(409,'真实绑定缺少可追溯装配检查')
                    self.manufacturing.require_approved(device['owner'],instance['manufacturing_version_id'],db)
                elif instance['manufacturing_version_id']:
                    self.manufacturing.require_approved(device['owner'],instance['manufacturing_version_id'],db)
                db.execute("UPDATE instances SET stage='bound',tag_uid=?,simulated=? WHERE id=?",(text(payload.get('tag_uid'),'标签 UID',4,40),int(device['kind']=='simulator'),instance['id']))
                db.execute("UPDATE commands SET state='acked' WHERE id=?",(pending['id'],))
            elif kind=='outfit_installed':
                instance=self._device_instance(db,device,payload.get('instance_id'))
                if instance['stage']!='bound':raise APIError(409,'外装尚未绑定')
                if device['kind']=='esp32' and (instance['simulated'] or instance['legacy_unverified']):raise APIError(409,'演示或旧档案不能代替真实 NFC 写入')
                if instance['manufacturing_version_id']:
                    self.manufacturing.require_approved(device['owner'],instance['manufacturing_version_id'],db)
            elif kind not in {'button','safety_trip','boot','unknown_tag','tag_write_failed','projection_timeout'}:
                raise APIError(400,'未知设备事件')
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(eid,device['id'],kind,json.dumps(payload,ensure_ascii=False),int(device['kind']=='simulator'),stamp()))

    def handle_device_events(self,device,events):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._handle_device_events(db,device,events)

    def poll_device(self,device,p):
        # One batch is atomic: malformed reported state cannot leave a half-applied binding.
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE commands SET state='expired',error='设备未在有效期内确认' WHERE device_id=? AND state='queued' AND expires<=?",(device['id'],stamp()))
            self._handle_device_events(db,device,p.get('events',[]))
            acks=p.get('acks',[])
            if not isinstance(acks,list) or len(acks)>16:raise APIError(400,'回执格式无效')
            for ack in acks:
                if not isinstance(ack,dict) or not isinstance(ack.get('ok'),bool):raise APIError(400,'回执须为对象且 ok 为布尔值')
                # A successful bind ack alone is not proof of NFC write/readback.
                db.execute("UPDATE commands SET state=?,error=? WHERE id=? AND device_id=? AND state='queued' AND (kind!='bind' OR ?=0)",
                    ('acked' if ack['ok'] else 'failed',str(ack.get('error',''))[:200],ack.get('id'),device['id'],int(ack['ok'])))
            reported=p.get('reported')
            if reported is not None:
                if not isinstance(reported,dict):raise APIError(400,'设备状态格式无效')
                clean={k:v for k,v in reported.items() if k in {'power','brightness','projection','theme','instance_id','temperature_c','fault','firmware','rssi'}}
                if 'brightness' in clean:integer(clean['brightness'],0,100,'亮度')
                if 'projection' in clean:integer(clean['projection'],0,35,'投影')
                if clean.get('theme'):theme(clean['theme'])
                for key in ('power','fault'):
                    if key in clean and not isinstance(clean[key],bool):raise APIError(400,'状态 '+key+' 须为布尔值')
                if clean.get('instance_id'):
                    instance=self._device_instance(db,device,clean['instance_id'])
                    if device['kind']=='esp32' and (instance['simulated'] or instance['legacy_unverified'] or instance['stage']!='bound'):
                        raise APIError(409,'真实设备不能上报模拟或未绑定外装')
                db.execute('UPDATE devices SET reported=?,seen=? WHERE id=?',(json.dumps(clean),stamp(),device['id']))
            else:db.execute('UPDATE devices SET seen=? WHERE id=?',(stamp(),device['id']))
            commands=[dict(row) for row in db.execute("SELECT id,kind,payload,expires FROM commands WHERE device_id=? AND state='queued' ORDER BY created,rowid LIMIT 8",(device['id'],)).fetchall()]
            for cmd in commands:cmd['payload']=json.loads(cmd['payload'])
            known=[dict(row) for row in db.execute("""SELECT i.id AS instance_id,i.tag_payload,d.theme
                FROM instances i JOIN designs d ON d.id=i.design_id
                LEFT JOIN manufacturing_versions v ON v.id=i.manufacturing_version_id
                WHERE i.owner=? AND i.stage='bound' AND (i.simulated=0 OR ?='simulator')
                AND ((v.status='approved' AND (i.legacy_unverified=0 OR ?='simulator')) OR (?='simulator' AND i.manufacturing_version_id IS NULL))
                ORDER BY i.created DESC LIMIT 16""",(device['owner'],device['kind'],device['kind'],device['kind'])).fetchall()]
        return {'commands':commands,'known_tags':known,'server_time':stamp(),'protocol':'bloom-device-v1'}
    def simulator_tick(self):
        if not self.demo:return
        for d in self.store.all("SELECT * FROM devices WHERE kind='simulator'"):
            state=json.loads(d['reported']); commands=self.store.all("SELECT * FROM commands WHERE device_id=? AND state='queued' AND expires>? ORDER BY created",(d['id'],stamp()))
            events=[];acks=[]
            for c in commands:
                p=json.loads(c['payload'])
                if c['kind']=='light':state.update(p)
                elif c['kind']=='bind':events.append({'id':'sim-'+c['id'],'kind':'tag_bound','payload':{'instance_id':p['instance_id'],'verified':True,'tag_uid':'SIM-'+p['instance_id'][:12]}})
                acks.append({'id':c['id'],'ok':True})
            self.poll_device(d,{'reported':state,'events':events,'acks':acks})
    def simulator_install(self,owner,device_id,instance_id):
        d=self.own('devices',device_id,owner)
        if not self.demo or d['kind']!='simulator':raise APIError(403,'只有明确标注的模拟设备可使用此操作')
        i=self.own('instances',instance_id,owner)
        if i['stage']!='bound':raise APIError(409,'先完成模拟绑定')
        des=self.own('designs',i['design_id'],owner);state=json.loads(d['reported'])
        if state.get('instance_id')==i['id']:return {'ok':True,'deduplicated':True}
        state.update({'instance_id':i['id'],'theme':des['theme'],'power':True})
        self.poll_device(d,{'reported':state,'events':[{'id':uid(),'kind':'outfit_installed','payload':{'instance_id':i['id']}}]})
        return {'ok':True}
    def asset_file(self,owner,id,name):
        row=self.design(id,owner);raw=self.store.one('SELECT asset FROM designs WHERE id=?',(id,))
        if not row['is_owner']:
            if not row.get('published_version_id'):raise APIError(404,'没有公开的文件快照')
            self.manufacturing.file(owner,row['published_version_id'],name)
            version=self.manufacturing._row(owner,row['published_version_id'],public=True)
            return self.manufacturing._path(version)/name
        allowed={'crown.stl','raw.glb','raw.stl','mesh-report.json','parameters.json','interface.json','adaptation.scad','adapter-report.json'}
        if name not in allowed or not raw['asset']:raise APIError(404,'文件不存在')
        target=self.data/raw['asset']/name
        if not target.is_file():raise APIError(404,'文件尚未生成')
        return target

    def manufacture_package(self,owner,id):
        row=self.design(id,owner)
        if row['is_owner']:
            versions=self.manufacturing.list(owner,id)
            if not versions:raise APIError(409,'生成尚未完成或缺少模型版本')
            version_id=versions[0]['id']
        else:version_id=row.get('published_version_id')
        if not version_id:raise APIError(404,'没有公开的制造版本')
        return self.manufacturing.package(owner,version_id)
    def adapt_live_model(self,directory):
        from .adaptation import adapt_model
        return adapt_model(directory)
    def _prepare_download(self,row,directory):
        id=row['id']
        try:
            raw=directory/'raw.glb'
            if raw.is_symlink() or not raw.is_file() or hashlib.sha256(raw.read_bytes()).hexdigest()!=row['raw_sha256']:
                raise ValueError('已下载原稿缺失或校验失败，需要核对原任务文件')
            (directory/'interface.json').write_text(json.dumps(INTERFACE,indent=2))
            adaptation_error=None
            try:self.adapt_live_model(directory)
            except Exception as exc:
                # A failed converter may leave a partial output; never include it
                # as the final manufacturing STL in the subsequent snapshot.
                (directory/'crown.stl').unlink(missing_ok=True)
                adaptation_error='生成原稿已保存；接口适配需人工处理：'+str(exc)[:250]
                (directory/'adapter-report.json').write_text(json.dumps({'status':'manual_required','error':str(exc)[:300]}))
            self.store.execute("UPDATE designs SET status='needs_review',progress=100,asset=?,error=? WHERE id=?",(f'assets/{id}',adaptation_error,id))
            if not self.manufacturing.list(row['owner'],id):self.manufacturing.create(row['owner'],id)
        except Exception as exc:
            self.store.execute("UPDATE designs SET status='preparation_failed',error=? WHERE id=?",(str(exc)[:400],id))

    def job_tick(self):
        with self.job_lock:
            row=self.store.one("SELECT * FROM designs WHERE status IN ('queued','polling','preparing') AND next_run<=? ORDER BY created LIMIT 1",(stamp(),))
            if not row:return
            id=row['id'];directory=self.data/'assets'/id;directory.mkdir(parents=True,exist_ok=True)
            if row['provider']=='parametric':
                self.store.execute("UPDATE designs SET status='generating',progress=20 WHERE id=?",(id,))
                try:
                    make_crown(directory,row['prompt'],row['theme'],row['seed'])
                    self.store.execute("UPDATE designs SET status='needs_review',progress=100,asset=?,error=NULL WHERE id=?",(f'assets/{id}',id))
                    self.manufacturing.create(row['owner'],id)
                except Exception as exc:self.store.execute("UPDATE designs SET status='failed',error=? WHERE id=?",(str(exc)[:500],id))
                return
            if row['status']=='preparing':return self._prepare_download(row,directory)
            phase='polling' if row['task_id'] else 'queued'
            try:
                provider=Tripo(json.loads(row['request_snapshot']))
                if not provider.key:raise ValueError('Tripo key is missing')
                if not row['task_id']:
                    token=row['upload_token']
                    if row['reference_asset_id'] and not token:
                        phase='uploading'
                        self.store.execute("UPDATE designs SET status='uploading' WHERE id=?",(id,))
                        data=self.references.content(row['owner'],row['reference_asset_id'])
                        if hashlib.sha256(data).hexdigest()!=json.loads(row['request_snapshot'])['reference_sha256']:raise ValueError('参考图已改变，停止发送')
                        token=provider.upload_image(data)
                        self.store.execute('UPDATE designs SET upload_token=? WHERE id=?',(token,id))
                    phase='submitting'
                    self.store.execute("UPDATE designs SET status='submitting',progress=0 WHERE id=?",(id,))
                    task=provider.create(row['prompt'],image_token=token,seed=row['seed'])
                    self.store.execute("UPDATE designs SET task_id=?,status='polling',next_run=?,progress=0 WHERE id=?",(task,stamp()+3,id));return
                data=provider.poll(row['task_id']);status=data.get('status','')
                if status in {'failed','cancelled','banned','expired'}:raise ProviderRejected('Tripo task ended: '+status)
                if status not in {'success','queued','running','pending','processing'}:raise ValueError('Unknown Tripo task status')
                if status!='success':
                    progress=data.get('progress',0)
                    if isinstance(progress,bool) or not isinstance(progress,(int,float)) or not 0<=progress<=100:raise ValueError('Invalid Tripo progress')
                    self.store.execute("UPDATE designs SET progress=?,next_run=?,attempts=0,error=NULL WHERE id=?",(int(progress),stamp()+max(2,int(os.getenv('TRIPO_POLL_SECONDS','8'))),id));return
                phase='downloading'
                self.store.execute("UPDATE designs SET status='downloading',progress=100 WHERE id=?",(id,))
                output=data.get('output',{});url=output.get('model_url') or output.get('model') or output.get('pbr_model') or output.get('base_model')
                if not isinstance(url,str):raise ValueError('No model URL in Tripo response')
                download_model(url,directory/'raw.glb')
                sha=hashlib.sha256((directory/'raw.glb').read_bytes()).hexdigest()
                self.store.execute("UPDATE designs SET status='preparing',raw_sha256=?,attempts=0 WHERE id=?",(sha,id))
                row['raw_sha256']=sha
                self._prepare_download(row,directory)
            except SubmissionUnknown as exc:self.store.execute("UPDATE designs SET status='submission_unknown',error=? WHERE id=?",(str(exc)[:500],id))
            except ProviderRejected as exc:
                self.store.execute('UPDATE designs SET status=?,error=? WHERE id=?',('upload_failed' if phase=='uploading' else 'failed',str(exc)[:400],id))
            except Exception as exc:
                if phase=='uploading':
                    self.store.execute("UPDATE designs SET status='upload_failed',error=? WHERE id=?",('参考图上传失败；未提交模型生成。'+type(exc).__name__,id))
                elif phase=='submitting':
                    self.store.execute("UPDATE designs SET status='submission_unknown',error=? WHERE id=?",('提交结果未能保存，请核对已有任务 ID。',id))
                elif row['task_id'] and row['attempts']<5:
                    self.store.execute("UPDATE designs SET status='polling',error=?,attempts=attempts+1,next_run=? WHERE id=?",(type(exc).__name__+': 查询或下载失败，将有限重试。',stamp()+min(120,8*2**row['attempts']),id))
                else:
                    failed='download_failed' if phase=='downloading' else 'poll_failed' if row['task_id'] else 'configuration_failed'
                    self.store.execute('UPDATE designs SET status=?,error=? WHERE id=?',(failed,type(exc).__name__+': 请核对配置或恢复已有任务。',id))
    def start(self):
        def run():
            while not self.stop_event.wait(0.6):
                try:self.job_tick()
                except Exception as exc:print('Worker error:',type(exc).__name__,str(exc)[:200],file=sys.stderr)
        self.worker=threading.Thread(target=run,name='bloom-worker',daemon=True);self.worker.start()
        def sim_run():
            while not self.stop_event.wait(.6):
                try:self.simulator_tick()
                except Exception as exc:print('Simulator error:',type(exc).__name__,str(exc)[:200],file=sys.stderr)
        self.sim_worker=threading.Thread(target=sim_run,name='bloom-simulator',daemon=True);self.sim_worker.start()
    @contextmanager
    def request_scope(self):
        with self.lifecycle:
            if self.closing:raise APIError(503,'服务正在停止，请稍后重试')
            self.active_requests+=1
        try:yield
        finally:
            with self.lifecycle:self.active_requests-=1;self.lifecycle.notify_all()

    def close(self):
        with self.lifecycle:self.closing=True
        self.stop_event.set()
        if self.worker:self.worker.join()
        if self.sim_worker:self.sim_worker.join()
        with self.lifecycle:
            while self.active_requests:self.lifecycle.wait()
        self.data_lock.close()
