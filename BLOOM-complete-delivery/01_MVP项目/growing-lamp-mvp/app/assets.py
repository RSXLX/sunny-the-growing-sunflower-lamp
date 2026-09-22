"""Private, canonical reference images. Uploading here never sends to a provider."""
from __future__ import annotations
import base64
import binascii
import hashlib
import io
import os
import tempfile
import threading
import warnings
from pathlib import Path
from .errors import APIError
from .store import uid, stamp

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError:
    Image = None

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 12_000_000
MAX_EDGE = 6000
MAX_ASSETS = 100
MAX_TOTAL_BYTES = 200 * 1024 * 1024
_decode_lock = threading.Lock()


def canonical_image(payload):
    if Image is None:
        raise APIError(503, '图片处理未安装：请在运行环境安装 requirements.txt')
    encoded = payload.get('data_base64')
    if not isinstance(encoded, str) or len(encoded) > (MAX_BYTES + 2) // 3 * 4:
        raise APIError(413, '参考图不能超过 8 MiB')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise APIError(400, '图片编码无效') from exc
    if not raw or len(raw) > MAX_BYTES:
        raise APIError(413, '参考图为空或超过 8 MiB')
    # Verify the format and fully decode; do not trust a filename or MIME label.
    # Serialize decoding to bound concurrent memory and isolate warnings filters.
    try:
        with _decode_lock, warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw), formats=['JPEG', 'PNG']) as image:
                if image.width * image.height > MAX_PIXELS or max(image.size) > MAX_EDGE or min(image.size) < 16:
                    raise APIError(400, '图片尺寸须至少 16×16，不超过 1200 万像素及 6000 像素边长')
                if getattr(image, 'n_frames', 1) != 1:
                    raise APIError(400, '请上传单帧 JPEG 或 PNG，暂不支持动画')
                image.verify()
            with Image.open(io.BytesIO(raw), formats=['JPEG', 'PNG']) as image:
                image.load()
                oriented = ImageOps.exif_transpose(image)
                try:
                    pixels = oriented.convert('RGBA' if 'A' in oriented.getbands() or 'transparency' in oriented.info else 'RGB')
                    pixels.info.clear()  # EXIF, XMP, ICC, comments never leave this module.
                    output = io.BytesIO()
                    pixels.save(output, format='PNG')
                    width, height = pixels.size
                    pixels.close()
                finally:
                    oriented.close()
        data = output.getvalue()
    except APIError:
        raise
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise APIError(400, '图片无法完整解码，请使用有效的单帧 JPEG 或 PNG') from exc
    if len(data) > MAX_BYTES:
        raise APIError(413, '清理后的图片超过 8 MiB，请缩小图片后上传')
    return data, width, height


class ReferenceAssets:
    def __init__(self, store):
        self.store = store
        self.root = store.directory / 'references'
        self.root.mkdir(mode=0o700, exist_ok=True)

    @staticmethod
    def available():
        return Image is not None

    def _row(self, owner, asset_id, db=None):
        sql = 'SELECT * FROM reference_assets WHERE id=? AND owner=?'
        row = db.execute(sql, (asset_id, owner)).fetchone() if db else self.store.one(sql, (asset_id, owner))
        if row is None:
            raise APIError(404, '参考图不存在或无权访问')
        return dict(row)

    @staticmethod
    def _public(row):
        return {k: v for k, v in row.items() if k != 'owner'} | {'url': f"/api/reference-assets/{row['id']}/content"}

    def list(self, owner):
        return [self._public(r) for r in self.store.all('SELECT * FROM reference_assets WHERE owner=? ORDER BY created DESC,rowid DESC', (owner,))]

    def get(self, owner, asset_id):
        return self._public(self._row(owner, asset_id))

    def content(self, owner, asset_id):
        row = self._row(owner, asset_id)
        path = self.root / (row['id'] + '.png')
        if path.is_symlink() or not path.is_file() or path.stat().st_size != row['bytes']:
            raise APIError(409, '参考图文件缺失或改变，请重新上传')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row['sha256']:
            raise APIError(409, '参考图校验失败，请重新上传')
        return data

    def create(self, owner, payload):
        name = payload.get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise APIError(400, '图片名称长度须为 1–120 字符')
        data, width, height = canonical_image(payload)
        asset_id = uid()
        target = self.root / (asset_id + '.png')
        fd, staging = tempfile.mkstemp(prefix='.upload-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                count, total = db.execute('SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM reference_assets WHERE owner=?', (owner,)).fetchone()
                if count >= MAX_ASSETS or total + len(data) > MAX_TOTAL_BYTES:
                    raise APIError(409, '参考图库已达 100 张或 200 MiB 上限，请删除未使用的图片')
                Path(staging).replace(target)
                db.execute('INSERT INTO reference_assets VALUES(?,?,?,?,?,?,?,?,?)',
                           (asset_id, owner, name.strip(), 'image/png', width, height, len(data), hashlib.sha256(data).hexdigest(), stamp()))
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            Path(staging).unlink(missing_ok=True)
        return self.get(owner, asset_id)

    def delete(self, owner, asset_id):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._row(owner, asset_id, db)
            if db.execute('SELECT 1 FROM designs WHERE reference_asset_id=?', (asset_id,)).fetchone():
                raise APIError(409, '参考图已被生成任务引用，请保留任务来源')
            db.execute('DELETE FROM reference_assets WHERE id=?', (asset_id,))
        (self.root / (asset_id + '.png')).unlink(missing_ok=True)
        return {'ok': True}
