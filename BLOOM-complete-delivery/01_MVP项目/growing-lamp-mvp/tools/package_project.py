#!/usr/bin/env python3
"""Create source/hardware deliverable; never include runtime secrets or databases."""
import argparse,hashlib,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
EXCLUDE_DIRS={'.git','__pycache__','.pio','.venv','node_modules','data','private-data','backups'}
EXCLUDE_NAMES={'.env','config.local.h','.DS_Store','DELIVERY_MANIFEST.json','browser-failure.png'}
def files():
 for p in sorted(ROOT.rglob('*')):
  if not p.is_file() or any(x in EXCLUDE_DIRS for x in p.relative_to(ROOT).parts):continue
  if p.name in EXCLUDE_NAMES or p.suffix in {'.pyc','.sqlite3','.db','.pem','.key','.woff','.woff2','.ttf','.otf'}:continue
  yield p

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,default=ROOT.parent/'BLOOM-growing-lamp-MVP.zip');args=parser.parse_args()
 members=list(files());manifest={'project':'BLOOM growing lamp MVP','version':'1.0.0','files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in members},'validation':'See docs/TEST_REPORT.md; digital files are not a physically tested lamp.'}
 m=ROOT/'DELIVERY_MANIFEST.json';m.write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
 with zipfile.ZipFile(args.out,'w',zipfile.ZIP_DEFLATED,compresslevel=7) as z:
  for p in members+[m]:z.write(p,ROOT.name+'/'+str(p.relative_to(ROOT)))
 with zipfile.ZipFile(args.out) as z:
  if z.testzip():raise SystemExit('ZIP integrity check failed')
 print(json.dumps({'zip':str(args.out),'files':len(members)+1,'bytes':args.out.stat().st_size,'sha256':hashlib.sha256(args.out.read_bytes()).hexdigest()},indent=2))
if __name__=='__main__':main()
