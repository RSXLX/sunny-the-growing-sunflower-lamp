#!/usr/bin/env python3
"""Isolated synthetic evidence UI checks. Never actual manufacturing evidence."""
import argparse,base64,json,os,sys,tempfile,threading
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.service import Service
from app.server import BloomServer
from app.manufacturing import MANUAL_CHECKS
from test_fabrication import photo
p=argparse.ArgumentParser();p.add_argument('--chromium');p.add_argument('--output',type=Path,required=True);args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
report={'checks':[],'page_errors':[],'mode':'native HTTP with synthetic evidence fixtures and no workers','paid_tripo_tested':False,'hardware_tested':False}
def passed(name):report['checks'].append({'name':name,'passed':True});print('PASS',name,flush=True)
with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'QUIET':'1'}):
 service=Service(folder,True);owner=service.store.one("SELECT id FROM users WHERE email='alice@bloom.local'")['id'];d=service.snapshot(owner)['designs'][0];v=d['versions'][0]
 def approve(v):
  for action,status in [('submit','draft'),('approve','submitted')]:service.manufacturing.review(owner,v['id'],{'action':action,'expected_status':status,'note':'QA fixture only; no actual inspection.','acknowledge':True,'checks':{k:True for k in MANUAL_CHECKS}})
 approve(v);instance=service.create_instance(owner,{'manufacturing_version_id':v['id'],'name':'QA 制作证据流程'})['id']
 newer=service.manufacturing.create(owner,d['id'],{'source_version_id':v['id'],'transform':{'rotation_z':15},'note':'QA remake model fixture.'});approve(newer)
 server=BloomServer(('127.0.0.1',0),service);threading.Thread(target=server.serve_forever,daemon=True).start();url='http://127.0.0.1:'+str(server.server_port)
 try:
  with sync_playwright() as engine:
   browser=engine.chromium.launch(executable_path=args.chromium,headless=True);page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
   def save(note):
    page.locator('#fabrication-form [name=note]').fill(note);page.locator('#fabrication-form [name=acknowledge]').check();page.get_by_role('button',name='保存实际制作记录',exact=True).click();page.get_by_role('heading',name='制作记录',exact=True).wait_for()
   try:
    page.goto(url);page.get_by_role('button',name='进入我的房间',exact=True).click();page.get_by_role('heading',name='让房间，长出你的样子。').wait_for();page.goto(url+'/#wardrobe')
    page.locator('.instance-row').filter(has=page.get_by_role('heading',name='QA 制作证据流程',exact=True)).get_by_role('button',name='制作记录',exact=True).click()
    page.get_by_role('button',name='开始制作',exact=True).click();save('QA 开始制作表单测试；没有实际打印。')
    page.get_by_role('button',name='制作失败',exact=True).click();form=page.locator('#fabrication-form')
    form.locator('[name=photos]').set_input_files({'name':'QA-only.png','mimeType':'image/png','buffer':base64.b64decode(photo()['data_base64'])})
    page.get_by_role('button',name='添加测量值',exact=True).click();form.locator('[name=measurement-name]').fill('安装孔径');form.locator('[name=measurement-value]').fill('76.1');form.locator('[name=measurement-method]').fill('QA synthetic caliper reading')
    page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1');page.screenshot(path=str(args.output/'evidence-form-mobile.png'),full_page=True)
    with page.expect_request(lambda r:r.method=='POST' and r.url.endswith('/records')) as submitted:save('QA 失败样例：这是合成证据，并非真实故障。')
    page.wait_for_function('() => document.querySelector("#modal .evidence-photos img")?.naturalWidth===64')
    assert '76.1 mm' in page.locator('#modal-content').inner_text()
    pid=service.fabrication_records(owner,instance)['records'][-1]['photos'][0]['id']
    assert b'PRIVATE_LOCATION' not in page.request.get(url+'/api/fabrication-photos/'+pid+'/content').body()
    passed('mobile form saves measurements and canonical photo on the failed attempt')
    request=submitted.value;csrf=page.request.get(url+'/api/auth/me').json()['csrf'];duplicate=page.request.post(request.url,data=request.post_data_json,headers={'X-CSRF-Token':csrf})
    assert duplicate.status==200 and duplicate.json()['reused'];assert len(service.fabrication_records(owner,instance)['records'])==2
    passed('network retry does not duplicate a record or attachment')
    page.get_by_role('button',name='同版本重试',exact=True).click();save('QA 第二次尝试测试；没有真实重试。')
    page.get_by_role('button',name='打印完成',exact=True).click();form=page.locator('#fabrication-form');form.locator('[name=material]').fill('QA fixture');form.locator('[name=machine]').fill('QA fixture printer');save('QA 打印完成软件测试；不是实物证据。')
    assert [r['attempt'] for r in service.fabrication_records(owner,instance)['records']]==[1,1,2,2]
    assert page.locator('#modal .evidence-photos img').count()==1
    passed('same-version retry keeps failure photo and increments attempt history')
    page.get_by_role('button',name='制作失败',exact=True).click();save('QA 装配阶段发现问题的合成测试。')
    page.get_by_role('button',name='建立关联重制档案',exact=True).click();form=page.locator('#fabrication-remake-form');form.locator('[name=manufacturing_version_id]').select_option(newer['id']);form.locator('[name=note]').fill('QA 改版重制关系测试；没有实物。');form.locator('[name=name]').fill('QA 改版重制件');page.get_by_role('button',name='创建关联档案',exact=True).click();page.get_by_role('heading',name='制作记录',exact=True).wait_for()
    child=service.store.one('SELECT * FROM instances WHERE source_instance_id=?',(instance,));assert child['manufacturing_version_id']==newer['id'];assert child['stage']=='planned';assert service.own('instances',instance,owner)['stage']=='failed'
    page.get_by_role('button',name='来源：QA 制作证据流程',exact=True).click();page.get_by_role('button',name='重制：QA 改版重制件',exact=True).wait_for()
    page.set_viewport_size({'width':1440,'height':1000});page.screenshot(path=str(args.output/'evidence-history.png'),full_page=True)
    passed('remake pins the selected version and links back without overwriting the failed instance')
    page.get_by_role('button',name='关闭',exact=True).click();page.get_by_role('button',name='退出账号',exact=True).click();page.get_by_role('button',name='林间的房间',exact=True).click();page.get_by_role('button',name='退出账号',exact=True).wait_for()
    assert page.request.get(url+'/api/fabrication-photos/'+pid+'/content').status==404;assert page.request.get(url+'/api/instances/'+instance+'/records').status==404
    passed('another account cannot read the private evidence or fabrication history')
    assert not report['page_errors'],report['page_errors'];passed('no uncaught JavaScript exceptions');report['passed']=True
   except Exception as exc:
    report['passed']=False;report['error']=str(exc);page.screenshot(path=str(args.output/'failure.png'),full_page=True);raise
   finally:(args.output/'browser-tests.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));browser.close()
 finally:server.shutdown();server.server_close();service.close()
