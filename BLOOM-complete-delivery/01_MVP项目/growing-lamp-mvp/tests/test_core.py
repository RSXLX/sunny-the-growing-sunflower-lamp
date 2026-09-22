"""Repeatable tests; stdlib only, no paid API, no physical hardware assumed."""
import base64,io,json,os,struct,tempfile,unittest,zipfile,hashlib
from pathlib import Path
from unittest.mock import patch
from app.geometry import make_crown,read_stl,inspect_mesh,glb_triangles
from app.service import Service,APIError
from app.store import uid,stamp,hash_secret,verify_password,password_hash
from app.providers import Tripo,SubmissionUnknown,download_model
from app.manufacturing import MANUAL_CHECKS

class CoreTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.s=Service(Path(cls.tmp.name),demo=True)
  cls.a=cls.s.store.one("SELECT id FROM users WHERE email='alice@bloom.local'")['id']
  cls.b=cls.s.store.one("SELECT id FROM users WHERE email='bob@bloom.local'")['id']
 @classmethod
 def tearDownClass(cls):cls.s.close();cls.tmp.cleanup()
 def new_design(self):
  d=self.s.create_design(self.a,{'title':'Test crown','prompt':'Quiet leaves around an annular crown','theme':'forest','provider':'parametric'},uid())['id']
  # Process only this job; earlier tests leave no active jobs.
  self.s.job_tick();return d
 def reviewed(self):
  d=self.new_design();self.s.review_design(self.a,d,{'acknowledge':True,'note':'Digital mesh checked; no physical validation.'})
  v=self.s.manufacturing.list(self.a,d)[0]
  self.s.manufacturing.review(self.a,v['id'],{'action':'submit','expected_status':'draft','note':'Fixture submits digital files for review.'})
  self.s.manufacturing.review(self.a,v['id'],{'action':'approve','expected_status':'submitted','note':'Fixture only; this is not physical evidence.','acknowledge':True,'checks':{k:True for k in MANUAL_CHECKS}})
  return d
 def instance(self):return self.s.create_instance(self.a,{'design_id':self.reviewed(),'name':'Test instance'})['id']
 def expect_api(self,status,fn,*args):
  with self.assertRaises(APIError) as cm:fn(*args)
  self.assertEqual(cm.exception.status,status)
 def test_01_passwords(self):
  h=password_hash('a long test password');self.assertTrue(verify_password('a long test password',h));self.assertFalse(verify_password('wrong',h));self.assertNotEqual(h,password_hash('a long test password'))
 def test_02_private_memory_isolation(self):
  mid=self.s.create_memory(self.a,{'title':'Private','body':'PRIVATE_CANARY_84739','theme':'forest'})['id']
  self.expect_api(404,self.s.own,'memories',mid,self.b);self.assertNotIn('PRIVATE_CANARY_84739',json.dumps(self.s.snapshot(self.b)))
 def test_03_private_design_isolation(self):
  d=self.s.store.one('SELECT id FROM designs WHERE owner=? AND public=0',(self.a,))['id']
  self.expect_api(404,self.s.design,d,self.b);self.expect_api(404,self.s.manufacture_package,self.b,d);self.expect_api(404,self.s.asset_file,self.b,d,'crown.stl')
 def test_04_generation_is_idempotent(self):
  key=uid();p={'title':'Idempotent','prompt':'Forest crown for test','theme':'forest'}
  a=self.s.create_design(self.a,p,key);b=self.s.create_design(self.a,p,key);self.assertEqual(a['id'],b['id']);self.assertTrue(b['reused']);self.s.job_tick()
 def test_05_generated_file_is_watertight(self):
  d=self.new_design();self.assertEqual(self.s.design(d,self.a)['status'],'needs_review');r=inspect_mesh(read_stl(self.s.asset_file(self.a,d,'crown.stl')));self.assertTrue(r['watertight_edge_count']);self.assertEqual(r['degenerate_faces'],0);self.assertGreater(r['signed_volume_mm3'],0)
 def test_06_meshes_differ_by_seed(self):
  with tempfile.TemporaryDirectory() as tmp:
   a=Path(tmp)/'a';b=Path(tmp)/'b';make_crown(a,'leaves','forest',123);make_crown(b,'leaves','forest',456);self.assertNotEqual(hashlib.sha256((a/'crown.stl').read_bytes()).hexdigest(),hashlib.sha256((b/'crown.stl').read_bytes()).hexdigest())
 def test_07_review_and_publication_gates(self):
  d=self.new_design();self.expect_api(409,self.s.publish_design,self.a,d,{'public':True,'confirm_rights':True});self.expect_api(400,self.s.review_design,self.a,d,{'note':'sufficient text without acknowledgement'})
  self.s.review_design(self.a,d,{'acknowledge':True,'note':'Digital review; physical fit remains untested.'});self.expect_api(400,self.s.publish_design,self.a,d,{'public':True})
 def test_08_public_memory_not_shared(self):
  mid=self.s.create_memory(self.a,{'title':'Diary','body':'PRIVATE_CANARY_293883','theme':'stars'})['id']
  d=self.new_design();self.s.store.execute('UPDATE designs SET memory_id=? WHERE id=?',(mid,d));self.s.review_design(self.a,d,{'acknowledge':True,'note':'Checked only the generated mesh.'});self.s.publish_design(self.a,d,{'public':True,'confirm_rights':True})
  public=self.s.design(d,self.b);self.assertNotIn('memory_id',public);self.assertNotIn('task_id',public)
  raw=self.s.manufacture_package(self.b,d)
  with zipfile.ZipFile(io.BytesIO(raw)) as z:
   self.assertIsNone(z.testzip());self.assertIn('crown.stl',z.namelist());self.assertIn('manifest.json',z.namelist());self.assertFalse(any(b'PRIVATE_CANARY_293883' in z.read(n) for n in z.namelist()))
 def test_09_remix_keeps_source(self):
  source=self.s.store.one('SELECT id FROM designs WHERE public=1 AND owner=?',(self.b,))['id'];d=self.s.create_design(self.a,{'title':'Remix','prompt':'New forest crown variation','theme':'forest','source_id':source},uid())['id'];self.s.job_tick();self.assertEqual(self.s.design(d,self.a)['source_id'],source)
 def test_10_delete_memory_preserves_design(self):
  mid=self.s.create_memory(self.a,{'title':'Delete me','body':'A private memory','theme':'forest'})['id'];d=self.new_design();self.s.store.execute('UPDATE designs SET memory_id=? WHERE id=?',(mid,d));self.s.store.execute('DELETE FROM memories WHERE id=?',(mid,));self.assertIsNone(self.s.design(d,self.a)['memory_id'])
 def test_11_real_device_starts_offline(self):
  d,t=self.s.store.device(self.a,'Real test');row=next(x for x in self.s.snapshot(self.a)['devices'] if x['id']==d);self.assertFalse(row['online']);self.assertEqual(self.s.device_auth(t)['id'],d);self.assertNotIn(t,json.dumps(self.s.snapshot(self.a)));self.expect_api(401,self.s.device_auth,'badtoken')
 def test_12_simulator_cannot_touch_real_device(self):
  d,t=self.s.store.device(self.a,'Not simulated');i=self.instance();self.expect_api(403,self.s.simulator_install,self.a,d,i)
 def test_13_real_binding_requires_physical_stage(self):
  d,t=self.s.store.device(self.a,'Stage test');i=self.instance();self.expect_api(409,self.s.bind_instance,self.a,i,{'device_id':d});self.expect_api(409,self.s.stage_instance,self.a,i,{'stage':'verified','acknowledge':True});self.expect_api(400,self.s.stage_instance,self.a,i,{'stage':'printed'})
 def test_14_binding_requires_pending_command(self):
  d,t=self.s.store.device(self.a,'Pending test');dev=self.s.device_auth(t);i=self.instance();self.expect_api(409,self.s.poll_device,dev,{'events':[{'id':uid(),'kind':'tag_bound','payload':{'instance_id':i,'tag_uid':'04789abc','verified':True}}]})
 def test_15_real_binding_and_event_dedupe(self):
  d,t=self.s.store.device(self.a,'Actual protocol only');dev=self.s.device_auth(t);i=self.instance()
  for stage in ['printed','verified']:self.s.stage_instance(self.a,i,{'stage':stage,'acknowledge':True,'note':'Test fixture, no physical validation.','details':{'material':'fixture','machine':'fixture','interface_fit':True,'retention':True,'electrical_thermal':True}})
  cmd=self.s.bind_instance(self.a,i,{'device_id':d});event={'id':uid(),'kind':'tag_bound','payload':{'instance_id':i,'verified':True,'tag_uid':'0478912345'}}
  out=self.s.poll_device(dev,{'events':[event],'acks':[{'id':cmd['id'],'ok':True}]});self.assertEqual(self.s.own('instances',i,self.a)['stage'],'bound');self.assertEqual(self.s.own('instances',i,self.a)['simulated'],0);self.assertTrue(any(x['instance_id']==i for x in out['known_tags']))
  self.s.poll_device(dev,{'events':[event]});self.assertEqual(self.s.store.one('SELECT count(*) AS n FROM events WHERE id=?',(event['id'],))['n'],1)
 def test_16_sim_binding_does_not_count_as_hardware(self):
  dev=self.s.store.one("SELECT * FROM devices WHERE owner=? AND kind='simulator'",(self.a,));i=self.instance();self.s.bind_instance(self.a,i,{'device_id':dev['id']});self.s.simulator_tick();self.assertEqual(self.s.own('instances',i,self.a)['simulated'],1)
  real,t=self.s.store.device(self.a,'Reject simulated tag');self.expect_api(409,self.s.poll_device,self.s.device_auth(t),{'events':[{'id':uid(),'kind':'outfit_installed','payload':{'instance_id':i}}]})
 def test_17_installation_deduplicates(self):
  dev=self.s.store.one("SELECT * FROM devices WHERE owner=? AND kind='simulator'",(self.a,));i=self.instance();self.s.bind_instance(self.a,i,{'device_id':dev['id']});self.s.simulator_tick();self.s.simulator_install(self.a,dev['id'],i);self.assertTrue(self.s.simulator_install(self.a,dev['id'],i)['deduplicated'])
 def test_18_light_supersession_ack_and_limits(self):
  d,t=self.s.store.device(self.a,'Commands');p={'power':True,'brightness':50,'projection':20,'theme':'forest'};a=self.s.command(self.a,d,'light',p);b=self.s.command(self.a,d,'light',dict(p,brightness=30));self.assertEqual(self.s.store.one('SELECT state FROM commands WHERE id=?',(a['id'],))['state'],'superseded')
  out=self.s.poll_device(self.s.device_auth(t),{'reported':p,'acks':[{'id':b['id'],'ok':True}]});self.assertEqual(out['commands'],[]);self.expect_api(400,self.s.command,self.a,d,'light',dict(p,projection=99))
 def test_19_expired_commands_not_delivered(self):
  d,t=self.s.store.device(self.a,'Expired');c=self.s.command(self.a,d,'light',{'power':False,'brightness':0,'projection':0,'theme':'forest'},-1);self.assertEqual(self.s.poll_device(self.s.device_auth(t),{})['commands'],[]);self.assertEqual(self.s.store.one('SELECT state FROM commands WHERE id=?',(c['id'],))['state'],'expired')
 def test_20_storage_survives_reopen(self):
  n=len(self.s.snapshot(self.a)['designs']);self.s.close();other=Service(self.tmp.name,True);self.assertEqual(len(other.snapshot(self.a)['designs']),n);other.close()
 def test_21_missing_key_never_silently_calls_paid_api(self):
  with patch.dict(os.environ,{'TRIPO_API_KEY':''}):self.expect_api(400,self.s.create_design,self.a,{'title':'Paid','prompt':'No API key supplied','theme':'forest','provider':'tripo','accept_charges':True},uid())
 def test_22_unknown_submission_not_repeated(self):
  with patch.dict(os.environ,{'TRIPO_API_KEY':'fixture-only-key','TRIPO_MAX_SUBMISSIONS':'10'}):
   d=self.s.create_design(self.a,{'confirm_provider_config':Tripo.configuration(),'title':'Paid fixture','prompt':'Fixture never calls the internet','theme':'forest','provider':'tripo','accept_charges':True},uid())['id']
   with patch('app.service.Tripo') as provider:
    provider.return_value.create.side_effect=SubmissionUnknown('uncertain');self.s.job_tick();self.s.job_tick();self.assertEqual(provider.return_value.create.call_count,1)
   self.assertEqual(self.s.design(d,self.a)['status'],'submission_unknown')
   self.s.recover_task(self.a,d,{'task_id':'fixture-existing-task'})
   with patch('app.service.Tripo') as provider:
    provider.return_value.poll.return_value={'status':'success','output':{'model':'https://example.com/model.glb'}}
    with patch('app.service.download_model',side_effect=ValueError('fixture stops before download')):self.s.job_tick()
    self.assertEqual(provider.return_value.create.call_count,0)
   self.s.store.execute("UPDATE designs SET status='failed' WHERE id=?",(d,))
 def test_23_tripo_request_contract(self):
  with patch.dict(os.environ,{'TRIPO_API_KEY':'fixture','TRIPO_API_BASE':'https://api.tripo3d.ai/v2/openapi','TRIPO_MODEL_VERSION':'v2.5-20250123'}),patch.object(Tripo,'request',return_value={'task_id':'task-fixture'}) as request:
   self.assertEqual(Tripo().create('crown shape'),'task-fixture');args=request.call_args.args;self.assertEqual(args[:2],('POST','/task'));self.assertEqual(args[2]['type'],'text_to_model');self.assertFalse(args[2]['texture'])
 def test_24_private_asset_urls_rejected(self):
  with tempfile.TemporaryDirectory() as d:self.assertRaises(ValueError,download_model,'http://127.0.0.1/file',Path(d)/'model.glb')
 def test_25_glb_translation_parsing(self):
  blob=struct.pack('<9f',0,0,0,1,0,0,0,1,0);doc={'asset':{'version':'2.0'},'buffers':[{'byteLength':len(blob)}],'bufferViews':[{'buffer':0,'byteLength':len(blob)}],'accessors':[{'bufferView':0,'componentType':5126,'count':3,'type':'VEC3'}],'meshes':[{'primitives':[{'attributes':{'POSITION':0}}]}],'nodes':[{'mesh':0,'translation':[3,4,5]}],'scenes':[{'nodes':[0]}],'scene':0};j=json.dumps(doc).encode();j+=b' '*((-len(j))%4);raw=struct.pack('<III',0x46546c67,2,12+8+len(j)+8+len(blob))+struct.pack('<II',len(j),0x4e4f534a)+j+struct.pack('<II',len(blob),0x004e4942)+blob
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'test.glb';p.write_bytes(raw);ts=glb_triangles(p);self.assertEqual(len(ts),1);self.assertEqual(tuple(ts[0][0]),(3.0,4.0,5.0))

if __name__=='__main__':unittest.main()
