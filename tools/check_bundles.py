"""檢查遊戲素材檔（.bundle）是否損毀。

用法：python check_bundles.py          快速檢查：找出尾端被截斷（全是 0）的檔案
      python check_bundles.py --full   完整檢查：逐一解壓每個 .bundle（較慢）

列出損毀的檔案與其中包含的素材，可在 Steam 用「驗證遊戲檔案完整性」重新下載。
"""
import os, sys
import UnityPy
from game_data import GameData


def tail_zero(path):
    sz = os.path.getsize(path)
    with open(path, "rb") as fh:
        fh.seek(max(0, sz - 4096))
        t = fh.read()
    return len(t) > 0 and t.count(0) == len(t)


def loads(path):
    try:
        UnityPy.load(path)
        return True
    except Exception:
        return False


def main():
    full = "--full" in sys.argv
    g = GameData()
    root = g.bdir
    bad = []
    n = 0
    for cat in sorted(os.listdir(root)):
        d = os.path.join(root, cat)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not f.endswith(".bundle"):
                continue
            n += 1
            p = os.path.join(d, f)
            if (full or tail_zero(p)) and not loads(p):
                bad.append((cat, f))
    print("已檢查 %d 個素材檔，損毀 %d 個" % (n, len(bad)))
    for cat, f in bad:
        try:
            keys = [k for k, v in g.catalog(cat).items() if v[1] == f]
        except Exception:
            keys = []
        short = [k.split("/ExternalResources/")[-1].split("/BundleAssets/")[-1] for k in keys]
        more = "…等 %d 項" % len(short) if len(short) > 3 else ""
        print("  %s/%s  %s%s" % (cat, f, "、".join(short[:3]), more))


if __name__ == "__main__":
    main()
