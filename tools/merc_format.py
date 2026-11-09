"""メルクストーリア - 癒術士と心の旋律 - (Steam/Android) 故事劇本讀取。

StoryMasterData/*.bundle 內每一話的 TextAsset（StoryYamlData_<id>），
讀出後為 MemoryPack 二進位格式：
    StoryYamlData { int Id; Dictionary<int, StorySceneYamlData> table }
"""
import hashlib
import struct

from Crypto.Cipher import AES

_KEY = hashlib.pbkdf2_hmac("sha256", b"2147483647", b"-2147483648", 1024, 32)


def decrypt(blob: bytes) -> bytes:
    iv, body = blob[:16], blob[16:]
    out = AES.new(_KEY, AES.MODE_CBC, iv).decrypt(body)
    pad = out[-1]
    if not 1 <= pad <= 16 or out[-pad:] != bytes([pad]) * pad:
        raise ValueError("bad PKCS7 padding")
    return out[:-pad]


def textasset_bytes(obj) -> tuple:
    """回傳 (name, raw bytes)，不經過 UnityPy 的字串解碼（避免破壞二進位）。"""
    raw = obj.get_raw_data()
    n = struct.unpack_from("<i", raw, 0)[0]
    name = raw[4:4 + n].decode("utf-8")
    p = (4 + n + 3) & ~3
    ln = struct.unpack_from("<i", raw, p)[0]
    return name, raw[p + 4:p + 4 + ln]


# ---------------------------------------------------------------- MemoryPack
class Reader:
    def __init__(self, b: bytes):
        self.b = b
        self.p = 0

    def i32(self):
        v = struct.unpack_from("<i", self.b, self.p)[0]
        self.p += 4
        return v

    def i64(self):
        v = struct.unpack_from("<q", self.b, self.p)[0]
        self.p += 8
        return v

    def f32(self):
        v = struct.unpack_from("<f", self.b, self.p)[0]
        self.p += 4
        return v

    def u8(self):
        v = self.b[self.p]
        self.p += 1
        return v

    def boolean(self):
        return self.u8() != 0

    def timespan(self):  # TimeSpan ticks -> 秒
        return self.i64() / 1e7

    def nullable_f32(self):  # Nullable<float> 為 8 bytes (hasValue, pad, value)
        has = self.b[self.p]
        v = struct.unpack_from("<f", self.b, self.p + 4)[0]
        self.p += 8
        return v if has else None

    def vec2(self):
        v = struct.unpack_from("<2f", self.b, self.p)
        self.p += 8
        return list(v)

    def vec3(self):
        v = struct.unpack_from("<3f", self.b, self.p)
        self.p += 12
        return list(v)

    def string(self):
        h = self.i32()
        if h == 0:
            return ""
        if h == -1:
            return None
        if h < 0:  # UTF8：~byteLength，接著 utf16 長度
            blen = ~h
            self.i32()
            s = self.b[self.p:self.p + blen].decode("utf-8")
            self.p += blen
            return s
        s = self.b[self.p:self.p + h * 2].decode("utf-16-le")  # UTF16
        self.p += h * 2
        return s

    def array(self, fn):
        n = self.i32()
        if n == -1:
            return None
        return [fn() for _ in range(n)]

    def obj(self, fn):
        h = self.u8()
        if h == 255:
            return None
        return fn(h)


ENUMS = {
    "MessageWindowType": ["Default", "Shout"],
    "MessageTextSize": ["Default", "Middle", "Big"],
    "SoundType": ["None", "BackgroundMusic", "SoundEffect", "StorySoundEffect", "Jingle"],
    "EffectType": ["None", "Attack", "Earthquake", "Fade", "Heal", "Movie", "Shake", "Still",
                   "Summon", "Scroll", "Wipe", "CustomWipe", "CrossFade", "BecameFriend",
                   "FadeStill", "InputName", "ShowCursor"],
    "ActressType": ["Default", "Navi", "Scout"],
    "Expression": ["通常", "喜", "喜d", "怒", "怒d", "哀", "哀d", "驚", "驚d", "呆", "照", "眠",
                   "真剣", "固有1", "固有2", "固有3", "固有4"],
    "Emotion": ["None", "哀", "汗1", "汗2", "疑問", "怒", "喜", "驚1", "驚2", "眠", "ぐるぐる",
                "花", "ハート", "うきうき"],
    "Appearance": ["None", "In", "Out", "RightIn", "LeftIn", "RightOut", "LeftOut"],
    "AssetType": ["None", "CommonTexture", "EffectSpriteStudio", "UnitSpriteStudio",
                  "MonsterSpriteStudio", "RawPrefab", "Timeline"],
    "SESType": ["None", "Stop", "FadeOut"],
    "MoveType": ["None", "Up", "Down", "Right", "Left"],
    "CursorType": ["None", "Time", "Tap", "LongTap", "Swipe"],
    "CursorDirection": ["None", "Up", "Down"],
    "TextAnim": ["None", "Character", "Line"],
}


def enum(r, name):
    v = r.i32()
    t = ENUMS[name]
    return t[v] if 0 <= v < len(t) else v


def _text_animation(r):
    return r.obj(lambda n: {
        "type": enum(r, "TextAnim"), "size": r.f32(), "interval": r.timespan(),
        "fadeIn": r.timespan(), "forceWait": r.boolean()})


def _blur(r):
    return r.obj(lambda n: {"startPower": r.nullable_f32(), "power": r.nullable_f32(),
                            "quality": r.nullable_f32(), "duration": r.timespan(),
                            "delay": r.timespan()})


def _bright(r):
    return r.obj(lambda n: {"startPower": r.nullable_f32(), "power": r.nullable_f32(),
                            "duration": r.timespan(), "delay": r.timespan()})


def _sepia(r):
    return r.obj(lambda n: {"type": r.i32(), "startPower": r.nullable_f32(),
                            "power": r.nullable_f32(), "saturation": r.nullable_f32(),
                            "duration": r.timespan(), "delay": r.timespan()})


def _bg_anim(r):
    return r.obj(lambda n: {"startX": r.nullable_f32(), "startY": r.nullable_f32(),
                            "startScale": r.nullable_f32(), "x": r.nullable_f32(),
                            "y": r.nullable_f32(), "scale": r.nullable_f32(),
                            "duration": r.timespan(), "delay": r.timespan()})


def _bg_effect(r):
    return r.obj(lambda n: {"z": r.nullable_f32(), "autoSkip": r.boolean(), "blur": _blur(r),
                            "bright": _bright(r), "sepia": _sepia(r), "animation": _bg_anim(r)})


def _bgm(r):
    return r.obj(lambda n: {"name": r.string(), "assetType": enum(r, "SoundType"),
                            "assetId": r.string(), "mute": r.timespan(), "fadeIn": r.timespan(),
                            "fadeOut": r.timespan(), "forceFade": r.boolean()})


def _se(r):
    return r.obj(lambda n: {"name": r.string(), "assetType": enum(r, "SoundType"),
                            "assetId": r.string(), "type": enum(r, "SESType"),
                            "playTimes": r.i32(), "interval": r.timespan(),
                            "delay": r.timespan(), "fadeIn": r.timespan(),
                            "fadeOut": r.timespan()})


def _cursor(r):
    return r.obj(lambda n: {"type": enum(r, "CursorType"), "time": r.timespan(),
                            "position": r.vec2(), "direction": enum(r, "CursorDirection"),
                            "touchPosition": r.vec2(), "touchScale": r.vec2(),
                            "image": r.boolean()})


def _effect_param(r):
    return r.obj(lambda n: {"fadeOut": r.timespan(), "fadeIn": r.timespan(),
                            "fadeWait": r.timespan(), "color": r.string(),
                            "alphaFadeIn": r.boolean(), "duration": r.timespan(),
                            "delay": r.timespan(), "move": enum(r, "MoveType"),
                            "scrollStartDelay": r.timespan(), "showUI": r.boolean(),
                            "autoSkip": r.boolean(), "assetName": r.string(),
                            "movieAssetName": r.string(), "cursor": _cursor(r)})


def _appearance(r):
    return r.obj(lambda n: {"type": enum(r, "Appearance"), "startX": r.nullable_f32(),
                            "startY": r.nullable_f32(), "startZ": r.nullable_f32(),
                            "endX": r.nullable_f32(), "endY": r.nullable_f32(),
                            "endZ": r.nullable_f32(), "duration": r.timespan(),
                            "active": r.boolean()})


def _character(r):
    return r.obj(lambda n: {"textureId": r.i32(), "faceTextureId": r.i32(),
                            "type": enum(r, "ActressType"), "key": r.string(),
                            "displayName": r.string(), "expression": enum(r, "Expression"),
                            "emotion": enum(r, "Emotion"), "active": r.boolean(),
                            "appearance": _appearance(r), "offset": r.vec3(),
                            "scale": r.vec3()})


def _asset_param(r):
    return r.obj(lambda n: {"id": r.string(), "assetType": enum(r, "AssetType"),
                            "assetName": r.string(), "spriteStudioName": r.string(),
                            "animationName": r.string(), "position": r.vec3(),
                            "scale": r.vec3(), "playTimes": r.i32(), "forcePlay": r.boolean(),
                            "frameReset": r.boolean(), "delay": r.timespan()})


def _scene(r):
    def body(n):
        assert n == 21, f"unexpected StorySceneYamlData member count {n}"
        return {
            "sceneId": r.i32(),
            "speakers": r.array(r.string),
            "text": r.string(),
            "windowType": enum(r, "MessageWindowType"),
            "textSize": enum(r, "MessageTextSize"),
            "textAnimation": _text_animation(r),
            "forceShowAllText": r.boolean(),
            "background": r.string(),
            "timezone": r.i32(),
            "backgroundEffect": _bg_effect(r),
            "bgm": _bgm(r),
            "soundEffects": r.array(lambda: _se(r)),
            "effect": enum(r, "EffectType"),
            "effectParam": _effect_param(r),
            "disableWipe": r.boolean(),
            "left": _character(r),
            "center": _character(r),
            "right": _character(r),
            "assetsKeys": r.array(r.string),
            "assetParams": r.array(lambda: _asset_param(r)),
            "waitTarget": r.string(),
        }
    return r.obj(body)


def parse_story(plain: bytes) -> dict:
    r = Reader(plain)
    h = r.u8()
    assert h == 2, h
    sid = r.i32()
    n = r.i32()
    scenes = []
    for _ in range(n):
        key = r.i32()
        sc = _scene(r)
        sc["_key"] = key
        scenes.append(sc)
    assert r.p == len(plain), f"trailing bytes {len(plain) - r.p}"
    return {"id": sid, "scenes": scenes}


# ------------------------------------------------------------ master data
STORY_TYPES = ["None", "メイン第一部", "メイン第二部", "メイン第一部オリジン", "イベント", "ユニット", "メモリアル"]


def _records(plain, fn, members):
    r = Reader(plain)
    assert r.u8() == 1
    out = []
    for _ in range(r.i32()):
        h = r.u8()
        if h == 255:
            continue
        assert h == members, (h, members)
        out.append(fn(r))
    assert r.p == len(plain), len(plain) - r.p
    return out


def parse_story_master(plain):
    return _records(plain, lambda r: {
        "chapterId": r.i32(), "storyId": r.i32(), "title": r.string(), "subTitle": r.string(),
        "eventName": r.string(), "type": r.i32(), "unitId": r.i32(),
        "children": r.array(r.string), "order": r.i32()}, 9)


def parse_chapter_master(plain):
    return _records(plain, lambda r: {
        "id": r.i32(), "name": r.string(), "type": r.i32(), "eventId": r.i32(),
        "eventName": r.string(), "eventCountry": r.i32(), "order": r.i32(),
        "mainStoryFilter": r.i32(), "eventStoryFilter": r.i32()}, 9)


def parse_background_master(plain):
    return _records(plain, lambda r: {
        "id": r.i32(), "code": r.string(), "type": r.i32(), "name": r.string(),
        "description": r.string(), "country": r.i32(), "order": r.i32(),
        "backgroundFilter": r.i32(), "countryFilter": r.i32()}, 9)


def parse_bgm_master(plain):
    return _records(plain, lambda r: {
        "id": r.i32(), "code": r.string(), "name": r.string(), "description": r.string(),
        "country": r.i32(), "order": r.i32(), "countryFilter": r.i32()}, 7)


def parse_still_master(plain):
    return _records(plain, lambda r: {
        "id": r.i32(), "mainStoryFilter": r.i32(), "countryFilter": r.i32(), "storyId": r.i32(),
        "thumbnailId": r.string(), "imageId": r.string()}, 6)


def scene_speakers(scene):
    """名字牌上實際顯示的名字（重現 StorySceneData.SetSpeakers）：
    speakers 裡的每個名字是角色的 key，依序比對左、中、右三個角色；key 相同就顯示該角色的 displayName
    （例如 key「過去メルク」→ 顯示「カラコリチア」）。畫面上沒有對應角色的說話者不顯示名字。"""
    out = []
    for sp in scene.get("speakers") or []:
        for slot in ("left", "center", "right"):
            c = scene.get(slot)
            if c and c.get("key") == sp and c.get("displayName"):
                out.append(c["displayName"])
    return out
