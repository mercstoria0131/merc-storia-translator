"""翻譯流程（依 translate-srt.md 的設計模式改寫成遊戲劇本版）：

  步驟 A  建立／更新「名詞轉換表」(work/glossary.json)：
          從所選章節擷取候選專有名詞（說話者、片假名詞、『』內詞、地名組織名），
          參考使用者提供的資料，由 Claude 決定唯一譯名；已鎖定或使用者指定的譯名最優先。
  步驟 B  分批翻譯：每話依序切成數批，每批附上前文脈絡與名詞表；
          新出現且重複的專有名詞會當下補進名詞表，後續批次沿用。
  步驟 C  驗證：禁用字（妳→你）、標籤保留（[RRGGBB] [-] <name>）、漏譯／空白、
          名詞表一致性警告；有缺漏的句子自動重譯一次。
  步驟 D  寫出 work/zh/<storyId>.json，再由 build_player 匯出 HTML 播放器。
"""
import json
import os
import re
import threading
import time
import urllib.request
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
ZH_DIR = os.path.join(WORK, "zh")
JA_DIR = os.path.join(ROOT, "data", "ja")
GLOSSARY_PATH = os.path.join(WORK, "glossary.json")
REFS_PATH = os.path.join(WORK, "refs.md")
os.makedirs(ZH_DIR, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from merc_format import scene_speakers  # noqa: E402
os.makedirs(JA_DIR, exist_ok=True)

MODELS = {
    "claude-opus-5-5": {"label": "Claude Opus 5.5（品質最佳，預設）", "in": 4.0, "out": 20.0},
    "claude-sonnet-5-5": {"label": "Claude Sonnet 5.5（較省）", "in": 2.0, "out": 10.0},
    "claude-haiku-5-5": {"label": "Claude Haiku 5.5（最便宜）", "in": 0.10, "out": 0.50},
}
DEFAULT_MODEL = "claude-opus-5-5"
BATCH_LINES = 60          # 每批句數（translate-srt 原為 40 條字幕；劇本單句較短）
CONTEXT_LINES = 8         # 每批附上的前文句數
FORBIDDEN = {"妳": "你"}   # 台灣用語規範：避免「妳」
# 自架模型（尤其 Qwen 系列）偶爾混入簡體字：只轉換「只會是簡體」的常用字，繁簡共用字（面、里、后、云、干…）不動
S2T = dict(zip("这们说为时么过还对会没样让见话应该个来东车长门问间关开头买卖觉经边动现实点从书写难认识请谁读听讲记钱进远连选适鸟马鱼龙飞风电气汉语爱处帮带当吗块乐亲热总虽觉错闻随须顾预领题颜饭饮馆鸡鸣黄",
                   "這們說為時麼過還對會沒樣讓見話應該個來東車長門問間關開頭買賣覺經邊動現實點從書寫難認識請誰讀聽講記錢進遠連選適鳥馬魚龍飛風電氣漢語愛處幫帶當嗎塊樂親熱總雖覺錯聞隨須顧預領題顏飯飲館雞鳴黃"))
TAG_RE = re.compile(r"\[[0-9A-Fa-f]{6}\]|\[-\]|<name>")

_glossary_lock = threading.Lock()


# ====================================================================== glossary
def load_glossary():
    if os.path.exists(GLOSSARY_PATH):
        with open(GLOSSARY_PATH, encoding="utf-8") as f:
            return json.load(f)
    g = {"src_lang": "ja", "tgt_lang": "zh", "terms": []}
    for src, tgt, typ in SEED_TERMS:
        g["terms"].append({"src": src, "tgt": tgt, "type": typ, "note": "預設", "source": "預設", "locked": True})
    save_glossary(g)
    return g


def save_glossary(g):
    g["terms"].sort(key=lambda t: (-len(t["src"]), t["src"]))
    tmp = GLOSSARY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False, indent=1)
    os.replace(tmp, GLOSSARY_PATH)


# 使用者資料夾名稱與交接文件已確立的譯名
SEED_TERMS = [
    ("メルクストーリア", "梅露可物語", "作品名"),
    ("メルク", "梅露可", "人名"),
    ("癒術士", "癒術士", "術語"),
    ("癒術", "癒術", "術語"),
    ("<name>", "<name>", "主角名（播放器代換）"),
]


def add_terms(new_terms, source, story_id=None, run=None):
    """把新名詞併入名詞表；已存在的 src 不覆蓋（使用者／先前決定的譯名優先）。

    run：這次工作開始的時間（同一次工作新增的名詞共用同一個值），名詞表分頁用它把「本次新增」集中顯示。
    """
    run = run or time.strftime("%Y-%m-%d %H:%M:%S")
    added = []
    with _glossary_lock:
        g = load_glossary()
        have = {t["src"] for t in g["terms"]}
        for t in new_terms:
            src = (t.get("src") or "").strip()
            tgt = (t.get("tgt") or "").strip()
            if not src or not tgt or src in have or len(src) > 40:
                continue
            for k, v in FORBIDDEN.items():
                tgt = tgt.replace(k, v)
            item = {"src": src, "tgt": tgt, "type": t.get("type") or "其他", "note": t.get("note") or "",
                    "source": source, "locked": False, "added": run}
            if story_id:
                item["first"] = story_id
            g["terms"].append(item)
            have.add(src)
            added.append(item)
        if added:
            save_glossary(g)
    return added


_KATA_ONLY = re.compile(r"[ァ-ヴー・]+")


_TERM_RE = {}


def term_in(src, text):
    """名詞是否出現在文字中。純片假名的名詞要整個詞相符：「アン」不算出現在「アンタ」裡，「ベル」不算在「レベル」裡。"""
    if src not in text:
        return False
    if _KATA_ONLY.fullmatch(src):
        # 後面可以接「ー」：喊叫時拉長音（「シエットーー！」）仍算是這個名字
        r = _TERM_RE.get(src)
        if r is None:
            r = _TERM_RE[src] = re.compile(r"(?<![ァ-ヴー])" + re.escape(src) + r"(?![ァ-ヴ])")
        return r.search(text) is not None
    return True


def speaker_key(sp):
    return sp.split("_")[0]


# ====================================================================== source text
_SCRIPT_DEPS = [os.path.join(ROOT, "tools", f) for f in ("spritestudio_texts.json", "timeline_image_texts.json")]


def load_ja(story_id, game=None):
    p = os.path.join(JA_DIR, "%d.json" % story_id)
    if game is not None and os.path.exists(p):
        newest = max([os.path.getmtime(d) for d in _SCRIPT_DEPS if os.path.exists(d)] or [0])
        if os.path.getmtime(p) < newest:
            os.remove(p)
    if not os.path.exists(p):
        if game is None:
            raise FileNotFoundError(p)
        d = game.story(story_id)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


TL_BASE = 10000   # Timeline 演出台詞的索引：TL_BASE × (場景 + 1) + 序號


def timeline_line_index(scene, n):
    return TL_BASE * (scene + 1) + n


def story_lines(ja):
    """[(sceneIndex, 顯示名字[], text)]，只取有文字的場景。名字用遊戲名字牌上的 displayName，不是角色 key。
    場景裡的 Timeline 演出台詞（沒有名字牌）接在該場景後面，索引為 timeline_line_index(場景, 序號)；
    SpriteStudio 特效裡畫成圖片的台詞（ssfx）接在 Timeline 台詞之後，序號連續。"""
    out = []
    for i, s in enumerate(ja["scenes"]):
        t = s.get("text") or ""
        if t.strip():
            out.append((i, scene_speakers(s), t))
        n = 0
        for tl in (s.get("timelines") or []) + (s.get("ssfx") or []):
            for txt in tl.get("texts") or []:
                out.append((timeline_line_index(i, n), [], txt))
                n += 1
    return out


KATA = re.compile(r"[ァ-ヴー・]{3,}")
BRACKET = re.compile(r"『([^』]{1,20})』")
PLACE = re.compile(r"[一-龥ァ-ヴー]{1,10}(?:王国|帝国|国|村|町|城|都|街|島|山|森|湖|教団|騎士団|団|族|学園|遺跡|神殿|塔)")
COMMON_KATA = set("""ユーザー モンスター ストーリー メンバー パーティ パーティー チーム リーダー ポイント
タイミング チャンス ピンチ ショック ダメージ エネルギー パワー スピード ミッション イベント
ノート メモ ゲーム ルール プレゼント デート ケーキ クッキー ジュース ミルク スープ パン
ドア ベッド テーブル イス ランプ ナイフ ロープ ボール カード ハート マスター サポート
グループ センス キャラ ペース レベル スキル クラス""".split())


def candidates(story_ids, game=None, limit=300):
    g = load_glossary()
    known = {t["src"] for t in g["terms"]}
    cnt, ex = Counter(), {}
    speakers = Counter()
    for sid in story_ids:
        for _i, sps, text in story_lines(load_ja(sid, game)):
            for sp in sps:
                if sp and sp not in ("‐", "-", "<name>"):
                    speakers[sp] += 1
            plain = TAG_RE.sub("", text).replace("\r\n", "")
            for rx in (KATA, PLACE):
                for m in rx.findall(plain):
                    w = m.strip("・ー")
                    if len(w) >= 2 and w not in COMMON_KATA:
                        cnt[w] += 1
                        ex.setdefault(w, plain[:80])
            for m in BRACKET.findall(plain):
                cnt[m] += 1
                ex.setdefault(m, plain[:80])
    out = []
    for sp, n in speakers.most_common():
        if sp not in known:
            out.append({"term": sp, "count": n, "kind": "說話者", "example": ""})
    for w, n in cnt.most_common():
        if len(out) >= limit:
            break
        if w in known or n < 2 or any(w == o["term"] for o in out):
            continue
        out.append({"term": w, "count": n, "kind": "候選", "example": ex.get(w, "")})
    return out


# ====================================================================== Claude client
class Translator:
    def __init__(self, settings, log=print, cancel=None):
        from backends import make_backend
        self.backend = make_backend(settings)
        self.provider = self.backend.name
        self.run = time.strftime("%Y-%m-%d %H:%M:%S")   # 本次工作的識別（名詞表「新增時間」）
        self.model = self.backend.model
        self.label = self.model if self.provider == "anthropic" else "%s:%s" % (self.provider, self.model)
        self.batch_lines = max(5, int(settings.get("batch_lines") or (BATCH_LINES if self.provider == "anthropic" else 30)))
        self.extra_rules = settings.get("extra_rules") or ""
        self.log = log
        self.cancel = cancel or threading.Event()
        self.usage = Counter()
        self._ulock = threading.Lock()

    # ---------------------------------------------------------- 低階呼叫
    def call_json(self, system_blocks, user_text, schema, max_tokens=16000):
        data, u = self.backend.call_json(system_blocks, user_text, schema, max_tokens)
        with self._ulock:
            for k, v in u.items():
                self.usage[k] += v
        return data

    def cost(self):
        if self.provider != "anthropic":
            return 0.0       # 自架 Ollama / vLLM 不計 API 費用
        p = MODELS.get(self.model, MODELS[DEFAULT_MODEL])
        u = self.usage
        return (u["input"] * p["in"] + u["cache_write"] * p["in"] * 1.25 + u["cache_read"] * p["in"] * 0.1
                + u["output"] * p["out"]) / 1e6

    def progress_note(self):
        if self.provider == "anthropic":
            return "累計約 US$%.2f" % self.cost()
        return "累計 %d/%d tokens" % (self.usage["input"], self.usage["output"])

    # ---------------------------------------------------------- 參考資料
    def build_refs(self, refs_text):
        """參考資料（網址或文字）→ 名詞對照筆記 work/refs.md（≤200 行）。"""
        chunks = []
        for line in refs_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if re.match(r"https?://", line):
                try:
                    req = urllib.request.Request(line, headers={"User-Agent": "merc-story-translator/1.0"})
                    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
                    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
                    txt = re.sub(r"<[^>]+>", " ", txt)
                    txt = re.sub(r"\s+", " ", txt)[:40000]
                    chunks.append("【來源 %s】\n%s" % (line, txt))
                    self.log("  已讀取參考網址 %s（%d 字）" % (line, len(txt)))
                except Exception as e:  # noqa: BLE001
                    self.log("  參考網址讀取失敗 %s：%s（略過，繼續處理）" % (line, e))
            else:
                chunks.append(line)
        if not chunks:
            return ""
        schema = {"type": "object", "properties": {"notes": {"type": "string"}}, "required": ["notes"],
                  "additionalProperties": False}
        r = self.call_json(
            [{"type": "text", "text": "你是遊戲在地化的資料整理員。參考資料只用來確立譯名，不要摘要劇情。"}],
            "請列出以下資料中出現的人名、地名、組織名、作品名、專有術語，以及資料中出現的其他語言對照譯名"
            "（尤其是繁體中文官方譯名）；若有角色關係或身分職稱請一併簡述。以 Markdown 表格「| 原文 | 繁中譯名 | 類型 | 來源 |」"
            "加上「## 背景備註」條列輸出，總長控制在 200 行以內。\n\n" + "\n\n".join(chunks),
            schema, max_tokens=12000)
        notes = r["notes"].strip()
        with open(REFS_PATH, "w", encoding="utf-8") as f:
            f.write("# 參考資料筆記\n\n" + notes + "\n")
        return notes

    # ---------------------------------------------------------- 步驟 A：名詞表
    def build_glossary(self, story_ids, game=None, refs_text=""):
        if refs_text.strip():
            self.log("讀取參考資料…")
            self.build_refs(refs_text)
        refs = open(REFS_PATH, encoding="utf-8").read() if os.path.exists(REFS_PATH) else ""
        cands = candidates(story_ids, game)
        self.log("擷取到 %d 個候選專有名詞（含說話者）" % len(cands))
        if not cands:
            return []
        g = load_glossary()
        cap = 600 if self.provider == "anthropic" else 200
        existing = "\n".join("%s=%s（%s）" % (t["src"], t["tgt"], t["type"]) for t in g["terms"][:cap])
        schema = {"type": "object", "properties": {"terms": {"type": "array", "items": {
            "type": "object",
            "properties": {"src": {"type": "string"}, "tgt": {"type": "string"},
                           "type": {"type": "string", "enum": ["人名", "地名", "組織", "術語", "物品", "作品名", "稱號", "其他"]},
                           "note": {"type": "string"}, "fix": {"type": "boolean"}},
            "required": ["src", "tgt", "type", "note", "fix"], "additionalProperties": False}}},
            "required": ["terms"], "additionalProperties": False}
        system = [{"type": "text", "text": GLOSSARY_RULES + ("\n\n# 參考資料筆記\n" + refs if refs else "")}]
        added_all = []
        step = 120 if self.provider == "anthropic" else 40
        for k in range(0, len(cands), step):
            if self.cancel.is_set():
                break
            part = cands[k:k + step]
            lines = "\n".join("- %s（%s，出現 %d 次）%s" % (c["term"], c["kind"], c["count"],
                                                     "　例：" + c["example"] if c["example"] else "") for c in part)
            r = self.call_json(system, "# 已確定的名詞（必須沿用，不要重複輸出）\n" + existing +
                               "\n\n# 本次候選名詞\n" + lines, schema)
            keep = [t for t in r["terms"] if t["fix"]]
            added = add_terms(keep, "Claude 建議" if self.provider == "anthropic" else "%s 建議" % self.label, run=self.run)
            added_all += added
            self.log("  名詞表新增 %d 筆（本批候選 %d）" % (len(added), len(part)))
        return added_all

    # ---------------------------------------------------------- 步驟 B/C：翻譯一話
    def translate_story(self, story_id, title, game=None, force=False):
        out_path = os.path.join(ZH_DIR, "%d.json" % story_id)
        ja = load_ja(story_id, game)
        lines = story_lines(ja)
        prev = None
        if os.path.exists(out_path) and not force:
            prev = json.load(open(out_path, encoding="utf-8"))
            have = {int(k) for k, v in prev.get("lines", {}).items() if (v.get("text") or "").strip()}
            todo_n = len([1 for i, _s, _t in lines if i not in have])
            if not todo_n:
                self.log("  [%d] 已翻譯，略過（勾選「重新翻譯」可覆蓋）" % story_id)
                return prev
            # 例如劇本後來補上了 children 段落：保留已有譯文，只翻缺少的句子
            self.log("  [%d] 已有 %d 句譯文，還有 %d 句未翻譯，只翻譯這些句子" % (story_id, len(have), todo_n))
        g = load_glossary()
        # hint：同時是一般詞的名字（例如 ナイト＝角色「奈特」，也是「騎士」），提醒模型依上下文判斷
        snapshot = [(t["src"], t["tgt"] + ("（%s）" % t["hint"] if t.get("hint") else "")) for t in g["terms"]]
        rules = TRANSLATE_RULES + ("\n\n# 追加規則（使用者指定）\n" + self.extra_rules if self.extra_rules.strip() else "")

        def system_for(texts):
            terms = snapshot
            if self.backend.local:   # 自架模型上下文較小：只送本批出現的名詞
                blob = "\n".join(texts)
                terms = [(a, b) for a, b in snapshot if term_in(a, blob)]
            gl = "\n".join("%s=%s" % (a, b) for a, b in terms) or "（本批沒有需要對照的名詞）"
            return [{"type": "text", "text": rules},
                    {"type": "text", "text": "# 名詞轉換表（原文=譯名，最高優先，必須完全照用）\n" + gl,
                     "cache_control": {"type": "ephemeral"}}]
        schema = {"type": "object", "properties": {
            "title": {"type": "string"},
            "lines": {"type": "array", "items": {"type": "object", "properties": {
                "i": {"type": "integer"}, "zh": {"type": "string"}},
                "required": ["i", "zh"], "additionalProperties": False}},
            "new_terms": {"type": "array", "items": {"type": "object", "properties": {
                "src": {"type": "string"}, "tgt": {"type": "string"}, "type": {"type": "string"},
                "note": {"type": "string"}}, "required": ["src", "tgt", "type", "note"], "additionalProperties": False}}},
            "required": ["title", "lines", "new_terms"], "additionalProperties": False}

        result, memo, history = {}, {}, []
        title_zh = ""
        if prev:
            title_zh = prev.get("title", "")
            for i, sps, t in lines:
                z = (prev["lines"].get(str(i)) or {}).get("text", "")
                if z.strip():
                    result[i] = z
                    memo.setdefault(t, z)
                    history.append((sps, t, z))
        new_since = []
        # 沒有台詞的場景（原文只有空的「」）不送去翻譯，原樣保留，播放器會隱藏
        for i, _sps, t in lines:
            if BLANK_RE.match(t):
                result[i] = t
        todo = [ln for ln in lines if ln[0] not in result]
        bl = self.batch_lines
        batches = [todo[k:k + bl] for k in range(0, len(todo), bl)]
        for bi, batch in enumerate(batches):
            if self.cancel.is_set():
                raise InterruptedError("已取消")
            need = [(i, sps, t) for i, sps, t in batch if t not in memo]
            if need:
                texts = [t + " " + " ".join(sps) for _i, sps, t in need] + [t for _s, t, _z in history[-CONTEXT_LINES:]]
                system = system_for(texts)
                r = self._translate_batch(system, schema, story_id, title if bi == 0 and not title_zh else "", need, history,
                                          new_since)
                if bi == 0 and r.get("title") and not (prev and prev.get("title")):
                    title_zh = r["title"].strip()
                got = {x["i"]: x["zh"] for x in r["lines"]}
                missing = [ln for ln in need if not got.get(ln[0], "").strip()]
                if missing:
                    self.log("  [%d] 第 %d 批漏譯 %d 句，重譯…" % (story_id, bi + 1, len(missing)))
                    r2 = self._translate_batch(system, schema, story_id, "", missing, history, new_since)
                    got.update({x["i"]: x["zh"] for x in r2["lines"] if x["zh"].strip()})
                    r["new_terms"] = r.get("new_terms", []) + r2.get("new_terms", [])
                for i, _sps, t in need:
                    if got.get(i, "").strip():
                        memo[t] = got[i]
                added = add_terms(r.get("new_terms", []), "翻譯中新增", story_id, run=self.run)
                if added:
                    new_since += added
                    self.log("  [%d] 名詞表新增：%s" % (story_id, "、".join("%s=%s" % (a["src"], a["tgt"]) for a in added)))
            for i, sps, t in batch:
                if t in memo:
                    result[i] = fix_text(t, memo[t])
                history.append((sps, t, result.get(i, "")))
            self.log("  [%d] 第 %d/%d 批完成（%s）" % (story_id, bi + 1, len(batches), self.progress_note()))

        # 步驟 C：驗證
        dismissed = (prev or {}).get("dismissed", [])   # 只補翻缺句時保留先前「拒絕」的警告
        warnings = drop_dismissed(verify(lines, result, load_glossary()), dismissed)
        doc = {"id": story_id, "title": title_zh, "title_ja": title, "model": self.label,
               "time": time.strftime("%Y-%m-%d %H:%M:%S"),
               "lines": {str(i): {"text": z} for i, z in sorted(result.items())},
               "warnings": warnings}
        if dismissed:
            doc["dismissed"] = dismissed
        tmp = out_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        os.replace(tmp, out_path)
        miss = len([1 for i, _s, _t in lines if i not in result])
        self.log("  [%d] 完成：%d 句，漏譯 %d，警告 %d" % (story_id, len(result), miss, len(warnings)))
        return doc

    def _translate_batch(self, system, schema, story_id, title, need, history, new_since):
        g = load_glossary()
        names = {t["src"]: t["tgt"] for t in g["terms"]}
        ctx = history[-CONTEXT_LINES:]
        parts = []
        if new_since:
            parts.append("# 本次工作新增的名詞（同樣必須照用）\n" + "\n".join("%s=%s" % (a["src"], a["tgt"]) for a in new_since))
        if title:
            parts.append("# 本話標題（請一併翻譯，填入 title）\n" + title)
        else:
            parts.append("# 本批不需翻譯標題，title 請填空字串")
        if ctx:
            parts.append("# 前文（僅供理解語氣與脈絡，不要輸出）\n" + "\n".join(
                "%s：%s\n→ %s" % ("・".join(s) or "旁白", t.replace("\r\n", "\\n"), z.replace("\n", "\\n"))
                for s, t, z in ctx))
        body = []
        for i, sps, t in need:
            who = "・".join("%s(%s)" % (s, names[s]) if s in names and names[s] != s else s for s in sps) or "旁白"
            body.append(json.dumps({"i": i, "speaker": who, "text": t.replace("\r\n", "\n")}, ensure_ascii=False))
        parts.append("# 要翻譯的台詞（JSON Lines；text 中的 \\n 是遊戲訊息框的換行）\n" + "\n".join(body))
        return self.call_json(system, "\n\n".join(parts), schema)

    def translate_name(self, text, kind="活動故事的章節名稱"):
        """翻譯單一名稱（例如活動故事的章節名稱），名詞表裡出現的專有名詞照用。"""
        g = load_glossary()
        terms = [(t["src"], t["tgt"] + ("（%s）" % t["hint"] if t.get("hint") else ""))
                 for t in longest_hits([t for t in g["terms"] if t["src"] != t["tgt"] and len(t["src"]) >= 2], text)]
        system = [{"type": "text", "text": "你是手機遊戲《メルクストーリア（梅露可物語）》的繁體中文（台灣）在地化譯者。"
                   "把給你的名稱翻成通順、有文學感的繁體中文，保留原文的修辭與標點風格（「と」多半譯成「與」，"
                   "「、」可譯成「，」），不要加註解。名詞表中的專有名詞必須照用。"}]
        user = "# 種類\n%s\n\n# 名詞表\n%s\n\n# 原文\n%s" % (
            kind, "\n".join("%s=%s" % p for p in terms) or "（無）", text)
        schema = {"type": "object", "properties": {"zh": {"type": "string"}}, "required": ["zh"], "additionalProperties": False}
        out = (self.call_json(system, user, schema, max_tokens=500).get("zh") or "").strip()
        if not out or KANA_RE.search(out):
            raise ValueError("名稱翻譯失敗：" + text)
        return fix_text("", out)

    def fix_line(self, ja, zh, speakers, problems):
        """校對「接受」：依警告內容對單句譯文做最小幅度修改（漏譯時直接翻譯）。"""
        ja = ja.replace("\r\n", "\n")
        g = load_glossary()
        terms = [(t["src"], t["tgt"] + ("（%s）" % t["hint"] if t.get("hint") else ""))
                 for t in g["terms"] if term_in(t["src"], ja)]
        rules = TRANSLATE_RULES.split("# 輸出")[0] + ("\n\n# 追加規則（使用者指定）\n" + self.extra_rules
                                                    if self.extra_rules.strip() else "")
        system = [{"type": "text", "text": rules},
                  {"type": "text", "text": "# 名詞轉換表（原文=譯名，必須完全照用）\n" +
                   ("\n".join("%s=%s" % p for p in terms) or "（本句沒有需要對照的名詞）")}]
        names = {t["src"]: t["tgt"] for t in g["terms"]}
        who = "・".join("%s(%s)" % (s, names[s]) if s in names and names[s] != s else s for s in speakers) or "旁白"
        if zh.strip():
            task = ("# 任務\n校對發現下面這句譯文有問題。請只針對列出的問題修改，其餘字句、語氣、標點、換行盡量保持原樣，"
                    "輸出修改後的完整譯文到 zh（換行用 \\n）。")
        else:
            task = "# 任務\n這句還沒有譯文，請翻譯並輸出到 zh（換行用 \\n）。"
        how = [FIX_HOWTO[p.split("：", 1)[0]] for p in problems if p.split("：", 1)[0] in FIX_HOWTO]
        user = "\n\n".join([task, "# 說話者\n" + who, "# 原文\n" + ja, "# 目前譯文\n" + (zh or "（空白）"),
                            "# 問題\n" + "\n".join("- " + p for p in problems)] +
                           (["# 修改方式\n" + "\n".join("- " + h for h in how)] if how else []))
        schema = {"type": "object", "properties": {"zh": {"type": "string"}}, "required": ["zh"],
                  "additionalProperties": False}
        kana_before = len(KANA_RE.findall(zh))
        for attempt in range(2):
            r = self.call_json(system, user, schema, max_tokens=4000)
            out = (r.get("zh") or "").replace("\\n", "\n").strip()
            if out and len(KANA_RE.findall(out)) <= kana_before:
                return fix_text(ja, out)
            user += "\n\n# 注意\n上一次的輸出" + ("是空的" if not out else "混入了日文假名「%s」" % out) + \
                    "。請輸出繁體中文譯文，不要用日文假名。"
        raise ValueError("模型沒有給出可用的修改（空白或混入日文假名），請手動修改")


def fix_text(ja, zh):
    zh = zh.replace("\r\n", "\n")
    for k, v in FORBIDDEN.items():
        zh = zh.replace(k, v)
    if any(c in S2T for c in zh):
        zh = "".join(S2T.get(c, c) for c in zh)
    # 原文整句包在「」『』內、譯文卻整個漏掉外層括號 → 補回
    j, z = (ja or "").strip(), zh.strip()
    for a, b in ("「」", "『』"):
        if j.startswith(a) and j.endswith(b) and z and not z.startswith(a) and not z.endswith(b):
            zh = a + z + b
            break
    # 以原文的換行格式寫回（遊戲使用 \r\n）
    return zh


BLANK_RE = re.compile(r"^\s*[「『（(]?\s*[」』）)]?\s*$")


def longest_hits(terms, ja):
    """原文中出現的名詞，長的優先：被較長名詞涵蓋的部分不再算短名詞
    （「フェアリー・サークル」裡的「フェアリー」不另外要求譯成「精靈」）。"""
    cand = sorted((t for t in terms if term_in(t["src"], ja)), key=lambda t: -len(t["src"]))
    masked, out = ja, []
    for t in cand:
        if term_in(t["src"], masked):
            out.append(t)
            masked = masked.replace(t["src"], "\u25a1" * len(t["src"]))
    return out


def verify(lines, result, glossary):
    """步驟 C：完整性、禁用字、標籤、名詞表一致性。回傳警告清單。"""
    warns = []
    terms = [t for t in glossary["terms"] if t["src"] != t["tgt"] and len(t["src"]) >= 2]
    for i, _sps, ja in lines:
        zh = result.get(i)
        if not zh:
            warns.append({"i": i, "type": "漏譯", "msg": "沒有譯文"})
            continue
        for k in FORBIDDEN:
            if k in zh:
                warns.append({"i": i, "type": "禁用字", "msg": "含有「%s」" % k})
        jt, zt = ja.strip(), zh.strip()
        if jt[:1] in "「『（" and zt[:1] != jt[:1] or jt[-1:] in "」』）" and zt[-1:] != jt[-1:]:
            warns.append({"i": i, "type": "括號", "msg": "句首／句尾括號與原文不一致"})
        if sorted(TAG_RE.findall(ja)) != sorted(TAG_RE.findall(zh)):
            warns.append({"i": i, "type": "標籤", "msg": "色碼或 <name> 標籤與原文不一致"})
        hits = longest_hits(terms, ja)
        # 名詞表本身就規定用英文的譯名（例：グランドフィナーレ=Grand Finale）不算混入外文
        allowed = ja + " " + " ".join(t["tgt"] for t in hits)
        stray = [w for w in re.findall(r"[A-Za-z]{2,}", TAG_RE.sub("", zh)) if w not in allowed]
        if stray:
            warns.append({"i": i, "type": "外文", "msg": "譯文混入原文沒有的外文：" + "、".join(stray[:3])})
        if ja.count("\n") + 2 < zh.count("\n"):
            warns.append({"i": i, "type": "行數", "msg": "譯文行數明顯多於原文，訊息框可能放不下"})
        for t in hits:
            if not t.get("hint") and t["tgt"] not in zh:
                warns.append({"i": i, "type": "名詞", "msg": "%s 應為「%s」" % (t["src"], t["tgt"])})
                break
    return warns


# ---------------------------------------------------------------- 校對：接受／拒絕警告
KANA_RE = re.compile(r"[ぁ-ゖァ-ヺ]")
FIX_HOWTO = {
    "名詞": "把原文中該名詞在譯文裡對應的譯法換成指定的譯名（照抄，不可改字），句子其他部分不要動。",
    "外文": "把譯文裡原文沒有的英文改成自然的繁體中文（意譯或音譯），不可改成日文假名；名詞表規定的譯名照用。",
    "標籤": "讓 [RRGGBB]、[-]、<name> 標籤的種類與數量和原文完全相同，並放在與原文對應的位置；文字內容不要改。",
    "行數": "重新斷行，行數不要多於原文（每行約 26 字、最多 4 行）；刪掉譯文中不屬於台詞的說明文字。",
    "括號": "句首與句尾的括號改成和原文相同。",
    "漏譯": "翻譯這一句。",
}


def warn_key(w):
    return (int(w["i"]), w["type"], w["msg"])


def drop_dismissed(warns, dismissed):
    """去掉使用者按「拒絕」的警告（以 句子編號＋類型＋訊息 比對）。"""
    gone = {warn_key(d) for d in dismissed or []}
    return [w for w in warns if warn_key(w) not in gone]


OPEN_BR, CLOSE_BR = "「『（(“\"", "」』）)”\""


def rewrap(ja, zh):
    """譯文行數太多：依原文行數（最多 4 行、每行約 26 字）重新斷行，盡量斷在標點後面。"""
    flat = zh.replace("\n", "")
    n = max(ja.count("\n") + 1, -(-len(flat) // 26))
    n = min(max(n, 1), 4)
    cuts, last = [], 0
    for k in range(1, n):
        ideal = round(len(flat) * k / n)
        best = None
        for d in range(0, 7):
            for p in (ideal + d, ideal - d):
                if last < p < len(flat) and flat[p - 1] in "，。！？、…～」』）:：;；" and flat[p] not in "」』）…！？":
                    best = p
                    break
            if best:
                break
        best = best or ideal
        if last < best < len(flat):
            cuts.append(best)
            last = best
    parts, prev = [], 0
    for c in cuts + [len(flat)]:
        parts.append(flat[prev:c])
        prev = c
    return "\n".join(p for p in parts if p)


def rule_fix(wtype, ja, zh):
    """不用模型就能修正的警告：回傳修正後的譯文；無法用規則修正時回傳 None。"""
    ja = (ja or "").replace("\r\n", "\n")
    if wtype == "禁用字":
        out = zh
        for k, v in FORBIDDEN.items():
            out = out.replace(k, v)
        return out
    if wtype == "括號":
        jt, body = ja.strip(), zh.strip()
        if jt[:1] in "「『（":
            body = (body[1:] if body[:1] in OPEN_BR else body)
            body = jt[:1] + body
        if jt[-1:] in "」』）":
            body = (body[:-1] if body[-1:] in CLOSE_BR else body)
            body = body + jt[-1:]
        return body
    if wtype == "行數":
        if len(zh.replace("\n", "")) > 4 * 30:   # 4 行也放不下（常是模型夾帶說明文字）：交給模型重寫
            return None
        return rewrap(ja, zh)
    return None


def estimate(story_ids, model, game=None, provider="anthropic"):
    chars = 0
    n = 0
    for sid in story_ids:
        try:
            for _i, _s, t in story_lines(load_ja(sid, game)):
                chars += len(t)
                n += 1
        except FileNotFoundError:
            pass
    p = MODELS.get(model, MODELS[DEFAULT_MODEL]) if provider == "anthropic" else {"in": 0, "out": 0}
    batches = max(1, n // BATCH_LINES + len(story_ids))
    tin = chars * 1.3 + batches * 2500           # 原文 + 前文脈絡與規則（名詞表多走快取）
    tout = chars * 1.6 + batches * 800           # 譯文 JSON + 思考
    return {"lines": n, "chars": chars, "input_tokens": int(tin), "output_tokens": int(tout),
            "usd": round((tin * p["in"] + tout * p["out"]) / 1e6, 2)}


GLOSSARY_RULES = """你是手機遊戲《メルクストーリア（梅露可物語）》的繁體中文（台灣）在地化專家，正在建立全作通用的「名詞轉換表」，
目的是讓人名、地名、組織名、專有術語在每一個章節都有固定且唯一的譯法。

判斷原則（衝突時由高至低）：
1. 「已確定的名詞」與參考資料中明確的繁體中文譯名 → 必須採用，不可改譯，也不要再次輸出。
2. 參考資料沒有、但你確定有通行的台灣譯法 → 採用通行譯法。
3. 其他專有名詞依台灣慣用音譯：片假名人名用標準音譯用字（例：ア→亞/阿、ル→爾/魯、ス→斯、ク→克、ト→特/托、
   リ→莉/里、ナ→娜/納、マ→瑪、ラ→拉、レ→雷/蕾、ロ→羅、ミ→米/蜜、メ→梅、ヴ→維），女性角色可用較柔和的字；
   有明顯意義的名稱（例：「瓶の中の少女」「〇〇の森」）以意譯為主。
4. 漢字專有名詞若中文可直接使用就保留（如「癒術士」）。
5. 不確定時 tgt 可保留原文並在 note 註明「保留原文」。

fix 欄位：只有「應該在全作固定譯法」的專有名詞（人名、地名、組織、種族、技能、道具、稱號、作品內術語）才填 true；
一般名詞、擬聲詞、普通外來語（如 モンスター→怪物 這種常用詞以外的普通單字）、句子片段填 false。
說話者名稱一律視為人名（或怪物名），fix 填 true。
使用台灣慣用的繁體中文，避免「妳」字，一律用「你」。note 簡短說明身分或譯名依據。"""

TRANSLATE_RULES = """你是手機遊戲《メルクストーリア（梅露可物語）》的專業繁體中文（台灣）在地化譯者，負責翻譯遊戲劇情台詞。

# 名詞轉換表使用規範（最高優先，凌駕以下所有規則）
- 原文出現名詞表中的「原文」時，譯文必須使用對應「譯名」，不得改用其他譯法。
- 名詞表中譯名與原文相同者，代表保留原文。
- 翻譯中遇到名詞表沒有、但屬於應固定譯法的新專有名詞（人名、地名、組織、術語），請自行決定譯法，
  並放進 new_terms（type 填 人名/地名/組織/術語/物品/其他），之後各批會沿用；普通名詞不要放。

# 翻譯要求
- 遊戲劇情翻譯風格，口語自然流暢，符合角色個性與語氣（敬語、方言、口癖要轉成相應的中文語感，例如活潑、傲嬌、老成的說話方式）。
- 保持語意準確，不要過度意譯，不要添加原文沒有的內容，不要省略。
- 使用台灣慣用的繁體中文與標點（「」『』…… ～ ！？），人名地名採台灣常見譯法。
- 避免使用「妳」字，一律用「你」。
- 原文的「」『』（）等括號要保留對應的中文括號；旁白的（心聲）也保留括號。
- text 中的 \\n 是訊息框換行：譯文請在相近的位置斷行，行數不要多於原文（訊息框一行約 26 個全形字、最多 4 行）。
  zh 欄位中請用 \\n 表示換行。
- 必須原封不動保留特殊標籤：<name>（主角名字，播放時代換）、[RRGGBB] 與 [-]（文字顏色），位置與原文對應。
- 擬聲詞、語氣詞翻成自然的中文（例：「えっ」→「咦」、「ふふっ」→「呵呵」、「うう……」→「嗚嗚……」）。
- speaker 欄位括號內是說話者的中文名，可用來判斷語氣與人稱，不需要輸出。

# 輸出
- lines：每一個輸入的 i 都必須輸出一筆，zh 為該句的譯文，不得遺漏、不得合併。
- title：若有提供本話標題則翻譯，否則填空字串。
- new_terms：本批新出現、需要固定的專有名詞（沒有就給空陣列）。"""
