#!/usr/bin/env python3
"""Offline BLOOM maintenance. Stop the service before backup/audit/quarantine."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.backup import create_backup,verify_backup,restore_backup,audit,quarantine

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    b=sub.add_parser('backup');b.add_argument('--data',type=Path,required=True);b.add_argument('--output',type=Path,required=True)
    v=sub.add_parser('verify');v.add_argument('archive',type=Path)
    r=sub.add_parser('restore');r.add_argument('archive',type=Path);r.add_argument('--target',type=Path,required=True)
    a=sub.add_parser('audit');a.add_argument('--data',type=Path,required=True);a.add_argument('--plan',type=Path,required=True)
    q=sub.add_parser('quarantine');q.add_argument('--data',type=Path,required=True);q.add_argument('--plan',type=Path,required=True)
    args=p.parse_args()
    try:
        if args.command=='backup':result=create_backup(args.data,args.output)
        elif args.command=='verify':result=verify_backup(args.archive)
        elif args.command=='restore':result=restore_backup(args.archive,args.target)
        elif args.command=='audit':
            result=audit(args.data)
            with args.plan.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
            args.plan.chmod(0o600)
            result={'plan':str(args.plan.absolute()),'referenced_files':len(result['files']),'orphan_files':len(result['orphans']),'counts':result['counts']}
        else:result=quarantine(args.data,json.loads(args.plan.read_text()))
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,OSError,RuntimeError) as exc:p.exit(1,'维护失败：'+str(exc)+'\n')

if __name__=='__main__':main()
