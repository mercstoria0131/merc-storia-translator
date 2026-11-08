"""Story Timeline（Unity Timeline 演出）匯出器。

故事場景的 assetParams 裡 assetType == "Timeline" 時，遊戲會把 BundleAssets 的
Particle/StoryTimeline_<名稱>/StoryTimeline_<名稱>.prefab 生成在畫面上，由 PlayableDirector 播放
（StoryTimelineComponent；UI 標記 TimelineUIEvent：1=WaitText、2=WaitTap、3=NextScene；
BGM 標記 TimelineBgmEvent：1=FadeIn、2=FadeOut）。

這裡把 prefab 的物件階層、精靈圖、網格、材質、粒子、AnimationClip 曲線、Timeline 軌道與標記
轉成播放器用的 output/assets/timeline/<名稱>/tl.js（資料與內嵌圖片），由 player/timeline.js 重現。

座標：正交攝影機，畫面中心為原點，可見範圍寬 568 × 高 320 單位（1 單位 = 畫面 2 px），z 越小越靠前。
"""
import base64
import io
import json
import math
import os
import struct
import zlib

import UnityPy

# Transform 在 AnimationClip 綁定裡的 attribute 代碼與曲線數
TRANSFORM_ATTR = {1: ("pos", 3), 2: ("rot", 4), 3: ("scale", 3), 4: ("euler", 3)}
CLASS_GAMEOBJECT, CLASS_TRANSFORM, CLASS_MESHRENDERER, CLASS_SPRITERENDERER = 1, 4, 23, 212
CLASS_PARTICLESYSTEM, CLASS_PARTICLERENDERER = 198, 199
PREFIX = {CLASS_GAMEOBJECT: "go", CLASS_SPRITERENDERER: "sr", CLASS_MESHRENDERER: "mr",
          CLASS_PARTICLESYSTEM: "ps", CLASS_PARTICLERENDERER: "pr"}

COMPONENT_PROPS = [
    "m_IsActive", "m_Enabled", "m_Sprite", "m_FlipX", "m_FlipY", "m_SortingOrder",
    "m_Color.r", "m_Color.g", "m_Color.b", "m_Color.a", "m_Size.x", "m_Size.y",
]
PARTICLE_PROPS = [
    "EmissionModule.m_Bursts.Array.data[0].countCurve.scalar", "ShapeModule.m_Scale.x", "ShapeModule.m_Scale.y",
    "ShapeModule.m_Position.x", "ShapeModule.m_Position.y",
    "InitialModule.startColor.maxColor.r", "InitialModule.startColor.maxColor.g",
    "InitialModule.startColor.maxColor.b", "InitialModule.startColor.maxColor.a",
    "EmissionModule.rateOverTime.scalar", "EmissionModule.enabled", "InitialModule.startSize.scalar",
    "InitialModule.startSpeed.scalar", "InitialModule.startLifetime.scalar", "ShapeModule.radius.value",
]
# RenderTexture 攝影機的後製平面：播放器直接畫場景，不畫這張
POST_SHADERS = ("Custom/MI/RadialBlur", "Custom/MI/ChromaticAberration", "Custom/MI/ColorEffect", "Custom/BlurGaussian")


_IMAGE_TEXTS = None


def image_texts(name):
    """timeline_image_texts.json：畫成圖片的日文台詞（精靈圖名稱 → {text, style}）。"""
    global _IMAGE_TEXTS
    if _IMAGE_TEXTS is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "timeline_image_texts.json")
        try:
            with open(p, encoding="utf-8") as f:
                _IMAGE_TEXTS = json.load(f)
        except OSError:
            _IMAGE_TEXTS = {}
    return _IMAGE_TEXTS.get(name) or {}


def blend_of(shader):
    s = (shader or "").lower()
    if "additive" in s or s.endswith(" add") or s.endswith("_add"):
        return "add"
    if "multiply" in s:
        return "mul"
    if "overlay" in s:
        return "overlay"
    if "screen" in s:
        return "screen"
    return "alpha"


def crc(s):
    return zlib.crc32(s.encode("utf-8"))


def _num(v):
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None


class TimelineExporter:
    def __init__(self, gd, name):
        self.gd = gd
        self.name = name                       # 例：main2_Endroll（不含 StoryTimeline_）
        deps = gd.catalog_deps("BundleAssets")
        key = next((k for k in deps if k.endswith("/StoryTimeline_%s/StoryTimeline_%s.prefab" % (name, name))), None)
        if not key:
            raise KeyError("找不到 Timeline：%s" % name)
        paths = [os.path.join(gd.bdir, "BundleAssets", b) for b in deps[key]]
        self.env = UnityPy.load(*[p for p in paths if os.path.exists(p)])
        # 物件以 (SerializedFile 名稱, path_id) 為鍵；PPtr 依擁有者所在檔案的 externals 解析
        self.files = {}
        self.main = set()
        for bpath, bf in self.env.files.items():
            for fn, sf in getattr(bf, "files", {}).items():
                if hasattr(sf, "objects"):
                    self.files[fn.lower()] = sf
                    if os.path.basename(bpath) == os.path.basename(paths[0]):
                        self.main.add(fn.lower())
        self.objs = {}
        for fn, sf in self.files.items():
            for pid, o in sf.objects.items():
                self.objs[(fn, pid)] = o
        self._tt = {}
        self.warn = []

    # ------------------------------------------------------------ helpers
    def T(self, key):
        if key not in self._tt:
            self._tt[key] = self.objs[key].read_typetree()
        return self._tt[key]

    def ref(self, ptr, owner):
        """PPtr → 物件鍵；owner 是含有這個 PPtr 的物件鍵。解析不到回傳 None。"""
        if not ptr or not owner:
            return None
        fid, pid = ptr.get("m_FileID", 0), ptr.get("m_PathID", 0)
        if not pid:
            return None
        fn = owner[0]
        if fid:
            try:
                fn = self.files[fn].externals[fid - 1].path.replace("\\", "/").split("/")[-1].lower()
            except Exception:
                return None
        k = (fn, pid)
        return k if k in self.objs else None

    def typ(self, key):
        return self.objs[key].type.name

    def main_objects(self, typ=None):
        return [k for k, o in self.objs.items() if k[0] in self.main and (typ is None or o.type.name == typ)]

    def components(self, go_key):
        go = self.T(go_key)
        out = []
        for c in go["m_Component"]:
            k = self.ref(c["component"], go_key)
            if k:
                out.append(k)
        return out

    # ------------------------------------------------------------ export
    def export(self, out_dir):
        os.makedirs(out_dir, exist_ok=True)
        self.out_dir = out_dir
        self.sprites, self.sprite_list = {}, []
        self.textures, self.texture_list = {}, []
        self.meshes, self.mesh_list = {}, []
        self.materials, self.material_list = {}, []
        self.clips, self.clip_list = {}, []
        self._build_nodes()
        tl = self._timeline()
        data = {
            "name": self.name, "nodes": self.nodes, "sprites": self.sprite_list,
            "textures": self.texture_list, "meshes": self.mesh_list, "materials": self.material_list,
            "clips": self.clip_list, "animators": self.animators, "timeline": tl, "cameras": self.cameras,
        }
        tmp = os.path.join(out_dir, "tl.js.part")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("MercTimeline.register(%s,%s);\n" % (json.dumps(self.name), json.dumps(data, ensure_ascii=False, separators=(",", ":"))))
        os.replace(tmp, os.path.join(out_dir, "tl.js"))
        # 播放器另外需要匯出的音效／BGM
        se = sorted({m["seName"] for x in tl for m in x["markers"] if m.get("seName")})
        bgm = sorted({"B-%d" % m["parameter"]["inBgmId"] for x in tl for m in x["markers"]
                      if isinstance(m.get("parameter"), dict) and m["parameter"].get("bgmEvent") == 1 and m["parameter"].get("inBgmId")})
        with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"se": se, "bgm": bgm}, f)
        return data

    # ------------------------------------------------------------ hierarchy
    def _build_nodes(self):
        root = None
        for k in self.main_objects():
            if self.typ(k) not in ("Transform", "RectTransform"):
                continue
            t = self.T(k)
            if self.ref(t["m_Father"], k):
                continue
            go = self.ref(t["m_GameObject"], k)
            if any(self.typ(c) == "PlayableDirector" for c in self.components(go)):
                root = k
        self.nodes = []
        self.node_of_go = {}
        self.animators = []
        self.animator_node = {}
        self.animator_index = {}
        self.director = None
        self._pending_animators = []
        self._cameras, self._rt_views, self._mr_node = [], [], {}
        self._walk(root, -1)
        # RenderTexture 攝影機：攝影機只拍 mask 內的圖層，結果貼在 renderTextureMeshRenderer 的平面上
        self.cameras = []
        cam_by_key = {c["key"]: c for c in self._cameras}
        for cam_key, mr_key in self._rt_views:
            c = cam_by_key.get(cam_key)
            q = self._mr_node.get(mr_key)
            if c is None or q is None:
                continue
            ci = len(self.cameras)
            self.cameras.append({k: v for k, v in c.items() if k != "key"} | {"quad": q})
            n = self.nodes[q]
            if "r" not in n and getattr(self, "_skipped_r", {}).get(q):
                n["r"] = self._skipped_r[q]
            if "r" in n:
                n["r"]["rt"] = ci
        for idx, key in self._pending_animators:
            self._animator(idx, key)

    def _walk(self, tr, parent):
        t = self.T(tr)
        go_key = self.ref(t["m_GameObject"], tr)
        go = self.T(go_key)
        idx = len(self.nodes)
        rot = t["m_LocalRotation"]
        node = {
            "name": go["m_Name"], "parent": parent, "active": bool(go["m_IsActive"]), "layer": go.get("m_Layer", 0),
            "pos": [t["m_LocalPosition"][k] for k in "xyz"],
            "rot": [rot[k] for k in "xyzw"],
            "scale": [t["m_LocalScale"][k] for k in "xyz"],
            "children": [],
        }
        self.nodes.append(node)
        self.node_of_go[go_key] = idx
        for ck in self.components(go_key):
            ty = self.typ(ck)
            if ty == "SpriteRenderer":
                self._sprite_renderer(node, ck)
            elif ty == "MeshRenderer":
                self._mesh_renderer(node, go_key, ck)
            elif ty == "ParticleSystem":
                node["ps"] = self._particle(ck, go_key)
            elif ty == "Animator":
                self.animator_node[ck] = idx
                self._pending_animators.append((idx, ck))
            elif ty == "PlayableDirector":
                self.director = ck
            elif ty == "Camera":
                c = self.T(ck)
                bg = c.get("m_BackGroundColor") or {}
                self._cameras.append({"node": idx, "mask": c["m_CullingMask"]["m_Bits"], "size": c.get("orthographic size", 160.0),
                                      "clear": c.get("m_ClearFlags", 2), "bg": [bg.get(k, 0) for k in "rgba"], "key": ck})
            elif ty == "MonoBehaviour":
                mb = self.T(ck)
                if "renderTextureCamera" in mb and "renderTextureMeshRenderer" in mb:
                    self._rt_views.append((self.ref(mb["renderTextureCamera"], ck), self.ref(mb["renderTextureMeshRenderer"], ck)))
        for c in t["m_Children"]:
            k = self.ref(c, tr)
            if k:
                node["children"].append(len(self.nodes))
                self._walk(k, idx)

    # ------------------------------------------------------------ renderers
    def _sprite_index(self, key):
        if not key:
            return -1
        if key in self.sprites:
            return self.sprites[key]
        sp = self.objs[key].read()
        t = self.T(key)
        rect, ppu, piv = t["m_Rect"], t["m_PixelsToUnits"], t["m_Pivot"]
        self.sprites[key] = len(self.sprite_list)
        ent = {"name": t["m_Name"], "src": self._data_uri(sp.image), "w": rect["width"] / ppu,
               "h": rect["height"] / ppu, "px": piv["x"], "py": piv["y"]}
        it = image_texts(self.name).get(t["m_Name"])
        if it:
            ent["txt"], ent["style"] = it["text"], it.get("style", "outline")
            # 原圖中文字（不透明部分）所佔的範圍（比例），讓譯文貼圖的字高與位置跟原圖一致
            img = sp.image.convert("RGBA")
            bb = img.getchannel("A").point(lambda a: 255 if a > 60 else 0).getbbox()
            if bb:
                ent["tbox"] = [round(bb[0] / img.width, 4), round(bb[1] / img.height, 4),
                               round(bb[2] / img.width, 4), round(bb[3] / img.height, 4)]
                ent["iw"], ent["ih"] = img.width, img.height
        self.sprite_list.append(ent)
        return self.sprites[key]

    @staticmethod
    def _data_uri(img):
        """圖片內嵌成 data URI：直接用 file:// 開啟播放器時，WebGL 也能使用（本機圖檔會被當成跨來源）。"""
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=90, method=4)
        return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    def _texture_index(self, key):
        if not key or self.typ(key) != "Texture2D":
            return -1
        if key in self.textures:
            return self.textures[key]
        tex = self.objs[key].read()
        self.textures[key] = len(self.texture_list)
        self.texture_list.append({"name": tex.m_Name, "src": self._data_uri(tex.image)})
        return self.textures[key]

    def _material(self, key):
        """材質索引；解析不到（例如內建 Sprites-Default）回傳 -1，播放器當一般 alpha。"""
        if not key:
            return -1
        if key in self.materials:
            return self.materials[key]
        t = self.T(key)
        sh = self.ref(t["m_Shader"], key)
        shader = self.T(sh)["m_ParsedForm"]["m_Name"] if sh and self.typ(sh) == "Shader" else ""
        sp = t["m_SavedProperties"]
        texs = {}
        for k, v in sp["m_TexEnvs"]:
            ti = self._texture_index(self.ref(v["m_Texture"], key))
            if ti >= 0:
                texs[k] = {"tex": ti, "scale": [v["m_Scale"]["x"], v["m_Scale"]["y"]],
                           "offset": [v["m_Offset"]["x"], v["m_Offset"]["y"]]}
        ent = {"name": t["m_Name"], "shader": shader, "blend": blend_of(shader),
               "floats": {k: v for k, v in sp["m_Floats"] if _num(v) is not None},
               "colors": {k: [v["r"], v["g"], v["b"], v["a"]] for k, v in sp["m_Colors"]},
               "tex": texs}
        if shader in POST_SHADERS:
            ent["post"] = True
        self.materials[key] = len(self.material_list)
        self.material_list.append(ent)
        return self.materials[key]

    def _renderer_common(self, key):
        t = self.T(key)
        mats = [self.ref(m, key) for m in t.get("m_Materials") or []]
        return {"enabled": bool(t["m_Enabled"]), "mat": self._material(mats[0]) if mats else -1,
                "layer": t.get("m_SortingLayer", 0), "order": t.get("m_SortingOrder", 0)}

    def _sprite_renderer(self, node, key):
        t = self.T(key)
        r = self._renderer_common(key)
        c = t["m_Color"]
        r.update({"kind": "sprite", "sprite": self._sprite_index(self.ref(t["m_Sprite"], key)),
                  "color": [c["r"], c["g"], c["b"], c["a"]], "flip": [bool(t["m_FlipX"]), bool(t["m_FlipY"])],
                  "draw": t.get("m_DrawMode", 0), "size": [t["m_Size"]["x"], t["m_Size"]["y"]]})
        node["r"] = r

    def _mesh_renderer(self, node, go_key, key):
        r = self._renderer_common(key)
        mesh = -1
        for ck in self.components(go_key):
            if self.typ(ck) == "MeshFilter":
                mesh = self._mesh(self.ref(self.T(ck)["m_Mesh"], ck))
        r.update({"kind": "mesh", "mesh": mesh})
        idx = len(self.nodes) - 1
        self._mr_node[key] = idx
        if r["mat"] >= 0 and self.material_list[r["mat"]].get("post"):
            # 後製材質的平面：只有被 RenderTexture 攝影機使用時才畫（見 _build_nodes）
            self._skipped_r = getattr(self, "_skipped_r", {})
            self._skipped_r[idx] = r
            return
        node["r"] = r

    def _mesh(self, key):
        if not key:
            return -1           # 內建 Quad（±0.5）
        if key in self.meshes:
            return self.meshes[key]
        from UnityPy.helpers.MeshHelper import MeshHandler
        h = MeshHandler(self.objs[key].read())
        h.process()
        idx = []
        for sub in h.get_triangles():
            for tri in sub:
                idx.extend(int(i) for i in tri)
        ent = {"pos": [[round(float(v[0]), 5), round(float(v[1]), 5), round(float(v[2]), 5)] for v in h.m_Vertices],
               "uv": [[round(float(u[0]), 5), round(float(u[1]), 5)] for u in (h.m_UV0 or [])], "idx": idx}
        self.meshes[key] = len(self.mesh_list)
        self.mesh_list.append(ent)
        return self.meshes[key]

    # ------------------------------------------------------------ particle
    def _minmax(self, v, scale=1.0):
        """MinMaxCurve → {"mode": 0 常數 1 曲線 2 兩曲線 3 兩常數, "s": scalar, "mn": minScalar, "c"/"cm": 取樣曲線}"""
        if not v:
            return None
        st = v.get("minMaxState", 0)
        out = {"mode": st, "s": v.get("scalar", 1.0) * scale, "mn": v.get("minScalar", 0.0) * scale}
        if st in (1, 2):
            out["c"] = self._sample_curve(v.get("maxCurve"))
            if st == 2:
                out["cm"] = self._sample_curve(v.get("minCurve"))
        return out

    @staticmethod
    def _sample_curve(curve, n=32):
        keys = (curve or {}).get("m_Curve") or []
        if not keys:
            return [1.0] * n

        def ev(t):
            if t <= keys[0]["time"]:
                return keys[0]["value"]
            if t >= keys[-1]["time"]:
                return keys[-1]["value"]
            for a, b in zip(keys, keys[1:]):
                if a["time"] <= t <= b["time"]:
                    dt = b["time"] - a["time"]
                    if dt <= 0:
                        return b["value"]
                    u = (t - a["time"]) / dt
                    m0, m1 = a.get("outSlope", 0) * dt, b.get("inSlope", 0) * dt
                    if not (math.isfinite(m0) and math.isfinite(m1)):
                        return a["value"]
                    u2, u3 = u * u, u * u * u
                    return ((2 * u3 - 3 * u2 + 1) * a["value"] + (u3 - 2 * u2 + u) * m0
                            + (-2 * u3 + 3 * u2) * b["value"] + (u3 - u2) * m1)
            return keys[-1]["value"]
        return [round(ev(i / (n - 1)), 4) for i in range(n)]

    @staticmethod
    def _gradient(g):
        if not g:
            return None
        nc, na = g.get("m_NumColorKeys", 2), g.get("m_NumAlphaKeys", 2)
        return {"c": [[g["key%d" % i]["r"], g["key%d" % i]["g"], g["key%d" % i]["b"], g["ctime%d" % i] / 65535.0] for i in range(nc)],
                "a": [[g["key%d" % i]["a"], g["atime%d" % i] / 65535.0] for i in range(na)]}

    def _mmgrad(self, v):
        if not v:
            return None
        return {"mode": v.get("minMaxState", 0),
                "max": [v["maxColor"][k] for k in "rgba"], "min": [v["minColor"][k] for k in "rgba"],
                "gmax": self._gradient(v.get("maxGradient")), "gmin": self._gradient(v.get("minGradient"))}

    def _particle(self, key, go_key):
        t = self.T(key)
        init, sh, em = t["InitialModule"], t["ShapeModule"], t["EmissionModule"]
        out = {
            "dur": t["lengthInSec"], "loop": bool(t["looping"]), "prewarm": bool(t["prewarm"]),
            "delay": self._minmax(t.get("startDelay")), "awake": bool(t.get("playOnAwake", True)),
            "speed": t.get("simulationSpeed", 1.0), "space": t.get("moveWithTransform", 0),
            "life": self._minmax(init["startLifetime"]), "v0": self._minmax(init["startSpeed"]),
            "size": self._minmax(init["startSize"]), "rot0": self._minmax(init["startRotation"]),
            "color": self._mmgrad(init["startColor"]), "max": init["maxNumParticles"],
            "grav": self._minmax(init.get("gravityModifier")),
            "rate": self._minmax(em["rateOverTime"]) if em.get("enabled") else None,
            "bursts": [{"t": b["time"], "n": self._minmax(b["countCurve"]), "cycles": b.get("cycleCount", 1),
                        "iv": b.get("repeatInterval", 0.01)} for b in em.get("m_Bursts", [])] if em.get("enabled") else [],
            "shape": {"on": bool(sh.get("enabled")), "type": sh.get("type", 0),
                      "r": sh.get("radius", {}).get("value", 1.0), "thick": sh.get("radiusThickness", 1.0),
                      "angle": sh.get("angle", 25.0), "arc": sh.get("arc", {}).get("value", 360.0),
                      "box": [sh.get("m_Scale", {}).get(k, 1.0) for k in "xyz"],
                      "pos": [sh.get("m_Position", {}).get(k, 0.0) for k in "xyz"],
                      "rot": [sh.get("m_Rotation", {}).get(k, 0.0) for k in "xyz"],
                      "rdir": sh.get("randomDirectionAmount", 0.0), "sph": sh.get("sphericalDirectionAmount", 0.0)},
            "scaling": t.get("scalingMode", 1),
        }
        col = t.get("ColorModule", {})
        if col.get("enabled"):
            out["col"] = self._mmgrad(col["gradient"])
        sz = t.get("SizeModule", {})
        if sz.get("enabled"):
            out["sol"] = self._minmax(sz["curve"])
        rot = t.get("RotationModule", {})
        if rot.get("enabled"):
            out["rs"] = self._minmax(rot["curve"])
        vel = t.get("VelocityModule", {})
        if vel.get("enabled"):
            out["vel"] = [self._minmax(vel[k]) for k in ("x", "y", "z")]
            out["velSpace"] = vel.get("inWorldSpace", False)
        fo = t.get("ForceModule", {})
        if fo.get("enabled"):
            out["force"] = [self._minmax(fo[k]) for k in ("x", "y", "z")]
        cl = t.get("ClampVelocityModule", {})
        if cl.get("enabled"):
            out["drag"] = self._minmax(cl.get("drag"))
            out["damp"] = cl.get("dampen", 0)
            out["limit"] = self._minmax(cl.get("magnitude"))
        no = t.get("NoiseModule", {})
        if no.get("enabled"):
            out["noise"] = {"str": self._minmax(no.get("strength")), "freq": no.get("frequency", 0.5)}
        uv = t.get("UVModule", {})
        if uv.get("enabled"):
            out["sheet"] = {"x": uv.get("tilesX", 1), "y": uv.get("tilesY", 1),
                            "frame": self._minmax(uv.get("frameOverTime")), "cycles": uv.get("cycles", 1)}
        for ck in self.components(go_key):
            if self.typ(ck) == "ParticleSystemRenderer":
                rt = self.T(ck)
                r = self._renderer_common(ck)
                out["render"] = {"enabled": r["enabled"], "mat": r["mat"], "mode": rt.get("m_RenderMode", 0),
                                 "maxSize": rt.get("m_MaxParticleSize", 0.5), "lenScale": rt.get("m_LengthScale", 2),
                                 "velScale": rt.get("m_VelocityScale", 0), "order": r["order"]}
        return out

    # ------------------------------------------------------------ animation
    def _animator(self, node_idx, key):
        """Animator：預設狀態，以及狀態機（狀態、Trigger 條件、Exit Time、Any State 轉換）。"""
        t = self.T(key)
        ctrl = self.ref(t["m_Controller"], key)
        ent = {"node": node_idx, "default": -1, "speed": 1.0, "states": [], "any": [], "start": 0, "params": []}
        self.animator_index[key] = len(self.animators)
        if ctrl and self.typ(ctrl) == "AnimatorController":
            c = self.T(ctrl)
            clips = [self.ref(x, ctrl) for x in c["m_AnimationClips"]]
            tos = {k: v for k, v in c.get("m_TOS", [])}
            vals = c["m_Controller"]["m_Values"]["data"]["m_ValueArray"]
            ent["params"] = [tos.get(v["m_ID"], str(v["m_ID"])) for v in vals]
            sm = c["m_Controller"]["m_StateMachineArray"]

            def trans(tr):
                d = tr["data"]
                return {"to": d["m_DestinationState"], "dur": d.get("m_TransitionDuration", 0), "off": d.get("m_TransitionOffset", 0),
                        "exit": d.get("m_ExitTime", 1) if d.get("m_HasExitTime", False) else None,
                        "fixed": bool(d.get("m_HasFixedDuration", True)),
                        "cond": [[x["data"]["m_ConditionMode"], tos.get(x["data"]["m_EventID"], ""), x["data"].get("m_EventThreshold", 0)]
                                 for x in d["m_ConditionConstantArray"]]}
            if sm:
                sm0 = sm[0]["data"]
                for si, st in enumerate(sm0["m_StateConstantArray"]):
                    sd = st["data"]
                    bt = sd["m_BlendTreeConstantArray"]
                    clip_idx = -1
                    if bt and bt[0]["data"]["m_NodeArray"]:
                        cid = bt[0]["data"]["m_NodeArray"][0]["data"]["m_ClipID"]
                        if 0 <= cid < len(clips) and clips[cid]:
                            nw = len(self.warn)
                            clip_idx = self._clip(clips[cid], node_idx)
                            if si != sm0["m_DefaultState"]:
                                del self.warn[nw:]       # 共用控制器的其他狀態：路徑對不上是正常的
                    ent["states"].append({"name": tos.get(sd["m_NameID"], str(sd["m_NameID"])), "clip": clip_idx,
                                          "speed": sd.get("m_Speed", 1.0), "loop": bool(sd.get("m_Loop", False)),
                                          "tr": [trans(x) for x in sd["m_TransitionConstantArray"]]})
                    if si == sm0["m_DefaultState"]:
                        ent["default"] = clip_idx
                        ent["speed"] = sd.get("m_Speed", 1.0)
                ent["start"] = sm0["m_DefaultState"]
                ent["any"] = [trans(x) for x in sm0["m_AnyStateTransitionConstantArray"]]
        self.animators.append(ent)

    def _paths(self, root_idx):
        """root 底下所有相對路徑的 CRC32 → 節點（Animator 綁定用）。"""
        paths = {0: root_idx}

        def rec(i, prefix):
            for k in self.nodes[i]["children"]:
                p = (prefix + "/" if prefix else "") + self.nodes[k]["name"]
                paths[crc(p)] = k
                rec(k, p)
        rec(root_idx, "")
        return paths

    def _attr_names(self):
        if getattr(self, "_attrs", None):
            return self._attrs
        names = list(COMPONENT_PROPS) + list(PARTICLE_PROPS)
        props = set()
        for m in self.material_list:
            props.update(m["floats"].keys())
            props.update(m["colors"].keys())
        for p in props:
            for suf in ("", ".r", ".g", ".b", ".a", ".x", ".y", ".z", ".w"):
                names.append("material.%s%s" % (p, suf))
        self._attrs = {crc(n): n for n in names}
        return self._attrs

    def _mat_props(self):
        if getattr(self, "_mprops", None):
            return self._mprops
        names = set()
        for k in self.objs:
            if self.typ(k) == "Material":
                sp = self.T(k)["m_SavedProperties"]
                names |= {a for a, _ in sp["m_Floats"]} | {a for a, _ in sp["m_Colors"]} | {a for a, _ in sp["m_TexEnvs"]}
        self._mprops = {crc(n) & 0x0FFFFFFF: n for n in names}
        return self._mprops

    def _clip(self, key, root_idx):
        ck = (key, root_idx)
        if ck in self.clips:
            return self.clips[ck]
        t = self.T(key)
        mc = t["m_MuscleClip"]
        c = mc["m_Clip"]["data"]
        streamed, dense = c["m_StreamedClip"], c["m_DenseClip"]
        const = c["m_ConstantClip"]["data"]
        s_count, d_count = streamed["curveCount"], dense["m_CurveCount"]
        bindings = t["m_ClipBindingConstant"]["genericBindings"]
        pptr_map = [self.ref(p, key) for p in t["m_ClipBindingConstant"]["pptrCurveMapping"]]
        # 曲線索引 → (binding, 分量)
        curve_of = []
        for bi, b in enumerate(bindings):
            n = TRANSFORM_ATTR.get(b["attribute"], (None, 1))[1] if b["typeID"] == CLASS_TRANSFORM else 1
            curve_of += [(bi, k) for k in range(n)]
        # streamed：每個 key = (曲線索引, a, b, c, d)，該段值 = ((a*dt+b)*dt+c)*dt+d
        keys = {}
        raw = struct.pack("<%dI" % len(streamed["data"]), *streamed["data"])
        p = 0
        while p + 8 <= len(raw):
            tm, nk = struct.unpack_from("<fi", raw, p)
            p += 8
            if not math.isfinite(tm) or abs(tm) > 1e30:
                tm = -1e9 if tm < 0 else 1e9
            for _ in range(nk):
                ci, a, b_, cc, d = struct.unpack_from("<i4f", raw, p)
                p += 20
                keys.setdefault(ci, []).append([tm, a, b_, cc, d])
        attrs = self._attr_names()
        paths = self._paths(root_idx)
        out = []

        def target(ci):
            if ci >= len(curve_of):
                return None
            bi, comp = curve_of[ci]
            b = bindings[bi]
            node = paths.get(b["path"])
            if node is None:
                self.warn.append("%s：找不到路徑 %d" % (t["m_Name"], b["path"]))
                return None
            ty = b["typeID"]
            if ty == CLASS_TRANSFORM:
                return node, TRANSFORM_ATTR.get(b["attribute"], ("?", 1))[0], comp, False
            if b.get("customType") == 22:
                # Renderer 材質屬性：attribute = (CRC32(名稱) & 0x0FFFFFFF) | (分量 << 28)；最高位 8 = float
                a = b["attribute"]
                nm = self._mat_props().get(a & 0x0FFFFFFF)
                if nm is None:
                    self.warn.append("%s：未知材質屬性 %d" % (t["m_Name"], a))
                    return None
                hi = a >> 28
                return node, "mat:" + nm + ("" if hi & 8 else "." + "rgba"[hi & 3]), 0, False
            nm = attrs.get(b["attribute"])
            if b["isPPtrCurve"]:
                return node, "pptr:" + (nm or "m_Sprite"), 0, True
            if nm is None:
                self.warn.append("%s：未知屬性 type=%d attr=%d" % (t["m_Name"], ty, b["attribute"]))
                return None
            return node, PREFIX.get(ty, "t%d" % ty) + ":" + nm, 0, False

        def sprite_of(v):
            k = int(round(v))
            return self._sprite_index(pptr_map[k]) if 0 <= k < len(pptr_map) and pptr_map[k] and self.typ(pptr_map[k]) == "Sprite" else -1

        for ci in range(s_count):
            tg = target(ci)
            if not tg:
                continue
            node, prop, comp, is_pptr = tg
            ks = keys.get(ci, [])
            if is_pptr:
                out.append({"n": node, "p": prop, "i": comp, "step": [[k[0], sprite_of(k[4])] for k in ks]})
            else:
                out.append({"n": node, "p": prop, "i": comp, "k": [[round(x, 6) for x in k] for k in ks]})
        if d_count:
            fc, arr = dense["m_FrameCount"], dense["m_SampleArray"]
            for j in range(d_count):
                tg = target(s_count + j)
                if not tg:
                    continue
                node, prop, comp, is_pptr = tg
                vals = [arr[f * d_count + j] for f in range(fc)]
                if is_pptr:
                    out.append({"n": node, "p": prop, "i": comp,
                                "step": [[dense["m_BeginTime"] + f / dense["m_SampleRate"], sprite_of(v)] for f, v in enumerate(vals)]})
                else:
                    out.append({"n": node, "p": prop, "i": comp,
                                "dense": {"t0": dense["m_BeginTime"], "rate": dense["m_SampleRate"], "v": [round(v, 6) for v in vals]}})
        for j, v in enumerate(const):
            tg = target(s_count + d_count + j)
            if not tg:
                continue
            node, prop, comp, is_pptr = tg
            if is_pptr:
                out.append({"n": node, "p": prop, "i": comp, "step": [[-1e9, sprite_of(v)]]})
            else:
                out.append({"n": node, "p": prop, "i": comp, "const": round(v, 6)})
        ent = {"name": t["m_Name"], "start": mc["m_StartTime"], "stop": mc["m_StopTime"], "loop": bool(mc["m_LoopTime"]),
               "curves": out,
               "events": [{"t": e["time"], "fn": e["functionName"], "s": e.get("data", "")} for e in t.get("m_Events", [])]}
        self.clips[ck] = len(self.clip_list)
        self.clip_list.append(ent)
        return self.clips[ck]

    # ------------------------------------------------------------ timeline
    def _timeline(self):
        d = self.T(self.director)
        self.bindings = {}
        for b in d["m_SceneBindings"]:
            k, v = self.ref(b["key"], self.director), self.ref(b["value"], self.director)
            if k and v:
                self.bindings[k] = v
        assets = [self.ref(d["m_PlayableAsset"], self.director)]
        # StoryTimelineComponent.playableAssets：依序播放的多段 Timeline
        for k in self.main_objects("MonoBehaviour"):
            tt = self.T(k)
            if "playableAssets" in tt and "playableDirector" in tt:
                more = [self.ref(a, k) for a in tt["playableAssets"]]
                assets = [a for a in more if a] or assets
        return [self._timeline_asset(a) for a in assets if a]

    def _bound_node(self, track):
        v = self.bindings.get(track)
        if not v:
            return -1
        ty = self.typ(v)
        if ty == "Animator":
            return self.animator_node.get(v, -1)
        if ty == "GameObject":
            return self.node_of_go.get(v, -1)
        go = self.ref(self.T(v).get("m_GameObject"), v)
        return self.node_of_go.get(go, -1)

    def _timeline_asset(self, a):
        t = self.T(a)
        out = {"name": t["m_Name"], "tracks": [], "markers": []}
        end = 0.0

        def walk(tk, group_muted=False):
            nonlocal end
            tr = self.T(tk)
            muted = group_muted or bool(tr.get("m_Muted"))
            node = self._bound_node(tk)
            kind = "anim" if "m_InfiniteClip" in tr else "activation" if "m_PostPlaybackState" in tr else None
            clips = []
            for c in tr["m_Clips"]:
                ak = self.ref(c["m_Asset"], tk)
                at = self.T(ak) if ak else {}
                ent = {"start": c["m_Start"], "dur": c["m_Duration"], "in": c["m_ClipIn"], "scale": c["m_TimeScale"],
                       "easeIn": c.get("m_EaseInDuration", 0), "easeOut": c.get("m_EaseOutDuration", 0),
                       "blendIn": max(0, c.get("m_BlendInDuration", -1)), "blendOut": max(0, c.get("m_BlendOutDuration", -1)),
                       "pre": c.get("m_PreExtrapolationMode", 0), "post": c.get("m_PostExtrapolationMode", 0),
                       "name": c.get("m_DisplayName", "")}
                end = max(end, c["m_Start"] + c["m_Duration"])
                if kind == "anim" and "m_Clip" in at:
                    cp = self.ref(at["m_Clip"], ak)
                    ent["clip"] = self._clip(cp, node) if cp and node >= 0 else -1
                    ent["loop"] = at.get("m_Loop", 0)      # 0 依 clip、1 開、2 關
                    if cp:
                        ms = self.T(cp)["m_MuscleClip"]
                        ent["clipLoop"] = bool(ms["m_LoopTime"])
                        ent["clipLen"] = ms["m_StopTime"] - ms["m_StartTime"]
                elif "animationClipTransitionSequence" in (at.get("parameter") or {}):
                    kind = "animancer"
                    seq = at["parameter"]["animationClipTransitionSequence"]
                    cp = self.ref(seq["_Clip"], ak)
                    ent["clip"] = self._clip(cp, node) if cp and node >= 0 else -1
                    ent["speed"] = seq.get("_Speed", 1.0)
                    ent["fade"] = seq.get("_FadeDuration", 0.25)
                    ent["nstart"] = _num(seq.get("_NormalizedStartTime"))
                    # ClipTransitionSequence._Others：第一段播完後依序接著播（最後一段循環）
                    others = []
                    for o in seq.get("_Others") or []:
                        ok = self.ref(o.get("_Clip"), ak)
                        if ok and node >= 0:
                            others.append({"clip": self._clip(ok, node), "speed": o.get("_Speed", 1.0)})
                    if others:
                        ent["seq"] = others
                elif at and kind is None:
                    kind = "custom"
                    ent["params"] = json.loads(json.dumps({k: v for k, v in at.items() if not k.startswith("m_")}, default=str))
                clips.append(ent)
            for mk in tr.get("m_Markers", {}).get("m_Objects", []):
                mp = self.ref(mk, tk)
                if not mp:
                    continue
                mt = self.T(mp)
                ent = {"t": mt.get("m_Time", 0), "name": mt.get("m_Name", "")}
                for k, v in mt.items():
                    if not k.startswith("m_"):
                        ent[k] = v
                if node >= 0:
                    ent["node"] = node
                rv = self.bindings.get(tk)
                if rv and self.typ(rv) == "MonoBehaviour" and "animator" in self.T(rv):
                    ak = self.ref(self.T(rv)["animator"], rv)
                    if ak in self.animator_index:
                        ent["anim"] = self.animator_index[ak]
                out["markers"].append(json.loads(json.dumps(ent, default=str)))
                end = max(end, ent["t"])
            if clips or tr.get("m_InfiniteClip", {}).get("m_PathID"):
                ent = {"name": tr["m_Name"], "kind": kind or "custom", "node": node, "muted": muted, "clips": clips}
                if kind == "anim":
                    inf = self.ref(tr.get("m_InfiniteClip"), tk)
                    if inf and node >= 0:
                        ent["infinite"] = self._clip(inf, node)
                        end = max(end, self.T(inf)["m_MuscleClip"]["m_StopTime"])
                if kind == "activation":
                    ent["post"] = tr.get("m_PostPlaybackState", 3)
                out["tracks"].append(ent)
            for ch in tr["m_Children"]:
                k = self.ref(ch, tk)
                if k:
                    walk(k, muted)

        for tp in t["m_Tracks"]:
            k = self.ref(tp, a)
            if k:
                walk(k)
        mt = self.ref(t.get("m_MarkerTrack"), a)
        if mt:
            walk(mt)
        out["markers"].sort(key=lambda m: m["t"])
        out["duration"] = t["m_FixedDuration"] if t.get("m_DurationMode") == 1 else end
        return out


def export_timeline(gd, name, out_dir):
    ex = TimelineExporter(gd, name)
    data = ex.export(out_dir)
    return data, sorted(set(ex.warn))


def timeline_texts(gd, name):
    """Timeline 裡要翻譯的台詞（[UI] テキストフェードイン 標記的 text），依出現時間排序、去除重複。"""
    ex = TimelineExporter(gd, name)
    found = []
    for k in ex.main_objects("MonoBehaviour"):
        t = ex.T(k)
        p = t.get("parameter")
        if isinstance(p, dict) and isinstance(p.get("text"), str) and "fadeInSec" in p:
            found.append((t.get("m_Time", 0), p["text"]))
    found.sort()
    out = []
    for _, s in found:
        if s not in out:
            out.append(s)
    # 畫成圖片的台詞（timeline_image_texts.json），接在文字標記之後
    for _k, it in sorted(image_texts(name).items()):
        if it["text"] not in out:
            out.append(it["text"])
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from game_data import GameData
    gd = GameData()
    for nm in sys.argv[2:]:
        data, warn = export_timeline(gd, nm, os.path.join(sys.argv[1], nm))
        print(nm, "nodes", len(data["nodes"]), "clips", len(data["clips"]), "sprites", len(data["sprites"]),
              "textures", len(data["textures"]), "meshes", len(data["meshes"]),
              "duration", [round(x["duration"], 2) for x in data["timeline"]])
        for w in warn[:40]:
            print("  !", w)
