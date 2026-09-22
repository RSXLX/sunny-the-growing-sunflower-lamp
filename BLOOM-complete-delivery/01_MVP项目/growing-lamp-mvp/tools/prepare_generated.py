#!/usr/bin/env python3
"""Prepare a Tripo GLB with the same deterministic adapter used by the worker.
Never directly print an arbitrary generated mesh without review.
"""
import argparse,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.service import Service

def main():
 p=argparse.ArgumentParser();p.add_argument('glb',type=Path);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
 args.output.mkdir(parents=True,exist_ok=True);source=args.glb.resolve();target=(args.output/'raw.glb').resolve()
 if source!=target:shutil.copy(source,target)
 # This method only processes local geometry; it does not initialize a server or call Tripo.
 Service.adapt_live_model(None,args.output.resolve())
 print('Draft exported. Inspect the source orientation, connectivity, walls and clamping region.')
if __name__=='__main__':main()
