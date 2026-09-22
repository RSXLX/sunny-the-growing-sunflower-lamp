#!/usr/bin/env python3
"""Run available repeatable checks. A missing tool is SKIP, never PASS."""
import shutil,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def run(cmd):
 print('+',' '.join(map(str,cmd)),flush=True)
 subprocess.run(cmd,cwd=ROOT,check=True)
run([sys.executable,'-m','unittest','discover','-s','tests','-v'])
if shutil.which('node'):
 for f in ['web/app.js','web/viewer.js','web/fabrication.js']:run(['node','--check',f])
else:print('SKIP: node syntax check; node not installed')
if shutil.which('g++'):
 with tempfile.TemporaryDirectory() as d:
  exe=str(Path(d)/'bloom-logic')
  run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-Ihardware/firmware/include','hardware/firmware/test/logic_test.cpp','-o',exe]);run([exe])
else:print('SKIP: portable firmware tests; g++ not installed')
print('NOT RUN here: full PlatformIO board build, flash, paid API and physical tests. See docs/TEST_REPORT.md.')
