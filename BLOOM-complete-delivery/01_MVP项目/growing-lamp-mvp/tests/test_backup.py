"""Backup/recovery invariants with isolated user data and synthetic evidence."""
import json,os,sqlite3,tempfile,threading,unittest,zipfile,stat
from pathlib import Path
from unittest.mock import patch
from app.backup import create_backup,verify_backup,restore_backup,audit,quarantine,BackupError,sha,info
from app.data_lock import DataLock,DataBusy
from app.service import Service
from app.manufacturing import MANUAL_CHECKS
from app.store import uid,stamp,hash_secret,verify_password
from test_fabrication import photo


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.data=self.root/'source';self.archive=self.root/'backup.zip';self.target=self.root/'restored'
        self.s=Service(self.data,True);self.a=self.s.store.one("SELECT id FROM users WHERE email='alice@bloom.local'")['id'];self.b=self.s.store.one("SELECT id FROM users WHERE email='bob@bloom.local'")['id']
        self.d=self.s.snapshot(self.a)['designs'][0];self.v=self.d['versions'][0]
        for action,expected in [('submit','draft'),('approve','submitted')]:self.s.manufacturing.review(self.a,self.v['id'],{'action':action,'expected_status':expected,'note':'QA fixture, no physical approval.','acknowledge':True,'checks':{k:True for k in MANUAL_CHECKS}})
        self.i=self.s.create_instance(self.a,{'manufacturing_version_id':self.v['id'],'name':'backup QA'})['id']
        self.s.stage_instance(self.a,self.i,{'stage':'printed','acknowledge':True,'note':'QA photo, not a real fabrication record.','details':{'material':'fixture','machine':'fixture'},'photos':[photo()]})
        self.pid=self.s.fabrication_records(self.a,self.i)['records'][0]['photos'][0]['id']
        self.ref=self.s.references.create(self.a,photo());self.s.close()
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def build(self):return create_backup(self.data,self.archive)
    def edit_zip(self,edit):
        with zipfile.ZipFile(self.archive) as z:entries={n:z.read(n) for n in z.namelist()}
        edit(entries)
        other=self.root/'changed.zip'
        with zipfile.ZipFile(other,'w') as z:
            for name,body in entries.items():z.writestr(name,body)
        return other
    def test_whole_dataset_restore_keeps_accounts_relationships_hashes(self):
        original=audit(self.data);self.build();self.assertTrue(verify_backup(self.archive)['valid']);restore_backup(self.archive,self.target)
        after=audit(self.target);self.assertEqual(original['files'],after['files']);self.assertEqual(original['counts'],after['counts'])
        app=Service(self.target,False)
        try:
            self.assertTrue(verify_password('BloomDemo!2026',app.store.one('SELECT password FROM users WHERE id=?',(self.a,))['password']))
            self.assertEqual(app.own('instances',self.i,self.a)['manufacturing_version_id'],self.v['id'])
            self.assertEqual(app.manufacturing.get(self.a,self.v['id'])['files'],self.v['files'])
            self.assertTrue(app.fabrication.content(self.a,self.pid));self.assertTrue(app.references.content(self.a,self.ref['id']))
        finally:app.close()
    def test_active_service_and_other_maintenance_are_rejected(self):
        app=Service(self.data,False)
        try:
            for fn,args in [(create_backup,(self.data,self.archive)),(audit,(self.data,)),(Service,(self.data,False))]:
                with self.assertRaises(DataBusy):fn(*args)
        finally:app.close()
        with DataLock(self.data),self.assertRaises(DataBusy):audit(self.data)
        self.assertFalse(self.archive.exists())
    def test_close_holds_lock_until_http_request_finishes(self):
        app=Service(self.data,False);done=threading.Event()
        with app.request_scope():
            thread=threading.Thread(target=lambda:(app.close(),done.set()));thread.start()
            self.assertFalse(done.wait(.05))
            with self.assertRaises(DataBusy):DataLock(self.data)
        thread.join(2);self.assertTrue(done.is_set())
        with DataLock(self.data):pass
    def test_restore_invalidates_live_credentials_commands_and_paid_work(self):
        app=Service(self.data,False)
        try:
            app.store.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hash_secret('old-session'),self.a,'csrf',stamp()+500))
            device,token=app.store.device(self.a,'restore fixture')
            app.command(self.a,device,'light',{'power':True,'brightness':45,'theme':'forest'})
            ids=[]
            for task in (None,'known-task-fixture'):
                did=uid();ids.append(did)
                app.store.execute("INSERT INTO designs(id,owner,title,prompt,theme,seed,provider,status,task_id,created,paid_reserved) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(did,self.a,'QA','QA restore','forest',1,'tripo','queued' if task is None else 'polling',task,stamp(),1))
        finally:app.close()
        self.build();restored=restore_backup(self.archive,self.target);self.assertEqual(restored['actions']['tripo_tasks_held'],2)
        app=Service(self.target,False)
        try:
            self.assertEqual(app.store.all('SELECT * FROM sessions'),[])
            self.assertNotEqual(app.store.one('SELECT token FROM devices WHERE id=?',(device,))['token'],hash_secret(token))
            self.assertEqual(app.store.one('SELECT state FROM commands WHERE device_id=?',(device,))['state'],'expired')
            self.assertEqual([app.store.one('SELECT status FROM designs WHERE id=?',(did,))['status'] for did in ids],['submission_unknown','poll_failed'])
            with patch('app.service.Tripo') as provider:app.job_tick();provider.assert_not_called()
            self.assertEqual(app.store.one('SELECT SUM(paid_reserved) AS n FROM designs')['n'],2)
        finally:app.close()
    def test_tampered_missing_and_extra_archive_members_rejected(self):
        self.build()
        for edit in [lambda e:e.__setitem__('bloom.sqlite3',e['bloom.sqlite3']+b'bad'),lambda e:e.pop('references/'+self.ref['id']+'.png'),lambda e:e.__setitem__('../outside','oops')]:
            archive=self.edit_zip(edit)
            with self.assertRaises(BackupError):restore_backup(archive,self.target)
            self.assertFalse(self.target.exists())
    def test_duplicate_zip_paths_and_symlinks_rejected(self):
        self.build()
        other=self.root/'duplicate.zip'
        with zipfile.ZipFile(self.archive) as src,zipfile.ZipFile(other,'w') as dest:
            for n in src.namelist():dest.writestr(n,src.read(n))
            with self.assertWarns(UserWarning):dest.writestr('manifest.json',src.read('manifest.json'))
        with self.assertRaises(BackupError):verify_backup(other)
        other=self.root/'symlink.zip'
        with zipfile.ZipFile(self.archive) as src,zipfile.ZipFile(other,'w') as dest:
            for n in src.namelist():
                entry=zipfile.ZipInfo(n)
                if n=='bloom.sqlite3':entry.external_attr=(stat.S_IFLNK|0o777)<<16
                dest.writestr(entry,src.read(n))
        with self.assertRaises(BackupError):verify_backup(other)
    def test_source_corruption_prevents_backup(self):
        p=self.data/'references'/(self.ref['id']+'.png');p.write_bytes(b'corrupt')
        with self.assertRaises(BackupError):self.build()
        self.assertFalse(self.archive.exists())
    def test_source_symlink_and_backup_inside_source_rejected(self):
        p=self.data/'references'/'external.png';p.symlink_to(self.data/'bloom.sqlite3')
        with self.assertRaises(BackupError):self.build()
        p.unlink()
        with self.assertRaises(BackupError):create_backup(self.data,self.data/'copy.zip')
    def test_existing_restore_and_backup_destinations_are_not_overwritten(self):
        self.build();original=self.archive.read_bytes()
        with self.assertRaises(BackupError):self.build()
        self.assertEqual(self.archive.read_bytes(),original)
        self.target.mkdir();(self.target/'keep').write_text('keep')
        with self.assertRaises(BackupError):restore_backup(self.archive,self.target)
        self.assertEqual((self.target/'keep').read_text(),'keep')
    def test_unreferenced_files_quarantined_with_plan_and_live_files_preserved(self):
        orphan=self.data/'references'/(uid()+'.png');orphan.write_bytes(b'unreferenced')
        pending=self.data/'assets'/self.d['id']/'unfinished.part';pending.write_bytes(b'working file')
        plan=audit(self.data);self.assertEqual(len(plan['orphans']),1)
        self.build();self.assertTrue(verify_backup(self.archive)['valid'])
        result=quarantine(self.data,plan);self.assertEqual(result['moved'],1);self.assertFalse(orphan.exists());self.assertTrue(pending.exists());self.assertTrue((Path(result['path'])/orphan.relative_to(self.data)).exists())
        self.assertEqual(audit(self.data)['files'],plan['files'])
    def test_stale_cleanup_plan_does_not_move_any_file(self):
        orphan=self.data/'references'/(uid()+'.png');orphan.write_bytes(b'orphan');plan=audit(self.data)
        orphan.write_bytes(b'changed')
        with self.assertRaises(BackupError):quarantine(self.data,plan)
        self.assertTrue(orphan.exists());self.assertFalse((self.data/'quarantine').exists())
    def test_restore_write_failure_leaves_marker_and_cannot_start(self):
        self.build()
        with patch('app.backup.shutil.move',side_effect=OSError('fixture disk error')),self.assertRaises(OSError):restore_backup(self.archive,self.target)
        self.assertTrue((self.target/'restore-incomplete').exists())
        with self.assertRaises(RuntimeError):Service(self.target,False)
        self.assertFalse((self.target/'bloom.sqlite3').exists())
    def test_added_sql_objects_are_not_restored(self):
        with sqlite3.connect(self.data/'bloom.sqlite3') as db:db.execute('CREATE VIEW unexpected AS SELECT * FROM users')
        with self.assertRaises(BackupError):self.build()
    def test_limits_prevent_oversized_extraction(self):
        self.build()
        with patch('app.backup.MAX_TOTAL',100),self.assertRaises(BackupError):verify_backup(self.archive)

if __name__=='__main__':unittest.main()
