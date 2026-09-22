import hashlib
import json
import math
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.gltf import glb_triangles
from app.geometry import read_stl,inspect_mesh
from app.adaptation import adapt_model,original_preview
from app.service import Service
from app.errors import APIError
from app.store import uid,stamp
from gltf_fixtures import encode,tetrahedron


class GLBTests(unittest.TestCase):
    def parse(self,doc,blob):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'model.glb';path.write_bytes(encode(doc,blob))
            return glb_triangles(path)

    def test_static_scene_and_parent_transform(self):
        doc,blob=tetrahedron()
        doc['nodes']=[{'translation':[10,20,30],'children':[1]},{'mesh':0,'scale':[-2,3,4]}]
        ts=self.parse(doc,blob);report=inspect_mesh(ts)
        self.assertEqual(report['bounds_mm'],[[6,20,30],[10,23,42]])
        self.assertGreater(report['signed_volume_mm3'],0)
        self.assertEqual(len(ts),4)

    def test_sparse_position_without_base_buffer(self):
        doc,blob=tetrahedron();doc['buffers'][0]['byteLength']=52
        blob=bytes([1,2,3,0])+struct.pack('<9f',2,0,0,0,1,0,0,0,3)+struct.pack('<6H',0,1,2,0,3,1)
        doc['bufferViews']=[{'buffer':0,'byteOffset':0,'byteLength':3},{'buffer':0,'byteOffset':4,'byteLength':36},{'buffer':0,'byteOffset':40,'byteLength':12}]
        doc['accessors']=[{'componentType':5126,'type':'VEC3','count':4,'sparse':{'count':3,'indices':{'bufferView':0,'componentType':5121},'values':{'bufferView':1}}},
                          {'bufferView':2,'componentType':5123,'type':'SCALAR','count':6}]
        triangles=self.parse(doc,blob)
        self.assertEqual(triangles[0],[(0,0,0),(2,0,0),(0,1,0)])
        self.assertEqual(triangles[1][1],(0,0,3))

    def test_quantized_normalized_positions(self):
        doc,blob=tetrahedron();blob=struct.pack('<12h',0,0,0,32767,0,0,0,32767,0,0,0,32767)+blob[48:]
        doc['buffers'][0]['byteLength']=len(blob);doc['bufferViews'][0]['byteLength']=24;doc['bufferViews'][1]['byteOffset']=24
        doc['accessors'][0].update(componentType=5122,normalized=True)
        doc['extensionsUsed']=doc['extensionsRequired']=['KHR_mesh_quantization']
        self.assertEqual(inspect_mesh(self.parse(doc,blob))['dimensions_mm'],[1,1,1])

    def test_interleaved_positions_respect_stride(self):
        doc,blob=tetrahedron();old=blob;blob=b''.join(old[i:i+12]+b'xxxx' for i in range(0,48,12))+old[48:]
        doc['buffers'][0]['byteLength']=len(blob);doc['bufferViews'][0].update(byteLength=64,byteStride=16);doc['bufferViews'][1]['byteOffset']=64
        self.assertEqual(inspect_mesh(self.parse(doc,blob))['dimensions_mm'],[2,1,3])

    def test_strip_and_fan_are_triangulated_without_winding_loss(self):
        for mode,points in [(5,[(0,0,0),(1,0,0),(0,1,0),(1,1,0)]),(6,[(0,0,0),(1,0,0),(1,1,0),(0,1,0)])]:
            doc,_=tetrahedron();blob=b''.join(struct.pack('<3f',*p) for p in points)
            doc['buffers'][0]['byteLength']=len(blob);doc['bufferViews']=doc['bufferViews'][:1];doc['accessors']=doc['accessors'][:1]
            doc['meshes'][0]['primitives'][0]={'attributes':{'POSITION':0},'mode':mode}
            ts=self.parse(doc,blob);self.assertEqual(len(ts),2)
            for a,b,c in ts:self.assertGreater((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]),0)

    def test_unsupported_geometry_is_never_silently_ignored(self):
        changes=[lambda d:d.update(animations=[{}]),lambda d:d['nodes'][0].update(skin=0),
                 lambda d:d['meshes'][0]['primitives'][0].update(targets=[{}]),
                 lambda d:d['meshes'][0]['primitives'][0].update(extensions={'KHR_draco_mesh_compression':{}}),
                 lambda d:d['bufferViews'][0].update(extensions={'EXT_meshopt_compression':{}}),
                 lambda d:d['buffers'][0].update(uri='https://example.com/remote.bin'),
                 lambda d:d['nodes'][0].update(children=[0]),lambda d:d['meshes'][0]['primitives'][0].update(mode=1)]
        for change in changes:
            doc,blob=tetrahedron();change(doc)
            with self.subTest(change=change),self.assertRaises(ValueError):self.parse(doc,blob)

    def test_malformed_accessors_and_indices_rejected(self):
        changes=[lambda d:d['accessors'][0].update(count=1000000),lambda d:d['bufferViews'][0].update(byteOffset=-1),
                 lambda d:d['bufferViews'][0].update(byteLength=4),lambda d:d['accessors'][1].update(count=11),
                 lambda d:d['nodes'][0].update(rotation=[0,0,0,0]),lambda d:d['nodes'][0].update(matrix=[0]*16)]
        for change in changes:
            doc,blob=tetrahedron();change(doc)
            with self.assertRaises(ValueError):self.parse(doc,blob)
        doc,blob=tetrahedron()
        with self.assertRaises(ValueError):self.parse(doc,blob[:48]+struct.pack('<H',100)+blob[50:])
        with self.assertRaises(ValueError):self.parse(doc,struct.pack('<f',float('nan'))+blob[4:])

    def test_invalid_object_structure_is_reported_as_value_error(self):
        for change in [lambda d:d.update(asset=[]),lambda d:d.update(nodes=[None]),lambda d:d.update(bufferViews=[5])]:
            doc,blob=tetrahedron();change(doc)
            with self.assertRaises(ValueError):self.parse(doc,blob)
        _,blob=tetrahedron()
        with self.assertRaises(ValueError):self.parse([],blob)

    def test_total_triangle_limit(self):
        doc,blob=tetrahedron()
        with patch('app.gltf.MAX_TRIANGLES',3),self.assertRaises(ValueError):self.parse(doc,blob)

    def test_truncated_chunk_and_header_rejected(self):
        doc,blob=tetrahedron();raw=encode(doc,blob)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bad.glb'
            for data in [raw[:-4],raw[:12]+struct.pack('<I',len(raw)+50)+raw[16:],b'glTF']:
                path.write_bytes(data)
                with self.assertRaises(ValueError):glb_triangles(path)


class TransformTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Service(self.tmp.name,True)
        self.a=self.s.store.one("SELECT id FROM users WHERE email='alice@bloom.local'")['id']
        self.d=self.s.snapshot(self.a)['designs'][0];self.v=self.d['versions'][0]
    def tearDown(self):self.s.close();self.tmp.cleanup()

    def transform(self,version=None,**params):
        return self.s.manufacturing.create(self.a,self.d['id'],{'source_version_id':(version or self.v)['id'],'transform':params,'note':'测试朝向与尺寸修改，尚未进行实物验证。'})

    def test_final_stl_rotation_preserves_bore_and_old_file(self):
        original=self.s.manufacturing.file(self.a,self.v['id'],'crown.stl')
        new=self.transform(rotation_z=32)
        self.assertEqual(new['status'],'draft');self.assertEqual(new['source_version_id'],self.v['id'])
        self.assertEqual(self.s.manufacturing.file(self.a,self.v['id'],'crown.stl'),original)
        oldrow=self.s.manufacturing._row(self.a,self.v['id']);row=self.s.manufacturing._row(self.a,new['id'])
        before=read_stl(self.s.manufacturing._path(oldrow)/'crown.stl');after=read_stl(self.s.manufacturing._path(row)/'crown.stl')
        for old,newtri in zip(before,after):
            for a,b in zip(old,newtri):self.assertAlmostEqual(math.hypot(a[0],a[1]),math.hypot(b[0],b[1]),places=4);self.assertEqual(a[2],b[2])
        record=json.loads(self.s.manufacturing.file(self.a,new['id'],'transform.json'))
        self.assertEqual(record['source_sha256'],hashlib.sha256(original).hexdigest())

    def test_invalid_or_interface_changing_transform_rejected(self):
        for params in [{'rotation_x':90},{'width_mm':120},{'rotation_z':float('nan')},{'rotation_z':181},{'mode':'raw_model'}]:
            with self.assertRaises(APIError) as e:self.transform(**params)
            self.assertEqual(e.exception.status,400)
        for invalid in [[], '', 0, False]:
            with self.assertRaises(APIError):
                self.s.manufacturing.create(self.a,self.d['id'],{'source_version_id':self.v['id'],'transform':invalid,'note':'无效的变换参数测试。'})
        self.assertEqual(len(self.s.manufacturing.list(self.a,self.d['id'])),1)
        self.assertEqual(list(self.s.manufacturing.root.glob('.draft-*')),[])

    def raw_version(self):
        directory=self.s.data/'assets'/self.d['id'];directory.mkdir(exist_ok=True)
        doc,blob=tetrahedron();(directory/'raw.glb').write_bytes(encode(doc,blob))
        self.s.store.execute('UPDATE designs SET asset=? WHERE id=?',('assets/'+self.d['id'],self.d['id']))
        return self.s.manufacturing.create(self.a,self.d['id'])

    def test_raw_transform_records_sizes_and_requires_final_boolean(self):
        raw=self.raw_version()
        with patch('app.adaptation.shutil.which',return_value=None):
            new=self.transform(raw,mode='raw_model',rotation_x=20,rotation_y=30,rotation_z=-15,width_mm=180,depth_mm=12,fit='uniform')
        self.assertFalse(new['has_final_stl']);self.assertTrue(new['original_preview_url']);self.assertTrue(new['decoration_url'])
        self.assertEqual(new['mesh_report']['transform']['fit'],'uniform')
        row=self.s.manufacturing._row(self.a,new['id']);report=json.loads((self.s.manufacturing._path(row)/'adapter-report.json').read_text())
        self.assertEqual(report['scale_xyz'][0],report['scale_xyz'][2])
        lo,hi=report['decorative_bounds_mm'];self.assertLessEqual(hi[2]-lo[2],12.001);self.assertAlmostEqual(lo[2],4)
        self.assertEqual(self.s.manufacturing.file(self.a,raw['id'],'raw.glb'),self.s.manufacturing.file(self.a,new['id'],'raw.glb'))
        self.assertNotIn('original_preview',json.loads(self.s.manufacturing.file(self.a,new['id'],'mesh-report.json')))

    def test_original_preview_keeps_proportions_and_axis_mapping(self):
        raw=self.raw_version();row=self.s.manufacturing._row(self.a,raw['id'])
        points=read_stl(self.s.manufacturing._path(row)/'original-preview.stl')
        self.assertEqual(inspect_mesh(points)['dimensions_mm'],[2,3,1])
        self.assertFalse(raw['mesh_report']['original_preview']['manufacturing_dimensions'])


if __name__=='__main__':unittest.main()
