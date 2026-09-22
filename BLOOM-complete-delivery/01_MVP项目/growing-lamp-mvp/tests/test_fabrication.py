"""Evidence lifecycle using synthetic photos; no assertion of physical completion."""
import base64,io,json,tempfile,unittest,zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from PIL import Image,PngImagePlugin
from app.service import Service
from app.errors import APIError
from app.manufacturing import MANUAL_CHECKS
from app.store import uid,stamp
from app import migrations


def photo():
    buf=io.BytesIO();meta=PngImagePlugin.PngInfo();meta.add_text('location','PRIVATE_LOCATION')
    with Image.new('RGB',(64,48),'green') as im:im.save(buf,'PNG',pnginfo=meta)
    return {'name':'QA fixture only.png','data_base64':base64.b64encode(buf.getvalue()).decode()}


class FabricationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Service(self.tmp.name,True)
        self.a=self.s.store.one("SELECT id FROM users WHERE email='alice@bloom.local'")['id'];self.b=self.s.store.one("SELECT id FROM users WHERE email='bob@bloom.local'")['id']
        self.d=self.s.snapshot(self.a)['designs'][0];self.v=self.d['versions'][0];self.approve(self.v)
        self.i=self.s.create_instance(self.a,{'manufacturing_version_id':self.v['id'],'name':'QA only'})['id']
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def approve(self,v):
        for action,expected in [('submit','draft'),('approve','submitted')]:self.s.manufacturing.review(self.a,v['id'],{'action':action,'expected_status':expected,'note':'QA fixture, no real physical validation.','acknowledge':True,'checks':{k:True for k in MANUAL_CHECKS}})
    def payload(self,stage='printed',**overrides):
        return {'stage':stage,'acknowledge':True,'note':'QA fixture only; no actual fabrication.', 'request_key':uid(),'details':{'material':'fixture','machine':'fixture','measurements':[{'name':'孔径','value':76.1,'unit':'mm','method':'fixture caliper'}]},'photos':[photo()],**overrides}
    def error(self,status,fn,*args):
        with self.assertRaises(APIError) as caught:fn(*args)
        self.assertEqual(caught.exception.status,status)
    def test_private_canonical_photos_and_measurements(self):
        p=self.payload();out=self.s.stage_instance(self.a,self.i,p);record=self.s.fabrication_records(self.a,self.i)['records'][0]
        self.assertEqual(record['id'],out['id']);self.assertEqual(record['details']['measurements'][0]['value'],76.1)
        pid=record['photos'][0]['id'];data=self.s.fabrication.content(self.a,pid)
        self.assertNotIn(b'PRIVATE_LOCATION',data);self.error(404,self.s.fabrication.content,self.b,pid)
        self.s.publish_design(self.a,self.d['id'],{'public':True,'confirm_rights':True,'version_id':self.v['id']})
        self.error(404,self.s.fabrication_records,self.b,self.i)
        package=self.s.manufacturing.package(self.b,self.v['id'])
        with zipfile.ZipFile(io.BytesIO(package)) as z:self.assertFalse(any('photo' in n for n in z.namelist()))
        self.s.close();self.s=Service(self.tmp.name,True);self.assertEqual(self.s.fabrication.content(self.a,pid),data)
    def test_duplicate_concurrent_request_is_one_record(self):
        p=self.payload()
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:self.s.stage_instance(self.a,self.i,p),range(2)))
        self.assertEqual(results[0]['id'],results[1]['id']);self.assertEqual(len(list(self.s.fabrication.root.glob('*.png'))),1)
        self.error(409,self.s.stage_instance,self.a,self.i,p|{'note':'Changed payload, same retry identifier.'})
    def test_failure_retry_and_annotation_preserve_attempts(self):
        for stage in ['printing','failed','annotation','printing','printed']:
            self.s.stage_instance(self.a,self.i,self.payload(stage))
        records=self.s.fabrication_records(self.a,self.i)['records'];self.assertEqual([r['attempt'] for r in records],[1,1,1,2,2]);self.assertEqual(records[1]['stage'],'failed')
        self.assertEqual(self.s.own('instances',self.i,self.a)['stage'],'printed')
    def test_remake_pins_new_version_and_source_with_idempotency(self):
        for stage in ['printing','failed']:self.s.stage_instance(self.a,self.i,self.payload(stage))
        newer=self.s.manufacturing.create(self.a,self.d['id'],{'source_version_id':self.v['id'],'transform':{'rotation_z':12},'note':'QA new version for fixture remake.'});self.approve(newer)
        p={'source_instance_id':self.i,'manufacturing_version_id':newer['id'],'note':'QA remake fixture, not actually fabricated.','request_key':uid()}
        new=self.s.create_instance(self.a,p);self.assertEqual(new['id'],self.s.create_instance(self.a,p)['id'])
        self.error(409,self.s.create_instance,self.a,p|{'name':'different request'})
        out=self.s.fabrication_records(self.a,new['id']);self.assertEqual(out['source']['id'],self.i);self.assertEqual(out['instance']['manufacturing_version_id'],newer['id'])
        self.assertEqual(self.s.fabrication_records(self.a,self.i)['remakes'][0]['id'],new['id']);self.assertEqual(self.s.own('instances',self.i,self.a)['stage'],'failed')
    def test_invalid_photos_rollback_and_wrong_owner(self):
        self.error(404,self.s.stage_instance,self.b,self.i,self.payload())
        for photos in [[photo()]*4,[{'name':'x','data_base64':'not base64'}],[{'name':'x','data_base64':'A'*3000000}]]:
            with self.assertRaises(APIError):self.s.stage_instance(self.a,self.i,self.payload(photos=photos))
        self.assertEqual(self.s.fabrication_records(self.a,self.i)['records'],[]);self.assertEqual(list(self.s.fabrication.root.iterdir()),[])
    def test_measurement_validation_and_stale_state(self):
        for m in [{'name':'x','value':float('nan'),'unit':'mm','method':'caliper'},{'name':'x','value':True,'unit':'mm','method':'x'},{'name':'x','value':1,'unit':'inch','method':'x'},{'name':'x','value':1,'unit':'mm','method':''}]:
            p=self.payload();p['details']['measurements']=[m];self.error(400,self.s.stage_instance,self.a,self.i,p)
        self.error(409,self.s.stage_instance,self.a,self.i,self.payload(expected_stage='printing'))
        self.assertEqual(self.s.fabrication_records(self.a,self.i)['records'],[])
    def test_revocation_allows_failure_evidence_but_not_new_attempt(self):
        self.s.stage_instance(self.a,self.i,self.payload('printing'))
        self.s.manufacturing.review(self.a,self.v['id'],{'action':'revoke','expected_status':'approved','note':'QA revoke fixture approval.'})
        self.s.stage_instance(self.a,self.i,self.payload('failed'))
        self.s.stage_instance(self.a,self.i,self.payload('annotation'))
        self.error(409,self.s.stage_instance,self.a,self.i,self.payload('printing'))
    def test_tampered_or_missing_photo_is_rejected(self):
        self.s.stage_instance(self.a,self.i,self.payload());record=self.s.fabrication_records(self.a,self.i)['records'][0];pid=record['photos'][0]['id'];p=self.s.fabrication.root/(pid+'.png')
        data=p.read_bytes();p.write_bytes(b'x'*len(data));self.error(409,self.s.fabrication.content,self.a,pid)
        p.unlink();self.error(409,self.s.fabrication.content,self.a,pid)
    def test_disk_failure_rolls_back_record_and_files(self):
        original=Path.open;writes=0
        def failing(path,*args,**kwargs):
            nonlocal writes
            if args and args[0]=='xb':
                writes+=1
                if writes==2:raise OSError('fixture disk failure')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',failing),self.assertRaises(OSError):self.s.stage_instance(self.a,self.i,self.payload(photos=[photo(),photo()]))
        self.assertEqual(self.s.fabrication_records(self.a,self.i)['records'],[]);self.assertEqual(list(self.s.fabrication.root.iterdir()),[])
        self.assertEqual(self.s.own('instances',self.i,self.a)['stage'],'planned')
    def test_simulated_records_cannot_become_real_evidence(self):
        sim=self.s.store.one('SELECT id FROM instances WHERE owner=? AND simulated=1',(self.a,))['id']
        self.error(409,self.s.stage_instance,self.a,sim,self.payload('annotation'))
    def test_migration_recovers_old_attempt_numbers(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(migrations,'MIGRATIONS',migrations.MIGRATIONS[:2]):old=Service(folder,True)
            row=old.store.one('SELECT id,owner FROM instances LIMIT 1')
            for stage in ['printing','failed','printing','printed']:
                old.store.execute('INSERT INTO fabrication_records VALUES(?,?,?,?,?,?,?)',(uid(),row['id'],row['owner'],stage,'old fixture','{}',stamp()))
            old.close();new=Service(folder,True)
            try:self.assertEqual([r['attempt'] for r in new.fabrication_records(row['owner'],row['id'])['records']],[1,1,2,2])
            finally:new.close()

if __name__=='__main__':unittest.main()
