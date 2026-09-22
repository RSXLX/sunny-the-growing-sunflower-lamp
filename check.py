"""Run the application checks from the repository root."""
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent / 'BLOOM-complete-delivery/01_MVP项目/growing-lamp-mvp'
if __name__ == '__main__':
    if sys.version_info < (3, 10):
        raise SystemExit('Use Python 3.10+, for example python3.13 check.py')
    raise SystemExit(subprocess.call([sys.executable, 'tools/check.py'], cwd=APP))
