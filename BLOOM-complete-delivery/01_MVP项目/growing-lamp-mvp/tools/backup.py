#!/usr/bin/env python3
"""Compatibility entry point for full verified backups. Stop BLOOM first."""
import argparse,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.backup import create_backup
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',type=Path,default=Path('data'));p.add_argument('--out',type=Path,default=Path('backups'));a=p.parse_args()
try:print(json.dumps(create_backup(a.data,a.out/('bloom-'+str(time.time_ns())+'.zip')),ensure_ascii=False,indent=2))
except (ValueError,OSError,RuntimeError) as exc:p.exit(1,str(exc)+'\n')
