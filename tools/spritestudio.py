"""SpriteStudio 6 特效動畫匯出器（故事場景 assetParams 的 EffectSpriteStudio）。

遊戲把 SpriteStudio/Effect/<名稱>/ 的 DataAnimation（da_<名稱>）與 DataCellMap（dc_<名稱>）載入後，
依場景指定的 animationName 播放。這裡把動畫「烘焙」成逐格的繪製清單，輸出
output/assets/ss/<名稱>/ss.js（資料）與 tex0.png、tex1.png…（貼圖），由 player/spritestudio.js 用 Canvas 重現。

格式（SS6 for Unity，PackAttribute StandardCPE）：每個屬性只存「數值改變的那一格」，
TableCode 的低 15 位元是格數、高位元是 TableValue 的索引；兩個變化點之間維持前一個值。
Status.Flags：0x20000000＝隱藏、0x10000000／0x08000000＝左右／上下翻轉。
StatusParts：0x10000000＝這個動畫沒有用到此零件、0x08000000＝不受遮罩影響（例如全畫面底色）。
遮罩零件（Feature 6／7）：遮罩範圍內「受遮罩影響」的零件不顯示，用來做文字由左到右出現的效果。
網格零件（Feature 13，由骨架彎曲）：以未變形的四邊形繪製（近似）。
貼圖：DataAnimation 的 TableMaterial 每個 CellMap 佔 20 個材質（混色方式 × 遮罩），第一個材質的 _MainTex
就是這個 CellMap 的貼圖。零件的 OperationBlendTarget：0＝一般、1＝加算。
PartsColor（零件顏色）：Operation 0＝混合、1＝加算、2＝減算、3＝乘算；VertexColor.a 是混合比例、RateAlpha 乘到不透明度。

只處理 tools/spritestudio_texts.json 列出的特效（裡面是畫成圖片的日文台詞），其他特效仍用 CSS 近似。
座標：SpriteStudio 1 px × 場景 scale × 2 ＝ 播放器 1 px（畫面 1136 × 640，原點在中央，y 向上）。
"""
import json
import math
import os

import UnityPy

FEATURE_NORMAL = (2, 3, 13)
FEATURE_MASK = (6, 7)
ST_HIDE, ST_FLIPX, ST_FLIPY = 0x20000000, 0x10000000, 0x08000000
SP_UNUSED, SP_NOMASK = 0x10000000, 0x08000000
MATERIALS_PER_CELLMAP = 20

_TEXTS = None
_TEXTS_MTIME = None
TEXTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spritestudio_texts.json")


def effect_texts():
    """spritestudio_texts.json：{特效名稱: {cell 名稱: {text, color, ...}, "_anims": [...]}}（依貼圖人工抄錄的日文）。
    檔案改過就重新讀（伺服器不必重新啟動）。"""
    global _TEXTS, _TEXTS_MTIME
    try:
        m = os.path.getmtime(TEXTS_PATH)
    except OSError:
        m = None
    if _TEXTS is None or m != _TEXTS_MTIME:
        try:
            with open(TEXTS_PATH, encoding="utf-8") as f:
                _TEXTS = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
        except (OSError, ValueError):
            _TEXTS = {}
        _TEXTS_MTIME = m
    return _TEXTS


def supported(name, anim=None):
    """這個特效（的這個動畫）要不要由播放器重現。_anims 列出時，只重現這些動畫（其他動畫沒有字，維持 CSS 近似）。"""
    t = effect_texts().get(name)
    if t is None:
        return False
    only = t.get("_anims")
    return anim is None or not only or str(anim) in only


def _key(name, kind):
    sub = {"da": "DataAnimation", "dc": "DataCellMap"}[kind]
    return "Assets/ExternalResources/SpriteStudio/Effect/%s/%s/%s_%s.asset" % (name, sub, kind, name)


class Effect:
    def __init__(self, gd, name):
        self.name = name
        path = gd.bundle_path("SpriteStudio", _key(name, "da"))
        if not path:
            raise KeyError(name)
        env = UnityPy.load(path)
        self.da = self.dc = None
        objs = {}
        for o in env.objects:
            objs[o.path_id] = o
            if o.type.name == "MonoBehaviour":
                t = o.read_typetree()
                if t.get("m_Name") == "da_" + name:
                    self.da = t
                elif t.get("m_Name") == "dc_" + name:
                    self.dc = t
        if self.da is None or self.dc is None:
            raise KeyError(name)
        self.warn = []
        self.parts = self.da["TableParts"]
        self.anims = {a["Name"]: a for a in self.da["TableAnimation"]}
        self.cellmaps = self.dc["TableCellMap"]
        # CellMap → 貼圖（經由材質表）
        self.textures = []
        tex_index = {}
        self.cm_tex = []
        mats = self.da.get("TableMaterial") or []
        for i, cm in enumerate(self.cellmaps):
            tex = None
            pp = mats[i * MATERIALS_PER_CELLMAP] if len(mats) > i * MATERIALS_PER_CELLMAP else None
            mo = objs.get(pp["m_PathID"]) if pp else None
            if mo is not None:
                envs = mo.read_typetree()["m_SavedProperties"]["m_TexEnvs"]
                for k, v in envs:
                    if k == "_MainTex" and v["m_Texture"]["m_PathID"] in objs:
                        tex = objs[v["m_Texture"]["m_PathID"]]
            if tex is None:
                self.warn.append("CellMap %s 找不到貼圖" % cm["Name"])
                self.cm_tex.append(None)
                continue
            if tex.path_id not in tex_index:
                tex_index[tex.path_id] = len(self.textures)
                self.textures.append(tex.read())
            self.cm_tex.append(tex_index[tex.path_id])
        # 所有 CellMap 的 cell 攤平成一個清單；名稱只有一個 CellMap 時用 cell 名稱，否則用「CellMap 名稱/cell 名稱」
        single = len(self.cellmaps) == 1
        self.cells, self.cm_offset = [], []
        for i, cm in enumerate(self.cellmaps):
            self.cm_offset.append(len(self.cells))
            for c in cm["TableCell"]:
                self.cells.append({"cm": i, "cell": c, "key": c["Name"] if single else "%s/%s" % (cm["Name"], c["Name"])})
        self.texts = {k: v for k, v in effect_texts().get(name, {}).items() if not k.startswith("_")}

    # ------------------------------------------------------------ 屬性
    @staticmethod
    def track(attr):
        if not attr or not attr.get("TableCodeValue"):
            return None
        vals = attr["TableValue"]
        return sorted(((c & 0x7FFF), vals[c >> 15]) for c in attr["TableCodeValue"][0]["TableCode"])

    @staticmethod
    def at(tr, f, default=None):
        if not tr:
            return default
        v = tr[0][1]
        for fr, x in tr:
            if fr > f:
                break
            v = x
        return v

    @staticmethod
    def _color(pc):
        """PartsColor → [運算, r, g, b, 比例, 不透明度倍率]（四個頂點取平均）；沒有作用時回傳 None。"""
        if not pc or not pc.get("Bound"):
            return None
        vc = pc.get("VertexColor") or []
        if not vc:
            return None
        n = len(vc)
        r = sum(c["r"] for c in vc) / n
        g = sum(c["g"] for c in vc) / n
        b = sum(c["b"] for c in vc) / n
        a = sum(c["a"] for c in vc) / n
        ra = pc.get("RateAlpha") or [1.0]
        return [pc.get("Operation", 0), round(r, 3), round(g, 3), round(b, 3), round(a, 3), round(sum(ra) / len(ra), 3)]

    def bake(self, anim_name):
        """逐格繪製清單：[[零件, cell, a, b, c, d, e, f, 不透明度, (混色), (零件顏色)], ...]
        （依優先度排序；矩陣為 y 向上的世界座標；混色 1＝加算；零件顏色見 _color）。"""
        a = self.anims[anim_name]
        tracks = []
        for p in a["TableParts"]:
            name = self.parts[len(tracks)]["Name"]
            tracks.append({k: self.track(p.get(k)) for k in ("Status", "Cell", "Position", "Rotation", "Scaling",
                                                             "ScalingLocal", "RateOpacity", "Priority", "PartsColor")})
            for k in ("OffsetPivot", "SizeForce", "PositionAnchor",
                      "PositionTexture", "RotationTexture", "ScalingTexture", "Instance", "Effect"):
                if (p.get(k) or {}).get("TableValue"):
                    self.warn.append("%s／%s：未支援的屬性 %s" % (anim_name, name, k))
            vc = self.track(p.get("VertexCorrection"))
            if vc and any(abs(c["x"]) + abs(c["y"]) > 1e-6 for _f, v in vc for c in v["Coordinate"]):
                self.warn.append("%s／%s：未支援的頂點變形（以未變形繪製）" % (anim_name, name))
        sp = [p["StatusParts"] for p in a["TableParts"]]
        frames = []
        for f in range(a["CountFrame"]):
            world, opac, out = {}, {}, []
            for i, part in enumerate(self.parts):
                tr = tracks[i]
                pos = self.at(tr["Position"], f, {"x": 0.0, "y": 0.0})
                rot = self.at(tr["Rotation"], f, {"z": 0.0})
                scl = self.at(tr["Scaling"], f, {"x": 1.0, "y": 1.0})
                st = (self.at(tr["Status"], f) or {}).get("Flags", ST_HIDE)
                sx = scl["x"] * (-1 if st & ST_FLIPX else 1)
                sy = scl["y"] * (-1 if st & ST_FLIPY else 1)
                r = math.radians(rot.get("z", 0.0))
                cs, sn = math.cos(r), math.sin(r)
                local = (cs * sx, sn * sx, -sn * sy, cs * sy, pos["x"], pos["y"])
                pid = part["IDParent"]
                m = local if pid < 0 else _mul(world[pid], local)
                world[i] = m
                op = self.at(tr["RateOpacity"], f, 1.0) * (1.0 if pid < 0 else opac[pid])
                opac[i] = op
                feat = part["Feature"]
                if feat not in FEATURE_NORMAL + FEATURE_MASK or sp[i] & SP_UNUSED or st & ST_HIDE:
                    continue
                cell = self.at(tr["Cell"], f)
                if not cell or cell["IndexCell"] < 0 or cell.get("IndexCellMap", 0) < 0:
                    continue
                ci = self.cm_offset[cell.get("IndexCellMap", 0)] + cell["IndexCell"]
                col = self._color(self.at(tr["PartsColor"], f))
                o2 = op * (col[5] if col else 1.0)
                if feat in FEATURE_NORMAL and o2 <= 0:
                    continue
                # ScalingLocal：只縮放零件本身，不影響子零件
                sl = self.at(tr["ScalingLocal"], f)
                dm = _mul(m, (sl["x"], 0, 0, sl["y"], 0, 0)) if sl and (sl["x"], sl["y"]) != (1.0, 1.0) else m
                e = [i, ci] + [round(x, 4) for x in dm] + [round(o2, 3)]
                blend = part.get("OperationBlendTarget", 0)
                if blend not in (0, -1) or col:
                    e.append(blend if blend not in (-1,) else 0)
                if col and not (col[0] == 0 and col[4] <= 0):
                    e.append(col[:5])
                prio = self.at(tr["Priority"], f, 0)
                out.append((prio, i, e))
            out.sort(key=lambda x: (x[0], x[1]))
            frames.append([x[2] for x in out])
        return {"fps": a["FramePerSecond"], "n": a["CountFrame"], "f": frames}

    def anim_cells(self, anim_name):
        """這個動畫實際顯示過的 cell 名稱（key；依第一次出現排序）。"""
        seen = []
        for fr in self.bake(anim_name)["f"]:
            for e in fr:
                k = self.cells[e[1]]["key"]
                if k not in seen:
                    seen.append(k)
        return seen

    def anim_texts(self, anim_name):
        out = []
        for c in self.anim_cells(anim_name):
            t = self.texts.get(c)
            if t and t["text"] not in out:
                out.append(t["text"])
        return out

    # ------------------------------------------------------------ 輸出
    def _ink_lines(self, img, box, count, label):
        """區域 box（cell 內座標 [x0, y0, x1, y1]）裡每一行實際有墨跡的範圍 [x0, x1, y0, y1]。"""
        x0b, y0b, x1b, y1b = box
        c = img.crop((x0b, y0b, x1b, y1b))
        px = c.getchannel("A").load()
        w, h = c.size
        rows = [any(px[x, y] > 64 for x in range(w)) for y in range(h)]
        segs, s = [], None
        for y, on in enumerate(rows + [False]):
            if on and s is None:
                s = y
            elif not on and s is not None:
                segs.append((s, y))
                s = None
        segs = [x for x in segs if x[1] - x[0] >= 4]
        if len(segs) != count:
            self.warn.append("%s：圖上有 %d 行字，抄錄的文字有 %d 行" % (label, len(segs), count))
        out = []
        for y0, y1 in segs:
            xs = [x for x in range(w) if any(px[x, y] > 64 for y in range(y0, y1))]
            out.append([xs[0] + x0b, xs[-1] + 1 + x0b, y0 + y0b, y1 + y0b])
        return out

    def export(self, out_dir, anim_names=None):
        os.makedirs(out_dir, exist_ok=True)
        imgs = []
        for i, t in enumerate(self.textures):
            im = t.image.convert("RGBA")
            im.save(os.path.join(out_dir, "tex%d.png" % i), optimize=True)
            imgs.append(im)
        cells = []
        for c in self.cells:
            cl = c["cell"]
            r, pv = cl["Rectangle"], cl["Pivot"]
            ti = self.cm_tex[c["cm"]]
            d = {"n": c["key"], "t": ti if ti is not None else 0,
                 "r": [r["x"], r["y"], r["width"], r["height"]], "p": [pv["x"], pv["y"]]}
            t = self.texts.get(c["key"])
            if t and ti is not None:
                crop = imgs[ti].crop((int(r["x"]), int(r["y"]), int(r["x"] + r["width"]), int(r["y"] + r["height"])))
                if t.get("rot") == 90:          # 貼圖裡順時針轉 90° 存放：轉正後再找行
                    crop = crop.rotate(90, expand=True)
                lines = t["text"].split("\n")
                txt = dict(t)
                if not txt.get("color") or txt["color"] == "auto":
                    txt["color"] = _ink_color(crop)
                if "lines" not in t and t.get("single"):     # 一個大字（含墨點）：整個墨跡範圍當一行
                    bb = crop.getchannel("A").point(lambda v: 255 if v > 64 else 0).getbbox()
                    if bb:
                        txt["lines"] = [[bb[0], bb[2], bb[1], bb[3]]]
                if "lines" not in txt:
                    box = t.get("box") or [0, 0, crop.width, crop.height]
                    txt["lines"] = self._ink_lines(crop, box, len(lines), c["key"])
                d["txt"] = txt
            cells.append(d)
        parts = [{"n": p["Name"], "mask": p["Feature"] in FEATURE_MASK} for p in self.parts]
        anims = {}
        for an in anim_names or sorted(self.anims):
            b = self.bake(an)
            sp = [q["StatusParts"] for q in self.anims[an]["TableParts"]]
            b["thru"] = [i for i, v in enumerate(sp) if v & SP_NOMASK]
            anims[an] = b
        data = {"name": self.name, "tex": ["tex%d.png" % i for i in range(len(imgs))],
                "parts": parts, "cells": cells, "anims": anims}
        with open(os.path.join(out_dir, "ss.js"), "w", encoding="utf-8") as f:
            f.write("(window.MERC_SS = window.MERC_SS || {})[%s] = %s;\n" % (
                json.dumps(self.name, ensure_ascii=False), json.dumps(data, ensure_ascii=False, separators=(",", ":"))))
        return data


def _ink_color(img):
    """文字原本的顏色：不透明像素的中位數。"""
    raw = img.convert("RGBA").tobytes()
    px = [raw[i:i + 4] for i in range(0, len(raw), 4) if raw[i + 3] > 200]
    if not px:
        return "#ffffff"
    med = [sorted(p[k] for p in px)[len(px) // 2] for k in range(3)]
    return "#%02x%02x%02x" % tuple(med)


def _mul(p, c):
    a1, b1, c1, d1, e1, f1 = p
    a2, b2, c2, d2, e2, f2 = c
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2, a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


_CACHE = {}


def effect(gd, name):
    if name not in _CACHE:
        _CACHE[name] = Effect(gd, name)
    return _CACHE[name]


def export_effect(gd, name, out_dir):
    ex = effect(gd, name)
    only = effect_texts().get(name, {}).get("_anims")
    data = ex.export(out_dir, [a for a in only if a in ex.anims] if only else None)
    return data, sorted(set(ex.warn))


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from game_data import GameData
    gd = GameData()
    for nm in sys.argv[2:]:
        data, warn = export_effect(gd, nm, os.path.join(sys.argv[1], nm))
        print(nm, "parts", len(data["parts"]), "cells", len(data["cells"]), "anims", len(data["anims"]))
        for w in warn[:40]:
            print("  !", w)
