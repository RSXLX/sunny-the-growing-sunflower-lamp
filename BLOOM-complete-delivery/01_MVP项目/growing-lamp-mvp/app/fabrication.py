"""Append-only fabrication evidence. User records are not sensor verification."""
import hashlib
import json
import math
from .assets import canonical_image
from .errors import APIError
from .manufacturing import note_text
from .store import uid,stamp

TRANSITIONS={'planned':{'printing','printed'},'printing':{'printed','failed'},'failed':{'printing'},'printed':{'verified','failed'},'verified':{'failed'}}
PHOTO_LIMIT=2*1024*1024

def field(value,label,maximum=120):
    if not isinstance(value,str) or not 1<=len(value.strip())<=maximum:raise APIError(400,label+'格式无效')
    return value.strip()


def measurements(values):
    if not isinstance(values,list) or len(values)>20:raise APIError(400,'最多记录 20 个测量值')
    out=[]
    for item in values:
        if not isinstance(item,dict) or set(item)-{'name','value','unit','method'}:raise APIError(400,'测量记录格式无效')
        value=item.get('value');unit=item.get('unit')
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not -10000<=value<=10000:raise APIError(400,'测量值须为有限数字，范围 -10000–10000')
        if unit not in ('mm','°C','g','N'):raise APIError(400,'测量单位须为 mm、°C、g 或 N')
        out.append({'name':field(item.get('name'),'测量名称',60),'value':value,'unit':unit,'method':field(item.get('method'),'测量方法',200)})
    return out


class Fabrication:
    def __init__(self,store,manufacturing):
        self.store=store;self.manufacturing=manufacturing
        self.root=store.directory/'fabrication-photos';self.root.mkdir(mode=0o700,exist_ok=True)

    def own(self,owner,instance,db=None):
        sql='SELECT * FROM instances WHERE id=? AND owner=?'
        row=db.execute(sql,(instance,owner)).fetchone() if db else self.store.one(sql,(instance,owner))
        if not row:raise APIError(404,'实物档案不存在或无权访问')
        return row

    def records(self,owner,instance):
        current=dict(self.own(owner,instance));current.pop('creation_key',None);current.pop('creation_hash',None)
        rows=self.store.all('SELECT * FROM fabrication_records WHERE instance_id=? ORDER BY created,rowid',(instance,))
        for row in rows:
            row['details']=json.loads(row['details']);row.pop('request_key',None);row.pop('request_hash',None)
            row['photos']=self.store.all('SELECT * FROM fabrication_photos WHERE record_id=? ORDER BY rowid',(row['id'],))
            for photo in row['photos']:photo['url']=f"/api/fabrication-photos/{photo['id']}/content"
        return {'records':rows,'instance':current,'source':{k:self.own(owner,current['source_instance_id'])[k] for k in ('id','name','stage','manufacturing_version_id')} if current['source_instance_id'] else None,
                'remakes':self.store.all('SELECT id,name,manufacturing_version_id,stage FROM instances WHERE source_instance_id=? AND owner=? ORDER BY created,rowid',(instance,owner))}

    def content(self,owner,photo_id):
        row=self.store.one('''SELECT p.* FROM fabrication_photos p JOIN fabrication_records r ON r.id=p.record_id
            JOIN instances i ON i.id=r.instance_id WHERE p.id=? AND i.owner=?''',(photo_id,owner))
        if not row:raise APIError(404,'制作照片不存在或无权访问')
        path=self.root/(photo_id+'.png')
        if path.is_symlink() or not path.is_file() or path.stat().st_size!=row['bytes']:raise APIError(409,'制作照片缺失或改变')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=row['sha256']:raise APIError(409,'制作照片校验失败')
        return data

    def record(self,owner,instance,p):
        self.own(owner,instance)
        stage=p.get('stage')
        if not isinstance(stage,str) or stage not in {'printing','printed','failed','verified','annotation'}:raise APIError(400,'未知制作记录类型')
        if p.get('acknowledge') is not True:raise APIError(400,'必须确认是实际记录；演示请使用模拟绑定')
        note=p.get('note');details=p.get('details',{})
        if not isinstance(details,dict):raise APIError(400,'制作记录格式无效')
        details=dict(details)
        try:
            if len(json.dumps(details,allow_nan=False))>12000:raise ValueError()
            digest=hashlib.sha256(json.dumps(p,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
        except (ValueError,TypeError):raise APIError(400,'制作记录格式或数值无效')
        details['measurements']=measurements(details.get('measurements',[]))
        key=p.get('request_key')
        if key is not None:key=field(key,'请求标识',100)
        photos=p.get('photos',[])
        if not isinstance(photos,list) or len(photos)>3:raise APIError(400,'每条记录最多 3 张照片')
        def existing(db):
            old=db.execute('SELECT id,request_hash,attempt FROM fabrication_records WHERE instance_id=? AND request_key=?',(instance,key)).fetchone() if key else None
            if old and old['request_hash']!=digest:raise APIError(409,'此提交标识已用于另一份记录，请刷新后重新填写')
            return {'ok':True,'id':old['id'],'attempt':old['attempt'],'reused':True} if old else None
        # Retry before decoding the same images again.
        with self.store.connect() as db:
            previous=existing(db)
            if previous:return previous
        prepared=[]
        for photo in photos:
            if not isinstance(photo,dict):raise APIError(400,'照片格式无效')
            name=field(photo.get('name'),'照片名称')
            encoded=photo.get('data_base64')
            if not isinstance(encoded,str) or len(encoded)>(PHOTO_LIMIT+2)//3*4:raise APIError(413,'每张制作照片不能超过 2 MiB')
            data,width,height=canonical_image(photo)
            if len(data)>PHOTO_LIMIT:raise APIError(413,'清理后的制作照片超过 2 MiB，请缩小后上传')
            prepared.append((uid(),name,data,width,height))
        written=[]
        try:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE');row=self.own(owner,instance,db)
                previous=existing(db)
                if previous:return previous
                if p.get('expected_stage') is not None and p['expected_stage']!=row['stage']:raise APIError(409,'制作状态已改变，请刷新记录后重试')
                if row['simulated'] or row['legacy_unverified'] or not row['manufacturing_version_id']:raise APIError(409,'模拟或旧档案不能登记真实制作证据，请建立新的制作档案')
                if stage!='annotation' and stage not in TRANSITIONS.get(row['stage'],set()):raise APIError(409,'制作状态顺序无效，请刷新后按实际进度记录')
                note=note_text(note)
                # Revocation must not prevent documenting a discovered failure.
                if stage not in {'failed','annotation'}:self.manufacturing.require_approved(owner,row['manufacturing_version_id'],db)
                if stage=='printed':
                    details['material']=field(details.get('material'),'实际材料');details['machine']=field(details.get('machine'),'打印设备或服务')
                if stage=='verified' and any(details.get(k) is not True for k in ('interface_fit','retention','electrical_thermal')):raise APIError(400,'请记录接口配合、可靠固定以及电气与温度检查结果')
                count,total=db.execute('''SELECT COUNT(*),COALESCE(SUM(p.bytes),0) FROM fabrication_photos p JOIN fabrication_records r ON r.id=p.record_id
                    JOIN instances i ON i.id=r.instance_id WHERE i.owner=?''',(owner,)).fetchone()
                if count+len(prepared)>500 or total+sum(len(x[2]) for x in prepared)>300*1024*1024:raise APIError(409,'制作照片已达 500 张或 300 MiB 上限')
                attempt=db.execute('SELECT COALESCE(MAX(attempt),1) FROM fabrication_records WHERE instance_id=?',(instance,)).fetchone()[0]
                if row['stage']=='failed' and stage=='printing':attempt+=1
                record_id=uid()
                db.execute('INSERT INTO fabrication_records(id,instance_id,actor,stage,note,details,created,attempt,request_key,request_hash) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (record_id,instance,owner,stage,note,json.dumps(details,ensure_ascii=False),stamp(),attempt,key,digest))
                for photo_id,name,data,width,height in prepared:
                    path=self.root/(photo_id+'.png');written.append(path)
                    with path.open('xb') as f:f.write(data)
                    db.execute('INSERT INTO fabrication_photos VALUES(?,?,?,?,?,?,?)',(photo_id,record_id,name,len(data),hashlib.sha256(data).hexdigest(),width,height))
                if stage!='annotation':db.execute('UPDATE instances SET stage=? WHERE id=?',(stage,instance))
            return {'ok':True,'id':record_id,'attempt':attempt,'reused':False}
        except Exception:
            for path in written:path.unlink(missing_ok=True)
            raise
