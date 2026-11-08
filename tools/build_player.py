"""把指定的故事匯出成可離線播放的 HTML 播放器資料。

output/
  index.html, player.html, player/   (前端，由 tools/player_src 複製)
  assets/bg/*.jpg  assets/chara/*.webp  assets/bgm/*.mp3  assets/se/*.mp3  assets/ui/*.png
  data/<storyId>.js                  (每話腳本，JS 包裝，file:// 也能載入)
  data/catalog.js                    (章節目錄)

用法：python build_player.py [--files] <storyId|chapter:<id>> ...
（預設只產生劇本資料，背景／立繪／聲音／影片由伺服器播放時即時解碼；加 --files 才把素材也匯出成檔案）
"""
import json
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from game_data import GameData  # noqa: E402
import merc_format as mf  # noqa: E402

OUT = os.path.join(ROOT, "output")
JA_DIR = os.path.join(ROOT, "data", "ja")
ZH_DIR = os.path.join(ROOT, "work", "zh")
GLOSSARY = os.path.join(ROOT, "work", "glossary.json")

# 立繪圖集格位（由 GameAssembly.dll 內 CharacterActressConstant 反組譯得到）
ACTRESS_LAYOUT = {
    "Default": {"body": [2, 2, 700, 788], "face": 150, "slots": {}},
    "Navi": {"body": [2, 2, 400, 500], "face": 200, "slots": {}},
}
_order = ["喜", "喜d", "怒", "怒d", "哀", "哀d", "驚", "驚d", "呆", "照", "眠", "真剣"]
for i, e in enumerate(_order):
    ACTRESS_LAYOUT["Default"]["slots"][e] = [702 + 153 * (i % 2), 153 * (i // 2)]
for i, e in enumerate(["固有1", "固有2", "固有3", "固有4"]):
    ACTRESS_LAYOUT["Default"]["slots"][e] = [153 * i, 790]
_navi = {"喜": (0, 0), "喜d": (1, 0), "怒": (0, 1), "怒d": (1, 1), "哀": (0, 2), "哀d": (1, 2),
         "驚": (0, 3), "驚d": (1, 3), "呆": (2, 0), "照": (2, 1), "眠": (2, 2), "真剣": (2, 3)}
for e, (c, r) in _navi.items():
    ACTRESS_LAYOUT["Navi"]["slots"][e] = [415 + 203 * c, 2 + 203 * r]
for i, e in enumerate(["固有1", "固有2", "固有3", "固有4"]):
    ACTRESS_LAYOUT["Navi"]["slots"][e] = [2 + 203 * (i % 2), 618 + 203 * (i // 2)]
# Scout：整張 Unit/LargeNoStage 圖當身體（匯出時已縮成 700×788），沒有表情格
ACTRESS_LAYOUT["Scout"] = {"body": [0, 0, 700, 788], "face": 150, "slots": {}}


class Builder:
    def __init__(self, game=None, log=print, media="memory"):
        """media="memory"（預設）：背景、立繪、BGM、音效、影片不匯出到硬碟，只在劇本資料記下需要哪些素材（pre），
        播放時由伺服器即時解碼（translator/media.py）；media="files"：照舊全部匯出成檔案（可離線複製到別處）。"""
        self.g = game or GameData()
        self.log = log
        self.media = media
        self.lock = threading.Lock()
        self.done = set()
        for d in (("bg", "chara", "bgm", "se", "ui", "movie") if media == "files" else ("ui",)):
            os.makedirs(os.path.join(OUT, "assets", d), exist_ok=True)
        os.makedirs(os.path.join(OUT, "data"), exist_ok=True)
        self.missing = []

    # ---------------------------------------------------------- frontend
    def install_frontend(self):
        src = os.path.join(HERE, "player_src")
        for name in os.listdir(src):
            s = os.path.join(src, name)
            d = os.path.join(OUT, name)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            elif not name.startswith("._"):
                shutil.copy2(s, d)
        ui = os.path.join(OUT, "assets", "ui")
        if not os.path.exists(os.path.join(ui, "bg_message_fs.png")):
            self.g.export_story_ui(ui)
        with open(os.path.join(OUT, "data", "layout.js"), "w", encoding="utf-8") as f:
            f.write("window.ACTRESS_LAYOUT=" + json.dumps(ACTRESS_LAYOUT, ensure_ascii=False) + ";\n")

    # ---------------------------------------------------------- assets
    def _once(self, key, fn):
        with self.lock:
            if key in self.done:
                return
            self.done.add(key)
        try:
            if not fn():
                self.missing.append(key)
        except Exception as e:  # noqa: BLE001
            self.missing.append(key)
            self.log("  素材匯出失敗 %s: %s" % (key, e))

    def asset_bg(self, name):
        p = os.path.join(OUT, "assets", "bg", name + ".jpg")
        if not os.path.exists(p):
            self._once("bg:" + name, lambda: self.g.export_background(name, p))

    def asset_unit(self, uid):
        p = os.path.join(OUT, "assets", "chara", "u%d.webp" % uid)
        if not os.path.exists(p):
            self._once("unit:%d" % uid, lambda: self.g.export_unit_actress(uid, p))

    def asset_chara(self, tid):
        p = os.path.join(OUT, "assets", "chara", "%d.webp" % tid)
        if not os.path.exists(p):
            self._once("chara:%d" % tid, lambda: self.g.export_actress(tid, p))

    def asset_movie(self, name):
        p = os.path.join(OUT, "assets", "movie", name)
        if not os.path.exists(p):
            self.log("  解出影片 %s（檔案較大，需要一點時間）" % name)
            self._once("movie:" + name, lambda: self.g.export_movie(name, p))

    def asset_timeline(self, name):
        """Timeline 演出（tools/timeline.py）→ assets/timeline/<名稱>/tl.js；回傳需要的音效與 BGM。"""
        d = os.path.join(OUT, "assets", "timeline", name)
        meta = os.path.join(d, "meta.json")
        if not os.path.exists(os.path.join(d, "tl.js")) or not os.path.exists(meta):
            def run():
                from timeline import export_timeline
                self.log("  匯出 Timeline 演出 %s" % name)
                _data, warn = export_timeline(self.g, name, d)
                return True
            self._once("timeline:" + name, run)
        try:
            with open(meta, encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            return {"se": [], "bgm": []}

    def asset_ss(self, name):
        """SpriteStudio 特效（tools/spritestudio.py）→ assets/ss/<名稱>/ss.js＋tex0.png。"""
        d = os.path.join(OUT, "assets", "ss", name)
        js = os.path.join(d, "ss.js")
        # 抄錄的文字或匯出器更新過就重新匯出
        src = max(os.path.getmtime(os.path.join(HERE, f)) for f in ("spritestudio.py", "spritestudio_texts.json"))
        if not os.path.exists(js) or os.path.getmtime(js) < src:
            def run():
                from spritestudio import export_effect
                self.log("  匯出 SpriteStudio 特效 %s" % name)
                _data, warn = export_effect(self.g, name, d)
                for w in warn:
                    self.log("    ! " + w)
                return True
            self._once("ss:" + name, run)

    def asset_sound(self, kind, name):
        p = os.path.join(OUT, "assets", kind, name + ".mp3")
        if not os.path.exists(p):
            self._once(kind + ":" + name, lambda: self.g.export_sound(name, p))

    # ---------------------------------------------------------- story
    def compile_story(self, sid, meta, zh, glossary_names):
        ja = json.load(open(os.path.join(JA_DIR, "%d.json" % sid), encoding="utf-8"))
        pos_table = self.g.master()["actressPosition"]
        zh_lines = (zh or {}).get("lines", {})
        need_bg, need_tex, need_bgm, need_se, need_movie, need_unit = set(), set(), set(), set(), set(), set()
        need_tl = set()
        tl_tr = {}       # 演出名稱 → 全部譯文（跨場景共用）
        need_ss, ss_tr = set(), {}   # SpriteStudio 特效名稱；特效名稱 → 圖上台詞的譯文
        scenes = []
        for i, s in enumerate(ja["scenes"]):
            bg = self.g.background_key(s["background"], s["timezone"])
            if bg:
                need_bg.add(bg)
            # sp：名字牌顯示的名字（displayName）；spk：原本的角色 key，用來決定名字牌靠哪個角色
            sc = {"bg": bg, "text": s["text"] or "", "sp": mf.scene_speakers(s), "spk": s["speakers"] or [],
                  "win": s["windowType"], "size": s["textSize"], "fx": s["effect"]}
            z = zh_lines.get(str(i))
            if z:
                sc["text_zh"] = z.get("text", "")
            sc["sp_zh"] = [glossary_names.get(x, x) for x in sc["sp"]]
            if s["bgm"]:
                b = s["bgm"]
                sc["bgm"] = {"name": b["name"], "mute": b["mute"], "fi": b["fadeIn"], "fo": b["fadeOut"]}
                if b["name"]:
                    need_bgm.add(b["name"])
            ses = []
            for x in s["soundEffects"] or []:
                if x["name"]:
                    need_se.add(x["name"])
                ses.append({"name": x["name"], "type": x["type"], "n": x["playTimes"],
                            "iv": x["interval"], "delay": x["delay"], "fi": x["fadeIn"], "fo": x["fadeOut"]})
            if ses:
                sc["se"] = ses
            ep = s["effectParam"] or {}
            if s["effect"] != "None":
                sc["ep"] = {k: ep.get(k) for k in ("fadeOut", "fadeIn", "fadeWait", "color", "duration",
                                                   "delay", "move", "scrollStartDelay", "movieAssetName")}
            if s["effect"] == "Heal":
                need_ss.add("effect_iyashi")      # 癒術效果（StoryHealEffectView）播放的 SpriteStudio 特效
            if s["effect"] == "Movie" and ep.get("movieAssetName"):
                need_movie.add(ep["movieAssetName"])
            if s["textAnimation"]:
                sc["ta"] = s["textAnimation"]
            if s["forceShowAllText"]:
                sc["all"] = True
            # 特效名稱（assetsKeys 與 assetParams 一一對應）：由 SpriteStudio 播放器重現的特效不再用 CSS 近似
            ak, ap = s["assetsKeys"] or [], s.get("assetParams") or []
            if len(ak) == len(ap):
                import spritestudio
                ak = [k for k, a in zip(ak, ap)
                      if not (a.get("assetType") == "EffectSpriteStudio"
                              and spritestudio.supported(a.get("assetName"), a.get("animationName")))]
            keys = [k for k in ak if k]
            if keys:
                sc["fxa"] = keys
            chars = {}
            for slot in ("left", "center", "right"):
                c = s[slot]
                if not c or not c["textureId"]:
                    continue
                scout = c["type"] == "Scout"      # 夥伴角色：圖在 Unit 類別，不是 Story/StoryActress
                # 劇本資料偶有標成 Default、但 StoryActress 沒有這張圖、只有同編號夥伴全身圖的情形
                # （3303 的シユエン，textureId 1661；同一角色在 2581 標為 Scout）：改用夥伴全身圖
                if not scout and not self.g.has("Story", "Assets/ExternalResources/Story/StoryActress/%d.png" % c["textureId"]) \
                        and self.g.has("Unit", "Assets/ExternalResources/Unit/LargeNoStage/%d.png" % c["textureId"]):
                    scout = True
                if scout:
                    need_unit.add(c["textureId"])
                else:
                    need_tex.add(c["textureId"])
                if c["faceTextureId"] and not scout:
                    need_tex.add(c["faceTextureId"])
                # 臉的位置表只對應 StoryActress；Scout 沒有表情格，感情圖示放在頭部附近
                fp = [290, 170, 0] if scout else pos_table.get(c["textureId"], {}).get("face", [0, 0, 0])
                ap = c["appearance"] or {}
                chars[slot] = {"tex": "u%d" % c["textureId"] if scout else c["textureId"],
                               "ftex": 0 if scout else (c["faceTextureId"] or 0),
                               "type": c["type"], "key": c["key"], "expr": c["expression"],
                               "emo": c["emotion"], "on": c["active"], "fp": fp[:2],
                               "off": c["offset"][:2], "sc": c["scale"][:2],
                               "ap": ap.get("type") if ap.get("type") not in (None, "None") else None}
            sc["ch"] = chars
            # Timeline 演出：名稱＋台詞對照（原文 → 譯文），譯文索引見 pipeline.timeline_line_index
            n = 0
            if s.get("timelines"):
                tls = []
                for tl in s["timelines"]:
                    tr = {}
                    for txt in tl.get("texts") or []:
                        z = zh_lines.get(str(10000 * (i + 1) + n))
                        if z and z.get("text"):
                            tr[txt] = z["text"]
                        n += 1
                    tls.append({"name": tl["name"], "tr": tr})
                    tl_tr.setdefault(tl["name"], {}).update(tr)
                    need_tl.add(tl["name"])
                if tls:
                    sc["tl"] = tls
                    if s.get("waitTarget"):
                        sc["wt"] = True     # 這個場景要開始／繼續播放演出（其他引用同一演出的場景只顯示訊息框）
            # SpriteStudio 特效（畫成圖片的台詞）：譯文索引接在 Timeline 台詞之後
            if s.get("ssfx"):
                lst = []
                for e in s["ssfx"]:
                    tr = {}
                    for txt in e.get("texts") or []:
                        z = zh_lines.get(str(10000 * (i + 1) + n))
                        if z and z.get("text"):
                            tr[txt] = z["text"]
                        n += 1
                    ss_tr.setdefault(e["name"], {}).update(tr)
                    need_ss.add(e["name"])
                    # 深度（position.z）：大於 0 畫在角色後面（例如ぱっぱかぱん的放射光），其餘畫在角色前面（集中線等）
                    z = next(((a.get("position") or [0, 0, 0])[2] for a in s.get("assetParams") or []
                              if a.get("assetName") == e["name"]), 0)
                    lst.append({"name": e["name"], "anim": e["anim"], "pos": e["pos"], "sc": e["scale"],
                                "n": e["times"], "delay": e["delay"], "z": z})
                sc["ss"] = lst
            scenes.append(sc)
        if self.media == "files":
            for b in need_bg:
                self.asset_bg(b)
            for t in need_tex:
                self.asset_chara(t)
            for u in need_unit:
                self.asset_unit(u)
        for sc in scenes:
            for t in sc.get("tl") or []:
                t["tr"] = tl_tr.get(t["name"], {})
        for name in need_ss:
            self.asset_ss(name)
        for name in need_tl:
            meta_tl = self.asset_timeline(name)
            need_se.update(meta_tl.get("se", []))
            need_bgm.update(meta_tl.get("bgm", []))
        if self.media == "files":
            for b in need_bgm:
                self.asset_sound("bgm", b)
            for x in need_se:
                self.asset_sound("se", x)
            for m in need_movie:
                self.asset_movie(m)
        # 這一話用到的素材（相對於 output/assets/）：點選故事時伺服器據此先解碼進記憶體
        pre = (["bg/%s.jpg" % b for b in sorted(need_bg)] + ["chara/%d.webp" % t for t in sorted(need_tex)]
               + ["chara/u%d.webp" % u for u in sorted(need_unit)] + ["bgm/%s.mp3" % b for b in sorted(need_bgm)]
               + ["se/%s.mp3" % x for x in sorted(need_se)] + ["movie/%s" % m for m in sorted(need_movie)])
        doc = {"id": sid, "chapter": meta["chapter"], "title": meta["title"], "subTitle": meta["subTitle"],
               "title_zh": (zh or {}).get("title", ""), "prev": meta.get("prev"), "next": meta.get("next"),
               "translated": bool(zh), "scenes": scenes, "pre": pre}
        if ss_tr:
            doc["ss_tr"] = ss_tr     # 特效名稱 → {圖上的日文: 譯文}
        with open(os.path.join(OUT, "data", "%d.js" % sid), "w", encoding="utf-8") as f:
            f.write("MERC.load(" + json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + ");\n")

    def build(self, story_ids, chapters, workers=4, catalog_all=None):
        """story_ids: 要匯出的故事；chapters: GameData.chapter_list() 的結果（提供標題與上下話）。"""
        self.install_frontend()
        gl = load_glossary()
        names = {t["src"]: t["tgt"] for t in gl.get("terms", []) if t.get("tgt")}
        meta = {}
        for c in chapters:
            sts = c["stories"]
            for i, s in enumerate(sts):
                meta[s["storyId"]] = {"chapter": {"id": c["id"], "name": c["name"],
                                                  "name_zh": chapter_name_zh(c["name"], names)},
                                      "title": s["title"], "subTitle": s["subTitle"],
                                      "prev": sts[i - 1]["storyId"] if i > 0 else None,
                                      "next": sts[i + 1]["storyId"] if i + 1 < len(sts) else None}

        def one(sid):
            zh_path = os.path.join(ZH_DIR, "%d.json" % sid)
            zh = json.load(open(zh_path, encoding="utf-8")) if os.path.exists(zh_path) else None
            self.compile_story(sid, meta[sid], zh, names)
            self.log("  已匯出播放器資料 %d" % sid)

        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(one, story_ids))
        self.write_catalog(chapters, names, self.media == "memory" if catalog_all is None else catalog_all)
        return self.missing

    def write_catalog(self, chapters, names, all_stories=False):
        """all_stories：列出全部故事（劇本資料在第一次開啟時才建立）；否則只列已匯出的。"""
        cat = []
        for c in chapters:
            sts = [s for s in c["stories"]
                   if all_stories or os.path.exists(os.path.join(OUT, "data", "%d.js" % s["storyId"]))]
            if not sts:
                continue
            items = []
            for s in sts:
                zh_path = os.path.join(ZH_DIR, "%d.json" % s["storyId"])
                tz = ""
                if os.path.exists(zh_path):
                    tz = json.load(open(zh_path, encoding="utf-8")).get("title", "")
                items.append({"id": s["storyId"], "title": s["title"], "title_zh": tz, "sub": s["subTitle"]})
            cat.append({"id": c["id"], "part": c["part"], "name": c["name"],
                        "name_zh": chapter_name_zh(c["name"], names), "stories": items})
        with open(os.path.join(OUT, "data", "catalog.js"), "w", encoding="utf-8") as f:
            f.write("window.MERC_CATALOG=" + json.dumps(cat, ensure_ascii=False) + ";\n")


def load_glossary():
    if os.path.exists(GLOSSARY):
        return json.load(open(GLOSSARY, encoding="utf-8"))
    return {"src_lang": "ja", "tgt_lang": "zh", "terms": []}


_NUM = {"一": "一", "二": "二"}


CHAPTERS_ZH = os.path.join(ROOT, "work", "chapters_zh.json")
FIXED_CHAPTER_ZH = {"ユニットストーリー": "夥伴故事", "メモリアルストーリー": "回憶故事"}


def load_chapter_names():
    """活動故事等的章節名稱譯名 {日文: 中文}（work/chapters_zh.json，翻譯時自動補上，也可手動修改）。"""
    try:
        with open(CHAPTERS_ZH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_chapter_names(m):
    tmp = CHAPTERS_ZH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CHAPTERS_ZH)


def chapter_name_zh(name, names=None, table=None):
    """章節名稱的中文；還沒翻譯時回傳空字串。"""
    table = load_chapter_names() if table is None else table
    if name in table:
        return table[name]
    if name in FIXED_CHAPTER_ZH:
        return FIXED_CHAPTER_ZH[name]
    if name.startswith("メインストーリー"):
        return name.replace("メインストーリー", "主線故事").replace("第一部オリジン", "第一部 起源")
    return (names or {}).get(name, "")


def translate_chapter_name(name, names):
    return chapter_name_zh(name, names) or name


if __name__ == "__main__":
    g = GameData()
    chapters = g.chapter_list()
    ids = []
    media = "memory"
    for a in sys.argv[1:]:
        if a == "--files":       # 素材也匯出成檔案（可把 output/ 整包帶到沒有遊戲檔的地方離線看）
            media = "files"
        elif a.startswith("chapter:"):
            cid = int(a.split(":")[1])
            ids += [s["storyId"] for c in chapters if c["id"] == cid for s in c["stories"]]
        else:
            ids.append(int(a))
    b = Builder(g, media=media)
    miss = b.build(ids, chapters)
    print("missing:", miss)
