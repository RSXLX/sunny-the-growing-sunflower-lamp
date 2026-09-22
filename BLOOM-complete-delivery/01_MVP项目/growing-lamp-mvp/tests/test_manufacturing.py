"""Manufacturing invariants exercised through application interfaces, not a real printer."""
import base64
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch

from app.errors import APIError
from app.manufacturing import MANUAL_CHECKS
from app.service import Service
from app.store import SCHEMA, Store, uid, stamp, hash_secret
from app import migrations


class ManufacturingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Service(self.tmp.name, True)
        self.a = self.s.store.one("SELECT id FROM users WHERE email='alice@bloom.local'")['id']
        self.b = self.s.store.one("SELECT id FROM users WHERE email='bob@bloom.local'")['id']
        self.d = self.s.create_design(self.a, {'title': '制造测试', 'prompt': 'A connected forest crown', 'theme': 'forest'}, uid())['id']
        self.s.job_tick()
        self.v = self.s.manufacturing.list(self.a, self.d)[0]

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def error(self, status, callback, *args):
        with self.assertRaises(APIError) as caught:
            callback(*args)
        self.assertEqual(caught.exception.status, status, str(caught.exception))

    def approve(self, version=None):
        v = version or self.v
        m = self.s.manufacturing
        m.review(self.a, v['id'], {'action': 'submit', 'expected_status': 'draft', 'note': '提交测试制造候选版本，不是实物证据。'})
        return m.review(self.a, v['id'], {'action': 'approve', 'expected_status': 'submitted', 'note': '测试用人工检查输入，不代表真实制作。',
                                         'acknowledge': True, 'checks': {k: True for k in MANUAL_CHECKS}})

    def instance(self):
        self.approve()
        return self.s.create_instance(self.a, {'design_id': self.d, 'manufacturing_version_id': self.v['id']})['id']

    def test_design_review_cannot_bypass_manufacturing(self):
        self.s.review_design(self.a, self.d, {'acknowledge': True, 'note': '只有设计记录，尚未批准制造。'})
        self.error(409, self.s.create_instance, self.a, {'design_id': self.d})
        self.error(409, self.s.manufacturing.review, self.a, self.v['id'], {'action': 'approve', 'expected_status': 'draft',
                   'note': '试图跳过提交阶段进行制造审批。', 'acknowledge': True, 'checks': {k: True for k in MANUAL_CHECKS}})

    def test_manual_checks_are_required(self):
        self.s.manufacturing.review(self.a, self.v['id'], {'action': 'submit', 'expected_status': 'draft', 'note': '开始人工制造版本审核。'})
        self.error(400, self.s.manufacturing.review, self.a, self.v['id'], {'action': 'approve', 'expected_status': 'submitted', 'note': '漏掉人工检查项不能批准。', 'acknowledge': True})

    def test_instance_pins_approved_version(self):
        instance = self.instance()
        v2 = self.s.manufacturing.create(self.a, self.d, {'source_version_id': self.v['id']})
        self.assertEqual(v2['revision'], 2)
        self.assertEqual(v2['status'], 'draft')
        self.assertEqual(self.s.own('instances', instance, self.a)['manufacturing_version_id'], self.v['id'])
        self.error(409, self.s.create_instance, self.a, {'manufacturing_version_id': v2['id']})

    def test_snapshot_does_not_change_with_live_asset(self):
        original = self.s.manufacturing.file(self.a, self.v['id'], 'crown.stl')
        (self.s.data / 'assets' / self.d / 'crown.stl').write_bytes(b'modified-live-file')
        self.assertEqual(self.s.manufacturing.file(self.a, self.v['id'], 'crown.stl'), original)

    def test_tampered_snapshot_stops_approval_instance_and_package(self):
        self.approve()
        row = self.s.manufacturing._row(self.a, self.v['id'])
        (self.s.manufacturing._path(row) / 'crown.stl').write_bytes(b'tampered')
        self.error(409, self.s.create_instance, self.a, {'manufacturing_version_id': self.v['id']})
        self.error(409, self.s.manufacturing.package, self.a, self.v['id'])

    def test_missing_final_stl_cannot_pass(self):
        directory = self.s.data / 'assets' / self.d
        (directory / 'crown.stl').unlink()
        (directory / 'raw.glb').write_bytes(b'raw-provider-placeholder')
        v2 = self.s.manufacturing.create(self.a, self.d)
        self.assertFalse(v2['has_final_stl'])
        self.error(409, self.s.manufacturing.review, self.a, v2['id'], {'action': 'submit', 'expected_status': 'draft', 'note': '没有最终制造模型，只保存了原稿。'})

    def test_import_units_format_and_version_source(self):
        stl = self.s.manufacturing.file(self.a, self.v['id'], 'crown.stl')
        payload = {'stl_base64': base64.b64encode(stl).decode(), 'note': '人工修正版测试输入，未做物理验证。', 'source_version_id': self.v['id']}
        self.error(400, self.s.manufacturing.create, self.a, self.d, payload)
        payload['units'] = 'mm'
        v2 = self.s.manufacturing.create(self.a, self.d, payload)
        self.assertEqual(v2['origin'], 'manual_import')
        self.assertEqual(v2['source_version_id'], self.v['id'])
        self.error(400, self.s.manufacturing.create, self.a, self.d, dict(payload, stl_base64='%%%%'))
        self.error(400, self.s.manufacturing.create, self.a, self.d, dict(payload, stl_base64=base64.b64encode(b'not a mesh').decode()))

    def test_publication_is_a_file_snapshot(self):
        self.s.review_design(self.a, self.d, {'acknowledge': True, 'note': '允许公开这份数字设计检查记录。'})
        self.s.publish_design(self.a, self.d, {'public': True, 'confirm_rights': True, 'version_id': self.v['id']})
        directory = self.s.data / 'assets' / self.d
        (directory / 'private-diary.json').write_text('PRIVATE_CANARY')
        v2 = self.s.manufacturing.create(self.a, self.d)
        self.error(404, self.s.manufacturing.get, self.b, v2['id'])
        self.error(404, self.s.manufacturing.file, self.b, self.v['id'], 'parameters.json')
        with zipfile.ZipFile(io.BytesIO(self.s.manufacture_package(self.b, self.d))) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['version_id'], self.v['id'])
            self.assertFalse(manifest['manufacturing_approved'])
            for name, sha in manifest['sha256'].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), sha)
            self.assertNotIn('private-diary.json', archive.namelist())
            self.assertNotIn('parameters.json', archive.namelist())
        public = self.s.design(self.d, self.b)
        self.assertNotIn('review_note', public['versions'][0])
        self.s.publish_design(self.a, self.d, {'public': False})
        self.error(404, self.s.manufacturing.file, self.b, self.v['id'], 'crown.stl')

    def test_cross_account_mutations_rejected(self):
        self.error(404, self.s.manufacturing.list, self.b, self.d)
        self.error(404, self.s.manufacturing.create, self.b, self.d)
        self.error(404, self.s.manufacturing.review, self.b, self.v['id'], {'action': 'submit', 'expected_status': 'draft', 'note': '别的账号不能提交自己的制造版本。'})
        self.approve()
        self.error(404, self.s.create_instance, self.b, {'manufacturing_version_id': self.v['id']})

    def test_revoke_cancels_binding_and_blocks_new_instances(self):
        instance = self.instance()
        device = self.s.store.one("SELECT * FROM devices WHERE owner=? AND kind='simulator'", (self.a,))
        cmd = self.s.bind_instance(self.a, instance, {'device_id': device['id']})
        self.s.manufacturing.review(self.a, self.v['id'], {'action': 'revoke', 'expected_status': 'approved', 'note': '发现结构缺陷，撤销此版本制造批准。'})
        self.assertEqual(self.s.store.one('SELECT state FROM commands WHERE id=?', (cmd['id'],))['state'], 'failed')
        self.error(409, self.s.bind_instance, self.a, instance, {'device_id': device['id']})
        self.error(409, self.s.manufacturing.package, self.a, self.v['id'])
        self.assertEqual(self.s.own('instances', instance, self.a)['manufacturing_version_id'], self.v['id'])

    def test_stage_requires_records_and_preserves_failed_attempt(self):
        instance = self.instance()
        self.error(400, self.s.stage_instance, self.a, instance, {'stage': 'printed', 'acknowledge': True})
        for stage in ['printing', 'failed', 'printing', 'printed', 'verified']:
            self.s.stage_instance(self.a, instance, {'stage': stage, 'acknowledge': True, 'note': '测试制作记录，不代表实际打印或检查。',
                 'details': {'material': 'fixture resin', 'machine': 'fixture printer', 'interface_fit': True, 'retention': True, 'electrical_thermal': True}})
        records = self.s.fabrication_records(self.a, instance)['records']
        self.assertEqual([r['stage'] for r in records], ['printing','failed','printing','printed','verified'])
        self.error(404, self.s.fabrication_records, self.b, instance)

    def test_concurrent_revision_numbers_are_unique(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            versions = list(pool.map(lambda _: self.s.manufacturing.create(self.a, self.d), range(2)))
        self.assertEqual(sorted(v['revision'] for v in versions), [2,3])

    def test_poll_batch_rolls_back_failed_report(self):
        instance = self.instance()
        device = self.s.store.one("SELECT * FROM devices WHERE owner=? AND kind='simulator'", (self.a,))
        cmd = self.s.bind_instance(self.a, instance, {'device_id': device['id']})
        event = {'id': uid(), 'kind': 'tag_bound', 'payload': {'instance_id': instance, 'verified': True, 'tag_uid': 'SIM-TEST'}}
        self.error(400, self.s.poll_device, device, {'events': [event], 'reported': {'brightness': 999}})
        self.assertEqual(self.s.own('instances', instance, self.a)['stage'], 'planned')
        self.assertIsNone(self.s.store.one('SELECT * FROM events WHERE id=?', (event['id'],)))
        self.assertEqual(self.s.store.one('SELECT state FROM commands WHERE id=?', (cmd['id'],))['state'], 'queued')
        self.s.poll_device(device, {'events': [event]})
        self.assertEqual(self.s.own('instances', instance, self.a)['stage'], 'bound')
        self.error(409, self.s.poll_device, device, {'events': [dict(event, payload=dict(event['payload'], tag_uid='OTHER'))]})

    def test_bind_ack_alone_is_not_readback(self):
        instance = self.instance()
        device = self.s.store.one("SELECT * FROM devices WHERE owner=? AND kind='simulator'", (self.a,))
        cmd = self.s.bind_instance(self.a, instance, {'device_id': device['id']})
        self.s.poll_device(device, {'acks': [{'id': cmd['id'], 'ok': True}]})
        self.assertEqual(self.s.own('instances', instance, self.a)['stage'], 'planned')
        self.assertEqual(self.s.store.one('SELECT state FROM commands WHERE id=?', (cmd['id'],))['state'], 'queued')


class MigrationTests(unittest.TestCase):
    def test_old_instances_are_unverified_and_migration_is_repeatable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bloom.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(SCHEMA)
                db.execute('INSERT INTO users VALUES(?,?,?,?,?)', ('a','A','a@example.com','fixture',0))
                db.execute("INSERT INTO designs(id,owner,title,prompt,theme,seed,provider,status,created) VALUES('d','a','D','P','forest',1,'parametric','reviewed',0)")
                db.execute("INSERT INTO instances(id,owner,design_id,name,tag_payload,stage,created) VALUES('i','a','d','I','BLM1:old','bound',0)")
            store = Store(folder)
            before = store.one('SELECT * FROM instances')
            self.assertEqual(before['legacy_unverified'], 1)
            self.assertIsNone(before['manufacturing_version_id'])
            self.assertEqual(before['stage'], 'bound')
            self.assertEqual(Store(folder).one('SELECT * FROM instances'), before)
            self.assertEqual(len(store.all('SELECT * FROM schema_migrations')), len(migrations.MIGRATIONS))
            backups=list((Path(folder)/'backups').glob('*.sqlite3'))
            self.assertEqual(len(backups),1)
            with closing(sqlite3.connect(backups[0])) as backup:
                self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(backup.execute('SELECT stage FROM instances').fetchone()[0],'bound')
                self.assertNotIn('legacy_unverified',[r[1] for r in backup.execute('PRAGMA table_info(instances)')])

    def test_failed_schema_migration_is_atomic(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bloom.sqlite3'
            with closing(sqlite3.connect(path)) as db, db: db.executescript(SCHEMA)
            def broken(db):
                db.execute('ALTER TABLE instances ADD COLUMN should_rollback TEXT')
                raise RuntimeError('injected migration failure')
            with patch.object(migrations, 'MIGRATIONS', [(1, broken)]):
                with self.assertRaises(RuntimeError): migrations.migrate(path)
            with closing(sqlite3.connect(path)) as db, db:
                self.assertNotIn('should_rollback', [r[1] for r in db.execute('PRAGMA table_info(instances)')])
            Store(folder)


if __name__ == '__main__':
    unittest.main()
