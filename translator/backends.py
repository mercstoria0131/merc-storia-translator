"""翻譯引擎後端：Anthropic Claude（官方 SDK）、遠端 Ollama、遠端 vLLM。

每個後端提供：
  call_json(system_text, user_text, schema, max_tokens) -> (dict, usage)
      usage = {"input", "output", "cache_read", "cache_write"}
  list_models() -> [{"id", "label"}]
  name / model / local（True 表示自架服務，不計 API 費用，且名詞表只送本批相關條目以節省上下文）
"""
import json
import re
import time
import urllib.error
import urllib.request

PROVIDERS = {
    "anthropic": "Anthropic Claude（API 金鑰）",
    "ollama": "遠端 Ollama",
    "vllm": "遠端 vLLM（OpenAI 相容 API）",
}


class BackendError(RuntimeError):
    pass


def _parse_json_text(text):
    """容錯解析：移除 <think>…</think>、```json 圍欄，取出第一個 JSON 物件。"""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a:b + 1])
        raise BackendError("模型輸出不是合法 JSON：" + t[:200])


def _system_text(system_blocks):
    if isinstance(system_blocks, str):
        return system_blocks
    return "\n\n".join(b["text"] for b in system_blocks)


class HttpBackend:
    local = True

    def __init__(self, base_url, model, api_key="", timeout=900, temperature=0.3, retries=3):
        if not base_url:
            raise BackendError("尚未設定服務網址")
        self.base = base_url.rstrip("/")
        self.model = model
        self.key = api_key or ""
        self.timeout = timeout
        self.temperature = temperature
        self.retries = retries

    def _req(self, method, path, body=None, timeout=None):
        headers = {"Content-Type": "application/json", "User-Agent": "merc-storia-translator/1.0"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        data = json.dumps(body).encode("utf-8") if body is not None else None
        last = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "replace")[:500]
                if e.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    last = BackendError("HTTP %d：%s" % (e.code, msg))
                    time.sleep(2 * (attempt + 1))
                    continue
                err = BackendError("HTTP %d：%s" % (e.code, msg))
                err.code = e.code
                raise err
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = BackendError("無法連線到 %s：%s" % (self.base, getattr(e, "reason", e)))
                if attempt < self.retries:
                    time.sleep(2 * (attempt + 1))
                    continue
        raise last


class OllamaBackend(HttpBackend):
    """Ollama 原生 API：/api/chat（format 傳 JSON Schema 做結構化輸出）、/api/tags 列模型。"""
    name = "ollama"

    def __init__(self, base_url, model, api_key="", num_ctx=16384, think="off", **kw):
        super().__init__(base_url, model, api_key, **kw)
        self.num_ctx = int(num_ctx or 16384)
        self.think = think or "off"   # off / on / auto（auto＝不傳 think，依模型預設）

    def list_models(self):
        r = self._req("GET", "/api/tags", timeout=20)
        out = []
        for m in r.get("models", []):
            d = m.get("details") or {}
            extra = " ".join(x for x in (d.get("parameter_size"), d.get("quantization_level")) if x)
            out.append({"id": m["name"], "label": m["name"] + ("（%s）" % extra if extra else "")})
        return out

    def call_json(self, system_blocks, user_text, schema, max_tokens=16000):
        if not self.model:
            raise BackendError("尚未選擇 Ollama 模型")
        body = {"model": self.model, "stream": False, "format": schema, "keep_alive": "30m",
                "messages": [{"role": "system", "content": _system_text(system_blocks)},
                             {"role": "user", "content": user_text}],
                "options": {"num_ctx": self.num_ctx, "temperature": self.temperature, "num_predict": max_tokens}}
        if self.think in ("off", "on"):
            body["think"] = self.think == "on"
        try:
            r = self._req("POST", "/api/chat", body)
        except BackendError as e:
            if "think" in body and getattr(e, "code", None) == 400 and "think" in str(e).lower():
                self.think = "auto"          # 不支援思考模式的模型：改為不傳 think
                body.pop("think")
                r = self._req("POST", "/api/chat", body)
            else:
                raise
        if r.get("done_reason") == "length":
            raise BackendError("輸出被截斷（num_predict / num_ctx 不足），請調小「每批句數」或加大上下文長度")
        usage = {"input": r.get("prompt_eval_count", 0) or 0, "output": r.get("eval_count", 0) or 0,
                 "cache_read": 0, "cache_write": 0}
        return _parse_json_text((r.get("message") or {}).get("content", "")), usage


class VllmBackend(HttpBackend):
    """vLLM OpenAI 相容 API：/v1/chat/completions（response_format=json_schema 引導解碼）、/v1/models。"""
    name = "vllm"

    def __init__(self, base_url, model, api_key="", **kw):
        base_url = (base_url or "").rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        super().__init__(base_url, model, api_key, **kw)
        self.mode = None  # 記住伺服器支援的結構化輸出方式

    def list_models(self):
        r = self._req("GET", "/v1/models", timeout=20)
        out = []
        for m in r.get("data", []):
            ml = m.get("max_model_len")
            out.append({"id": m["id"], "label": m["id"] + ("（上下文 %d）" % ml if ml else "")})
        return out

    def call_json(self, system_blocks, user_text, schema, max_tokens=16000):
        if not self.model:
            raise BackendError("尚未選擇 vLLM 模型")
        base = {"model": self.model, "max_tokens": max_tokens, "temperature": self.temperature,
                "messages": [{"role": "system", "content": _system_text(system_blocks)},
                             {"role": "user", "content": user_text}]}
        modes = [self.mode] if self.mode else ["json_schema", "guided_json", "json_object"]
        last = None
        for mode in modes:
            body = dict(base)
            if mode == "json_schema":
                body["response_format"] = {"type": "json_schema",
                                           "json_schema": {"name": "result", "schema": schema, "strict": True}}
            elif mode == "guided_json":
                body["guided_json"] = schema
            else:
                body["response_format"] = {"type": "json_object"}
                body["messages"] = [base["messages"][0], {"role": "user", "content": user_text +
                                    "\n\n請只輸出符合此 JSON Schema 的 JSON：\n" + json.dumps(schema, ensure_ascii=False)}]
            try:
                r = self._req("POST", "/v1/chat/completions", body)
            except BackendError as e:
                if getattr(e, "code", None) == 400 and not self.mode:
                    last = e
                    continue
                raise
            self.mode = mode
            ch = r["choices"][0]
            if ch.get("finish_reason") == "length":
                raise BackendError("輸出被截斷（max_tokens 或模型上下文不足），請調小「每批句數」")
            u = r.get("usage") or {}
            usage = {"input": u.get("prompt_tokens", 0) or 0, "output": u.get("completion_tokens", 0) or 0,
                     "cache_read": 0, "cache_write": 0}
            return _parse_json_text(ch["message"].get("content") or ""), usage
        raise last or BackendError("vLLM 不支援結構化輸出")


class AnthropicBackend:
    """Claude：官方 anthropic SDK，output_config.format 結構化輸出，名詞表走 prompt cache。"""
    name = "anthropic"
    local = False

    def __init__(self, api_key, model, effort="medium"):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=4, timeout=600.0) if api_key else \
            anthropic.Anthropic(max_retries=4, timeout=600.0)
        self.model = model
        self.effort = effort

    def list_models(self):
        from pipeline import MODELS
        return [{"id": k, "label": v["label"]} for k, v in MODELS.items()]

    def call_json(self, system_blocks, user_text, schema, max_tokens=16000):
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=system_blocks,
                      messages=[{"role": "user", "content": user_text}],
                      output_config={"effort": self.effort,
                                     "format": {"type": "json_schema", "schema": schema}})
        if self.model in ("claude-opus-5-5", "claude-sonnet-5-5"):
            # 拒答時由伺服器自動改用備援模型（Opus 5.5 / Sonnet 5.5 建議預設開啟）
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
            resp = self.client.beta.messages.create(**kwargs)
        else:
            resp = self.client.messages.create(**kwargs)
        u = resp.usage
        usage = {"input": u.input_tokens or 0, "output": u.output_tokens or 0,
                 "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                 "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0}
        if resp.stop_reason == "refusal":
            raise BackendError("模型拒絕回應此批內容（refusal）")
        if resp.stop_reason == "max_tokens":
            raise BackendError("輸出超過 max_tokens，請調小批次")
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text), usage


def make_backend(settings, provider=None, model=None):
    p = provider or settings.get("provider") or "anthropic"
    temp = float(settings.get("temperature", 0.3) or 0.3)
    if p == "ollama":
        return OllamaBackend(settings.get("ollama_url", ""), model if model is not None else settings.get("ollama_model", ""),
                             settings.get("ollama_key", ""), num_ctx=settings.get("num_ctx", 16384),
                             think=settings.get("ollama_think", "off"), temperature=temp)
    if p == "vllm":
        return VllmBackend(settings.get("vllm_url", ""), model if model is not None else settings.get("vllm_model", ""),
                           settings.get("vllm_key", ""), temperature=temp)
    from pipeline import DEFAULT_MODEL
    return AnthropicBackend(settings.get("api_key") or None, model or settings.get("model") or DEFAULT_MODEL,
                            settings.get("effort") or "medium")
