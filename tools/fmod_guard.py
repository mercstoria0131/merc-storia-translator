"""UnityPy 一開始轉換任何物件（包括圖片）就會載入 FMOD 的 DLL；載入失敗時，連背景、立繪都無法解碼。

Windows ARM64 版 Python 就是這種情況：fmod_toolkit 只附 x64 與 32 位元 ARM 的 fmod.dll，沒有 ARM64 版。
這裡先試著載入 FMOD；失敗時改放一個替身模組，讓圖片照常解碼，只有聲音回報清楚的錯誤。
必須在 import UnityPy 之前匯入。
"""
import sys
import types

FMOD_ERROR = None

try:
    import fmod_toolkit  # noqa: F401
except Exception as e:  # noqa: BLE001  （OSError：找不到或無法載入 DLL）
    for _k in [k for k in sys.modules if k.split(".")[0] in ("fmod_toolkit", "pyfmodex")]:
        del sys.modules[_k]
    FMOD_ERROR = ("無法載入 FMOD，BGM 與音效無法播放（%s）。"
                  "Windows on ARM 請安裝 Miniconda（或 python.org 的「Windows installer (64-bit)」版 Python），"
                  "再重新啟動翻譯器，啟動器會自動建立 x64 版環境。" % e.__class__.__name__)

    def _unavailable(*_a, **_k):
        raise RuntimeError(FMOD_ERROR)

    _stub = types.ModuleType("fmod_toolkit")
    _stub.raw_to_wav = _stub.sound_to_wav = _stub.subsound_to_wav = _unavailable
    _stub.get_pyfmodex_system_instance = _unavailable
    sys.modules["fmod_toolkit"] = _stub
    print(FMOD_ERROR, file=sys.stderr)
