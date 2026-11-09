"""梅露可物語 離線版 故事翻譯器 —— 本機網頁介面伺服器。

啟動：雙擊「啟動翻譯器.command」（macOS）或「啟動翻譯器.bat」（Windows），然後瀏覽 http://localhost:8765

播放器（/output/）的背景、立繪、BGM、音效、影片不存在硬碟上：播放時才從使用者自己的 Steam 遊戲檔解碼，
只放在記憶體（translator/media.py）；劇本資料 data/<id>.js 在第一次開啟該話時才建立。
"""
import argparse
import json
import os
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, HERE)

import backends  # noqa: E402
import pipeline  # noqa: E402
import build_player  # noqa: E402
from build_player import Builder  # noqa: E402
from game_data import DEFAULT_GAME_DIR, GameData  # noqa: E402
from media import MediaCache  # noqa: E402

WEB = os.path.join(HERE, "web")
OUTPUT = os.path.join(ROOT, "output")
SETTINGS_PATH = os.path.join(ROOT, "work", "settings.json")
SETTINGS_TEMPLATE = os.path.join(ROOT, "work", "settings.template.json")
CRED_DIR = os.path.expanduser("~/.config/merc-story-translator")
CRED_PATH = os.path.join(CRED_DIR, "credentials.json")

STATE = {"game": None, "chapters": None}
JOBS = {}
JOB_LOCK = threading.Lock()


# ---------------------------------------------------------------- 設定
SETTING_KEYS = ("provider", "model", "effort", "workers", "game_dir", "extra_rules", "refs", "ollama_url",
                "ollama_model", "vllm_url", "vllm_model", "num_ctx", "temperature", "batch_lines", "ollama_think",
                "media_cache_mb")
SECRET_KEYS = ("api_key", "ollama_key", "vllm_key")


def load_settings():
    s = {"provider": "anthropic", "model": pipeline.DEFAULT_MODEL, "effort": "medium", "workers": 3,
         "game_dir": DEFAULT_GAME_DIR, "extra_rules": "", "refs": "", "ollama_url": "http://192.168.1.10:11434",
         "ollama_model": "", "vllm_url": "http://192.168.1.10:8000", "vllm_model": "", "num_ctx": 16384,
         "temperature": 0.3, "batch_lines": 0, "ollama_think": "off", "media_cache_mb": 512}
    if not os.path.exists(SETTINGS_PATH) and os.path.exists(SETTINGS_TEMPLATE):
        # 第一次執行：由樣板建立 work/settings.json（樣板含預設的追加翻譯規則；settings.json 不納入版本控制）
        with open(SETTINGS_TEMPLATE, encoding="utf-8") as f, open(SETTINGS_PATH, "w", encoding="utf-8") as g:
            g.write(f.read())
    if os.path.exists(SETTINGS_PATH):
        s.update(json.load(open(SETTINGS_PATH, encoding="utf-8")))
    if not s.get("game_dir"):      # 空白＝自動尋找 Steam 遊戲資料夾
        s["game_dir"] = DEFAULT_GAME_DIR
    if os.path.exists(CRED_PATH):
        cred = json.load(open(CRED_PATH))
        for k in SECRET_KEYS:
            s[k] = cred.get(k, "")
    return s


def save_settings(new):
    s = load_settings()
    secrets = {k: new.pop(k) for k in SECRET_KEYS if k in new}
    s.update({k: v for k, v in new.items() if k in SETTING_KEYS})
    for k in SECRET_KEYS:
        s.pop(k, None)
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    json.dump(s, open(SETTINGS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if secrets:  # 金鑰存在 SD 卡之外，權限 600
        cred = json.load(open(CRED_PATH)) if os.path.exists(CRED_PATH) else {}
        cred.update({k: (v or "").strip() for k, v in secrets.items()})
        os.makedirs(CRED_DIR, exist_ok=True)
        with open(CRED_PATH, "w") as f:
            json.dump(cred, f)
        os.chmod(CRED_PATH, 0o600)
    return load_settings()


def engine_ready(s):
    """回傳 (是否可翻譯, 說明)。"""
    p = s.get("provider", "anthropic")
    if p == "anthropic":
        ok = bool(s.get("api_key")) or bool(os.environ.get("ANTHROPIC_API_KEY"))
        return ok, ("Claude：" + s.get("model", "")) if ok else "Claude API 金鑰未設定"
    url, model = s.get(p + "_url", ""), s.get(p + "_model", "")
    if not url:
        return False, backends.PROVIDERS[p] + "：未設定網址"
    if not model:
        return False, backends.PROVIDERS[p] + "：未選擇模型"
    return True, "%s：%s" % ("Ollama" if p == "ollama" else "vLLM", model)


def game():
    if STATE["game"] is None:
        STATE["game"] = GameData(load_settings().get("game_dir") or DEFAULT_GAME_DIR)
    return STATE["game"]


MEDIA = MediaCache(lambda: game(), limit_mb=int(load_settings().get("media_cache_mb") or 512),
                   log=lambda m: print(m, flush=True))
STORY_LOCKS = {}
STORY_LOCK = threading.Lock()


def install_frontend():
    """把播放器前端（tools/player_src）與立繪格位表放進 output/；不需要遊戲檔。"""
    src = os.path.join(ROOT, "tools", "player_src")
    for name in os.listdir(src):
        if name.startswith("._"):
            continue
        a, b = os.path.join(src, name), os.path.join(OUTPUT, name)
        if os.path.isdir(a):
            import shutil
            shutil.copytree(a, b, dirs_exist_ok=True, ignore=lambda _d, fs: [f for f in fs if f.startswith("._")])
        else:
            with open(a, "rb") as f, open(b, "wb") as g:
                g.write(f.read())
    os.makedirs(os.path.join(OUTPUT, "data"), exist_ok=True)
    with open(os.path.join(OUTPUT, "data", "layout.js"), "w", encoding="utf-8") as f:
        f.write("window.ACTRESS_LAYOUT=" + json.dumps(build_player.ACTRESS_LAYOUT, ensure_ascii=False) + ";\n")


def ensure_ui_assets():
    """播放器介面圖片（訊息框、按鈕等）不隨專案附上：第一次啟動時從使用者的遊戲檔匯出到 output/assets/ui。
    找不到遊戲時略過；設定好遊戲目錄後，第一次開啟故事時也會補上。"""
    ui = os.path.join(OUTPUT, "assets", "ui")
    if os.path.exists(os.path.join(ui, "bg_message_fs.png")):
        return
    try:
        game().export_story_ui(ui)
        print("已從遊戲檔匯出播放器介面圖片", flush=True)
    except Exception as e:  # noqa: BLE001
        print("暫時無法匯出播放器介面圖片（請到「設定」確認遊戲目錄）：%s" % e, flush=True)


def ensure_story(sid):
    """播放器要讀 data/<id>.js 時：沒有或比譯文／名詞表舊就即時建立（只有劇本資料，素材不匯出）。回傳檔案路徑。"""
    path = os.path.join(OUTPUT, "data", "%d.js" % sid)
    deps = [os.path.join(pipeline.ZH_DIR, "%d.json" % sid), pipeline.GLOSSARY_PATH, build_player.CHAPTERS_ZH] + \
        pipeline._SCRIPT_DEPS
    newest = max([os.path.getmtime(d) for d in deps if os.path.exists(d)] or [0])
    with STORY_LOCK:
        lk = STORY_LOCKS.setdefault(sid, threading.Lock())
    with lk:
        if not os.path.exists(path) or os.path.getmtime(path) < newest:
            pipeline.load_ja(sid, game())
            b = Builder(game(), log=lambda m: None)
            b.build([sid], chapters(), workers=1)
    return path


def ensure_catalog():
    path = os.path.join(OUTPUT, "data", "catalog.js")
    deps = [p for p in (build_player.CHAPTERS_ZH, pipeline.GLOSSARY_PATH) if os.path.exists(p)]
    if not os.path.exists(path) or any(os.path.getmtime(p) > os.path.getmtime(path) for p in deps):
        b = Builder(game(), log=lambda m: None)
        b.write_catalog(chapters(), {t["src"]: t["tgt"] for t in pipeline.load_glossary()["terms"] if t.get("tgt")},
                        all_stories=True)
    return path


def story_assets(path):
    """data/<id>.js 裡的 pre（這一話用到的素材清單）。"""
    try:
        with open(path, encoding="utf-8") as f:
            txt = f.read()
        return json.loads(txt[txt.index("(") + 1:txt.rindex(")")]).get("pre", [])
    except (OSError, ValueError):
        return []


def chapters():
    if STATE["chapters"] is None:
        cache = os.path.join(ROOT, "data", "master", "chapters.json")
        if os.path.exists(cache):
            STATE["chapters"] = json.load(open(cache, encoding="utf-8"))
        else:
            STATE["chapters"] = game().chapter_list()
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            json.dump(STATE["chapters"], open(cache, "w", encoding="utf-8"), ensure_ascii=False)
    return STATE["chapters"]


def story_title(sid):
    for c in chapters():
        for s in c["stories"]:
            if s["storyId"] == sid:
                return s["title"]
    return ""


def save_reviewed(sid, zh, ja=None, write=True):
    """重新驗證（扣掉已拒絕的警告）後寫回 work/zh/<id>.json（write=False 只更新 zh["warnings"]）。"""
    ja = ja or pipeline.load_ja(sid, game())
    res = {int(k): v["text"] for k, v in zh["lines"].items()}
    zh["warnings"] = pipeline.drop_dismissed(
        pipeline.verify(pipeline.story_lines(ja), res, pipeline.load_glossary()), zh.get("dismissed"))
    if not write:
        return
    zp = os.path.join(pipeline.ZH_DIR, "%d.json" % sid)
    tmp = zp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(zh, f, ensure_ascii=False, indent=1)
    os.replace(tmp, zp)


def handle_warning(b):
    """校對頁的「接受」（依警告修改該句並儲存）與「拒絕」（記住這筆警告，之後不再顯示）。"""
    sid, i = int(b["id"]), int(b["i"])
    w = {"i": i, "type": b["type"], "msg": b["msg"]}
    zp = os.path.join(pipeline.ZH_DIR, "%d.json" % sid)
    if not os.path.exists(zp):
        return {"error": "這一話還沒有譯文"}
    zh = json.load(open(zp, encoding="utf-8"))
    if "text" in b:   # 畫面上尚未儲存的修改：以它為準
        zh["lines"][str(i)] = {"text": pipeline.fix_text("", b["text"])}
    if b.get("action") == "reject":
        d = zh.setdefault("dismissed", [])
        if pipeline.warn_key(w) not in {pipeline.warn_key(x) for x in d}:
            d.append(w)
        save_reviewed(sid, zh)
        return {"ok": True, "text": zh["lines"].get(str(i), {}).get("text", ""), "warnings": zh["warnings"]}
    ja = pipeline.load_ja(sid, game())
    line = next((ln for ln in pipeline.story_lines(ja) if ln[0] == i), None)
    if not line:
        return {"error": "找不到第 %d 句原文" % i}
    _i, sps, ja_text = line
    cur = zh["lines"].get(str(i), {}).get("text", "")
    save_reviewed(sid, zh, ja, write=False)
    if pipeline.warn_key(w) not in {pipeline.warn_key(x) for x in zh["warnings"]}:
        save_reviewed(sid, zh, ja)   # 例如名詞表已更新，這則警告已不成立：不改譯文
        return {"ok": True, "text": cur, "how": "警告已不成立，譯文未變", "resolved": True, "warnings": zh["warnings"]}
    new = pipeline.rule_fix(w["type"], ja_text, cur)
    how = "規則"
    if new is None:
        s = load_settings()
        ready, desc = engine_ready(s)
        if not ready:
            return {"error": "這類警告需要翻譯引擎修改，但" + desc}
        tr = pipeline.Translator(s)
        new = tr.fix_line(ja_text, cur, sps, [w["type"] + "：" + w["msg"]])
        how = tr.label
    zh["lines"][str(i)] = {"text": new}
    save_reviewed(sid, zh, ja)
    still = pipeline.warn_key(w) in {pipeline.warn_key(x) for x in zh["warnings"]}
    return {"ok": True, "text": new, "how": how, "resolved": not still, "warnings": zh["warnings"]}


def story_status(sid):
    zh = os.path.join(pipeline.ZH_DIR, "%d.json" % sid)
    st = {"ja": os.path.exists(os.path.join(pipeline.JA_DIR, "%d.json" % sid)),
          "zh": os.path.exists(zh),
          "html": os.path.exists(os.path.join(OUTPUT, "data", "%d.js" % sid))}
    if st["zh"]:
        try:
            d = json.load(open(zh, encoding="utf-8"))
            st["title_zh"] = d.get("title", "")
            st["warnings"] = len(d.get("warnings", []))
        except Exception:  # noqa: BLE001
            pass
    return st


# ---------------------------------------------------------------- 工作
class Job:
    def __init__(self, kind, title):
        self.id = uuid.uuid4().hex[:10]
        self.kind, self.title = kind, title
        self.log_lines = []
        self.state = "running"
        self.progress = [0, 0]
        self.cancel = threading.Event()
        self.started = time.time()
        self.cost = 0.0

    def log(self, msg):
        line = time.strftime("%H:%M:%S ") + msg
        self.log_lines.append(line)
        print(line, flush=True)

    def info(self, since=0):
        return {"id": self.id, "kind": self.kind, "title": self.title, "state": self.state,
                "progress": self.progress, "log": self.log_lines[since:], "logLen": len(self.log_lines),
                "cost": round(self.cost, 3), "elapsed": int(time.time() - self.started)}


def start_job(kind, title, fn):
    job = Job(kind, title)
    with JOB_LOCK:
        JOBS[job.id] = job

    def run():
        try:
            fn(job)
            if job.state == "running":
                job.state = "done" if not job.cancel.is_set() else "cancelled"
            job.log("完成" if job.state == "done" else "已取消")
        except InterruptedError:
            job.state = "cancelled"
            job.log("已取消")
        except Exception as e:  # noqa: BLE001
            job.state = "error"
            job.log("錯誤：%s" % e)
            job.log(traceback.format_exc())
    threading.Thread(target=run, daemon=True).start()
    return job


def job_glossary(ids, refs):
    def fn(job):
        s = load_settings()
        tr = pipeline.Translator(s, log=job.log, cancel=job.cancel)
        job.log("翻譯引擎：" + tr.label)
        job.log("步驟 A：建立名詞轉換表（%d 話）" % len(ids))
        added = tr.build_glossary(ids, game(), refs)
        job.cost = tr.cost()
        job.log("名詞表新增 %d 筆。請在「名詞對照表」分頁檢查、修改後再開始翻譯。" % len(added))
    return fn


def translate_chapter_names(tr, ids, job):
    """這次翻譯到的話所屬章節，若章節名稱還沒有中文就一併翻譯（存到 work/chapters_zh.json）。"""
    want = set(ids)
    table = build_player.load_chapter_names()
    names = {t["src"]: t["tgt"] for t in pipeline.load_glossary()["terms"] if t.get("tgt")}
    todo = [c["name"] for c in chapters() if any(st["storyId"] in want for st in c["stories"])
            and not build_player.chapter_name_zh(c["name"], names, table)]
    for name in dict.fromkeys(todo):
        if job.cancel.is_set():
            return
        try:
            table[name] = tr.translate_name(name)
            build_player.save_chapter_names(table)
            job.log("章節名稱：%s → %s" % (name, table[name]))
        except Exception as e:  # noqa: BLE001
            job.log("章節名稱翻譯失敗 %s：%s" % (name, e))


def job_translate(ids, force, export):
    def fn(job):
        s = load_settings()
        tr = pipeline.Translator(s, log=job.log, cancel=job.cancel)
        job.log("翻譯引擎：%s（每批 %d 句，同時 %s 話）" % (tr.label, tr.batch_lines, s.get("workers")))
        job.progress = [0, len(ids)]
        g = pipeline.load_glossary()
        known = {t["src"] for t in g["terms"]}
        # 名詞表中缺少的說話者先補齊（translate-srt 步驟 3.6：翻譯前先鎖定譯名）
        missing = [c for c in pipeline.candidates(ids, game(), limit=100000) if c["kind"] == "說話者" and c["term"] not in known]
        if missing:
            job.log("名詞表缺少 %d 位說話者，先補齊譯名…" % len(missing))
            tr.build_glossary(ids, game(), "")
        workers = max(1, int(s.get("workers") or 3))
        sem = threading.Semaphore(workers)
        done_lock = threading.Lock()
        errors = []

        def one(sid):
            with sem:
                if job.cancel.is_set():
                    return
                try:
                    job.log("翻譯 %d「%s」" % (sid, story_title(sid)))
                    tr.translate_story(sid, story_title(sid), game(), force=force)
                except InterruptedError:
                    return
                except Exception as e:  # noqa: BLE001
                    errors.append(sid)
                    job.log("  [%d] 失敗：%s" % (sid, e))
                with done_lock:
                    job.progress[0] += 1
                    job.cost = tr.cost()
        ths = [threading.Thread(target=one, args=(sid,)) for sid in ids]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        translate_chapter_names(tr, ids, job)
        job.log("Token 用量：輸入 %(input)d、輸出 %(output)d、快取讀取 %(cache_read)d" % tr.usage)
        if tr.provider == "anthropic":
            job.log("本次費用估計 US$%.2f" % tr.cost())
        if errors:
            job.log("失敗的故事：%s（可重新執行）" % errors)
        if export and not job.cancel.is_set():
            do_export(job, ids)
    return fn


def do_export(job, ids):
    job.log("更新播放資料（%d 話）…" % len(ids))
    for sid in ids:
        pipeline.load_ja(sid, game())
    b = Builder(game(), log=job.log)
    miss = b.build(ids, chapters())
    if miss:
        job.log("缺少的素材：%s" % ", ".join(miss[:30]))
    job.log("已更新，可按「開啟播放器」觀看。")


def job_export(ids):
    def fn(job):
        do_export(job, ids)
    return fn


# ---------------------------------------------------------------- HTTP
class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def translate_path(self, path):
        p = unquote(urlparse(path).path)
        if p.startswith("/output/"):
            return os.path.join(OUTPUT, p[len("/output/"):])
        if p == "/":
            p = "/index.html"
        return os.path.join(WEB, p.lstrip("/"))

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _bytes(self, data, ctype):
        """回傳記憶體裡的資料；支援 Range（Safari 播放聲音與影片需要）。"""
        total = len(data)
        start, end, code = 0, total - 1, 200
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].partition("-")
            try:
                if a:
                    start, end = int(a), (int(b) if b else total - 1)
                else:
                    start = max(0, total - int(b))
                end = min(end, total - 1)
                code = 206
            except ValueError:
                pass
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % total)
                self.end_headers()
                return
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, total))
        self.end_headers()
        if self.command != "HEAD":
            mv = memoryview(data)
            for k in range(start, end + 1, 1 << 20):     # 分段寫出，大影片不另外複製
                try:
                    self.wfile.write(mv[k:min(k + (1 << 20), end + 1)])
                except (BrokenPipeError, ConnectionResetError):
                    return

    def _media(self, rel):
        """output/assets/<bg|chara|bgm|se|movie>/… 硬碟上沒有時：從遊戲檔即時解碼（只放記憶體）。"""
        p = MediaCache.parse(rel)
        if not p:
            return False
        if os.path.exists(os.path.join(OUTPUT, "assets", rel)):
            return False          # 用 --files 匯出過的素材照常由檔案提供
        if p[0] == "movie":
            view = MEDIA.movie_view(p[1])
            data, ctype = view, "video/mp4"
        else:
            data, ctype = MEDIA.get(rel)
        if data is None:
            self.send_error(404, "asset not found")
            return True
        self._bytes(data, ctype)
        return True

    def do_HEAD(self):
        u = urlparse(self.path)
        p = unquote(u.path)
        if p.startswith("/output/assets/") and self._media(p[len("/output/assets/"):]):
            return
        super().do_HEAD()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            p = unquote(u.path)
            if p.startswith("/output/assets/") and self._media(p[len("/output/assets/"):]):
                return
            if p.startswith("/output/data/"):
                name = p[len("/output/data/"):]
                if name == "catalog.js":
                    ensure_catalog()
                elif name.endswith(".js") and name[:-3].isdigit():
                    # 點選故事：建立（或更新）劇本資料，並在背景把這一話的素材解碼進記憶體
                    MEDIA.preload(story_assets(ensure_story(int(name[:-3]))))
            if u.path == "/api/media":
                return self._json(MEDIA.stats())
            if u.path == "/api/chapters":
                out = []
                table = build_player.load_chapter_names()
                names = {t["src"]: t["tgt"] for t in pipeline.load_glossary()["terms"] if t.get("tgt")}
                for c in chapters():
                    out.append({**c, "name_zh": build_player.chapter_name_zh(c["name"], names, table),
                                "stories": [{**s, **story_status(s["storyId"])} for s in c["stories"]]})
                return self._json(out)
            if u.path == "/api/settings":
                s = load_settings()
                ready, desc = engine_ready(s)
                k = s.get("api_key", "")
                s["api_key_set"] = bool(k) or bool(os.environ.get("ANTHROPIC_API_KEY"))
                s["api_key_hint"] = ("…" + k[-4:]) if k else ("（使用環境變數）" if os.environ.get("ANTHROPIC_API_KEY") else "")
                for key in ("ollama_key", "vllm_key"):
                    s[key + "_set"] = bool(s.get(key))
                for key in SECRET_KEYS:
                    s.pop(key, None)
                s["engine_ready"], s["engine_desc"] = ready, desc
                s["models"] = pipeline.MODELS
                s["providers"] = backends.PROVIDERS
                return self._json(s)
            if u.path == "/api/models":
                # 列出遠端服務上的模型（可傳入尚未儲存的網址與金鑰做「測試連線」）
                prov = q.get("provider", ["anthropic"])[0]
                s = load_settings()
                if "url" in q:
                    s[prov + "_url"] = q["url"][0]
                if q.get("key", [""])[0]:
                    s[prov + "_key"] = q["key"][0]
                try:
                    t0 = time.time()
                    ms = backends.make_backend(s, provider=prov, model="").list_models()
                    return self._json({"ok": True, "models": ms, "ms": int((time.time() - t0) * 1000)})
                except Exception as e:  # noqa: BLE001
                    return self._json({"ok": False, "error": str(e), "models": []})
            if u.path == "/api/glossary":
                return self._json(pipeline.load_glossary())
            if u.path == "/api/story":
                sid = int(q["id"][0])
                ja = pipeline.load_ja(sid, game())
                zp = os.path.join(pipeline.ZH_DIR, "%d.json" % sid)
                zh = json.load(open(zp, encoding="utf-8")) if os.path.exists(zp) else None
                if zh:   # 名詞表可能已更新：開啟校對時重新驗證，有變動就寫回
                    old = zh.get("warnings", [])
                    save_reviewed(sid, zh, ja, write=False)
                    if zh["warnings"] != old:
                        save_reviewed(sid, zh, ja)
                lines = [{"i": i, "sp": sps, "ja": t, "zh": (zh or {}).get("lines", {}).get(str(i), {}).get("text", "")}
                         for i, sps, t in pipeline.story_lines(ja)]
                return self._json({"id": sid, "title": story_title(sid), "title_zh": (zh or {}).get("title", ""),
                                   "lines": lines, "warnings": (zh or {}).get("warnings", []),
                                   "model": (zh or {}).get("model", "")})
            if u.path == "/api/estimate":
                ids = [int(x) for x in q.get("ids", [""])[0].split(",") if x]
                s = load_settings()
                e = pipeline.estimate(ids, s.get("model"), game(), s.get("provider", "anthropic"))
                e["provider"] = s.get("provider", "anthropic")
                e["engine"] = engine_ready(s)[1]
                return self._json(e)
            if u.path.startswith("/api/jobs"):
                parts = u.path.split("/")
                if len(parts) == 4:
                    j = JOBS.get(parts[3])
                    return self._json(j.info(int(q.get("since", ["0"])[0])) if j else {"error": "no job"})
                return self._json([j.info(len(j.log_lines)) for j in JOBS.values()])
            return super().do_GET()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            b = self._body()
            if u.path == "/api/settings":
                save_settings(b)
                STATE["game"] = None
                MEDIA.clear()
                MEDIA.limit = int(load_settings().get("media_cache_mb") or 512) * 1048576
                return self._json({"ok": True})
            if u.path == "/api/glossary":
                g = pipeline.load_glossary()
                g["terms"] = b["terms"]
                pipeline.save_glossary(g)
                return self._json({"ok": True, "count": len(g["terms"])})
            if u.path == "/api/story":
                sid = int(b["id"])
                zp = os.path.join(pipeline.ZH_DIR, "%d.json" % sid)
                zh = json.load(open(zp, encoding="utf-8")) if os.path.exists(zp) else \
                    {"id": sid, "title": "", "lines": {}, "warnings": [], "model": "manual"}
                if "title_zh" in b and b["title_zh"] != zh.get("title"):
                    zh["title"] = b["title_zh"]
                    try:      # 目錄上的標題要跟著更新：下次開啟目錄時重建
                        os.remove(os.path.join(OUTPUT, "data", "catalog.js"))
                    except OSError:
                        pass
                for i, t in (b.get("lines") or {}).items():
                    zh["lines"][str(i)] = {"text": pipeline.fix_text("", t)}
                save_reviewed(sid, zh)
                return self._json({"ok": True, "warnings": zh["warnings"]})
            if u.path == "/api/warning":
                return self._json(handle_warning(b))
            ids = [int(x) for x in b.get("ids", [])]
            if u.path in ("/api/translate", "/api/glossary/build"):
                ready, desc = engine_ready(load_settings())
                if not ready:
                    return self._json({"error": "翻譯引擎尚未設定完成：" + desc}, 400)
            if u.path == "/api/glossary/build":
                j = start_job("glossary", "建立名詞表", job_glossary(ids, b.get("refs", "")))
                return self._json(j.info())
            if u.path == "/api/translate":
                j = start_job("translate", "翻譯 %d 話" % len(ids),
                              job_translate(ids, bool(b.get("force")), bool(b.get("export", True))))
                return self._json(j.info())
            if u.path == "/api/export":
                j = start_job("export", "更新播放資料 %d 話" % len(ids), job_export(ids))
                return self._json(j.info())
            if u.path.startswith("/api/jobs/") and u.path.endswith("/cancel"):
                j = JOBS.get(u.path.split("/")[3])
                if j:
                    j.cancel.set()
                return self._json({"ok": True})
            return self._json({"error": "unknown"}, 404)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    install_frontend()
    threading.Thread(target=ensure_ui_assets, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = "http://localhost:%d/" % a.port
    print("梅露可物語 故事翻譯器 已啟動：" + url, flush=True)
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
