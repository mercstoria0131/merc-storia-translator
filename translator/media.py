"""播放用素材的即時解碼：背景、立繪、BGM、音效、影片不事先匯出到硬碟，
播放器要用時才從使用者自己的 Steam 遊戲檔解碼，結果只放在記憶體。

網址對應（與播放器原本的相對路徑相同，播放器不需要改）：
  output/assets/bg/<名稱>.jpg        → StoryBackground
  output/assets/chara/<編號>.webp    → StoryActress 立繪圖集
  output/assets/chara/u<編號>.webp   → 夥伴全身圖（Unit/LargeNoStage）
  output/assets/bgm/<名稱>.mp3       → Sound（BGM）
  output/assets/se/<名稱>.mp3        → Sound（音效）
  output/assets/movie/<名稱>         → 影片（MP4，不複製，直接從解開的 bundle 取用）

點選故事時（播放器載入 data/<id>.js），伺服器會在背景把這一話用到的素材先解碼進記憶體。
"""
import io
import os
import re
import subprocess
import sys
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

KINDS = ("bg", "chara", "bgm", "se", "movie")
CTYPE = {".jpg": "image/jpeg", ".webp": "image/webp", ".mp3": "audio/mpeg", ".mp4": "video/mp4"}
_NAME = re.compile(r"^[^/\\]+$")
TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
# 在子程序解開影片：UnityPy 解 bundle 時會暫時佔用數倍於影片的記憶體（402 MB 的影片約 2 GB），
# 子程序結束後全部歸還，伺服器只保留 MP4 本身
_MOVIE_CHILD = (
    "import sys; sys.path.insert(0, sys.argv[1]); from game_data import GameData\n"
    "v, _e = GameData(sys.argv[2]).movie_view(sys.argv[3]); o = sys.stdout.buffer\n"
    "o.write((len(v) if v is not None else 0).to_bytes(8, 'little'))\n"
    "if v is not None: o.write(v)\n"
    "o.flush()\n")


def read_movie(game_dir, name):
    p = subprocess.Popen([sys.executable, "-c", _MOVIE_CHILD, TOOLS, game_dir, name],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    n = int.from_bytes(p.stdout.read(8) or b"\0" * 8, "little")
    buf = bytearray(n)
    view, got = memoryview(buf), 0
    while got < n:
        k = p.stdout.readinto(view[got:])
        if not k:
            break
        got += k
    err = p.stderr.read().decode("utf-8", "replace")
    p.wait()
    if n == 0 or got < n:
        raise RuntimeError("影片解開失敗：%s %s" % (name, err.strip().splitlines()[-1:] if err else ""))
    return buf


class MediaCache:
    def __init__(self, game_fn, limit_mb=512, workers=3, log=print):
        self.game_fn = game_fn            # 取得 GameData 的函式（設定變更後會換新的）
        self.limit = limit_mb * 1024 * 1024
        self.items = OrderedDict()        # key → bytes（最近用過的放最後）
        self.size = 0
        self.lock = threading.Lock()
        self.pending = {}                 # key → threading.Event（正在解碼，其他請求等它完成）
        self.missing = set()
        self.missing_movies = set()
        self.movie = (None, None)         # (名稱, bytearray)：影片很大，同時只留一部
        self.movie_lock = threading.Lock()
        self.pool = ThreadPoolExecutor(workers)
        self.log = log

    # ------------------------------------------------------------ 共用
    @staticmethod
    def parse(rel):
        """'bg/xxx.jpg' → (kind, name, ext)；不是即時解碼的路徑則回傳 None。"""
        parts = rel.split("/", 1)
        if len(parts) != 2 or parts[0] not in KINDS or not _NAME.match(parts[1]):
            return None
        kind, fname = parts
        if kind == "movie":
            return kind, fname, ".mp4"
        name, ext = os.path.splitext(fname)
        return (kind, name, ext) if ext in CTYPE else None

    def _decode(self, kind, name):
        g = self.game_fn()
        buf = io.BytesIO()
        if kind == "bg":
            ok = g.export_background(name, buf)
        elif kind == "chara":
            ok = g.export_unit_actress(int(name[1:]), buf) if name.startswith("u") else g.export_actress(int(name), buf)
        else:
            ok = g.export_sound(name, buf)
        return buf.getvalue() if ok else None

    def clear(self):
        with self.lock:
            self.items.clear()
            self.size = 0
            self.missing.clear()
            self.missing_movies.clear()
            self.movie = (None, None)

    # ------------------------------------------------------------ 圖片、聲音
    def get(self, rel):
        """回傳 (bytes, content_type)；找不到素材時回傳 (None, None)。"""
        p = self.parse(rel)
        if not p:
            return None, None
        kind, name, ext = p
        if kind == "movie":
            return None, None
        key = kind + "/" + name
        while True:
            with self.lock:
                if key in self.items:
                    self.items.move_to_end(key)
                    return self.items[key], CTYPE[ext]
                if key in self.missing:
                    return None, None
                ev = self.pending.get(key)
                if ev is None:
                    ev = self.pending[key] = threading.Event()
                    mine = True
                else:
                    mine = False
            if not mine:          # 別的執行緒（例如預先載入）正在解碼同一個素材
                ev.wait(60)
                continue
            try:
                data = self._decode(kind, name)
            except Exception as e:  # noqa: BLE001
                self.log("素材解碼失敗 %s：%s" % (key, e))
                data = None
            with self.lock:
                self.pending.pop(key, None)
                if data is None:
                    self.missing.add(key)
                else:
                    self.items[key] = data
                    self.size += len(data)
                    while self.size > self.limit and len(self.items) > 1:
                        _k, v = self.items.popitem(last=False)
                        self.size -= len(v)
            ev.set()
            return (data, CTYPE[ext]) if data is not None else (None, None)

    # ------------------------------------------------------------ 影片
    def movie_view(self, name):
        with self.movie_lock:            # 同一部影片同時只解一次（預先載入與播放器的請求可能同時到）
            with self.lock:
                if self.movie[0] == name:
                    return self.movie[1]
                if name in self.missing_movies:
                    return None
                self.movie = (None, None)        # 先放掉上一部，避免兩部同時佔記憶體
            try:
                data = read_movie(self.game_fn().game_dir, name)
            except Exception as e:  # noqa: BLE001
                self.log(str(e))
                with self.lock:
                    self.missing_movies.add(name)
                return None
            with self.lock:
                self.movie = (name, data)
            return data

    # ------------------------------------------------------------ 預先載入
    def preload(self, rels):
        """點選故事時：把這一話用到的素材排進背景解碼；影片同時只留一部，先解開這一話的第一部。"""
        movies = []
        for rel in rels:
            p = self.parse(rel)
            if p and p[0] == "movie":
                movies.append(p[1])
            elif p:
                self.pool.submit(self.get, rel)
        if movies:
            threading.Thread(target=self.movie_view, args=(movies[0],), daemon=True).start()

    def stats(self):
        with self.lock:
            return {"items": len(self.items), "mb": round(self.size / 1048576, 1),
                    "limit_mb": self.limit // 1048576, "missing": sorted(self.missing)[:50],
                    "movie": self.movie[0]}
