"""Offline whole-dataset backup, verified restore, and conservative quarantine."""
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
import time
import zipfile
from contextlib import closing,contextmanager
from functools import lru_cache,wraps
from pathlib import Path,PurePosixPath
from .data_lock import DataLock
from .migrations import MIGRATIONS
from .store import SCHEMA,hash_secret

ROOTS={'assets','manufacturing','references','fabrication-photos'}
MAX_FILE=256*1024*1024
MAX_TOTAL=2*1024*1024*1024
MAX_FILES=20000
FORMAT='bloom-backup-1'
ID=re.compile(r'[a-f0-9]{32}')

class BackupError(ValueError):pass

def checked(fn):
    @wraps(fn)
    def run(*args,**kwargs):
        try:return fn(*args,**kwargs)
        except (KeyError,TypeError,AttributeError,UnicodeError,RecursionError,sqlite3.DatabaseError,zipfile.BadZipFile) as exc:
            raise BackupError('备份数据结构或文件格式无效：'+type(exc).__name__) from exc
    return run

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def info(path):
    return {'bytes':path.stat().st_size,'sha256':sha(path)}

def path_name(value):
    if not isinstance(value,str) or '\\' in value or '\x00' in value or ':' in value:raise BackupError('非法备份路径')
    p=PurePosixPath(value)
    if p.is_absolute() or any(x in {'','..','.'} for x in value.split('/')) or str(p)!=value:raise BackupError('非法备份路径')
    if value!='bloom.sqlite3' and (len(p.parts)<2 or p.parts[0] not in ROOTS):raise BackupError('备份包含非数据文件')
    return value

def files_under(root):
    files={}
    for name in ROOTS:
        base=root/name
        if base.is_symlink():raise BackupError('数据目录含符号链接')
        if not base.exists():continue
        if not base.is_dir():raise BackupError('数据目录不是文件夹')
        for folder,dirs,names in os.walk(base,followlinks=False):
            for entry in dirs+names:
                p=Path(folder)/entry
                if p.is_symlink():raise BackupError('数据目录含符号链接')
            for entry in names:
                p=Path(folder)/entry
                if not stat.S_ISREG(p.stat().st_mode):raise BackupError('数据目录含非普通文件')
                if p.stat().st_size>MAX_FILE:raise BackupError('单文件超过 256 MiB')
                files[path_name(p.relative_to(root).as_posix())]=p
                if len(files)>MAX_FILES:raise BackupError('文件数超过 20000')
    return files

@lru_cache(maxsize=1)
def expected_schema():
    with closing(sqlite3.connect(':memory:')) as db:
        db.executescript(SCHEMA)
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)')
        for _,apply in MIGRATIONS:apply(db)
        return schema(db)

def schema(db):
    return [(kind,name,table,' '.join((sql or '').split())) for kind,name,table,sql in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]

@contextmanager
def read_database(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_FILE:raise BackupError('数据库缺失、过大或是链接')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA trusted_schema=OFF')
        if schema(db)!=expected_schema():raise BackupError('数据库结构与当前应用不匹配；拒绝旧版、新版或额外 SQL 对象')
        if [r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')]!=[v for v,_ in MIGRATIONS]:raise BackupError('数据库迁移记录不匹配')
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchone():raise BackupError('数据库完整性或外键检查失败')
        yield db


def inventory(root):
    actual=files_under(root);needed=set();counts={}
    def require(name,expected=None):
        name=path_name(name);p=actual.get(name)
        if p is None:raise BackupError('缺少被引用的数据文件：'+name)
        if expected is not None and info(p)!=expected:raise BackupError('文件与数据库记录不匹配：'+name)
        needed.add(name)
    with read_database(root/'bloom.sqlite3') as db:
        for table in ('users','memories','designs','instances','manufacturing_versions','fabrication_records','reference_assets','fabrication_photos'):
            counts[table]=db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
        # Check ownership/design relationships beyond individual SQL foreign keys.
        bad_queries=[
          'SELECT 1 FROM instances i JOIN manufacturing_versions v ON v.id=i.manufacturing_version_id JOIN designs d ON d.id=i.design_id WHERE i.design_id!=v.design_id OR i.owner!=d.owner',
          'SELECT 1 FROM manufacturing_versions v JOIN manufacturing_versions s ON s.id=v.source_version_id WHERE v.design_id!=s.design_id',
          'SELECT 1 FROM designs d JOIN manufacturing_versions v ON v.id=d.published_version_id WHERE d.id!=v.design_id',
          'SELECT 1 FROM instances i JOIN instances s ON s.id=i.source_instance_id WHERE i.owner!=s.owner OR i.design_id!=s.design_id',
          'SELECT 1 FROM fabrication_records r JOIN instances i ON i.id=r.instance_id WHERE r.actor!=i.owner',
          'SELECT 1 FROM designs d JOIN reference_assets r ON r.id=d.reference_asset_id WHERE d.owner!=r.owner',
          'SELECT 1 FROM designs d JOIN memories m ON m.id=d.memory_id WHERE d.owner!=m.owner']
        if any(db.execute(q+' LIMIT 1').fetchone() for q in bad_queries):raise BackupError('数据库跨对象归属关系不一致')
        for d in db.execute('SELECT id,asset,raw_sha256 FROM designs'):
            if not ID.fullmatch(d['id']):raise BackupError('设计 ID 无效')
            prefix='assets/'+d['id']+'/'
            owned={n for n in actual if n.startswith(prefix)}
            # Keep every file in a live design directory, including interrupted jobs.
            needed.update(owned)
            if d['asset'] is not None and (d['asset']!=prefix[:-1] or not owned):raise BackupError('设计资产目录缺失或不匹配')
            if d['raw_sha256']:
                require(prefix+'raw.glb')
                if sha(actual[prefix+'raw.glb'])!=d['raw_sha256']:raise BackupError('原始 GLB 哈希与任务不一致')
        for v in db.execute('SELECT id,directory,manifest,interface FROM manufacturing_versions'):
            if not ID.fullmatch(v['id']) or v['directory']!='manufacturing/'+v['id']:raise BackupError('制造版本目录无效')
            manifest=json.loads(v['manifest'])
            if not isinstance(manifest,dict) or not manifest:raise BackupError('制造文件清单无效')
            for name,expected in manifest.items():
                if not isinstance(name,str) or '/' in name:raise BackupError('制造文件名无效')
                require(v['directory']+'/'+name,expected)
            if json.loads(actual[v['directory']+'/interface.json'].read_text())!=json.loads(v['interface']):raise BackupError('接口快照不匹配')
        for table,folder in [('reference_assets','references'),('fabrication_photos','fabrication-photos')]:
            for r in db.execute('SELECT id,bytes,sha256 FROM '+table):
                if not ID.fullmatch(r['id']):raise BackupError('图片 ID 无效')
                require(folder+'/'+r['id']+'.png',{'bytes':r['bytes'],'sha256':r['sha256']})
    return {'files':{n:info(actual[n]) for n in sorted(needed)},'orphans':{n:info(actual[n]) for n in sorted(set(actual)-needed)},'counts':counts}


@contextmanager
def snapshot(data):
    data=Path(data).resolve()
    if (data/'bloom.sqlite3').is_symlink() or not (data/'bloom.sqlite3').is_file():raise BackupError('指定目录没有普通 BLOOM 数据库文件')
    if (data/'restore-incomplete').exists():raise BackupError('此目录恢复未完成，请先从备份重新恢复')
    with DataLock(data),tempfile.TemporaryDirectory(prefix='bloom-maintenance-') as folder:
        # Reserve the SQLite writer too, so non-server SQL writes cannot race files.
        with closing(sqlite3.connect(data/'bloom.sqlite3',timeout=0)) as reserved:
            reserved.execute('BEGIN IMMEDIATE')
            target=Path(folder)/'bloom.sqlite3'
            with closing(sqlite3.connect((data/'bloom.sqlite3').as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(target)) as copied:source.backup(copied)
            yield data,target


@checked
def audit(data):
    with snapshot(data) as (root,dbcopy):
        result=inventory(root)
        return {'format':'bloom-cleanup-plan-1','data':str(root),'database_sha256':sha(dbcopy),**result}


@checked
def create_backup(data,output):
    output=Path(output).absolute();source=Path(data).resolve()
    if output.exists() or output.is_symlink():raise BackupError('输出文件已存在')
    if output.resolve().is_relative_to(source):raise BackupError('备份包须保存到数据目录之外')
    output.parent.mkdir(parents=True,exist_ok=True)
    with snapshot(source) as (root,dbcopy):
        result=inventory(root);manifest={'format':FORMAT,'created':int(time.time()),'schema_versions':[v for v,_ in MIGRATIONS],
            'counts':result['counts'],'files':{'bloom.sqlite3':info(dbcopy),**result['files']},'excluded_orphan_files':len(result['orphans'])}
        if sum(v['bytes'] for v in manifest['files'].values())>MAX_TOTAL:raise BackupError('备份超过 2 GiB')
        fd,name=tempfile.mkstemp(prefix='.bloom-backup-',dir=output.parent);os.close(fd);temporary=Path(name)
        try:
            with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
                z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
                for path in manifest['files']:z.write(dbcopy if path=='bloom.sqlite3' else root/path,path)
            verify_backup(temporary)
            os.chmod(temporary,0o600)
            # Publish without overwriting even if an output appeared concurrently.
            os.link(temporary,output)
        finally:temporary.unlink(missing_ok=True)
        return {'path':str(output),'sha256':sha(output),'files':len(manifest['files']),'counts':manifest['counts'],'excluded_orphan_files':len(result['orphans'])}


@contextmanager
def unpack_verified(archive,parent=None):
    archive=Path(archive)
    if not archive.is_file() or archive.stat().st_size>MAX_TOTAL:raise BackupError('备份包不存在或超过 2 GiB')
    with tempfile.TemporaryDirectory(prefix='.bloom-restore-',dir=parent) as folder,zipfile.ZipFile(archive) as z:
        root=Path(folder);entries=z.infolist();names=[x.filename for x in entries]
        if len(entries)>MAX_FILES+2 or len(set(names))!=len(names):raise BackupError('备份文件数超限或路径重复')
        if 'manifest.json' not in names or z.getinfo('manifest.json').file_size>4*1024*1024:raise BackupError('缺少有效备份清单')
        manifest=json.loads(z.read('manifest.json'))
        if manifest.get('format')!=FORMAT or manifest.get('schema_versions')!=[v for v,_ in MIGRATIONS]:raise BackupError('备份格式或应用版本不兼容')
        expected=manifest.get('files')
        if not isinstance(expected,dict) or 'bloom.sqlite3' not in expected or set(names)!={'manifest.json',*expected}:raise BackupError('备份成员与清单不一致')
        if sum(x.file_size for x in entries)>MAX_TOTAL:raise BackupError('展开数据超过 2 GiB')
        for name,record in expected.items():
            path_name(name);entry=z.getinfo(name)
            if entry.is_dir() or stat.S_IFMT(entry.external_attr>>16) not in (0,stat.S_IFREG) or entry.flag_bits&1:raise BackupError('备份含链接、目录或加密成员')
            if not isinstance(record,dict) or set(record)!={'bytes','sha256'} or type(record['bytes']) is not int or not 0<=record['bytes']<=MAX_FILE or entry.file_size!=record['bytes'] or not isinstance(record['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',record['sha256']):raise BackupError('备份文件声明无效')
            dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True);h=hashlib.sha256();size=0
            with z.open(entry) as src,dest.open('xb') as out:
                while block:=src.read(1024*1024):
                    size+=len(block)
                    if size>record['bytes']:raise BackupError('文件展开超出声明')
                    h.update(block);out.write(block)
            if size!=record['bytes'] or h.hexdigest()!=record['sha256']:raise BackupError('备份文件哈希不匹配：'+name)
        result=inventory(root)
        if result['orphans'] or set(result['files'])!=set(expected)-{'bloom.sqlite3'} or result['counts']!=manifest.get('counts'):raise BackupError('备份引用关系与文件清单不一致')
        yield root,manifest


@checked
def verify_backup(archive):
    with unpack_verified(archive) as (_,manifest):
        return {'valid':True,'format':FORMAT,'files':len(manifest['files']),'counts':manifest['counts'],'sha256':sha(Path(archive))}


@checked
def restore_backup(archive,target):
    target=Path(target).absolute()
    if target.exists() or target.is_symlink():raise BackupError('恢复目录必须不存在；不会覆盖或合并现有数据')
    target.parent.mkdir(parents=True,exist_ok=True)
    with unpack_verified(archive,target.parent) as (root,manifest):
        actions={}
        with closing(sqlite3.connect(root/'bloom.sqlite3')) as db,db:
            actions['sessions_invalidated']=db.execute('DELETE FROM sessions').rowcount
            actions['commands_expired']=db.execute("UPDATE commands SET state='expired',error='备份恢复后过期，需重新发出命令' WHERE state='queued'").rowcount
            actions['tripo_tasks_held']=db.execute("UPDATE designs SET status=CASE WHEN task_id IS NULL THEN 'submission_unknown' ELSE 'poll_failed' END,error='备份恢复：先核对 Tripo 控制台已有任务，再手动恢复；不会自动重新生成' WHERE provider='tripo' AND status IN ('queued','uploading','submitting','generating','polling','downloading','preparing')").rowcount
            devices=db.execute('SELECT id FROM devices').fetchall();actions['device_credentials_invalidated']=len(devices)
            for (device,) in devices:db.execute("UPDATE devices SET token=?,seen=0,reported=? WHERE id=?",(hash_secret(secrets.token_urlsafe(32)),json.dumps({'power':False,'brightness':0,'projection':0,'theme':'sunflower','instance_id':None,'temperature_c':None,'recovery_pending':True}),device))
        inventory(root)
        (root/'restore-report.json').write_text(json.dumps({'format':FORMAT,'restored_at':int(time.time()),'source_sha256':sha(Path(archive)),'actions':actions},ensure_ascii=False,indent=2))
        # Reserve a never-existing destination, then publish while holding its lock.
        target.mkdir(mode=0o700)
        published=[]
        try:
            with DataLock(target):
                (target/'restore-incomplete').write_text('Restore is not committed. Do not start BLOOM here.\n')
                for child in root.iterdir():
                    shutil.move(str(child),str(target/child.name));published.append(target/child.name)
                inventory(target)
                (target/'restore-incomplete').unlink()
        except BaseException:
            # Only remove files this restoration created; never unrelated contents.
            for child in published:
                if child.is_dir():shutil.rmtree(child)
                else:child.unlink(missing_ok=True)
            raise
        return {'path':str(target),'counts':manifest['counts'],'actions':actions}


@checked
def quarantine(data,plan):
    with snapshot(data) as (root,dbcopy):
        current={'format':'bloom-cleanup-plan-1','data':str(root),'database_sha256':sha(dbcopy),**inventory(root)}
        if current!=plan:raise BackupError('清理计划已变化；请重新 audit 后核对')
        if not current['orphans']:return {'moved':0,'path':None}
        dest=root/'quarantine'/('orphans-'+str(time.time_ns()));dest.mkdir(parents=True,mode=0o700)
        moved=[]
        try:
            for name in current['orphans']:
                to=dest/name;to.parent.mkdir(parents=True,exist_ok=True);os.replace(root/name,to);moved.append(name)
            (dest/'manifest.json').write_text(json.dumps({'files':current['orphans'],'created':int(time.time())},ensure_ascii=False,indent=2))
        except BaseException:
            for name in reversed(moved):os.replace(dest/name,root/name)
            raise
        return {'moved':len(moved),'path':str(dest),'permanently_deleted':0}
