"""Immutable manufacturing snapshots, review gates, and bounded STL import.

Design review, manufacturing approval and physical verification are independent.
Callers never construct snapshot paths or decide whether a version is usable.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import math
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .errors import APIError
from .adaptation import adapt_model, original_preview
from .geometry import INTERFACE, inspect_mesh, read_stl
from .store import stamp, uid

MAX_STL_BYTES = 12 * 1024 * 1024
MAX_FACES = 100_000
SOURCE_FILES = {'crown.stl', 'raw.glb', 'raw.stl', 'parameters.json', 'adaptation.scad', 'adapter-report.json', 'transform.json', 'original-preview.stl', 'preview-report.json'}
PUBLIC_FILES = {'crown.stl', 'interface.json', 'mesh-report.json'}
MANUAL_CHECKS = {
    'interface_dimensions': '安装接口与关键尺寸已检查',
    'wall_thickness': '薄壁、细节和连接强度已人工检查',
    'connected_geometry': '装饰连通性和悬空部件已检查',
    'material_process': '拟用材料、支撑和制造流程已检查',
    'appearance': '朝向、比例和外观符合设计意图',
}


def digest(content):
    return hashlib.sha256(content).hexdigest()


def note_text(value):
    if not isinstance(value, str) or not 8 <= len(value.strip()) <= 2000:
        raise APIError(400, '请填写 8–2000 字的具体检查或修改说明')
    return value.strip()


class Manufacturing:
    def __init__(self, store, data):
        self.store = store
        self.data = Path(data)
        self.root = self.data / 'manufacturing'
        self.root.mkdir(exist_ok=True)

    def _design(self, owner, design_id, db=None):
        sql = 'SELECT * FROM designs WHERE id=? AND owner=?'
        row = db.execute(sql, (design_id, owner)).fetchone() if db else self.store.one(sql, (design_id, owner))
        if row is None:
            raise APIError(404, '设计不存在或无权访问')
        return dict(row)

    def _row(self, owner, version_id, db=None, public=False):
        sql = '''SELECT v.*,d.owner,d.public,d.published_version_id,d.title,d.theme,d.provider,d.source_id
                 FROM manufacturing_versions v JOIN designs d ON d.id=v.design_id WHERE v.id=?'''
        row = db.execute(sql, (version_id,)).fetchone() if db else self.store.one(sql, (version_id,))
        if row is None:
            raise APIError(404, '制造版本不存在或无权访问')
        row = dict(row)
        if row['owner'] != owner and not (public and row['public'] and row['published_version_id'] == version_id):
            raise APIError(404, '制造版本不存在或无权访问')
        return row

    def _path(self, row):
        target = self.data / row['directory']
        if target.is_symlink() or target.resolve().parent != self.root.resolve():
            raise APIError(409, '制造版本目录异常，停止读取')
        return target

    def _verified_files(self, row):
        directory = self._path(row)
        files = {}
        for name, info in json.loads(row['manifest']).items():
            if name not in SOURCE_FILES | {'interface.json', 'mesh-report.json'}:
                raise APIError(409, '制造版本包含不支持的文件')
            path = directory / name
            if path.is_symlink() or not path.is_file():
                raise APIError(409, '制造版本文件缺失，需重新创建版本')
            data = path.read_bytes()
            if len(data) != info['bytes'] or digest(data) != info['sha256']:
                raise APIError(409, '制造版本文件已改变，审核失效；请创建新版本')
            files[name] = data
        return files

    def _mesh_report(self, path):
        if path.stat().st_size > MAX_STL_BYTES:
            raise APIError(413, 'STL 文件超过 12 MiB 限制')
        try:
            triangles = read_stl(path)
            if not triangles or len(triangles) > MAX_FACES:
                raise ValueError('triangle count')
            if any(not math.isfinite(c) for tri in triangles for point in tri for c in point):
                raise ValueError('non-finite coordinate')
            report = inspect_mesh(triangles)
            # Count collinear triangles as degenerate, not just repeated vertices.
            for tri in triangles:
                a, b, c = tri
                u = [b[i] - a[i] for i in range(3)]
                v = [c[i] - a[i] for i in range(3)]
                cross = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
                if sum(x*x for x in cross) < 1e-16 and len(set(tri)) == 3:
                    report['degenerate_faces'] += 1
            return report
        except (ValueError, UnicodeError, KeyError, OverflowError, IndexError) as exc:
            raise APIError(400, 'STL 无效：需要有限坐标、非空且不超过 100000 面的三角网格') from exc

    def create(self, owner, design_id, payload=None):
        payload = payload or {}
        design = self._design(owner, design_id)
        if not design['asset'] or design['status'] not in {'needs_review', 'reviewed'}:
            raise APIError(409, '先完成生成并保存可用的原始资产')
        source_version = payload.get('source_version_id') or None
        source = None
        if source_version:
            source = self._row(owner, source_version)
            if source['design_id'] != design_id:
                raise APIError(409, '修正版必须属于同一设计')
            source_files = self._verified_files(source)
        else:
            source_dir = self.data / design['asset']
            if source_dir.is_symlink() or not source_dir.resolve().is_relative_to((self.data / 'assets').resolve()):
                raise APIError(409, '设计资产目录无效')
            source_files = {}
            for name in SOURCE_FILES:
                path = source_dir / name
                if path.is_file() and not path.is_symlink():
                    if path.stat().st_size > 50 * 1024 * 1024:
                        raise APIError(413, '设计资产过大')
                    source_files[name] = path.read_bytes()
        transform = payload.get('transform')
        if transform is not None:
            if source is None or payload.get('stl_base64') is not None:raise APIError(400, '变换必须基于一个固定版本，且不能同时导入 STL')
            if 'raw.glb' not in source_files and 'crown.stl' not in source_files:raise APIError(409, '此版本没有可调整的模型')
            note_text(payload.get('note'))
        raw_import = payload.get('stl_base64')
        if raw_import is not None:
            if not isinstance(raw_import, str) or len(raw_import) > (MAX_STL_BYTES + 2) // 3 * 4:
                raise APIError(413, 'STL 文件超过上传限制')
            if payload.get('units') != 'mm':
                raise APIError(400, 'STL 不包含单位，请确认导入单位为 mm')
            note_text(payload.get('note'))
            try:
                source_files['crown.stl'] = base64.b64decode(raw_import, validate=True)
                source_files.pop('transform.json',None)
                source_files['adapter-report.json']=json.dumps({'status':'manual_import','physical_validation':'NOT_PERFORMED'}).encode()
            except (ValueError, binascii.Error) as exc:
                raise APIError(400, 'STL 编码无效') from exc
        version_id = uid()
        target = self.root / version_id
        staging = Path(tempfile.mkdtemp(prefix='.draft-', dir=self.root))
        try:
            for name, data in source_files.items():
                if name in SOURCE_FILES:
                    (staging / name).write_bytes(data)
            if transform is not None:
                try:adapt_model(staging, transform, source_version)
                except (ValueError, OSError) as exc:raise APIError(400, str(exc)) from exc
                except subprocess.TimeoutExpired as exc:raise APIError(504, '模型适配超时；原版本未改变，请改用人工修正后导入') from exc
            original_preview(staging)
            interface = json.loads(source['interface']) if source else dict(INTERFACE)
            if transform is not None and interface != INTERFACE:raise APIError(409, '旧接口版本需要显式迁移后再调整')
            (staging / 'interface.json').write_text(json.dumps(interface, ensure_ascii=False, indent=2))
            report = self._mesh_report(staging / 'crown.stl') if (staging / 'crown.stl').exists() else {'status': 'missing_final_stl', 'physical_validation': 'NOT_PERFORMED'}
            if (staging/'transform.json').exists():report['transform']=json.loads((staging/'transform.json').read_text())['parameters']
            if (staging/'preview-report.json').exists():report['original_preview']=json.loads((staging/'preview-report.json').read_text())
            (staging / 'mesh-report.json').write_text(json.dumps({k:v for k,v in report.items() if k!='original_preview'}, ensure_ascii=False, indent=2))
            manifest = {p.name: {'sha256': digest(p.read_bytes()), 'bytes': p.stat().st_size} for p in sorted(staging.iterdir())}
            if not any(name in manifest for name in {'crown.stl', 'raw.glb', 'raw.stl'}):
                raise APIError(409, '没有可以建立版本的模型文件')
            staging.rename(target)
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self._design(owner, design_id, db)
                revision = db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM manufacturing_versions WHERE design_id=?', (design_id,)).fetchone()[0]
                db.execute('''INSERT INTO manufacturing_versions
                    (id,design_id,revision,source_version_id,directory,manifest,mesh_report,interface,origin,created)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (version_id, design_id, revision, source_version, str(target.relative_to(self.data)),
                     json.dumps(manifest), json.dumps(report), json.dumps(interface), 'transform' if transform is not None else 'manual_import' if raw_import is not None else 'generated_snapshot', stamp()))
                if raw_import is not None or transform is not None:
                    db.execute('INSERT INTO manufacturing_reviews VALUES(?,?,?,?,?,?,?)',
                               (uid(), version_id, owner, 'transform' if transform is not None else 'import', payload['note'].strip(), '{}', stamp()))
            return self.get(owner, version_id)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(target, ignore_errors=True)
            raise

    def bootstrap(self):
        """Capture legacy/generated files without promoting historical review."""
        rows = self.store.all('''SELECT d.* FROM designs d WHERE d.asset IS NOT NULL
            AND d.status IN ('needs_review','reviewed')
            AND NOT EXISTS(SELECT 1 FROM manufacturing_versions v WHERE v.design_id=d.id)''')
        for row in rows:
            # Unreadable legacy files remain unverified and do not prevent startup.
            try:
                self.create(row['owner'], row['id'])
            except APIError:
                continue
        # Existing public samples are frozen, but remain unapproved manufacturing drafts.
        with self.store.connect() as db:
            db.execute('''UPDATE designs SET published_version_id=(
                SELECT id FROM manufacturing_versions WHERE design_id=designs.id ORDER BY revision LIMIT 1)
                WHERE public=1 AND published_version_id IS NULL''')

    def _serialize(self, row, owner):
        own = row['owner'] == owner
        report = json.loads(row['mesh_report'])
        files = json.loads(row['manifest'])
        if not own:
            report.pop('original_preview',None)
            files = {k: v for k, v in files.items() if k in PUBLIC_FILES}
        result = {key: row[key] for key in ('id', 'design_id', 'revision', 'status', 'origin', 'created')}
        result.update({'files': files, 'mesh_report': report, 'interface': json.loads(row['interface']),
                       'physical_validation': 'NOT_PERFORMED', 'has_final_stl': 'crown.stl' in files,
                       'asset_url': f"/api/versions/{row['id']}/asset/crown.stl" if 'crown.stl' in files else None,
                       'package_url': f"/api/versions/{row['id']}/package"})
        if own:
            result['original_preview_url']=f"/api/versions/{row['id']}/asset/original-preview.stl" if 'original-preview.stl' in files else None
            result['decoration_url']=f"/api/versions/{row['id']}/asset/raw.stl" if 'raw.stl' in files else None
            result['raw_download_url']=f"/api/versions/{row['id']}/asset/raw.glb" if 'raw.glb' in files else None
            result['transform_mode']='raw_model' if 'raw.glb' in files else 'final_stl' if 'crown.stl' in files else None
            result.update({'review_note': row['review_note'], 'review_checks': json.loads(row['review_checks'] or '{}'),
                           'reviewed_at': row['reviewed_at'], 'source_version_id': row['source_version_id'],
                           'manual_checks': MANUAL_CHECKS})
        return result

    def get(self, owner, version_id):
        return self._serialize(self._row(owner, version_id, public=True), owner)

    def list(self, owner, design_id):
        self._design(owner, design_id)
        ids = self.store.all('SELECT id FROM manufacturing_versions WHERE design_id=? ORDER BY revision DESC', (design_id,))
        return [self.get(owner, row['id']) for row in ids]

    def require_approved(self, owner, version_id, db=None):
        row = self._row(owner, version_id, db)
        if row['status'] != 'approved':
            raise APIError(409, '先完成对应制造版本的人工审核；旧设计检查记录不能替代制造批准')
        self._verified_files(row)
        return row

    def _digital_gate(self, row):
        files = self._verified_files(row)
        report = json.loads(row['mesh_report'])
        if 'crown.stl' not in files:
            raise APIError(409, '缺少最终 crown.stl，请导入人工修正后的制造文件')
        if not report.get('watertight_edge_count') or report.get('degenerate_faces') or abs(report.get('signed_volume_mm3', 0)) < 0.01:
            raise APIError(409, '基础网格检查未通过，请修正后创建新版本')
        dims = report['dimensions_mm']
        interface = json.loads(row['interface'])
        if max(dims[:2]) > interface['max_outer_diameter'] + .01 or dims[2] > interface['max_depth'] + .01:
            raise APIError(409, '模型超过当前安装接口允许的外形尺寸')

    def review(self, owner, version_id, payload):
        action = payload.get('action')
        expected = payload.get('expected_status')
        if action not in {'submit', 'approve', 'reject', 'revoke'}:
            raise APIError(400, '未知审核操作')
        note = note_text(payload.get('note'))
        checks = payload.get('checks', {})
        if not isinstance(checks, dict):
            raise APIError(400, '检查项格式错误')
        if action == 'approve':
            if payload.get('acknowledge') is not True or any(checks.get(key) is not True for key in MANUAL_CHECKS):
                raise APIError(400, '请逐项完成人工制造检查，并确认这不是实物认证')
        transition = {'submit': ('draft', 'submitted'), 'approve': ('submitted', 'approved'),
                      'reject': ('submitted', 'rejected'), 'revoke': ('approved', 'revoked')}
        before, after = transition[action]
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._row(owner, version_id, db)
            if expected != row['status'] or row['status'] != before:
                raise APIError(409, '版本状态已改变，请刷新后重试')
            if action in {'submit', 'approve'}:
                self._digital_gate(row)
            db.execute('''UPDATE manufacturing_versions SET status=?,review_note=?,review_checks=?,reviewed_by=?,reviewed_at=? WHERE id=?''',
                       (after, note, json.dumps({k: checks.get(k, False) for k in MANUAL_CHECKS}), owner, stamp(), version_id))
            db.execute('INSERT INTO manufacturing_reviews VALUES(?,?,?,?,?,?,?)', (uid(), version_id, owner, action, note, json.dumps(checks), stamp()))
            if action == 'revoke':
                # Cancel queued bindings to this version; already made objects retain their history.
                pending = db.execute("SELECT c.id,c.payload FROM commands c JOIN devices d ON d.id=c.device_id WHERE d.owner=? AND c.kind='bind' AND c.state='queued'", (owner,)).fetchall()
                for cmd in pending:
                    instance_id = json.loads(cmd['payload']).get('instance_id')
                    match = db.execute('SELECT id FROM instances WHERE id=? AND manufacturing_version_id=?', (instance_id, version_id)).fetchone()
                    if match:
                        db.execute("UPDATE commands SET state='failed',error='制造版本批准已撤销' WHERE id=?", (cmd['id'],))
        return self.get(owner, version_id)

    def file(self, owner, version_id, name):
        row = self._row(owner, version_id, public=True)
        if row['status'] == 'revoked':
            raise APIError(409, '该制造版本批准已撤销，停止下载')
        if row['owner'] != owner and name not in PUBLIC_FILES:
            raise APIError(404, '文件未公开')
        files = self._verified_files(row)
        if name not in files:
            raise APIError(404, '文件不存在')
        return files[name]

    def package(self, owner, version_id):
        row = self._row(owner, version_id, public=True)
        if row['status'] == 'revoked':
            raise APIError(409, '制造版本批准已撤销，停止下载')
        files = self._verified_files(row)
        if row['owner'] != owner:
            files = {k: v for k, v in files.items() if k in PUBLIC_FILES}
        manifest = {'design_id': row['design_id'], 'version_id': version_id, 'revision': row['revision'],
                    'title': row['title'], 'theme': row['theme'], 'provider': row['provider'],
                    'units': 'mm for manufacturing STL; original-preview.stl uses original scene units', 'interface': json.loads(row['interface']), 'status': row['status'],
                    'manufacturing_approved': row['status'] == 'approved', 'physical_validation': False,
                    'sha256': {name: digest(data) for name, data in files.items()}}
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr('READ_FIRST.txt', 'BLOOM 固定版本制造资料\n未批准版本仅为草案，不可视作制造放行。\n人工制造审核不是打印成功、装配验证或安全认证。请核对 manifest 的版本、状态和哈希。\n')
        return output.getvalue()
