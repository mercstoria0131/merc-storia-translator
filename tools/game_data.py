"""遊戲資料存取層：讀取 Steam 版（或 Android 版解出的）AssetBundle，
提供章節清單、劇本解碼、素材匯出（背景 / 立繪 / BGM / 音效 / UI）。

依賴：UnityPy、pycryptodome、Pillow、soundfile（皆在 ~/.venvs/merc-story）。
"""
import glob
import io
import json
import os
import threading

import fmod_guard  # noqa: F401  必須在 UnityPy 之前（Windows ARM64 載不到 FMOD 時，圖片仍可解碼）
import UnityPy

import merc_format as mf
from addressables_catalog import Catalog

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

GAME_FOLDER = "メルクストーリア - 癒術士と心の旋律 -"
BUNDLE_ROOT = "jp_co_happyelements/メルストM/AssetBundle"      # 底下是 <Unity 版本>/StandaloneWindows64
EMBED_SUB = "メルストM_Data/StreamingAssets/aa"


def _steam_libraries():
    """常見的 Steam 資料庫位置（Windows 會再讀 libraryfolders.vdf 找其他磁碟上的資料庫）。"""
    import re
    libs = []
    for steam in [os.path.expandvars(r"%ProgramFiles(x86)%\Steam"), os.path.expandvars(r"%ProgramFiles%\Steam"),
                  r"C:\Program Files (x86)\Steam", os.path.expanduser("~/Library/Application Support/Steam"),
                  os.path.expanduser("~/.steam/steam"), os.path.expanduser("~/.local/share/Steam")]:
        if not os.path.isdir(steam):
            continue
        libs.append(steam)
        vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
        if os.path.exists(vdf):
            for m in re.finditer(r'"path"\s+"([^"]+)"', open(vdf, encoding="utf-8", errors="replace").read()):
                libs.append(m.group(1).replace("\\\\", "\\"))
    for d in "DEFGH":   # 其他磁碟上自建的 SteamLibrary
        libs.append(d + r":\SteamLibrary")
    libs += glob.glob("/Volumes/*/SteamLibrary")   # macOS：外接磁碟或與 Parallels 共用的資料庫
    return libs


def find_game_dir():
    """自動尋找遊戲安裝位置；找不到時回傳空字串（請在「設定」分頁手動指定）。"""
    for lib in _steam_libraries():
        d = os.path.join(lib, "steamapps", "common", GAME_FOLDER)
        if os.path.isdir(os.path.join(d, BUNDLE_ROOT)):
            return d
    return ""


DEFAULT_GAME_DIR = find_game_dir()


def bundle_dir(game_dir):
    """AssetBundle 目錄；Unity 版本號會隨遊戲更新改變，取現有的最新一個。"""
    cands = sorted(glob.glob(os.path.join(game_dir, BUNDLE_ROOT, "*", "StandaloneWindows64")))
    return cands[-1] if cands else os.path.join(game_dir, BUNDLE_ROOT, "StandaloneWindows64")

# 立繪圖集中各表情的格位（1024x1024 圖集；身體 0,0,700,788；臉 150x150）
FACE_SLOTS = {}
_right = ["喜", "喜d", "怒", "怒d", "哀", "哀d", "驚", "驚d", "呆", "照", "眠", "真剣"]
for _i, _name in enumerate(_right):
    FACE_SLOTS[_name] = (702 + 153 * (_i % 2), 153 * (_i // 2))
for _i, _name in enumerate(["固有1", "固有2", "固有3", "固有4"]):
    FACE_SLOTS[_name] = (153 * _i, 790)


class GameData:
    def __init__(self, game_dir=None):
        self.game_dir = game_dir or DEFAULT_GAME_DIR
        if not self.game_dir:
            raise FileNotFoundError("找不到遊戲安裝位置，請在「設定」分頁指定 Steam 版遊戲資料夾。")
        self.bdir = bundle_dir(self.game_dir)
        self.edir = os.path.join(self.game_dir, EMBED_SUB)
        if not os.path.isdir(self.bdir):
            raise FileNotFoundError("找不到遊戲 AssetBundle 目錄：" + self.bdir)
        self._cat = {}
        self._lock = threading.Lock()
        self._master = None

    # ------------------------------------------------------------ catalog
    def catalog(self, category):
        with self._lock:
            if category not in self._cat:
                if category == "_embedded":
                    path = os.path.join(self.edir, "catalog.bin")
                else:
                    path = glob.glob(os.path.join(self.bdir, category, "catalog_*.bin"))[0]
                self._cat[category] = Catalog(path).asset_to_bundle()
            return self._cat[category]

    def catalog_deps(self, category):
        """{asset key: [bundle 檔名（自身＋依賴）]}"""
        with self._lock:
            k = "deps:" + category
            if k not in self._cat:
                path = glob.glob(os.path.join(self.bdir, category, "catalog_*.bin"))[0]
                self._cat[k] = Catalog(path).asset_deps()
            return self._cat[k]

    def bundle_path(self, category, key):
        m = self.catalog(category)
        if key not in m or not m[key][1]:
            return None
        if category == "_embedded":
            return os.path.join(self.edir, "StandaloneWindows64", m[key][1])
        return os.path.join(self.bdir, category, m[key][1])

    def has(self, category, key):
        return key in self.catalog(category)

    def _textasset(self, category, key):
        env = UnityPy.load(self.bundle_path(category, key))
        for o in env.objects:
            if o.type.name == "TextAsset":
                return mf.decrypt(mf.textasset_bytes(o)[1])
        raise KeyError(key)

    # ------------------------------------------------------------ master
    def master(self):
        if self._master is None:
            base = "Assets/ExternalResources/MasterData/"
            self._master = {
                "chapters": mf.parse_chapter_master(self._textasset("MasterData", base + "ChapterMasterData.bytes")),
                "stories": mf.parse_story_master(self._textasset("MasterData", base + "StoryMasterData.bytes")),
            }
            pos = mf.Reader(self._textasset(
                "StoryMasterData", "Assets/ExternalResources/StoryMasterData/ActressPositionYamlData.bytes"))
            assert pos.u8() == 1
            table = {}
            for _ in range(pos.i32()):
                k = pos.i32()
                assert pos.u8() == 4
                table[k] = {"id": pos.i32(), "body": pos.vec3(), "face": pos.vec3(), "emotion": pos.vec3()}
            self._master["actressPosition"] = table
        return self._master

    def chapter_list(self):
        """回傳 [{id,name,part,order,stories:[{storyId,title,subTitle}]}]，依遊戲內順序。"""
        m = self.master()
        by = {}
        for s in m["stories"]:
            by.setdefault(s["chapterId"], []).append(s)
        out = []
        for c in m["chapters"]:
            name = c["name"]
            if "第二部" in name:
                part = "メインストーリー 第二部"
            elif "第一部オリジン" in name:
                part = "メインストーリー 第一部オリジン"
            elif "第一部" in name:
                part = "メインストーリー 第一部"
            elif c["type"] == 4:
                part = "イベントストーリー"
            else:
                part = "その他"
            sts = sorted(by.get(c["id"], []), key=lambda s: (s["order"], s["storyId"]))
            out.append({"id": c["id"], "name": name, "part": part, "order": c["order"],
                        "stories": [{"storyId": s["storyId"], "title": s["title"], "subTitle": s["subTitle"]}
                                    for s in sts
                                    if self.has("StoryMasterData", self.story_key(s["storyId"]))]})
        out.sort(key=lambda c: (c["part"], c["order"]))
        return out

    @staticmethod
    def story_key(story_id):
        return "Assets/ExternalResources/StoryMasterData/StoryYamlData_%s.bytes" % story_id

    def story(self, story_id):
        """一話的完整劇本。StoryMasterData 的 children（例如 2111 → ["2111_3"]）是同一話的後續段落，
        原版在本體播完後依序接著播（中間缺號的段落是戰鬥，沒有劇本），所以這裡把場景接在後面。"""
        d = mf.parse_story(self._textasset("StoryMasterData", self.story_key(story_id)))
        rec = next((s for s in self.master()["stories"] if s["storyId"] == int(story_id)), None)
        parts = [{"key": str(story_id), "start": 0}]
        for child in (rec or {}).get("children") or []:
            c = mf.parse_story(self._textasset("StoryMasterData", self.story_key(child)))
            parts.append({"key": child, "start": len(d["scenes"])})
            d["scenes"].extend(c["scenes"])
        if len(parts) > 1:
            d["parts"] = parts
        # Timeline 演出（assetType == "Timeline"）：附上名稱與裡面的台詞，供翻譯與播放器使用
        # 同一個演出可能跨多個場景（例如 4283）：台詞只放在第一個引用它的場景，避免重複翻譯
        seen = set()
        for sc in d["scenes"]:
            names = [a["assetName"] for a in (sc.get("assetParams") or [])
                     if a.get("assetType") == "Timeline" and a.get("assetName")]
            if names:
                from timeline import timeline_texts
                sc["timelines"] = []
                for n in names:
                    short = n[len("StoryTimeline_"):] if n.startswith("StoryTimeline_") else n
                    texts = []
                    if short not in seen:
                        seen.add(short)
                        try:
                            texts = timeline_texts(self, short)
                        except KeyError:
                            texts = []
                    sc["timelines"].append({"name": short, "texts": texts})
        # SpriteStudio 特效裡畫成圖片的台詞（tools/spritestudio_texts.json 列出的特效）：
        # 附上動畫名稱與這個動畫顯示的台詞；同一句只放在第一個顯示它的場景
        import spritestudio
        seen = set()
        for sc in d["scenes"]:
            for a in sc.get("assetParams") or []:
                n = a.get("assetName")
                if a.get("assetType") != "EffectSpriteStudio" or not spritestudio.supported(n, a.get("animationName")):
                    continue
                try:
                    texts = spritestudio.effect(self, n).anim_texts(a.get("animationName"))
                except (KeyError, OSError):
                    continue
                new = [t for t in texts if (n, t) not in seen]
                seen.update((n, t) for t in new)
                sc.setdefault("ssfx", []).append({
                    "name": n, "anim": a.get("animationName"), "texts": new,
                    "pos": (a.get("position") or [0, 0])[:2], "scale": (a.get("scale") or [1, 1])[:2],
                    "times": a.get("playTimes", 1), "delay": a.get("delay", 0)})
        return d

    # ------------------------------------------------------------ assets
    def _first(self, category, key, typ):
        path = self.bundle_path(category, key)
        if not path:
            return None
        env = UnityPy.load(path)
        for o in env.objects:
            if o.type.name == typ:
                return o.read()
        return None

    def background_key(self, bg, timezone):
        """背景鍵值：<id>_<時段>，缺少時改用其他時段；still_ / gt 系列直接使用。"""
        base = "Assets/ExternalResources/Story/StoryBackground/"
        cands = []
        if bg:
            if timezone:
                cands.append("%s_%d" % (bg, timezone))
            cands.append(bg)
            cands += ["%s_%d" % (bg, t) for t in (2, 1, 3, 4)]
        for c in cands:
            if self.has("Story", base + c + ".png"):
                return c
        return None

    def export_background(self, name, out_path):
        tex = self._first("Story", "Assets/ExternalResources/Story/StoryBackground/%s.png" % name, "Texture2D")
        if tex is None:
            return False
        tex.image.convert("RGB").save(out_path, "JPEG", quality=88, optimize=True)
        return True

    def export_actress(self, texture_id, out_path):
        tex = self._first("Story", "Assets/ExternalResources/Story/StoryActress/%d.png" % texture_id, "Texture2D")
        if tex is None:
            return None
        img = tex.image.convert("RGBA")
        img.save(out_path, "WEBP", quality=90, method=4)
        return img.size

    def export_unit_actress(self, unit_id, out_path):
        """Scout 類角色（可招募的夥伴）：故事裡用的是 Unit/LargeNoStage（無背景全身圖 800×900），
        畫在 ScoutActress 的 700×788 身體框（ScoutActress..cctor 的 Vector2(700, 788)）。"""
        tex = self._first("Unit", "Assets/ExternalResources/Unit/LargeNoStage/%d.png" % unit_id, "Texture2D")
        if tex is None:
            return None
        from PIL import Image
        img = tex.image.convert("RGBA").resize((700, 788), Image.LANCZOS)
        img.save(out_path, "WEBP", quality=90, method=4)
        return img.size

    def export_sound(self, name, out_path):
        clip = self._first("Sound", "Assets/ExternalResources/Sound/%s.mp3" % name, "AudioClip")
        if clip is None:
            return False
        import soundfile as sf
        for _n, wav in clip.samples.items():
            data, sr = sf.read(io.BytesIO(wav))
            sf.write(out_path, data, sr, format="MP3", subtype="MPEG_LAYER_III", compression_level=0.6)
            return True
        return False

    def export_movie(self, name, out_path):
        """劇本 Movie 效果的影片：Common 類別的 VideoClip，原始 MP4 原封不動存在 bundle 附帶的 .resource 檔。"""
        path = self.bundle_path("Common", "Assets/ExternalResources/Common/Movie/%s" % name)
        if not path or not os.path.exists(path):
            return False
        env = UnityPy.load(path)
        for o in env.objects:
            if o.type.name != "VideoClip":
                continue
            r = o.read().m_ExternalResources
            res = env.file.files[r.m_Source.split("/")[-1]]
            raw = res.bytes if hasattr(res, "bytes") else res.read()
            data = memoryview(raw)[r.m_Offset:r.m_Offset + r.m_Size]   # 不複製，大影片可達 250 MB
            if bytes(data[4:8]) != b"ftyp":
                raise ValueError("影片資料不是 MP4：" + name)
            tmp = out_path + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, out_path)
            return True
        return False

    def movie_view(self, name):
        """影片內容（MP4）的 memoryview，不複製資料；回傳 (view, env)，env 要一起保留才不會被回收。"""
        path = self.bundle_path("Common", "Assets/ExternalResources/Common/Movie/%s" % name)
        if not path or not os.path.exists(path):
            return None, None
        env = UnityPy.load(path)
        for o in env.objects:
            if o.type.name != "VideoClip":
                continue
            r = o.read().m_ExternalResources
            res = env.file.files[r.m_Source.split("/")[-1]]
            raw = res.bytes if hasattr(res, "bytes") else res.read()
            view = memoryview(raw)[r.m_Offset:r.m_Offset + r.m_Size]
            if bytes(view[4:8]) != b"ftyp":
                raise ValueError("影片資料不是 MP4：" + name)
            return view, env
        return None, None

    def export_story_ui(self, out_dir):
        """匯出 StoryUI 圖集中的 UI 精靈圖（訊息框、按鈕…）。"""
        os.makedirs(out_dir, exist_ok=True)
        key = "Assets/EmbeddedResources/Texture/Story/StoryUI/StoryUI.spriteatlasv2"
        env = UnityPy.load(self.bundle_path("_embedded", key))
        names = []
        for o in env.objects:
            if o.type.name == "Sprite":
                d = o.read()
                d.image.save(os.path.join(out_dir, d.m_Name + ".png"))
                names.append(d.m_Name)
        return names
