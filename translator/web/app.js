(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const api = async (path, body) => {
    const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json();
    if (j && j.error) throw new Error(j.error);
    return j;
  };
  const PART_ZH = { "メインストーリー 第二部": "主線 第二部", "メインストーリー 第一部": "主線 第一部", "メインストーリー 第一部オリジン": "第一部 起源", "イベントストーリー": "活動故事", "その他": "其他" };

  let CH = [], part = localStorage.getItem("mt_part") || "メインストーリー 第二部";
  const selected = new Set(JSON.parse(localStorage.getItem("mt_sel") || "[]"));
  let settings = {};

  // ------------------------------------------------------------ 章節樹
  async function loadChapters() {
    CH = await api("/api/chapters");
    const ORDER = ["メインストーリー 第二部", "メインストーリー 第一部", "メインストーリー 第一部オリジン", "イベントストーリー", "その他"];
    const parts = [...new Set(CH.map((c) => c.part))].sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b));
    if (!parts.includes(part)) part = parts[0];
    $("#parts").innerHTML = parts.map((p) => '<button data-p="' + esc(p) + '" class="' + (p === part ? "on" : "") + '">' + esc(PART_ZH[p] || p) + "</button>").join("");
    renderTree();
    fillReviewSelect();
  }
  function renderTree() {
    const open = new Set($$("details.ch[open]").map((d) => d.dataset.id));
    const cs = CH.filter((c) => c.part === part);
    $("#tree").innerHTML = cs.map((c) => {
      const ids = c.stories.map((s) => s.storyId);
      const n = ids.filter((i) => selected.has(i)).length;
      const zh = c.stories.filter((s) => s.zh).length;
      return '<details class="ch" data-id="' + c.id + '"' + (open.has(String(c.id)) ? " open" : "") + '><summary><input type="checkbox" class="cc" data-id="' + c.id + '"' + (n && n === ids.length ? " checked" : "") + '><span class="nm"' + (c.name_zh ? ' title="' + esc(c.name) + '"' : "") + '>' + esc(c.name_zh ? c.name_zh.replace("主線故事 ", "") : c.name.replace("メインストーリー ", "")) + '</span><span class="cnt">譯 ' + zh + "/" + ids.length + "</span></summary>" +
        c.stories.map((s) => '<div class="ep" data-sid="' + s.storyId + '"><input type="checkbox" class="ec" data-sid="' + s.storyId + '"' + (selected.has(s.storyId) ? " checked" : "") + '><span class="t" title="' + esc(s.title) + '">' + esc(s.subTitle) + "　" + esc(s.title_zh || s.title) + "</span>" +
          (s.warnings ? '<span class="chip warn">' + s.warnings + "警告</span>" : "") + (s.zh ? '<span class="chip zh">中文</span>' : "") + (s.html ? '<span class="chip html">HTML</span>' : "") + "</div>").join("") + "</details>";
    }).join("");
    $$(".cc").forEach((cb) => { const c = CH.find((x) => x.id == cb.dataset.id); const n = c.stories.filter((s) => selected.has(s.storyId)).length; cb.indeterminate = n > 0 && n < c.stories.length; });
    updateSel();
  }
  function updateSel() {
    localStorage.setItem("mt_sel", JSON.stringify([...selected]));
    $("#selCount").textContent = "已選 " + selected.size + " 話";
    const ids = [...selected];
    ["#bGloss", "#bTrans", "#bExport"].forEach((b) => ($(b).disabled = !ids.length));
    if (!ids.length) { $("#est").textContent = "請在左側勾選要翻譯的章節或話數。"; return; }
    clearTimeout(updateSel.t);
    updateSel.t = setTimeout(async () => {
      try {
        const e = await api("/api/estimate?ids=" + ids.join(","));
        const base = "已選 <b>" + ids.length + "</b> 話，約 " + e.lines.toLocaleString() + " 句、" + e.chars.toLocaleString() + " 個日文字。";
        if (e.provider === "anthropic") {
          const m = (settings.models || {})[settings.model] || {};
          $("#est").innerHTML = base + "使用 " + esc(m.label || settings.model) + " 估計費用約 <b>US$" + e.usd + "</b>（實際依 token 計算，僅供參考）。";
        } else {
          $("#est").innerHTML = base + "使用 <b>" + esc(e.engine) + "</b>（自架服務，不計 API 費用），預估約 " + Math.round(e.input_tokens / 1000) + "k 輸入／" + Math.round(e.output_tokens / 1000) + "k 輸出 tokens。";
        }
      } catch (err) { $("#est").textContent = "估算失敗：" + err.message; }
    }, 250);
  }
  $("#parts").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; part = b.dataset.p; localStorage.setItem("mt_part", part); loadChapters(); });
  $("#tree").addEventListener("change", (e) => {
    const t = e.target;
    if (t.classList.contains("cc")) {
      const c = CH.find((x) => x.id == t.dataset.id);
      c.stories.forEach((s) => (t.checked ? selected.add(s.storyId) : selected.delete(s.storyId)));
    } else if (t.classList.contains("ec")) {
      const id = +t.dataset.sid; t.checked ? selected.add(id) : selected.delete(id);
    }
    renderTree();
  });
  $("#tree").addEventListener("click", (e) => {
    if (e.target.closest("input")) return;
    const ep = e.target.closest(".ep");
    if (ep) { $("#rStory").value = ep.dataset.sid; showTab("review"); loadReview(); }
  });
  $("#selAll").addEventListener("change", (e) => {
    CH.filter((c) => c.part === part).forEach((c) => c.stories.forEach((s) => (e.target.checked ? selected.add(s.storyId) : selected.delete(s.storyId))));
    renderTree();
  });

  // ------------------------------------------------------------ 分頁
  function showTab(name) {
    $$(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
    $$(".tab").forEach((t) => t.classList.toggle("on", t.id === "tab-" + name));
    if (name === "gloss") loadGlossary();
  }
  $(".tabs").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) showTab(b.dataset.tab); });

  // ------------------------------------------------------------ 工作
  const jobLog = {};
  async function startJob(path, body) {
    try {
      const j = await api(path, body);
      showTab("jobs");
      if ($("#jobs .muted")) $("#jobs").innerHTML = "";
      const el = document.createElement("div"); el.className = "job"; el.id = "job-" + j.id;
      el.innerHTML = '<div class="hd"><span class="ttl">' + esc(j.title) + '</span><span class="state running">進行中</span><span class="cost muted"></span><button class="btn small danger">取消</button></div><div class="bar"><i></i></div><pre></pre>';
      $("#jobs").prepend(el);
      $("button", el).onclick = () => api("/api/jobs/" + j.id + "/cancel", {});
      jobLog[j.id] = 0; poll(j.id);
    } catch (e) { alert(e.message); }
  }
  async function poll(id) {
    const el = $("#job-" + id); if (!el) return;
    const j = await api("/api/jobs/" + id + "?since=" + jobLog[id]);
    jobLog[id] = j.logLen;
    const pre = $("pre", el);
    if (j.log.length) { pre.textContent += j.log.join("\n") + "\n"; pre.scrollTop = pre.scrollHeight; }
    const [d, n] = j.progress;
    $(".bar i", el).style.width = n ? (100 * d / n) + "%" : (j.state === "running" ? "30%" : "100%");
    $(".cost", el).textContent = (n ? d + "/" + n + " 話　" : "") + (j.cost ? "US$" + j.cost.toFixed(2) + "　" : "") + j.elapsed + " 秒";
    const st = $(".state", el);
    st.className = "state " + j.state;
    st.textContent = { running: "進行中", done: "完成", error: "錯誤", cancelled: "已取消" }[j.state];
    if (j.state === "running") setTimeout(() => poll(id), 1200);
    else { $("button", el).remove(); loadChapters(); if (j.kind !== "export") loadGlossary(); }
  }
  $("#bGloss").onclick = () => !settings.engine_ready ? (alert("翻譯引擎尚未設定完成：" + settings.engine_desc), showTab("settings")) : startJob("/api/glossary/build", { ids: [...selected], refs: settings.refs || "" });
  $("#bTrans").onclick = () => {
    if (!settings.engine_ready) { alert("翻譯引擎尚未設定完成：" + settings.engine_desc + "\n請到「設定」分頁設定。"); showTab("settings"); return; }
    if (!confirm("即將翻譯 " + selected.size + " 話。\n" + $("#est").textContent + "\n\n確定開始？")) return;
    startJob("/api/translate", { ids: [...selected], force: $("#optForce").checked, export: $("#optExport").checked });
  };
  $("#bExport").onclick = () => startJob("/api/export", { ids: [...selected] });

  // ------------------------------------------------------------ 名詞表
  let G = { terms: [] };
  async function loadGlossary() {
    G = await api("/api/glossary");
    const types = [...new Set(G.terms.map((t) => t.type))].sort();
    const cur = $("#gType").value;
    $("#gType").innerHTML = '<option value="">全部類型</option>' + types.map((t) => "<option" + (t === cur ? " selected" : "") + ">" + esc(t) + "</option>").join("");
    fillRuns();
    renderGlossary();
  }
  // 新增批次：同一次工作新增的名詞有相同的 added（工作開始時間）；舊資料沒有記錄
  function runsOf() {
    const n = {};
    G.terms.forEach((t) => { if (t.added) n[t.added] = (n[t.added] || 0) + 1; });
    return Object.keys(n).sort().reverse().map((k) => [k, n[k]]);
  }
  function fillRuns() {
    const cur = $("#gRun").value, runs = runsOf();
    const old = G.terms.filter((t) => !t.added).length;
    $("#gRun").innerHTML = '<option value="">全部（最近一批置頂）</option>' +
      runs.map(([k, n], j) => '<option value="' + esc(k) + '">' + (j ? "" : "最近一批：") + esc(k.slice(0, 16)) + "（" + n + " 筆）</option>").join("") +
      (old ? '<option value="-">未記錄時間（' + old + " 筆）</option>" : "");
    if ([...$("#gRun").options].some((o) => o.value === cur)) $("#gRun").value = cur;
  }
  function renderGlossary() {
    const q = $("#gSearch").value.trim(), ty = $("#gType").value, run = $("#gRun").value;
    const latest = (runsOf()[0] || [])[0];
    let rows = G.terms.map((t, i) => [t, i]).filter(([t]) => (!q || t.src.includes(q) || t.tgt.includes(q) || (t.note || "").includes(q)) && (!ty || t.type === ty) &&
      (!run || (run === "-" ? !t.added : t.added === run)));
    const row = ([t, i]) => '<tr data-i="' + i + '" class="' + (t.locked ? "locked" : "") + (latest && t.added === latest ? " new" : "") + '"><td><input type="text" data-k="src" value="' + esc(t.src) + '"></td><td><input type="text" data-k="tgt" value="' + esc(t.tgt) + '"></td><td><input type="text" data-k="type" value="' + esc(t.type) + '" style="width:5em"></td><td><input type="text" data-k="note" value="' + esc(t.note) + '"></td><td class="muted">' + esc(t.source || "") + '</td><td class="muted nowrap">' + esc((t.added || "").slice(0, 16)) + '</td><td><input type="checkbox" data-k="locked"' + (t.locked ? " checked" : "") + '></td><td><button class="btn small danger" data-del="' + i + '">刪</button></td></tr>';
    const head = (txt) => '<tr class="ghead"><td colspan="8">' + esc(txt) + "</td></tr>";
    if (!run && latest) {
      // 「全部」：最近一批集中在最上面，其餘照原本的順序
      const top = rows.filter(([t]) => t.added === latest), rest = rows.filter(([t]) => t.added !== latest);
      $("#gTable tbody").innerHTML = (top.length ? head("最近一批新增：" + latest.slice(0, 16) + "（" + top.length + " 筆）") + top.map(row).join("") : "") +
        (rest.length ? head("其他名詞（" + rest.length + " 筆）") + rest.map(row).join("") : "");
    } else {
      $("#gTable tbody").innerHTML = rows.map(row).join("");
    }
    const by = {}; G.terms.forEach((t) => (by[t.source || "?"] = (by[t.source || "?"] || 0) + 1));
    $("#gInfo").textContent = "共 " + G.terms.length + " 筆（" + Object.entries(by).map(([k, v]) => k + " " + v).join("、") + "）";
  }
  $("#gTable").addEventListener("input", (e) => {
    const tr = e.target.closest("tr"); if (!tr) return;
    const t = G.terms[+tr.dataset.i], k = e.target.dataset.k;
    if (k === "locked") t.locked = e.target.checked; else t[k] = e.target.value;
    if (k !== "locked" && t.source !== "使用者指定") { t.source = "使用者指定"; t.locked = true; }
    $("#gSave").textContent = "儲存名詞表 *";
  });
  $("#gTable").addEventListener("click", (e) => { const d = e.target.dataset.del; if (d !== undefined) { G.terms.splice(+d, 1); fillRuns(); renderGlossary(); $("#gSave").textContent = "儲存名詞表 *"; } });
  $("#gSearch").addEventListener("input", renderGlossary);
  $("#gType").addEventListener("change", renderGlossary);
  $("#gRun").addEventListener("change", renderGlossary);
  $("#gAdd").onclick = () => { G.terms.unshift({ src: "", tgt: "", type: "人名", note: "", source: "使用者指定", locked: true, added: new Date().toLocaleString("sv").slice(0, 19) }); fillRuns(); $("#gRun").value = ""; $("#gSearch").value = ""; renderGlossary(); $("#gTable input").focus(); };
  $("#gSave").onclick = async () => {
    G.terms = G.terms.filter((t) => t.src.trim());
    const r = await api("/api/glossary", { terms: G.terms });
    $("#gSave").textContent = "儲存名詞表"; $("#gInfo").textContent = "已儲存 " + r.count + " 筆";
  };

  // ------------------------------------------------------------ 校對
  function fillReviewSelect() {
    const cur = $("#rStory").value;
    $("#rStory").innerHTML = CH.filter((c) => c.part === part).map((c) => '<optgroup label="' + esc(c.name_zh || c.name) + '">' + c.stories.map((s) => '<option value="' + s.storyId + '">' + esc(s.subTitle + " " + (s.title_zh || s.title)) + (s.zh ? " ✓" : "") + "</option>").join("") + "</optgroup>").join("");
    if (cur) $("#rStory").value = cur;
  }
  let R = null;
  async function loadReview() {
    const id = $("#rStory").value; if (!id) return;
    R = await api("/api/story?id=" + id);
    $("#rTitle").value = R.title_zh || "";
    $("#rTitle").placeholder = R.title;
    renderReview();
  }
  function renderReview() {
    const warn = {}; R.warnings.forEach((w, k) => (warn[w.i] = (warn[w.i] || []).concat([[k, w]])));
    const only = $("#rWarnOnly").checked;
    const row = ([k, w]) => '<div class="wi"><span>' + esc(w.type + "：" + w.msg) + '</span><button class="btn tiny ok" data-act="accept" data-k="' + k + '" title="依照這則警告自動修改右邊的譯文並儲存">接受</button><button class="btn tiny" data-act="reject" data-k="' + k + '" title="刪除這則警告，譯文不變">拒絕</button></div>';
    $("#rList").innerHTML = R.lines.filter((l) => !only || warn[l.i]).map((l) => '<div class="line' + (warn[l.i] ? " warn" : "") + '" data-line="' + l.i + '"><div class="no">#' + l.i + '<br><a href="/output/player.html?id=' + R.id + "&at=" + l.i + '" target="_blank" rel="noopener">▶播放</a></div><div><div class="sp">' + esc(l.sp.join("・") || "旁白") + '</div><div class="ja">' + esc(l.ja) + '</div></div><div><textarea data-i="' + l.i + '">' + esc(l.i in edits ? edits[l.i] : l.zh) + "</textarea></div>" + (warn[l.i] ? '<div class="w">' + warn[l.i].map(row).join("") + "</div>" : "") + "</div>").join("");
    $("#rInfo").textContent = R.lines.length + " 句，" + R.lines.filter((l) => l.zh).length + " 句已有譯文，警告 " + R.warnings.length + (R.model ? "（" + R.model + "）" : "");
  }
  const edits = {};
  $("#rList").addEventListener("input", (e) => { if (e.target.dataset.i) { edits[e.target.dataset.i] = e.target.value; $("#rSave").textContent = "儲存修改 *"; } });
  // 警告的「接受」／「拒絕」：接受＝依建議修改該句並儲存；拒絕＝刪除警告（記住，之後重新驗證也不再出現），譯文不變
  $("#rList").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]"); if (!b) return;
    const w = R.warnings[+b.dataset.k]; if (!w) return;
    const box = b.closest(".wi");
    const body = { id: R.id, i: w.i, type: w.type, msg: w.msg, action: b.dataset.act };
    if (w.i in edits) body.text = edits[w.i];
    box.querySelectorAll("button").forEach((x) => (x.disabled = true));
    const note = document.createElement("span"); note.className = "muted"; note.textContent = b.dataset.act === "accept" ? " 修改中…" : " 處理中…"; box.appendChild(note);
    let r;
    try { r = await api("/api/warning", body); } catch (err) {
      note.className = "bad"; note.textContent = " " + err.message;
      box.querySelectorAll("button").forEach((x) => (x.disabled = false)); return;
    }
    const l = R.lines.find((x) => x.i === w.i); if (l) l.zh = r.text;
    delete edits[w.i];
    if (!Object.keys(edits).length) $("#rSave").textContent = "儲存修改";
    R.warnings = r.warnings;
    renderReview();
    const msg = b.dataset.act === "reject" ? "#" + w.i + " 已拒絕警告（譯文未變）"
      : "#" + w.i + " 已依警告修改並儲存（" + r.how + "）" + (r.resolved ? "" : "，但這則警告仍在，請手動檢查");
    $("#rInfo").textContent += "　" + msg + "（播放器開啟這一話時會自動更新）";
  });
  $("#rStory").addEventListener("change", loadReview);
  $("#rWarnOnly").addEventListener("change", () => R && renderReview());
  $("#rSave").onclick = async () => {
    const r = await api("/api/story", { id: R.id, title_zh: $("#rTitle").value, lines: edits });
    for (const k in edits) delete edits[k];
    $("#rSave").textContent = "儲存修改";
    await loadReview();
    $("#rInfo").textContent += "　已儲存（播放器開啟這一話時會自動更新）";
  };

  // ------------------------------------------------------------ 設定
  let prov = "anthropic";
  function showProv(p) {
    prov = p;
    $$("#sProv label").forEach((l) => l.classList.toggle("on", l.dataset.p === p));
    $$(".psec").forEach((d) => d.classList.toggle("on", d.dataset.p === p || (d.dataset.p === "local" && p !== "anthropic")));
    $("#connInfo").textContent = ""; $("#connInfo").className = "muted";
  }
  function fillModels(sel, models, current) {
    const ids = models.map((m) => m.id);
    if (current && !ids.includes(current)) models = [{ id: current, label: current + "（目前設定）" }].concat(models);
    sel.innerHTML = (models.length ? "" : '<option value="">（請先取得模型清單）</option>') + models.map((m) => '<option value="' + esc(m.id) + '">' + esc(m.label) + "</option>").join("");
    if (current) sel.value = current;
  }
  async function listModels(p) {
    const url = p === "ollama" ? $("#oUrl").value.trim() : $("#vUrl").value.trim();
    const key = p === "ollama" ? $("#oKey").value.trim() : $("#vKey").value.trim();
    const info = $("#connInfo");
    info.className = "muted"; info.textContent = "連線中…";
    const r = await api("/api/models?provider=" + p + "&url=" + encodeURIComponent(url) + (key ? "&key=" + encodeURIComponent(key) : ""));
    if (!r.ok) { info.className = "bad"; info.textContent = "連線失敗：" + r.error; return; }
    const sel = p === "ollama" ? $("#oModel") : $("#vModel");
    fillModels(sel, r.models, sel.value || (p === "ollama" ? settings.ollama_model : settings.vllm_model));
    info.className = "ok"; info.textContent = "連線成功（" + r.ms + " ms），找到 " + r.models.length + " 個模型。";
  }
  $("#setForm").addEventListener("click", (e) => { const b = e.target.closest("[data-list]"); if (b) listModels(b.dataset.list); });
  $("#sProv").addEventListener("change", (e) => { if (e.target.name === "prov") showProv(e.target.value); });

  async function loadSettings() {
    settings = await api("/api/settings");
    const p = $("#keyPill");
    p.textContent = "翻譯引擎：" + settings.engine_desc + (settings.provider === "anthropic" && settings.api_key_hint ? " " + settings.api_key_hint : "");
    p.className = "pill " + (settings.engine_ready ? "ok" : "bad");
    $("#sProv").innerHTML = Object.entries(settings.providers).map(([k, v]) => '<label data-p="' + k + '"><input type="radio" name="prov" value="' + k + '"' + (k === settings.provider ? " checked" : "") + "> " + esc(v) + "</label>").join("");
    showProv(settings.provider);
    $("#sModel").innerHTML = Object.entries(settings.models).map(([k, v]) => '<option value="' + k + '">' + esc(v.label) + "</option>").join("");
    $("#sModel").value = settings.model; $("#sEffort").value = settings.effort; $("#sWorkers").value = settings.workers;
    $("#oUrl").value = settings.ollama_url || ""; $("#vUrl").value = settings.vllm_url || "";
    $("#oKey").placeholder = settings.ollama_key_set ? "（已設定，留空不變）" : "（未設定）";
    $("#vKey").placeholder = settings.vllm_key_set ? "（已設定，留空不變）" : "（未設定）";
    fillModels($("#oModel"), [], settings.ollama_model); fillModels($("#vModel"), [], settings.vllm_model);
    $("#oManual").value = ""; $("#vManual").value = "";
    $("#oCtx").value = settings.num_ctx; $("#oThink").value = settings.ollama_think || "off"; $("#sTemp").value = settings.temperature; $("#sBatch").value = settings.batch_lines || 0;
    $("#sGame").value = settings.game_dir; $("#sRefs").value = settings.refs || ""; $("#sRules").value = settings.extra_rules || "";
  }
  $("#keyPill").onclick = () => showTab("settings");
  $("#setForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { provider: prov, model: $("#sModel").value, effort: $("#sEffort").value, workers: +$("#sWorkers").value || 3,
      game_dir: $("#sGame").value, refs: $("#sRefs").value, extra_rules: $("#sRules").value,
      ollama_url: $("#oUrl").value.trim(), ollama_model: $("#oManual").value.trim() || $("#oModel").value,
      vllm_url: $("#vUrl").value.trim(), vllm_model: $("#vManual").value.trim() || $("#vModel").value,
      num_ctx: +$("#oCtx").value || 16384, ollama_think: $("#oThink").value, temperature: parseFloat($("#sTemp").value) || 0.3, batch_lines: +$("#sBatch").value || 0 };
    if ($("#sKey").value.trim()) body.api_key = $("#sKey").value.trim();
    if ($("#oKey").value.trim()) body.ollama_key = $("#oKey").value.trim();
    if ($("#vKey").value.trim()) body.vllm_key = $("#vKey").value.trim();
    await api("/api/settings", body);
    ["#sKey", "#oKey", "#vKey"].forEach((k) => ($(k).value = ""));
    await loadSettings(); updateSel();
    $("#sInfo").textContent = "已儲存。目前引擎：" + settings.engine_desc;
  });

  loadSettings().then(loadChapters);
})();
