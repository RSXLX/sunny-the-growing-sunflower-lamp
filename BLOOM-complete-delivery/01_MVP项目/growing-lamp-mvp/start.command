#!/bin/bash
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null; then
 echo "需要 Python 3.10+。安装后重新打开，或在终端运行 python3 run.py。"; read -r; exit 1
fi
python3 run.py
