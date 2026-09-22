"""Real image decoding and persisted provider workflows, with no paid/network calls."""
import base64
import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from PIL import Image, PngImagePlugin
from app.assets import MAX_BYTES
from app.errors import APIError
from app.providers import Tripo, SubmissionUnknown, ProviderRejected
from app.service import Service
from app.store import uid


def picture(fmt='PNG', size=(64, 48)):
    buffer = io.BytesIO()
    with Image.new('RGB', size, '#8ba073') as image:
        if fmt == 'PNG':
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text('private_diary', 'DO_NOT_TRANSMIT')
            image.save(buffer, fmt, pnginfo=metadata)
        else:
            exif = Image.Exif()
            exif[274] = 6
            exif[270] = 'DO_NOT_TRANSMIT'
            image.save(buffer, fmt, exif=exif)
    return buffer.getvalue()


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'TRIPO_API_KEY': 'fixture-not-real', 'TRIPO_MAX_SUBMISSIONS': '10',
                                          'TRIPO_API_BASE': 'https://openapi.tripo3d.ai/v3', 'TRIPO_MODEL_VERSION': 'v3.1-20260211'})
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Service(self.tmp.name, True)
        self.a = self.s.store.one("SELECT id FROM users WHERE email='alice@bloom.local'")['id']
        self.b = self.s.store.one("SELECT id FROM users WHERE email='bob@bloom.local'")['id']

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()
        self.env.stop()

    def upload(self, data=None):
        return self.s.references.create(self.a, {'name': '窗边参考图', 'data_base64': base64.b64encode(data or picture()).decode()})

    def job(self, ref=None, key=None, **overrides):
        payload = {'title': '参考图任务', 'prompt': 'PRIVATE_LOCAL_DESCRIPTION', 'provider': 'tripo', 'accept_charges': True, 'confirm_provider_config': Tripo.configuration()}
        if ref:
            payload.update(reference_asset_id=ref['id'], confirm_reference_sha256=ref['sha256'])
        return self.s.create_design(self.a, payload | overrides, key or uid())['id']

    def error(self, status, function, *args, **kwargs):
        with self.assertRaises(APIError) as result:
            function(*args, **kwargs)
        self.assertEqual(result.exception.status, status)

    def test_real_decoding_strips_metadata_and_corrects_orientation(self):
        for fmt in ['PNG', 'JPEG']:
            asset = self.upload(picture(fmt))
            data = self.s.references.content(self.a, asset['id'])
            self.assertNotIn(b'DO_NOT_TRANSMIT', data)
            self.assertEqual(hashlib.sha256(data).hexdigest(), asset['sha256'])
            with Image.open(io.BytesIO(data)) as image:
                self.assertEqual(image.format, 'PNG')
                self.assertEqual(image.size, (48, 64) if fmt == 'JPEG' else (64, 48))
                self.assertFalse(image.getexif())
                self.assertFalse(image.info)

    def test_fake_truncated_animated_and_oversize_input_rejected(self):
        for data in [b'<svg><script>no</script></svg>', picture()[:50]]:
            self.error(400, self.upload, data)
        animated = io.BytesIO()
        with Image.new('RGB', (32, 32), 'red') as first, Image.new('RGB', (32, 32), 'blue') as second:
            first.save(animated, 'PNG', save_all=True, append_images=[second])
        self.error(400, self.upload, animated.getvalue())
        self.error(413, self.s.references.create, self.a, {'name': 'large', 'data_base64': 'A' * ((MAX_BYTES + 2) // 3 * 4 + 1)})
        self.error(400, self.upload, picture(size=(6001, 16)))
        self.assertEqual(self.s.references.list(self.a), [])

    def test_cross_account_ownership_and_tamper(self):
        asset = self.upload()
        for method in [self.s.references.get, self.s.references.content, self.s.references.delete]:
            self.error(404, method, self.b, asset['id'])
        path = self.s.references.root / (asset['id'] + '.png')
        original = path.read_bytes()
        path.write_bytes(b'x' * len(original))
        self.error(409, self.s.references.content, self.a, asset['id'])
        self.error(409, self.job, asset)

    def test_only_unreferenced_asset_can_be_deleted(self):
        unused = self.upload()
        self.s.references.delete(self.a, unused['id'])
        self.error(404, self.s.references.get, self.a, unused['id'])
        used = self.upload()
        self.job(used)
        self.error(409, self.s.references.delete, self.a, used['id'])
        self.assertTrue(self.s.references.content(self.a, used['id']))

    def test_reference_requires_exact_confirmation_and_cannot_feed_parametric(self):
        asset = self.upload()
        self.error(400, self.job, asset, confirm_reference_sha256='wrong')
        self.error(400, self.job, asset, provider='parametric')
        self.error(404, self.s.create_design, self.b, {'title': 'Cross account', 'prompt': 'reference', 'provider': 'tripo', 'accept_charges': True, 'confirm_provider_config': Tripo.configuration(),
                   'reference_asset_id': asset['id'], 'confirm_reference_sha256': asset['sha256']}, uid())

    def test_changed_model_requires_fresh_confirmation(self):
        old = Tripo.configuration()
        with patch.dict(os.environ, {'TRIPO_MODEL_VERSION': 'v2.5-20250123'}):
            self.error(409, self.job, confirm_provider_config=old)
        self.assertEqual(self.s.tripo_allowance()['reserved'], 0)

    def test_concurrent_allowance_reservation_and_idempotency(self):
        with patch.dict(os.environ, {'TRIPO_MAX_SUBMISSIONS': '1'}):
            key = uid()
            with ThreadPoolExecutor(max_workers=4) as pool:
                ids = list(pool.map(lambda _: self.job(key=key), range(4)))
            self.assertEqual(len(set(ids)), 1)
            self.assertEqual(self.s.tripo_allowance()['reserved'], 1)
            self.error(409, self.job)
            self.s.store.execute("UPDATE designs SET status='submission_unknown' WHERE id=?", (ids[0],))
            self.error(409, self.job)
            self.assertEqual(self.job(key=key), ids[0])

    def test_distinct_concurrent_jobs_cannot_overspend_allowance(self):
        def attempt(_):
            try:return self.job()
            except APIError as exc:return exc.status
        with patch.dict(os.environ, {'TRIPO_MAX_SUBMISSIONS': '1'}), ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt, range(4)))
        self.assertEqual(sum(isinstance(r, str) for r in results), 1)
        self.assertEqual(results.count(409), 3)
        self.assertEqual(self.s.tripo_allowance()['reserved'], 1)

    def test_restart_after_upload_preserves_token_and_never_repeats_unknown_submit(self):
        design = self.job(self.upload())
        with patch.object(Tripo, 'upload_image', return_value='file_saved'), patch.object(Tripo, 'create', side_effect=SubmissionUnknown('lost response')):
            self.s.job_tick()
        self.s.close()
        reopened = Service(self.tmp.name, True)
        try:
            with patch.object(Tripo, 'upload_image') as upload, patch.object(Tripo, 'create') as create:
                reopened.job_tick()
                upload.assert_not_called()
                create.assert_not_called()
            self.assertEqual(reopened.design(design, self.a)['status'], 'submission_unknown')
        finally:reopened.close()

    def test_legacy_job_migration_polls_historical_endpoint(self):
        design = self.job()
        self.s.store.execute("UPDATE designs SET request_snapshot=NULL,task_id='old-task',status='polling',next_run=0 WHERE id=?", (design,))
        self.s.close()
        reopened = Service(self.tmp.name, True)
        try:
            with patch.object(Tripo, 'request', return_value={'status': 'running', 'progress': 32}) as request:
                reopened.job_tick()
                self.assertEqual(request.call_args.args, ('GET', '/task/old-task'))
            self.assertTrue(reopened.design(design, self.a)['generation']['legacy_assumed'])
        finally:reopened.close()

    def test_frozen_config_image_upload_and_private_input(self):
        asset = self.upload()
        design = self.job(asset)
        with patch.dict(os.environ, {'TRIPO_API_BASE': 'https://api.tripo3d.ai/v2/openapi', 'TRIPO_MODEL_VERSION': 'v2.5-20250123'}), patch.object(Tripo, 'request') as request:
            request.side_effect = [{'file_token': 'file_fixture'}, {'task_id': 'task_fixture'}]
            self.s.job_tick()
            upload, generation = request.call_args_list
            self.assertEqual(upload.args[:2], ('POST', '/files'))
            self.assertIn(self.s.references.content(self.a, asset['id']), upload.kwargs['raw'])
            self.assertNotIn(b'DO_NOT_TRANSMIT', upload.kwargs['raw'])
            self.assertEqual(generation.args[:2], ('POST', '/generation/image-to-model'))
            self.assertEqual(generation.args[2]['model'], 'v3.1-20260211')
            self.assertEqual(generation.args[2]['input'], 'file_fixture')
            self.assertNotIn('prompt', generation.args[2])
        row = self.s.design(design, self.a)
        self.assertEqual(row['task_id'], 'task_fixture')
        self.assertNotIn('upload_token', row)
        self.assertEqual(row['generation']['reference_sha256'], asset['sha256'])

    def test_upload_failure_recovers_before_any_generation(self):
        design = self.job(self.upload())
        with patch.object(Tripo, 'upload_image', side_effect=OSError('temporary')), patch.object(Tripo, 'create') as create:
            self.s.job_tick()
            create.assert_not_called()
        self.assertEqual(self.s.design(design, self.a)['status'], 'upload_failed')
        self.s.recover_task(self.a, design, {})
        with patch.object(Tripo, 'upload_image', return_value='file_fixture'), patch.object(Tripo, 'create', return_value='task_fixture') as create:
            self.s.job_tick()
            create.assert_called_once()

    def test_removed_key_can_recover_without_new_reservation(self):
        design = self.job()
        with patch.dict(os.environ, {'TRIPO_API_KEY': ''}), patch.object(Tripo, 'create') as create:
            self.s.job_tick()
            create.assert_not_called()
        self.assertEqual(self.s.design(design, self.a)['status'], 'configuration_failed')
        self.s.recover_task(self.a, design, {})
        with patch.object(Tripo, 'create', return_value='task_fixture'):
            self.s.job_tick()
        self.assertEqual(self.s.tripo_allowance()['reserved'], 1)
        self.assertEqual(self.s.design(design, self.a)['task_id'], 'task_fixture')

    def test_partial_converter_output_is_not_snapshot_final_stl(self):
        design = self.job()
        directory = self.s.data / 'assets' / design
        directory.mkdir()
        (directory / 'raw.glb').write_bytes(b'fixture raw model')
        sha = hashlib.sha256((directory / 'raw.glb').read_bytes()).hexdigest()
        self.s.store.execute("UPDATE designs SET status='preparing',raw_sha256=? WHERE id=?", (sha, design))
        def broken(directory):
            (directory / 'crown.stl').write_bytes(b'partial garbage')
            raise ValueError('converter failed')
        with patch.object(self.s, 'adapt_live_model', side_effect=broken):self.s.job_tick()
        self.assertEqual(self.s.design(design, self.a)['status'], 'needs_review')
        self.assertFalse((directory / 'crown.stl').exists())
        self.assertFalse(self.s.manufacturing.list(self.a, design)[0]['has_final_stl'])

    def test_preparation_resume_never_submits_or_redownloads(self):
        design = self.job()
        with patch.object(Tripo, 'create', return_value='task_fixture'):
            self.s.job_tick()
        self.s.store.execute('UPDATE designs SET next_run=0 WHERE id=?', (design,))
        # A deliberately unsupported model exercises the manual-repair boundary.
        def download(url, target):target.write_bytes(b'fixture-unsupported-glb')
        with patch.object(Tripo, 'poll', return_value={'status': 'success', 'output': {'model_url': 'https://example.com/file.glb'}}), patch('app.service.download_model', side_effect=download), patch.object(self.s.manufacturing, 'create', side_effect=OSError('disk transient')):
            self.s.job_tick()
        self.assertEqual(self.s.design(design, self.a)['status'], 'preparation_failed')
        self.s.recover_task(self.a, design, {})
        with patch.object(Tripo, 'create') as create, patch('app.service.download_model') as download:
            self.s.job_tick()
            create.assert_not_called()
            download.assert_not_called()
        self.assertEqual(self.s.design(design, self.a)['status'], 'needs_review')
        self.assertFalse(self.s.manufacturing.list(self.a, design)[0]['has_final_stl'])

    def test_reference_not_published_with_design(self):
        asset = self.upload()
        design = self.job(asset)
        self.s.store.execute("UPDATE designs SET status='needs_review',asset=(SELECT asset FROM designs WHERE provider='parametric' LIMIT 1) WHERE id=?", (design,))
        self.s.manufacturing.create(self.a, design)
        self.s.review_design(self.a, design, {'note': '软件测试中的人工检查输入。', 'acknowledge': True})
        self.s.publish_design(self.a, design, {'public': True, 'confirm_rights': True})
        public = self.s.design(design, self.b)
        for field in ['reference_asset_id', 'generation', 'request_snapshot', 'upload_token']:
            self.assertNotIn(field, public)
        self.assertNotIn(asset['sha256'], self.s.manufacture_package(self.b, design).decode('latin1'))
        self.error(404, self.s.references.content, self.b, asset['id'])


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'TRIPO_API_KEY': 'fixture', 'TRIPO_API_BASE': 'https://openapi.tripo3d.ai/v3', 'TRIPO_MODEL_VERSION': 'v3.1-20260211'})
        self.env.start()
    def tearDown(self):self.env.stop()

    def test_v3_text_and_task_query_contract(self):
        with patch.object(Tripo, 'request', return_value={'task_id': 'task_fixture'}) as request:
            p = Tripo()
            p.create('Forest crown', seed=24)
            method, path, body = request.call_args.args
            self.assertEqual((method, path), ('POST', '/generation/text-to-model'))
            self.assertEqual(body, {'texture': False, 'pbr': False, 'face_limit': 20000, 'model': 'v3.1-20260211', 'model_seed': 24, 'prompt': 'Forest crown'})
            p.poll('task_fixture')
            self.assertEqual(request.call_args.args, ('GET', '/tasks/task_fixture'))

    def test_unknown_submission_is_distinct_from_rejection(self):
        for error in [TimeoutError(), ValueError('invalid JSON'), urllib.error.HTTPError('fixture', 503, 'unavailable', {}, None)]:
            with patch.object(Tripo, 'request', side_effect=error):
                with self.assertRaises(SubmissionUnknown):Tripo().create('Forest crown')
        with patch.object(Tripo, 'request', side_effect=ProviderRejected('quota')):
            with self.assertRaises(ProviderRejected):Tripo().create('Forest crown')

    def test_legacy_poll_stays_v2_and_disallows_image(self):
        legacy = Tripo({'base': 'https://api.tripo3d.ai/v2/openapi', 'api_version': 'v2', 'model': 'v2.5-20250123'})
        with patch.object(Tripo, 'request', return_value={}) as request:
            legacy.poll('old-task')
            self.assertEqual(request.call_args.args, ('GET', '/task/old-task'))
            with self.assertRaises(ValueError):legacy.create('forest', image_token='file_fixture')

    def test_official_host_but_wrong_path_or_version_rejected(self):
        with patch.dict(os.environ, {'TRIPO_API_BASE': 'https://openapi.tripo3d.ai/v3/evil'}):
            with self.assertRaises(ValueError):Tripo()
        with self.assertRaises(ValueError):Tripo({'base': 'https://openapi.tripo3d.ai/v3', 'api_version': 'v2', 'model': 'v2.5-20250123'})


if __name__ == '__main__':unittest.main()
