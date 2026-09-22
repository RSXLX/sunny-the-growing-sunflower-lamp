#!/usr/bin/env python3
"""Native UI against an isolated real HTTP server, with generation workers disabled.
The fixture key is not real. This verifies confirmation and queued jobs, not Tripo.
"""
import argparse
import io
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from PIL import Image, PngImagePlugin
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.service import Service
from app.server import BloomServer

p = argparse.ArgumentParser()
p.add_argument('--chromium')
p.add_argument('--output', type=Path, required=True)
args = p.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
report = {'checks': [], 'page_errors': [], 'paid_tripo_tested': False, 'hardware_tested': False,
          'mode': 'native HTTP against isolated fixture server; generation worker disabled'}

def passed(name):
    report['checks'].append({'name': name, 'passed': True})
    print('PASS', name, flush=True)

with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
    'QUIET': '1', 'TRIPO_API_KEY': 'fixture-not-a-real-api-key', 'TRIPO_MAX_SUBMISSIONS': '2',
    'TRIPO_API_BASE': 'https://openapi.tripo3d.ai/v3', 'TRIPO_MODEL_VERSION': 'v3.1-20260211'}):
    service = Service(folder, True)
    server = BloomServer(('127.0.0.1', 0), service)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = 'http://127.0.0.1:' + str(server.server_port)
    # Do not start service workers: no code here can submit paid generation.
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.chromium, headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda e: report['page_errors'].append(str(e)))
            try:
                page.goto(url)
                page.get_by_role('button', name='进入我的房间', exact=True).click()
                page.get_by_role('heading', name='让房间，长出你的样子。').wait_for()
                page.goto(url + '/#studio')
                page.locator('#design-title').fill('参考图端到端 QA')
                page.locator('#design-prompt').fill('PRIVATE_LOCAL_DESCRIPTION_ONLY')
                page.get_by_role('button', name='上传参考图', exact=True).click()
                page.locator('#reference-upload-form input[name=name]').fill('QA 私人叶片')
                buffer = io.BytesIO()
                with Image.new('RGB', (64, 48), '#9aa577') as image:
                    metadata = PngImagePlugin.PngInfo()
                    metadata.add_text('private-note', 'EXIF_PRIVATE_FIXTURE')
                    image.save(buffer, 'PNG', pnginfo=metadata)
                page.locator('#reference-upload-form input[type=file]').set_input_files({'name': 'leaf.png', 'mimeType': 'image/png', 'buffer': buffer.getvalue()})
                page.get_by_role('button', name='保存参考图到本机', exact=True).click()
                page.wait_for_function('() => document.querySelector(".reference-image")?.naturalWidth===64')
                assert page.locator('#design-prompt').input_value() == 'PRIVATE_LOCAL_DESCRIPTION_ONLY'
                asset_id = page.locator('#reference-select').input_value()
                content = page.request.get(url + '/api/reference-assets/' + asset_id + '/content')
                assert content.status == 200 and b'EXIF_PRIVATE_FIXTURE' not in content.body()
                passed('native upload, canonical image preview and metadata removal')

                page.locator('#provider').select_option('tripo')
                page.locator('#input-mode').select_option('image')
                page.get_by_role('button', name='生成我的外装', exact=True).click()
                dialog = page.locator('#generation-confirm-form')
                dialog.wait_for()
                assert 'PRIVATE_LOCAL_DESCRIPTION_ONLY' not in dialog.inner_text()
                assert dialog.locator('img').get_attribute('src').endswith(asset_id + '/content')
                owner = service.store.one("SELECT id FROM users WHERE email='alice@bloom.local'")['id']
                assert service.store.one("SELECT COUNT(*) AS n FROM designs WHERE provider='tripo'")['n'] == 0
                passed('generation confirmation displays only actual outgoing image')
                page.screenshot(path=str(args.output / 'reference-confirmation.png'), full_page=True)
                dialog.locator('input[type=checkbox]').check()
                with page.expect_request(lambda r: r.method == 'POST' and r.url.endswith('/api/designs')) as submitted:
                    page.get_by_role('button', name='确认并创建生成任务', exact=True).click()
                page.locator('#generation-confirm-form').wait_for(state='hidden')
                request = submitted.value
                body = request.post_data_json
                assert body['reference_asset_id'] == asset_id and body['confirm_reference_sha256']
                row = service.store.one("SELECT * FROM designs WHERE provider='tripo'")
                assert row['status'] == 'queued' and row['task_id'] is None
                assert json.loads(row['request_snapshot'])['prompt'] is None
                csrf = page.request.get(url + '/api/auth/me').json()['csrf']
                duplicate = page.request.post(url + '/api/designs', data=body, headers={
                    'X-CSRF-Token': csrf, 'Idempotency-Key': request.headers['idempotency-key']})
                assert duplicate.status == 202 and duplicate.json()['id'] == row['id']
                assert service.tripo_allowance()['reserved'] == 1
                passed('one confirmed job, exact image hash, persistent idempotency and allowance')

                page.reload()
                page.get_by_role('button', name='任务详情', exact=True).click()
                page.get_by_role('heading', name='生成任务详情', exact=True).wait_for()
                assert 'v3.1-20260211' in page.locator('#modal-content').inner_text()
                assert page.locator('#modal-content img').get_attribute('src').endswith(asset_id + '/content')
                page.get_by_role('button', name='关闭', exact=True).click()
                passed('page reload preserves queued task configuration and reference')

                page.locator('#reference-select').select_option(asset_id)
                page.get_by_role('button', name='删除这张未使用的参考图', exact=True).click()
                page.get_by_role('status').filter(has_text='参考图已被生成任务引用').wait_for()
                assert service.references.get(owner, asset_id)
                passed('referenced image cannot be deleted')

                page.locator('#design-prompt').fill('DO_NOT_LEAK_DRAFT_TO_BOB')
                page.get_by_role('button', name='退出账号', exact=True).click()
                page.get_by_role('button', name='林间的房间', exact=True).click()
                page.get_by_role('button', name='退出账号', exact=True).wait_for()
                page.goto(url + '/#studio')
                assert page.locator('#design-prompt').input_value() == ''
                assert page.locator('#reference-select option').count() == 1
                assert page.request.get(url + '/api/reference-assets/' + asset_id + '/content').status == 404
                passed('second account has no reference access or retained private draft')

                page.set_viewport_size({'width': 390, 'height': 844})
                page.screenshot(path=str(args.output / 'reference-mobile.png'), full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                assert not report['page_errors'], report['page_errors']
                passed('mobile studio layout and no uncaught JavaScript errors')
                report['passed'] = True
            except Exception as exc:
                report['passed'] = False
                report['error'] = str(exc)
                page.screenshot(path=str(args.output / 'failure.png'), full_page=True)
                raise
            finally:
                (args.output / 'browser-tests.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        service.close()
