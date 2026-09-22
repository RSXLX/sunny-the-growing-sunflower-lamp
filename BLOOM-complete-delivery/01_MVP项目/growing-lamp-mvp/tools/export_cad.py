#!/usr/bin/env python3
"""Export actual parametric CAD to STL; requires a locally installed OpenSCAD."""
import argparse, json, shutil, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.geometry import make_crown, INTERFACE, read_stl, inspect_mesh
PARTS=['base_shell','base_lid','head_core','retainer','head_socket','leaf_button','fit_coupon','crown_coupon','gobo_body','lens_holder','lens_retainer','gobo_forest','gobo_stars','projector_cradle']

def main():
    p=argparse.ArgumentParser();p.add_argument('--part',choices=PARTS);args=p.parse_args()
    exe=shutil.which('openscad')
    if not exe:raise SystemExit('Install OpenSCAD and ensure the openscad CLI is in PATH.')
    out=ROOT/'hardware/exports';out.mkdir(exist_ok=True);results=json.loads((out/'cad-mesh-checks.json').read_text()) if args.part and (out/'cad-mesh-checks.json').exists() else {}
    for part in ([args.part] if args.part else PARTS):
        cmd=[exe,'--export-format','binstl','-o',str(out/(part+'.stl')),'-D',f'part="{part}"',str(ROOT/'hardware/cad/lamp.scad')]
        done=subprocess.run(cmd,capture_output=True,text=True,timeout=120)
        (out/(part+'.log')).write_text(done.stdout+done.stderr)
        if done.returncode:raise SystemExit('CAD export failed: '+part+'\n'+done.stderr)
        results[part]=inspect_mesh(read_stl(out/(part+'.stl')));print(part,results[part]['triangles'],results[part]['watertight_edge_count'],flush=True)
    (out/'cad-mesh-checks.json').write_text(json.dumps(results,indent=2))
    (ROOT/'hardware/cad/interface.json').write_text(json.dumps(INTERFACE,indent=2))
    for theme,seed in [('sunflower',11),('forest',21),('stars',32)]:
        tmp=out/('_'+theme);make_crown(tmp,'BLOOM sample '+theme,theme,seed)
        shutil.copy(tmp/'crown.stl',out/('crown_'+theme+'.stl'));shutil.copy(tmp/'parameters.json',out/('crown_'+theme+'.json'));shutil.rmtree(tmp)
    print('Exports complete. These are geometric checks, not physical validation.')
if __name__=='__main__':main()
