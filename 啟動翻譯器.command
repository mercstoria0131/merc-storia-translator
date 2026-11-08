#!/bin/zsh
# 梅露可物語 離線版 故事翻譯器 啟動腳本（macOS：雙擊執行）
cd "$(dirname "$0")"
VENV="$HOME/.venvs/merc-story"
if [ ! -x "$VENV/bin/python" ]; then
  echo "第一次執行：建立 Python 環境（需要 Python 3.10 以上）…"
  PY=""
  for c in python3.13 python3.12 python3.11 python3.10 "$HOME/miniforge3/envs/unitypy/bin/python" python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)'; then PY="$c"; break; fi
  done
  if [ -z "$PY" ]; then echo "找不到 Python 3.10 以上版本，請先安裝。"; read; exit 1; fi
  "$PY" -m venv "$VENV" && "$VENV/bin/pip" install -q --upgrade pip && "$VENV/bin/pip" install -q -r requirements.txt || { echo "安裝套件失敗"; read; exit 1; }
fi
exec "$VENV/bin/python" translator/server.py "$@"
