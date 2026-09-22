"""Traceable decorative transforms, with a fixed, separately built lamp interface."""
import hashlib
import json
import math
import shutil
import subprocess
from .geometry import INTERFACE, glb_triangles, read_stl, write_stl

DEFAULT = {'mode':'raw_model','rotation_x':0,'rotation_y':0,'rotation_z':0,'width_mm':190,'depth_mm':16,'fit':'fit_depth'}


def parameters(payload, raw):
    if not isinstance(payload,dict):raise ValueError('变换参数必须为对象')
    allowed=set(DEFAULT) if raw else {'mode','rotation_z'}
    if set(payload)-allowed:raise ValueError('不支持的变换参数；最终 STL 只允许绕安装轴旋转')
    out=dict(DEFAULT) if raw else {'mode':'final_stl','rotation_z':0}
    out.update(payload)
    if out['mode']!=('raw_model' if raw else 'final_stl'):raise ValueError('变换模式与原稿不一致')
    for key in ('rotation_x','rotation_y','rotation_z') if raw else ('rotation_z',):
        value=out[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not -180<=value<=180:raise ValueError('旋转角度须为 -180° 到 180°')
    if raw:
        for key,low,high in [('width_mm',50,196),('depth_mm',1,20)]:
            value=out[key]
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:raise ValueError(f'{key} 须为 {low}–{high}')
        if out['fit'] not in {'uniform','fit_depth'}:raise ValueError('未知尺寸适配方式')
    return out


def rotate(point, p):
    x,y,z=point
    for axis in ('x','y','z'):
        angle=math.radians(p.get('rotation_'+axis,0));c,s=math.cos(angle),math.sin(angle)
        if axis=='x':y,z=y*c-z*s,y*s+z*c
        if axis=='y':x,z=x*c+z*s,-x*s+z*c
        if axis=='z':x,y=x*c-y*s,x*s+y*c
    return x,y,z


def bounds(triangles):
    points=[v for tri in triangles for v in tri]
    return [[min(v[i] for v in points) for i in range(3)],[max(v[i] for v in points) for i in range(3)]]


def original_preview(directory):
    raw=directory/'raw.glb'
    if not raw.exists():return
    try:
        triangles=glb_triangles(raw)
        # Consistent Y-up -> engineering Z-up, with original proportions intact.
        triangles=[[(p[0],-p[2],p[1]) for p in tri] for tri in triangles]
        write_stl(directory/'original-preview.stl',triangles)
        report={'supported':True,'triangles':len(triangles),'units':'original_scene_units',
                'manufacturing_dimensions':False,'appearance':'geometry_only_no_materials','bounds':bounds(triangles)}
    except ValueError as exc:
        (directory/'original-preview.stl').unlink(missing_ok=True)
        report={'supported':False,'error':str(exc),'manufacturing_dimensions':False}
    (directory/'preview-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))


def adapt_model(directory, payload=None, source_version_id=None):
    raw=(directory/'raw.glb').exists()
    p=parameters((DEFAULT if raw else {}) if payload is None else payload,raw)
    source=directory/('raw.glb' if raw else 'crown.stl')
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    if not raw:
        triangles=read_stl(source)
        triangles=[[rotate(v,p) for v in tri] for tri in triangles]
        write_stl(source,triangles)
        report={'status':'draft','method':'rotation_about_mount_axis','transform':p}
    else:
        triangles=glb_triangles(source)
        triangles=[[rotate((v[0],-v[2],v[1]),p) for v in tri] for tri in triangles]
        lo,hi=bounds(triangles);width=max(hi[0]-lo[0],hi[1]-lo[1]);depth=hi[2]-lo[2]
        if min(width,depth)<1e-9:raise ValueError('原稿旋转后退化为平面或直线，无法进行外装适配')
        xy=p['width_mm']/width
        z=p['depth_mm']/depth if p['fit']=='fit_depth' else xy
        if p['fit']=='uniform':xy=z=min(xy,p['depth_mm']/depth)
        transformed=[[((v[0]-(lo[0]+hi[0])/2)*xy,(v[1]-(lo[1]+hi[1])/2)*xy,4+(v[2]-lo[2])*z) for v in tri] for tri in triangles]
        write_stl(directory/'raw.stl',transformed)
        script='''// BLOOM fixed clamp; millimetres; manually inspect connectivity and walls.
$fn=160;
union(){
 difference(){ cylinder(r=70,h=4); translate([0,0,-1])cylinder(r=38,h=30); }
 difference(){
  intersection(){ import("raw.stl",convexity=10); cylinder(r=98,h=24); }
  translate([0,0,-1])cylinder(r=43,h=30);
 }
}
'''
        (directory/'adaptation.scad').write_text(script)
        (directory/'crown.stl').unlink(missing_ok=True)
        report={'status':'manual_required','method':'fixed_carrier_and_annular_crop','transform':p,
                'decorative_bounds_mm':bounds(transformed),'scale_xyz':[xy,xy,z],
                'reason':'OpenSCAD 未安装；装饰预览尚未进行布尔裁切与连接验证'}
        if shutil.which('openscad'):
            result=subprocess.run(['openscad','-o',str(directory/'crown.stl'),str(directory/'adaptation.scad')],capture_output=True,text=True,timeout=90)
            if result.returncode or not (directory/'crown.stl').is_file():
                (directory/'crown.stl').unlink(missing_ok=True)
                report['reason']='OpenSCAD 适配失败；请下载草案并人工处理'
            else:
                report['status']='draft';report.pop('reason',None)
    record={'algorithm':'bloom-adapt-1','parameters':p,'source_version_id':source_version_id,
            'source_file':source.name,'source_sha256':sha,'interface_version':INTERFACE['version'],
            'physical_validation':'NOT_PERFORMED'}
    (directory/'transform.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
    report['not_checked']=['component connectivity','wall thickness','self intersections','fit','retention','thermal safety']
    (directory/'adapter-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return record
