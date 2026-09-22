#!/usr/bin/env python3
"""Native geometry UI tests with isolated synthetic GLB fixtures, no API workers."""
import argparse,json,os,sys,tempfile,threading
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.service import Service
from app.server import BloomServer
from gltf_fixtures import tetrahedron,encode
p=argparse.ArgumentParser();p.add_argument('--chromium');p.add_argument('--output',type=Path,required=True);args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
report={'checks':[],'page_errors':[],'mode':'native HTTP, synthetic GLB fixtures, no generation workers','paid_tripo_tested':False,'hardware_tested':False}
def passed(name):report['checks'].append({'name':name,'passed':True});print('PASS',name,flush=True)
with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'QUIET':'1'}),patch('app.adaptation.shutil.which',return_value=None):
 service=Service(folder,True)
 owner=service.store.one("SELECT id FROM users WHERE email='alice@bloom.local'")['id']
 designs=service.snapshot(owner)['designs'];design=designs[0];original=design['versions'][0]
 rawdesign=designs[1];directory=service.data/'assets'/rawdesign['id'];directory.mkdir(exist_ok=True)
 doc,blob=tetrahedron();(directory/'raw.glb').write_bytes(encode(doc,blob))
 service.store.execute('UPDATE designs SET asset=? WHERE id=?',('assets/'+rawdesign['id'],rawdesign['id']))
 raw=service.manufacturing.create(owner,rawdesign['id'])
 server=BloomServer(('127.0.0.1',0),service);threading.Thread(target=server.serve_forever,daemon=True).start();url='http://127.0.0.1:'+str(server.server_port)
 try:
  with sync_playwright() as engine:
   browser=engine.chromium.launch(executable_path=args.chromium,headless=True)
   page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
   def open_versions(d):
    page.goto(url+'/#wardrobe')
    page.locator('.design-card').filter(has=page.get_by_role('heading',name=d['title'],exact=True)).get_by_role('button',name='制造版本',exact=True).click()
   def version_card(v):return page.locator('.version-card[data-version-id="'+v['id']+'"]')
   def loaded():page.wait_for_function('() => document.querySelector("#modal canvas")?.dataset.loaded === "true"')
   try:
    page.goto(url);page.get_by_role('button',name='进入我的房间',exact=True).click();page.get_by_role('heading',name='让房间，长出你的样子。').wait_for()
    open_versions(design)
    version_card(original).get_by_role('button',name='调整朝向与尺寸').click()
    form=page.locator('#transform-form');assert form.locator('input[name=width_mm]').count()==0
    form.locator('input[name=rotation_z]').fill('32');form.locator('textarea').fill('QA 软件朝向验证，未进行实物制造检查。');form.locator('input[type=checkbox]').check()
    page.get_by_role('button',name='保存调整为新版本',exact=True).click();page.get_by_role('heading',name='制造 v2',exact=True).wait_for()
    new=service.manufacturing.list(owner,design['id'])[0];assert new['source_version_id']==original['id'] and new['status']=='draft'
    assert new['files']['crown.stl']['sha256']!=original['files']['crown.stl']['sha256']
    assert service.manufacturing.list(owner,design['id'])[1]['files']==original['files']
    passed('STL rotation creates a draft while preserving the source version')
    version_card(new).get_by_role('button',name='预览此版本').click();loaded()
    assert page.locator('#modal canvas').get_attribute('data-asset')==new['asset_url']
    page.get_by_role('button',name='在灯体上试装').click();loaded()
    assert page.locator('#modal canvas').get_attribute('data-asset')==new['asset_url']
    page.screenshot(path=str(args.output/'version-lamp-fit.png'),full_page=True)
    passed('version preview and lamp trial load the exact immutable STL')
    page.get_by_role('button',name='关闭',exact=True).click();open_versions(rawdesign)
    version_card(raw).get_by_role('button',name='预览此版本').click();page.get_by_role('button',name='原稿比例',exact=True).click();loaded()
    assert page.locator('#modal canvas').get_attribute('data-fit')=='true'
    assert page.locator('#modal canvas').get_attribute('data-asset')==raw['original_preview_url']
    assert '不是毫米制造尺寸' in page.locator('#modal-content').inner_text()
    passed('original GLB geometry preserves proportions and uses the shared server decoder')
    page.get_by_role('button',name='关闭',exact=True).click();open_versions(rawdesign)
    version_card(raw).get_by_role('button',name='调整朝向与尺寸').click();form=page.locator('#transform-form')
    form.locator('[name=rotation_x]').fill('20');form.locator('[name=rotation_y]').fill('30');form.locator('[name=width_mm]').fill('180');form.locator('[name=depth_mm]').fill('12');form.locator('[name=fit]').select_option('uniform')
    form.locator('textarea').fill('QA 原稿等比例适配，仅供数字检查。');form.locator('[type=checkbox]').check()
    page.get_by_role('button',name='保存调整为新版本',exact=True).click();page.get_by_role('heading',name='制造 v3',exact=True).wait_for()
    adapted=service.manufacturing.list(owner,rawdesign['id'])[0];assert not adapted['has_final_stl'] and adapted['mesh_report']['transform']['fit']=='uniform'
    version_card(adapted).get_by_role('button',name='预览此版本').click();loaded()
    assert page.locator('#modal canvas').get_attribute('data-asset')==adapted['decoration_url']
    assert page.get_by_role('button',name='在灯体上试装').count()==0
    page.screenshot(path=str(args.output/'adapted-draft.png'),full_page=True)
    passed('raw dimensions save with provenance and missing converter stays a non-manufacturable draft')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    page.screenshot(path=str(args.output/'geometry-mobile.png'),full_page=True)
    passed('mobile version preview has no horizontal overflow')
    page.get_by_role('button',name='关闭',exact=True).click();open_versions(design)
    page.route('**'+new['asset_url'],lambda route:route.fulfill(status=200,body=b'broken-stl',content_type='application/octet-stream'))
    version_card(new).get_by_role('button',name='预览此版本').click();page.locator('#modal .mesh-error').wait_for()
    assert page.locator('#modal canvas').get_attribute('data-loaded')=='false'
    assert not report['page_errors'],report['page_errors']
    passed('invalid mesh displays a visible error with no substituted model or uncaught exception')
    report['passed']=True
   except Exception as exc:
    report['passed']=False;report['error']=str(exc);page.screenshot(path=str(args.output/'failure.png'),full_page=True);raise
   finally:
    (args.output/'browser-tests.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));browser.close()
 finally:server.shutdown();server.server_close();service.close()
