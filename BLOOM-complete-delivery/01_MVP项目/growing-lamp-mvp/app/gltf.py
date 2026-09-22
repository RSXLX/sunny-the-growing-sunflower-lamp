"""Bounded glTF 2.0 geometry decoding; no network, textures or silent pose loss.
Geometry-only: embedded GLB, sparse/quantized positions, triangle/strip/fan modes.
Unsupported compression, skin, morph and animation fail explicitly.
"""
import json
import math
import struct
from pathlib import Path

MAX_BYTES = 50 * 1024 * 1024
MAX_VERTICES = 300_000
MAX_TRIANGLES = 100_000
IDENTITY = [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]
FORMATS = {5120:('b',1),5121:('B',1),5122:('h',2),5123:('H',2),5125:('I',4),5126:('f',4)}


def integer(value, low=0, high=MAX_BYTES):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError('GLB integer is outside supported bounds')
    return value


def item(items, index):
    if not isinstance(items, list):raise ValueError('Invalid GLB array')
    return items[integer(index, 0, len(items)-1)]


def vector(value, length):
    if not isinstance(value, list) or len(value) != length or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in value):
        raise ValueError('GLB vector requires finite coordinates')
    return value


def multiply(a,b):
    return [sum(a[k*4+r]*b[c*4+k] for k in range(4)) for c in range(4) for r in range(4)]


def node_matrix(node):
    if 'matrix' in node:
        if any(k in node for k in ('rotation','translation','scale')):raise ValueError('GLB node mixes matrix and TRS')
        m=vector(node['matrix'],16)
        if any(abs(m[i])>1e-9 for i in (3,7,11)) or abs(m[15]-1)>1e-9:raise ValueError('Non-affine GLB node')
        return m
    x,y,z,w=vector(node.get('rotation',[0,0,0,1]),4)
    if abs(x*x+y*y+z*z+w*w-1)>1e-3:raise ValueError('Invalid GLB quaternion')
    sx,sy,sz=vector(node.get('scale',[1,1,1]),3)
    tx,ty,tz=vector(node.get('translation',[0,0,0]),3)
    return [(1-2*(y*y+z*z))*sx,2*(x*y+z*w)*sx,2*(x*z-y*w)*sx,0,
            2*(x*y-z*w)*sy,(1-2*(x*x+z*z))*sy,2*(y*z+x*w)*sy,0,
            2*(x*z+y*w)*sz,2*(y*z-x*w)*sz,(1-2*(x*x+y*y))*sz,0,tx,ty,tz,1]


def glb_triangles(path: Path):
    try:return _decode(path)
    except (KeyError,TypeError,AttributeError,IndexError,struct.error,OverflowError,RecursionError) as exc:
        raise ValueError('Malformed or unsupported GLB structure') from exc


def _decode(path):
    with Path(path).open('rb') as stream:data=stream.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:raise ValueError('GLB exceeds 50 MiB')
    if len(data)<20 or data[:4]!=b'glTF':raise ValueError('Not a GLB')
    version,total=struct.unpack_from('<II',data,4)
    if version!=2 or total!=len(data):raise ValueError('Unsupported or truncated GLB')
    pos=12;chunks=[]
    while pos<len(data):
        if pos+8>len(data):raise ValueError('Truncated GLB chunk')
        size,kind=struct.unpack_from('<II',data,pos)
        if size%4 or pos+8+size>len(data):raise ValueError('Invalid GLB chunk length')
        chunks.append((kind,data[pos+8:pos+8+size]));pos+=8+size
    if len(chunks)<2 or chunks[0][0]!=0x4e4f534a or chunks[1][0]!=0x004e4942:raise ValueError('GLB needs JSON followed by embedded BIN')
    if any(k in {0x4e4f534a,0x004e4942} for k,_ in chunks[2:]):raise ValueError('Duplicate GLB chunks')
    doc=json.loads(chunks[0][1]);binary=chunks[1][1]
    if doc.get('asset',{}).get('version')!='2.0':raise ValueError('Only glTF 2.0 supported')
    required=doc.get('extensionsRequired',[])
    if any(e not in {'KHR_mesh_quantization','KHR_materials_unlit'} for e in required):raise ValueError('Required GLB extension unsupported; export an uncompressed static model')
    if doc.get('animations') or doc.get('skins'):raise ValueError('Animated/skinned model requires an explicit baked static export')
    buffers=doc.get('buffers',[])
    if len(buffers)!=1 or 'uri' in buffers[0]:raise ValueError('Only one embedded GLB buffer supported')
    buffer_size=integer(buffers[0]['byteLength'],1)
    if not buffer_size<=len(binary)<=buffer_size+3:raise ValueError('GLB BIN length does not match buffer')
    views=doc.get('bufferViews',[]);accessors=doc.get('accessors',[])
    if len(views)>4096 or len(accessors)>4096:raise ValueError('Too many GLB accessors')
    for view in views:
        if view.get('buffer',0)!=0:raise ValueError('External buffer unsupported')
        if view.get('extensions'):raise ValueError('Compressed/extended buffer view requires external conversion')
        if integer(view.get('byteOffset',0))+integer(view['byteLength'],1)>buffer_size:raise ValueError('Buffer view out of bounds')
    def read(view_id,offset,count,component,width,strided=True):
        view=item(views,view_id);fmt,size=FORMATS[component]
        offset=integer(offset);start=integer(view.get('byteOffset',0))+offset
        stride=integer(view.get('byteStride',size*width),size*width,252) if strided else size*width
        if not strided and 'byteStride' in view:raise ValueError('Sparse storage cannot be interleaved')
        if stride%size or start%size or offset+(count-1)*stride+size*width>view['byteLength']:raise ValueError('Accessor exceeds or misaligns its buffer view')
        return [struct.unpack_from('<'+fmt*width,binary,start+i*stride) for i in range(count)]
    cache={};decoded_components=0
    def accessor(index,positions=False):
        nonlocal decoded_components
        key=(index,positions)
        if key in cache:return cache[key]
        a=item(accessors,index);count=integer(a['count'],1,MAX_VERTICES);kind=a['componentType']
        if positions:
            if a['type']!='VEC3' or kind not in {5126,5120,5121,5122,5123}:raise ValueError('Unsupported GLB POSITION format')
            if kind!=5126 and 'KHR_mesh_quantization' not in doc.get('extensionsUsed',[]):raise ValueError('Quantized positions require KHR_mesh_quantization')
            width=3
        else:
            if a['type']!='SCALAR' or kind not in {5121,5123,5125} or a.get('normalized'):raise ValueError('Invalid triangle indices')
            width=1
        decoded_components+=count*width
        if decoded_components>1_500_000:raise ValueError('GLB decoded accessor budget exceeded')
        values=read(a['bufferView'],a.get('byteOffset',0),count,kind,width) if 'bufferView' in a else [(0,)*width for _ in range(count)]
        if 'bufferView' not in a and a.get('byteOffset',0):raise ValueError('Accessor offset without buffer')
        if 'sparse' in a:
            sparse=a['sparse'];n=integer(sparse['count'],1,count);indices=sparse['indices'];val=sparse['values']
            if indices['componentType'] not in {5121,5123,5125}:raise ValueError('Invalid sparse index type')
            ids=[v[0] for v in read(indices['bufferView'],indices.get('byteOffset',0),n,indices['componentType'],1,False)]
            if any(not 0<=idx<count or (i and idx<=ids[i-1]) for i,idx in enumerate(ids)):raise ValueError('Sparse indices must be unique, ordered and in bounds')
            replacements=read(val['bufferView'],val.get('byteOffset',0),n,kind,width,False)
            for idx,value in zip(ids,replacements):values[idx]=value
        if a.get('normalized'):
            if kind==5126:raise ValueError('Float accessor cannot be normalized')
            limit={5120:127,5121:255,5122:32767,5123:65535}[kind]
            values=[tuple(max(-1,v/limit) for v in row) for row in values]
        if any(not math.isfinite(v) for row in values for v in row):raise ValueError('Non-finite GLB coordinate')
        cache[key]=values;return values
    result=[];seen=set();nodes=doc.get('nodes',[])
    if len(nodes)>4096:raise ValueError('Too many GLB nodes')
    def visit(index,parent,depth=0):
        if depth>64 or index in seen:raise ValueError('GLB scene contains repeated nodes or a cycle')
        seen.add(index);node=item(nodes,index);mat=multiply(parent,node_matrix(node))
        if 'skin' in node or node.get('weights') or node.get('extensions'):raise ValueError('Unsupported skinned, morphed or extended node')
        if 'mesh' in node:
            mesh=item(doc['meshes'],node['mesh'])
            if mesh.get('weights'):raise ValueError('Morph weights require a baked mesh')
            for prim in mesh['primitives']:
                if prim.get('extensions') or prim.get('targets'):raise ValueError('Compressed or morphed geometry needs static conversion')
                mode=prim.get('mode',4)
                if mode not in {4,5,6}:raise ValueError('Non-triangle geometry is unsupported')
                vertices=accessor(prim['attributes']['POSITION'],True)
                ids=[v[0] for v in accessor(prim['indices'])] if 'indices' in prim else list(range(len(vertices)))
                if any(i>=len(vertices) for i in ids):raise ValueError('GLB index out of bounds')
                if mode==4 and len(ids)%3:raise ValueError('Triangle index count is not divisible by three')
                if mode==4:faces=[ids[i:i+3] for i in range(0,len(ids),3)]
                elif mode==5:faces=[(ids[i],ids[i+1],ids[i+2]) if i%2==0 else (ids[i+1],ids[i],ids[i+2]) for i in range(len(ids)-2)]
                else:faces=[(ids[0],ids[i],ids[i+1]) for i in range(1,len(ids)-1)]
                if len(result)+len(faces)>MAX_TRIANGLES:raise ValueError('GLB exceeds 100000 triangles')
                determinant=mat[0]*(mat[5]*mat[10]-mat[9]*mat[6])-mat[4]*(mat[1]*mat[10]-mat[9]*mat[2])+mat[8]*(mat[1]*mat[6]-mat[5]*mat[2])
                for face in faces:
                    if mode!=4 and len(set(face))<3:continue
                    tri=[tuple(sum(mat[k*4+r]*vertices[idx][k] for k in range(3))+mat[12+r] for r in range(3)) for idx in face]
                    if any(not math.isfinite(v) or abs(v)>1e9 for point in tri for v in point):raise ValueError('Transformed GLB coordinate outside supported range')
                    if determinant<0:tri[1],tri[2]=tri[2],tri[1]
                    result.append(tri)
        for child in node.get('children',[]):visit(child,mat,depth+1)
    scene=item(doc.get('scenes',[]),doc.get('scene',0))
    for index in scene.get('nodes',[]):visit(index,IDENTITY)
    if not result:raise ValueError('GLB scene has no supported geometry')
    return result
