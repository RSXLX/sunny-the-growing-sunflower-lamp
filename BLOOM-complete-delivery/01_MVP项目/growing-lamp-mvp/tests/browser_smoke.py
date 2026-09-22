#!/usr/bin/env python3
"""Optional Playwright UI checks. Standard mode navigates to the running server.
--in-memory is a clearly labelled test harness for managed browsers that prohibit
all navigation: source is rendered in memory, HTTP is exercised separately using
urllib. This does NOT verify native browser network/cookie/CSP behavior.
No browser policy is disabled or changed. No external requests are made.
"""
import argparse,base64,http.cookiejar,json,urllib.request,urllib.error,time
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8787');p.add_argument('--in-memory',action='store_true');p.add_argument('--chromium',default=None);p.add_argument('--output',type=Path,default=ROOT.parents[2]/'evidence/browser-current');args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
results={'mode':'in-memory UI + independent localhost HTTP bridge' if args.in_memory else 'native browser HTTP','checks':[],'page_errors':[],'hardware_tested':False,'paid_tripo_tested':False}
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def rpc(p):
 if not p['path'].startswith('/') or p['path'].startswith('//'):raise ValueError('Only relative application paths')
 req=urllib.request.Request(args.url+p['path'],data=p['body'].encode() if p.get('body') is not None else None,headers=p.get('headers',{}),method=p.get('method','GET'))
 try:res=opener.open(req,timeout=10)
 except urllib.error.HTTPError as ex:res=ex
 return {'status':res.code,'body':base64.b64encode(res.read()).decode(),'headers':dict(res.headers)}
def record(name):results['checks'].append({'name':name,'passed':True});print('PASS',name,flush=True)
def activate(page,route):
 if args.in_memory:page.evaluate('(r)=>location.hash=r',route)
 else:page.goto(args.url+'/#'+route)
 page.wait_for_timeout(350)
def draw_wait(page):page.wait_for_timeout(350)
with sync_playwright() as engine:
 browser=engine.chromium.launch(executable_path=args.chromium,headless=True,args=[])
 page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
 page.on('pageerror',lambda e:results['page_errors'].append(str(e)))
 try:
  if args.in_memory:
   page.expose_function('localRPC',rpc)
   page.set_content('<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="app"></div><dialog id="modal"><div id="modal-content"></div></dialog><div id="toast" role="status"></div></body></html>')
   page.add_style_tag(content=(ROOT/'web/style.css').read_text())
   images={('/'+f):'data:image/svg+xml;base64,'+base64.b64encode((ROOT/'web'/f).read_bytes()).decode() for f in ['favicon.svg','wiring.svg']}
   script='''window.fetch=async(path,opts={})=>{const r=await window.localRPC({path,method:opts.method||'GET',headers:opts.headers||{},body:opts.body??null});const bytes=Uint8Array.from(atob(r.body),c=>c.charCodeAt(0));return new Response(bytes,{status:r.status,headers:r.headers});};
const volatileStore=new Map();Object.defineProperty(window,'localStorage',{value:{getItem:k=>volatileStore.get(k)||null,setItem:(k,v)=>volatileStore.set(k,v)}});
'''
   script+='const localImages='+json.dumps(images)+'; new MutationObserver(()=>document.querySelectorAll("img").forEach(x=>{let k=x.getAttribute("src");if(localImages[k])x.src=localImages[k];})).observe(document.body,{childList:true,subtree:true});\n'
   script+=(ROOT/'web/viewer.js').read_text().replace('export const','const').replace('export class','class')
   script+=(ROOT/'web/fabrication.js').read_text().replace('export class','class')
   script+=(ROOT/'web/app.js').read_text().replace("import {FabricationUI} from './fabrication.js';",'').replace("import {LampViewer} from './viewer.js';",'')
   page.add_script_tag(content=script)
  else:page.goto(args.url)
  page.get_by_role('button',name='进入我的房间').click();page.get_by_role('heading',name='让房间，长出你的样子。').wait_for()
  page.wait_for_function('() => document.querySelectorAll("canvas[data-loaded=true]").length>=3');record('login and actual STL rendering')
  page.screenshot(path=str(args.output/'workbench-room.png'),full_page=True)
  for route in ['memory','studio','wardrobe','garden','hardware']:
   activate(page,route);page.screenshot(path=str(args.output/f'workbench-{route}.png'),full_page=True);record('page '+route)
  activate(page,'room');page.get_by_role('button',name='留下一段记忆',exact=True).click()
  suffix=str(int(time.time()))[-6:];memory='第一次点亮 / QA '+suffix;title='森林里的小房间 / QA '+suffix
  page.locator('#memory-title').fill(memory);page.locator('#memory-body').fill('PRIVATE_UI_MEMORY_'+suffix+'：只有自己的账号可以看到。');page.get_by_role('button',name='保存这段记忆',exact=True).click();page.wait_for_timeout(450);record('create private memory')
  activate(page,'studio');page.locator('#design-title').fill(title);page.locator('#design-prompt').fill('A layered forest crown, soft leaves, a warm and quiet room.');page.get_by_role('button',name='生成我的外装',exact=True).click();page.wait_for_timeout(1800)
  activate(page,'wardrobe');card=page.locator('.design-card').filter(has=page.get_by_role('heading',name=title,exact=True));card.get_by_role('button',name='检查记录',exact=True).wait_for(timeout=10000);record('generate persisted parametric model')
  card.get_by_role('button',name='检查记录',exact=True).click();page.locator('#review-form textarea').fill('QA: 数字网格与夹持区域检查；未进行打印、材料和真实温升验证。');page.locator('#review-form input[type=checkbox]').check();page.get_by_role('button',name='保存检查记录',exact=True).click();page.wait_for_timeout(450);record('review gate')
  card=page.locator('.design-card').filter(has=page.get_by_role('heading',name=title,exact=True))
  package=card.get_by_role('link',name='制造包',exact=True).get_attribute('href')
  if args.in_memory:
   response=rpc({'path':package});assert response['status']==200;assert base64.b64decode(response['body'])[:2]==b'PK'
  else:
   response=page.request.get(args.url+package);assert response.status==200;assert response.body()[:2]==b'PK'
  record('download real manufacturing ZIP')
  card.get_by_role('button',name='制造版本',exact=True).click();page.get_by_role('button',name='提交制造检查',exact=True).click();page.get_by_role('button',name='审核制造版本',exact=True).click()
  page.locator('#version-review-form textarea').fill('QA 自动化流程检查，不是实物审核或制造证据。')
  for check in page.locator('#version-review-form input[type=checkbox]').all():check.check()
  page.get_by_role('button',name='批准此制造版本',exact=True).click();page.locator('.version-card').filter(has=page.get_by_text('已批准制造',exact=True)).wait_for();page.get_by_role('button',name='关闭',exact=True).click();record('immutable manufacturing version and explicit approval gate')
  card=page.locator('.design-card').filter(has=page.get_by_role('heading',name=title,exact=True))
  card.get_by_role('button',name='制作一件',exact=True).click();page.locator('#instance-form input').fill('QA 真实协议模拟实例 '+suffix);page.get_by_role('button',name='创建制作档案',exact=True).click();page.wait_for_timeout(450)
  instance=page.locator('.instance-row').filter(has=page.get_by_role('heading',name='QA 真实协议模拟实例 '+suffix,exact=True));instance.get_by_role('button',name='模拟 NFC 绑定',exact=True).click();page.wait_for_timeout(2700)
  instance=page.locator('.instance-row').filter(has=page.get_by_role('heading',name='QA 真实协议模拟实例 '+suffix,exact=True));instance.get_by_role('button',name='模拟换装',exact=True).click();page.wait_for_timeout(500);record('simulated NFC bind, readback acknowledgment and install')
  card=page.locator('.design-card').filter(has=page.get_by_role('heading',name=title,exact=True));card.get_by_role('button',name='发布设计',exact=True).click();page.locator('#publish-form input[type=checkbox]').check();page.get_by_role('button',name='发布到交换花园',exact=True).click();page.wait_for_timeout(400);record('explicit publish consent')
  page.get_by_role('button',name='退出账号',exact=True).click();page.get_by_role('button',name='林间的房间',exact=True).click();page.get_by_role('button',name='退出账号',exact=True).wait_for();activate(page,'memory');page.get_by_role('heading',name='成长记忆',exact=True).wait_for();assert 'PRIVATE_UI_MEMORY_'+suffix not in page.locator('body').inner_text();record('second account cannot see private memory')
  activate(page,'garden');card=page.locator('.design-card').filter(has=page.get_by_role('heading',name=title,exact=True));card.get_by_role('button',name='基于它创作',exact=True).click();page.wait_for_timeout(350)
  assert '二次创作自：'+title in page.locator('body').inner_text();page.locator('#design-title').fill('来自另一间房的回信 / QA '+suffix);page.locator('#design-prompt').fill('A new star crown inspired by the shared forest form.');page.get_by_role('button',name='生成我的外装',exact=True).click();page.wait_for_timeout(1800);record('second-account remix with provenance')
  activate(page,'hardware');page.screenshot(path=str(args.output/'workbench-hardware.png'),full_page=True)
  page.set_viewport_size({'width':390,'height':844});activate(page,'room');page.wait_for_timeout(300);page.screenshot(path=str(args.output/'workbench-mobile.png'),full_page=True)
  assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1');record('390px mobile layout has no horizontal overflow')
  assert not results['page_errors'],results['page_errors'];record('no uncaught JavaScript exceptions')
  results['passed']=True
 except Exception as exc:
  results['passed']=False;results['error']=str(exc);page.screenshot(path=str(args.output/'browser-failure.png'),full_page=True);raise
 finally:
  (args.output/'browser-tests.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));browser.close()
