"""Run BLOOM from the repository root (Python 3.10+)."""
import sys
from pathlib import Path

if sys.version_info < (3, 10):
    raise SystemExit('BLOOM requires Python 3.10 or newer; try python3.13 run.py')

APP = Path(__file__).resolve().parent / 'BLOOM-complete-delivery/01_MVP项目/growing-lamp-mvp'
sys.path.insert(0, str(APP))

if __name__ == '__main__':
    from app.server import main
    main()
