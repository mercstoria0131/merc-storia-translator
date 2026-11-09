#!/bin/zsh
# 梅露可物語 離線版 故事翻譯器 啟動腳本（macOS：雙擊執行）
# 建議用 conda（Miniconda／Miniforge）：自動建立名為 merc-story 的環境；沒有 conda 時改用 Python venv。
cd "$(dirname "$0")"
ENVNAME="merc-story"
PYVER="3.12"
VENV="$HOME/.venvs/merc-story"
MARKNAME=".merc-installed"   # 套件安裝成功後才建立；上次安裝失敗時會重新安裝

ok310() { [ -x "$1" ] && "$1" -c 'import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null; }

CONDA=""
for c in "$CONDA_EXE" "$HOME/miniforge3/bin/conda" "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda" \
         /opt/homebrew/Caskroom/miniforge/base/bin/conda /opt/homebrew/Caskroom/miniconda/base/bin/conda \
         /opt/miniconda3/bin/conda /opt/anaconda3/bin/conda; do
  if [ -n "$c" ] && [ -x "$c" ]; then CONDA="$c"; break; fi
done
CENV=""
[ -n "$CONDA" ] && CENV="$(cd "$(dirname "$CONDA")/.." && pwd)/envs/$ENVNAME"

PY=""
if [ -n "$CENV" ] && ok310 "$CENV/bin/python" && [ -f "$CENV/$MARKNAME" ]; then
  PY="$CENV/bin/python"                                   # 1. 已經裝好的 conda 環境
elif ok310 "$VENV/bin/python" && [ -f "$VENV/$MARKNAME" ]; then
  PY="$VENV/bin/python"                                   # 2. 已經裝好的 venv
elif [ -n "$CONDA" ]; then                                # 3. 用 conda 建立環境
  echo "建立 conda 環境「$ENVNAME」（Python $PYVER，需要網路，約幾分鐘）…"
  if ! ok310 "$CENV/bin/python"; then
    [ -d "$CENV" ] && "$CONDA" env remove -y -q -n "$ENVNAME" >/dev/null 2>&1
    "$CONDA" create -y -q -n "$ENVNAME" --override-channels -c conda-forge "python=$PYVER" pip \
      || { echo "建立 conda 環境失敗，請確認網路連線後再試一次。"; read; exit 1; }
  fi
  PY="$CENV/bin/python"; ROOT="$CENV"
else                                                      # 4. 沒有 conda：用 venv
  echo "第一次執行：建立 Python 環境（需要 Python 3.10 以上）…"
  BASE=""
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    p="$(command -v "$c" 2>/dev/null)"
    if [ -n "$p" ] && ok310 "$p"; then BASE="$p"; break; fi
  done
  if ! ok310 "$VENV/bin/python"; then
    if [ -z "$BASE" ]; then
      echo "找不到 conda 或 Python 3.10 以上版本。建議安裝 Miniforge：https://github.com/conda-forge/miniforge"; read; exit 1
    fi
    "$BASE" -m venv "$VENV" || { echo "建立環境失敗"; read; exit 1; }
  fi
  PY="$VENV/bin/python"; ROOT="$VENV"
fi

if [ -n "$ROOT" ]; then                                   # 安裝套件
  "$PY" -m pip install -q --upgrade pip && "$PY" -m pip install -q -r requirements.txt && touch "$ROOT/$MARKNAME" \
    || { echo "安裝套件失敗，請確認網路連線後再試一次。"; read; exit 1; }
fi
exec "$PY" translator/server.py "$@"
