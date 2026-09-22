"""Dependency-free, deterministic crown geometry and binary STL/GLB utilities.
Units in generated STL: millimetres. Not a structural or thermal certification.
"""
from __future__ import annotations
import hashlib, json, math, struct
from pathlib import Path

INTERFACE = {
    'version': 'bloom-clamp-v1', 'units': 'mm', 'crown_bore_diameter': 76.0,
    'head_pilot_diameter': 75.0, 'bearing_zone_radii': [38.0, 43.0],
    'bearing_thickness': 4.0, 'max_outer_diameter': 196.0,
    'max_depth': 24.0, 'retainer_outer_diameter': 86.0,
    'retainer_screw_bcd': 70.0, 'retainer_screws': '3 x M3 through bolts + washers + nuts',
    'nfc_tag_center_xy': [0.0, -55.0], 'nfc_tag_max_size': [22.0, 22.0],
    'qualification': 'prototype; fit, retention, RF and thermal tests required',
}

def normal(a, b, c):
    u=[b[i]-a[i] for i in range(3)]; v=[c[i]-a[i] for i in range(3)]
    n=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
    length=math.sqrt(sum(x*x for x in n)) or 1
    return tuple(x/length for x in n)

def write_stl(path: Path, triangles):
    triangles=list(triangles)
    with path.open('wb') as f:
        f.write(b'BLOOM / millimetres / prototype only'.ljust(80,b' '))
        f.write(struct.pack('<I',len(triangles)))
        for a,b,c in triangles:
            f.write(struct.pack('<12fH',*normal(a,b,c),*a,*b,*c,0))

def read_stl(path: Path):
    data=path.read_bytes()
    if len(data)>=84:
        n=struct.unpack_from('<I',data,80)[0]
        if len(data)==84+50*n:
            return [[tuple(struct.unpack_from('<3f',data,84+i*50+12+j*12)) for j in range(3)] for i in range(n)]
    import re
    vertices=[tuple(map(float,m)) for m in re.findall(r'vertex\s+([\-+.\deE]+)\s+([\-+.\deE]+)\s+([\-+.\deE]+)',data.decode())]
    if len(vertices)%3: raise ValueError('Malformed STL')
    return [vertices[i:i+3] for i in range(0,len(vertices),3)]

def crown_parameters(prompt: str, theme: str, seed: int | None=None):
    seed=seed if seed is not None else int.from_bytes(hashlib.sha256((prompt+theme).encode()).digest()[:4],'big')
    digest=hashlib.sha256(f'{prompt}|{theme}|{seed}'.encode()).digest()
    lobes={'sunflower':12,'forest':8,'stars':7}.get(theme,12)+(digest[0]%3-1)
    return {'theme':theme,'seed':seed,'lobes':lobes,
            'root_radius':70.0,'tip_radius':88.0+int.from_bytes(digest[1:3],'big')/65535*8,
            'phase':int.from_bytes(digest[3:5],'big')/65535*math.tau/lobes,
            'inner_radius':38.0,'thickness':4.0}

def crown_triangles(params, segments=240, rings=8):
    n=params['lobes']; root=params['root_radius']; tip=params['tip_radius']; theme=params['theme']
    layers=[]
    for j in range(rings+1):
        layer=[]
        for i in range(segments):
            a=math.tau*i/segments
            angle=a+params.get('phase',0)
            wave=(math.cos(n*angle)+1)/2
            if theme=='stars': wave=1-abs((n*angle/math.pi)%2-1)
            elif theme=='forest': wave=wave**1.4
            else: wave=wave**0.65
            edge=root+(tip-root)*wave
            r=38.0 if j==0 else 43.0+(edge-43.0)*(j-1)/(rings-1)
            # Guaranteed flat annular clamp interface; organic relief outside r=43.
            h=4+max(0,min(1,(r-43)/12))*3*(0.4+0.6*wave)
            layer.append(((r*math.cos(a),r*math.sin(a),0.0),(r*math.cos(a),r*math.sin(a),h)))
        layers.append(layer)
    out=[]
    for j in range(rings):
        for i in range(segments):
            k=(i+1)%segments
            a,b,c,d=layers[j][i],layers[j+1][i],layers[j+1][k],layers[j][k]
            out.extend([(a[1],b[1],c[1]),(a[1],c[1],d[1]),(a[0],c[0],b[0]),(a[0],d[0],c[0])])
    for i in range(segments):
        k=(i+1)%segments
        a,b=layers[0][i],layers[0][k]
        out.extend([(a[0],a[1],b[1]),(a[0],b[1],b[0])])
        a,b=layers[-1][i],layers[-1][k]
        out.extend([(a[0],b[0],b[1]),(a[0],b[1],a[1])])
    return out

def inspect_mesh(triangles):
    vertices=[v for t in triangles for v in t]
    bounds=[[min(v[i] for v in vertices) for i in range(3)],[max(v[i] for v in vertices) for i in range(3)]]
    edges={}; volume=0.0; degenerate=0
    for tri in triangles:
        a,b,c=tri
        volume+=(a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0]))/6
        pts=[tuple(round(x,4) for x in v) for v in tri]
        if len(set(pts))<3: degenerate+=1
        for i in range(3):
            edge=tuple(sorted((pts[i],pts[(i+1)%3]))); edges[edge]=edges.get(edge,0)+1
    return {'triangles':len(triangles),'bounds_mm':bounds,'dimensions_mm':[round(bounds[1][i]-bounds[0][i],3) for i in range(3)],
            'watertight_edge_count':all(v==2 for v in edges.values()),'degenerate_faces':degenerate,
            'signed_volume_mm3':round(volume,2),'physical_validation':'NOT_PERFORMED',
            'not_checked':['minimum wall thickness','self intersection','heat resistance','retention force','optics','NFC read range']}

def make_crown(directory: Path, prompt: str, theme: str, seed=None):
    directory.mkdir(parents=True,exist_ok=True)
    params=crown_parameters(prompt,theme,seed); triangles=crown_triangles(params)
    write_stl(directory/'crown.stl',triangles)
    report=inspect_mesh(triangles)
    (directory/'parameters.json').write_text(json.dumps(params,indent=2))
    (directory/'mesh-report.json').write_text(json.dumps(report,indent=2))
    (directory/'interface.json').write_text(json.dumps(INTERFACE,indent=2))
    return params,report

# Public compatibility import; the bounded decoder lives in one module.
from .gltf import glb_triangles
