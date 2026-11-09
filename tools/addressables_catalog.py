"""Unity Addressables 2.x 二進位 catalog (catalog_*.bin) 的最小解析器。

格式：
  header: magic 0x0DE38942, version, keysOffset, idOffset, instanceProviderOffset,
          sceneProviderOffset, initObjectsArrayOffset, buildResultHashOffset
  每個物件位移的前 4 bytes 為該物件的 byte 長度。
  字串：位移 bit31 = UTF-16，bit30 = 動態字串（鏈結串列 {stringId, nextId}，
        各段反向串接，以 '/' 或 '.' 連接）。
  keys：uint 陣列，成對 (keyObject, locationArray)。
  location：{primaryKey, internalId, provider, dependencySet, depHash, extraData, type}
"""
import struct


class Catalog:
    def __init__(self, path):
        self.b = open(path, "rb").read()
        h = struct.unpack_from("<8I", self.b, 0)
        assert h[0] == 0x0DE38942, "not an addressables binary catalog"
        self.keys_off = h[2]

    def u(self, o):
        return struct.unpack_from("<I", self.b, o)[0]

    def arr(self, o):
        if o == 0xFFFFFFFF:
            return []
        n = self.u(o - 4)
        return list(struct.unpack_from("<%dI" % (n // 4), self.b, o))

    def _auto(self, o):
        if o & 0x80000000:
            o &= 0x7FFFFFFF
            return self.b[o:o + self.u(o - 4)].decode("utf-16-le")
        return self.b[o:o + self.u(o - 4)].decode("utf-8", "replace")

    def string(self, o, sep="/"):
        if o == 0xFFFFFFFF:
            return None
        if not o & 0x40000000:
            return self._auto(o)
        o &= 0x3FFFFFFF
        parts = []
        while o != 0xFFFFFFFF:
            sid, nxt = struct.unpack_from("<2I", self.b, o)
            parts.append(self._auto(sid))
            o = nxt
        return sep.join(reversed(parts))

    def location(self, o):
        pk, iid, prov, deps, _h, _extra, _typ = struct.unpack_from("<7I", self.b, o)
        return {
            "primaryKey": self.string(pk),
            "internalId": self.string(iid, "\\") if iid & 0x40000000 else self.string(iid),
            "provider": self.string(prov, "."),
            "deps": self.arr(deps),
        }

    def asset_to_bundle(self):
        """回傳 {asset primaryKey: (asset GUID, bundle 檔名)}。"""
        keys = self.arr(self.keys_off)
        seen = set()
        out = {}
        for i in range(1, len(keys), 2):
            for lo in self.arr(keys[i]):
                if lo in seen:
                    continue
                seen.add(lo)
                loc = self.location(lo)
                if not loc["provider"] or "BundledAssetProvider" not in loc["provider"]:
                    continue
                bundle = None
                for d in loc["deps"]:  # 第一個依賴就是資源本身所在的 bundle
                    bl = self.location(d)
                    if bl["internalId"] and bl["internalId"].endswith(".bundle"):
                        bundle = bl["internalId"].replace("\\", "/").split("/")[-1]
                        break
                out[loc["primaryKey"]] = (loc["internalId"], bundle)
        return out

    def asset_deps(self):
        """回傳 {asset primaryKey: [需要載入的 bundle 檔名（含自身與依賴）]}。"""
        keys = self.arr(self.keys_off)
        seen = set()
        out = {}
        for i in range(1, len(keys), 2):
            for lo in self.arr(keys[i]):
                if lo in seen:
                    continue
                seen.add(lo)
                loc = self.location(lo)
                if not loc["provider"] or "BundledAssetProvider" not in loc["provider"]:
                    continue
                bl = []
                for d in loc["deps"]:
                    x = self.location(d)
                    if x["internalId"] and x["internalId"].endswith(".bundle"):
                        bl.append(x["internalId"].replace("\\", "/").split("/")[-1])
                out[loc["primaryKey"]] = bl
        return out

