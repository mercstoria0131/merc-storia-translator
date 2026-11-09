/* メルクストーリア 故事播放器（重現原版 StoryModal 的操作方式）
 * 資料：data/<id>.js → MERC.load({...})；立繪格位：data/layout.js → ACTRESS_LAYOUT */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const qs = new URLSearchParams(location.search);
  const storyId = qs.get("id");
  const store = {
    get(k, d) { try { const v = localStorage.getItem("merc_" + k); return v === null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("merc_" + k, v); } catch (e) { /* ignore */ } },
  };

  // ------------------------------------------------------------------ 版面縮放
  const stage = $("#stage");
  function fit() {
    const s = Math.min(innerWidth / 1136, innerHeight / 640);
    stage.style.transform = "scale(" + s + ")";
  }
  addEventListener("resize", fit); fit();

  // ------------------------------------------------------------------ 狀態
  let D = null;           // 故事資料
  let idx = -1;           // 目前場景
  let lang = store.get("lang", "zh");
  let autoLv = 0, autoTimer = null;   // オート：0＝關閉，1～3＝箭頭數（越多越快）
  let typing = null;      // {timer, done()}
  let busy = false;       // 效果播放中
  let started = false;
  const logItems = [];
  const playerName = () => store.get("player_name", lang === "ja" ? "ユウ" : "優");

  const SLOT_X = { left: 568 - 240, center: 568, right: 568 + 240 };
  const EMO = { "哀": "💧", "汗1": "💦", "汗2": "💦", "疑問": "❓", "怒": "💢", "喜": "♪", "驚1": "❗", "驚2": "‼", "眠": "💤", "ぐるぐる": "🌀", "花": "🌸", "ハート": "❤", "うきうき": "♪" };

  // ------------------------------------------------------------------ 載入
  window.MERC = { load(doc) { D = doc; init(); } };
  if (!storyId) { location.href = "index.html"; return; }
  const sc = document.createElement("script");
  sc.src = "data/" + encodeURIComponent(storyId) + ".js";
  sc.onerror = () => {
    // 找不到這一話（例如尚未匯出）：顯示訊息，點畫面或スキップ都回到目錄
    $("#title .chap").textContent = "找不到故事資料（ID " + storyId + "）";
    $("#title .ep").textContent = "這一話可能尚未匯出";
    $("#title .tap").textContent = "點擊返回目錄";
    $("#title").classList.remove("loading");
  };
  $("#title .ring").classList.add("spin");    // 劇本資料第一次開啟時要先建立：進度未知，先轉圈
  const exported = new Set(((window.MERC_CATALOG) || []).flatMap((c) => c.stories.map((s) => String(s.id))));
  document.body.appendChild(sc);

  function init() {
    if (!D.translated && lang === "zh") lang = "ja";
    document.title = (D.title_zh || D.title) + "｜梅露可物語";
    $("#title .chap").textContent = (lang !== "ja" && D.chapter.name_zh ? D.chapter.name_zh : D.chapter.name) + "　" + D.subTitle.replace("第", "第 ").replace("話", " 話");
    $("#title .ep").textContent = lang !== "ja" && D.title_zh ? D.title_zh : D.title;
    updateLangBtn();
    preload(0, 6);
    loadAll();
  }

  // 點選故事後先把這一話用到的素材全部讀好（伺服器播放時才從遊戲檔解碼），讀完才出現「點擊開始」
  function storyAssets() {
    if (D.pre) return D.pre.filter((p) => !p.startsWith("movie/"));
    const set = new Set();     // 舊版劇本資料沒有 pre：從場景推算
    for (const s of D.scenes) {
      if (s.bg) set.add("bg/" + s.bg + ".jpg");
      for (const k in s.ch) { set.add("chara/" + s.ch[k].tex + ".webp"); if (s.ch[k].ftex) set.add("chara/" + s.ch[k].ftex + ".webp"); }
      if (s.bgm && s.bgm.name) set.add("bgm/" + s.bgm.name + ".mp3");
      for (const x of s.se || []) if (x.name) set.add("se/" + x.name + ".mp3");
    }
    return [...set];
  }
  function loadAll() {
    const list = storyAssets(), total = list.length, ring = $("#title .ring");
    let done = 0, next = 0, finished = false;
    ring.classList.remove("spin");
    const show = () => {
      const r = total ? done / total : 1;
      $("#title .fgc").style.strokeDashoffset = String(276.46 * (1 - r));
      $("#title .pct").textContent = Math.floor(r * 100) + "%";
    };
    const finish = () => { if (!finished) { finished = true; $("#title").classList.remove("loading"); } };
    const one = (p) => new Promise((res) => {
      const u = "assets/" + p;
      if (/\.(jpg|webp|png)$/.test(p)) {
        const im = loadImg(u);
        if (im.complete && im.naturalWidth) return res();
        im.addEventListener("load", res, { once: true }); im.addEventListener("error", res, { once: true });
      } else fetch(u).then((r) => r.arrayBuffer()).then(res, res);
    });
    const pump = () => {
      if (next >= total) return;
      one(list[next++]).then(() => { done++; show(); if (done >= total) finish(); else pump(); });
    };
    show();
    if (!total) return finish();
    for (let k = 0; k < 6; k++) pump();
    setTimeout(finish, 90000);     // 保險：讀太久時仍讓人開始
  }

  // ------------------------------------------------------------------ 素材
  const imgCache = {};
  function preload(from, n) {
    for (let i = from; i < Math.min(D.scenes.length, from + n); i++) {
      const s = D.scenes[i];
      if (s.bg) loadImg("assets/bg/" + s.bg + ".jpg");
      for (const k in s.ch) { loadImg(texUrl(s.ch[k].tex)); if (s.ch[k].ftex) loadImg(texUrl(s.ch[k].ftex)); }
      for (const t of s.tl || []) if (window.MercTimeline) MercTimeline.preload(t.name, tlBase(t.name));
      for (const t of s.ss || []) if (window.MercSS) MercSS.load(t.name, ssBase(t.name)).catch(() => {});
    }
  }
  function loadImg(u) { if (!imgCache[u]) { const im = new Image(); im.src = u; imgCache[u] = im; } return imgCache[u]; }
  const texUrl = (t) => "assets/chara/" + t + ".webp";
  const tlBase = (n) => "assets/timeline/" + encodeURIComponent(n) + "/";
  const ssBase = (n) => "assets/ss/" + encodeURIComponent(n) + "/";

  // ------------------------------------------------------------------ 音訊
  const audio = { bgm: null, bgmName: "", se: {}, unlocked: false };
  function fadeVol(el, to, sec, then) {
    const from = el.volume, t0 = performance.now(), ms = Math.max(1, sec * 1000);
    (function step(now) {
      const p = Math.min(1, (now - t0) / ms);
      el.volume = from + (to - from) * p;
      if (p < 1) requestAnimationFrame(step); else if (then) then();
    })(t0);
  }
  function playBgm(b) {
    if (!b) return;
    if (!b.name) { stopBgm(b.fo || 0.5); return; }
    if (b.name === audio.bgmName && audio.bgm) return;
    stopBgm(b.fo || 0.3);
    const el = new Audio("assets/bgm/" + b.name + ".mp3");
    el.loop = true; el.volume = b.fi ? 0 : bgmVol();
    el.play().catch(() => {});
    if (b.fi) fadeVol(el, bgmVol(), b.fi);
    audio.bgm = el; audio.bgmName = b.name;
  }
  function stopBgm(sec) {
    const el = audio.bgm; if (!el) return;
    audio.bgm = null; audio.bgmName = "";
    fadeVol(el, 0, sec || 0.3, () => el.pause());
  }
  const bgmVol = () => parseFloat(store.get("bgm_vol", "0.6"));
  const seVol = () => parseFloat(store.get("se_vol", "0.8"));
  function playSe(x) {
    if (!x.name) return;
    const cur = audio.se[x.name];
    if (x.type === "Stop") { if (cur) { cur.pause(); delete audio.se[x.name]; } return; }
    if (x.type === "FadeOut") { if (cur) { fadeVol(cur, 0, x.fo || 1, () => cur.pause()); delete audio.se[x.name]; } return; }
    setTimeout(() => {
      const el = new Audio("assets/se/" + x.name + ".mp3");
      el.volume = seVol();
      if (x.n <= 0) { el.loop = true; audio.se[x.name] = el; }
      else {
        let left = x.n - 1;
        el.addEventListener("ended", () => { if (left-- > 0) setTimeout(() => { el.currentTime = 0; el.play().catch(() => {}); }, (x.iv || 0) * 1000); });
      }
      el.play().catch(() => {});
      if (x.fi) { el.volume = 0; fadeVol(el, seVol(), x.fi); }
    }, (x.delay || 0) * 1000);
  }
  function stopAllSe() { for (const k in audio.se) audio.se[k].pause(); audio.se = {}; }

  // ------------------------------------------------------------------ 背景
  let bgFront = $("#bgA"), bgBack = $("#bgB"), curBg = null;
  function setBg(name, slow) {
    if (name === curBg) return;
    curBg = name;
    const b = bgBack;
    b.style.transition = "opacity " + (slow ? 1.0 : 0.35) + "s";
    if (name) { b.src = "assets/bg/" + name + ".jpg"; } else { b.removeAttribute("src"); }
    b.classList.add("on");
    bgFront.style.transition = b.style.transition;
    bgFront.classList.remove("on");
    bgBack = bgFront; bgFront = b;
  }

  // ------------------------------------------------------------------ 立繪
  const slots = {};
  document.querySelectorAll(".slot").forEach((el) => { slots[el.dataset.slot] = { el, node: null, cur: null }; });
  const layoutOf = (type) => ACTRESS_LAYOUT[type] || ACTRESS_LAYOUT.Default;

  function makeActress(c) {
    const L = layoutOf(c.type);
    const [bx, by, bw, bh] = L.body;
    const node = document.createElement("div");
    node.className = "actress";
    node.style.width = bw + "px"; node.style.height = bh + "px";
    const body = document.createElement("div");
    body.className = "body";
    body.style.cssText = "left:0;top:0;width:" + bw + "px;height:" + bh + "px;background-image:url(" + texUrl(c.tex) + ");background-position:" + (-bx) + "px " + (-by) + "px";
    const face = document.createElement("div");
    face.className = "face";
    face.style.width = face.style.height = L.face + "px";
    node.append(body, face);
    return node;
  }

  function placeActress(slot, node, c) {
    const L = layoutOf(c.type);
    const [, , bw, bh] = L.body;
    const cy = c.type === "Navi" ? 320 : 400;          // Default/Scout 子物件 y=-80
    const x = SLOT_X[slot] + (c.off ? c.off[0] : 0);
    const y = cy - (c.off ? c.off[1] : 0);
    const s = c.sc && c.sc[0] ? c.sc[0] : 1;
    node.style.left = (x - bw / 2) + "px";
    node.style.top = (y - bh / 2) + "px";
    node.style.transform = s !== 1 ? "scale(" + s + ")" : "";
  }

  function setFace(node, c) {
    const L = layoutOf(c.type);
    const face = node.querySelector(".face");
    const slotPos = L.slots[c.expr];
    const src = c.ftex || c.tex;
    if (!slotPos) {                       // 通常：使用身體本來的臉（或另一張臉圖的通常臉）
      if (c.ftex) {
        face.style.display = "block";
        face.style.backgroundImage = "url(" + texUrl(c.ftex) + ")";
        face.style.backgroundPosition = (-(L.body[0] + c.fp[0])) + "px " + (-(L.body[1] + c.fp[1])) + "px";
        face.style.left = c.fp[0] + "px"; face.style.top = c.fp[1] + "px";
      } else face.style.display = "none";
      return;
    }
    face.style.display = "block";
    face.style.backgroundImage = "url(" + texUrl(src) + ")";
    face.style.backgroundPosition = (-slotPos[0]) + "px " + (-slotPos[1]) + "px";
    face.style.left = c.fp[0] + "px"; face.style.top = c.fp[1] + "px";
  }

  function renderChars(s, animate) {
    const hide = s.fx === "Still" || s.fx === "FadeStill";
    for (const name of ["left", "center", "right"]) {
      const S = slots[name];
      const c = hide ? null : s.ch[name];
      if (!c) {
        if (S.node) { const n = S.node; S.node = null; S.cur = null; n.style.opacity = "0"; setTimeout(() => n.remove(), 300); }
        continue;
      }
      let node = S.node;
      if (!node || !S.cur || S.cur.tex !== c.tex || S.cur.type !== c.type || S.cur.ftex !== c.ftex) {
        if (node) node.remove();
        node = makeActress(c);
        S.el.appendChild(node); S.node = node;
        placeActress(name, node, c);
        if (animate) {
          let dx = 0;
          if (c.ap === "RightIn") dx = 720; else if (c.ap === "LeftIn") dx = -720;
          node.style.opacity = "0";
          if (dx) node.style.marginLeft = dx + "px";
          requestAnimationFrame(() => requestAnimationFrame(() => { node.style.transition += ",margin-left .45s"; node.style.opacity = "1"; node.style.marginLeft = "0"; }));
        }
      } else placeActress(name, node, c);
      setFace(node, c);
      node.classList.toggle("off", !c.on);
      if (animate && c.emo && c.emo !== "None" && (!S.cur || S.cur.emo !== c.emo)) showEmotion(node, c);
      if (animate && c.ap === "Out") { node.style.opacity = "0"; }
      else if (animate && c.ap === "RightOut") { node.style.marginLeft = "720px"; node.style.opacity = "0"; }
      else if (animate && c.ap === "LeftOut") { node.style.marginLeft = "-720px"; node.style.opacity = "0"; }
      S.cur = c;
    }
  }

  function showEmotion(node, c) {
    const L = layoutOf(c.type);
    const e = document.createElement("div");
    e.className = "emo";
    e.textContent = EMO[c.emo] || "";
    e.style.left = (c.fp[0] + L.face * 0.85) + "px";
    e.style.top = (c.fp[1] - 10) + "px";
    node.appendChild(e);
    node.classList.remove("hop"); void node.offsetWidth; node.classList.add("hop");
    setTimeout(() => e.remove(), 1300);
  }

  // ------------------------------------------------------------------ 天氣等特效素材（SpriteStudio 效果以 CSS 近似）
  let curWeather = "";
  function setWeather(keys) {
    const k = (keys || []).join("|");
    if (k === curWeather) return;
    curWeather = k;
    const w = $("#weather"); w.innerHTML = "";
    const add = (cls) => { const d = document.createElement("div"); d.className = cls; w.appendChild(d); };
    if (/黒い.*霧|闇|瘴気/.test(k)) add("fog dark"); else if (/霧|靄|煙/.test(k)) add("fog");
    if (/雨/.test(k)) add("rain");
    if (/吹雪/.test(k)) add("snow storm"); else if (/雪/.test(k)) add("snow");
    if (/炎|火/.test(k)) add("fire");
    if (/風/.test(k)) add("wind");
    if (/光|キラ|粒子|パウダー|星/.test(k)) add("sparkle");
  }

  // ------------------------------------------------------------------ SpriteStudio 特效（player/spritestudio.js）
  // 畫成圖片的台詞：中文模式改畫譯文；顯示的台詞也記到對話紀錄
  const ssp = window.MercSS ? new MercSS.SSPlayer($("#ssfx")) : null;
  const healp = window.MercSS ? new MercSS.SSPlayer($("#healfx")) : null;   // 畫面效果 Heal（癒術）
  function setSS(s, toEnd) {
    if (!ssp) return;
    const info = s.ss && s.ss[0];
    if (!info) { ssp.stop(); return; }
    // 原版依特效的深度決定前後：z > 0 在背景與角色之間，其餘在角色前面
    const cv = $("#ssfx");
    if ((info.z || 0) > 0) $("#chars").before(cv); else $("#weather").after(cv);
    const tr = (D.ss_tr || {})[info.name] || {};
    ssp.play(info, { base: ssBase(info.name), tr, lang, playerName: playerName(), toEnd }).then((texts) => {
      if (toEnd) return;
      for (const t of texts) {
        const show = lang === "ja" || !t.zh ? t.ja : t.zh;
        logItems.push({ n: "", t: show.replace(/<name>/g, playerName()), j: lang !== "ja" && t.zh ? t.ja : "" });
      }
    });
  }

  // ------------------------------------------------------------------ 文字
  function esc(t) { return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function richText(t) {
    t = (t || "").replace(/\r\n?/g, "\n").replace(/<name>/g, playerName());
    // NGUI 色碼 [RRGGBB]...[-]
    const out = []; let color = null;
    const re = /\[([0-9a-fA-F]{6})\]|\[-\]/g; let last = 0, m;
    const push = (s) => { for (const ch of s) out.push({ ch, color }); };
    while ((m = re.exec(t))) { push(t.slice(last, m.index)); color = m[1] ? "#" + m[1] : null; last = re.lastIndex; }
    push(t.slice(last));
    return out;
  }
  function speakerName(s) {
    const names = lang === "ja" ? s.sp : (s.sp_zh && s.sp_zh.length ? s.sp_zh : s.sp);
    return names.filter((n) => n && n !== "‐" && n !== "-").map((n) => n.replace(/<name>/g, playerName())).join("・");
  }
  // 沒有台詞的場景原文是空的「」，不顯示
  const blank = (t) => !t || /^\s*[「『（(]?\s*[」』）)]?\s*$/.test(t);
  function sceneText(s) {
    const t = lang === "ja" || !s.text_zh ? s.text : s.text_zh;
    return blank(t) ? "" : t;
  }

  function showText(s) {
    const box = $("#text");
    box.className = s.size === "Middle" ? "middle" : s.size === "Big" ? "big" : "";
    // 沒有台詞的場景（例如只看背景或影片前的空白「」）：收起訊息框與 BACK／NEXT，只留右下的「下一步」箭頭
    const empty = !sceneText(s);
    $("#window").className = (s.win === "Shout" ? "shout" : "default") + (empty ? " empty" : "");
    const chars = richText(sceneText(s));
    box.innerHTML = "";
    const spans = [];
    for (const c of chars) {
      if (c.ch === "\n") { box.appendChild(document.createElement("br")); continue; }
      const sp = document.createElement("span");
      sp.className = "ch"; sp.textContent = c.ch;
      if (c.color) sp.style.color = c.color;
      box.appendChild(sp); spans.push(sp);
    }
    if (lang === "both" && s.text_zh && !blank(s.text)) {
      const j = document.createElement("span"); j.className = "ja";
      j.textContent = richText(s.text).map((c) => c.ch).join("").replace(/\n/g, " ");
      box.appendChild(j);
    }
    // 名字牌
    const name = empty ? "" : speakerName(s);
    const plate = $("#nameplate");
    if (name) {
      let x = 568;
      for (const k of ["left", "center", "right"]) {
        const c = s.ch[k];
        if (c && (s.spk || s.sp).includes(c.key)) { x = SLOT_X[k] + (c.off ? c.off[0] : 0); break; }
      }
      plate.style.left = x + "px";
      plate.querySelector("span").textContent = name;
      plate.classList.add("on");
    } else plate.classList.remove("on");
    // 打字效果
    $("#nextIcon").classList.remove("on");
    let i = 0;
    const speed = parseInt(store.get("text_speed", "28"), 10);
    const finish = () => { clearInterval(t); spans.forEach((x) => x.classList.add("v")); typing = null; $("#nextIcon").classList.add("on"); scheduleAuto(chars.length); };
    if (typing) typing.finish();
    typing = { finish: () => finish() };
    const t = setInterval(() => { if (i < spans.length) spans[i++].classList.add("v"); else finish(); }, Math.max(speed, 1));
    if (s.all || speed <= 0) finish();
  }

  // ------------------------------------------------------------------ 特效
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  function fadeTo(color, alpha, sec) {
    const f = $("#fade");
    f.style.background = (color || "#000000").replace(/^##/, "#");
    f.style.transition = "opacity " + sec + "s";
    f.style.opacity = alpha;
    return wait(sec * 1000);
  }
  function retrigger(el, cls) { el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls); }

  async function playEffect(s, render) {
    const ep = s.ep || {};
    switch (s.fx) {
      case "Fade":
      case "FadeStill":
        await fadeTo(ep.color, 1, ep.fadeOut || 0.3);
        render();
        if (s.tl && s.tl.length) await prepareTimeline(s.tl[0]);   // 畫面蓋住時先載入演出
        await wait((ep.fadeWait || 0.2) * 1000);
        await fadeTo(ep.color, 0, ep.fadeIn || 0.3);
        return;
      case "Wipe": {
        const w = $("#wipe"); w.style.display = "block";
        const bars = w.querySelectorAll("div");
        bars.forEach((b, i) => { b.style.transition = "none"; b.style.left = "1136px"; });
        void w.offsetWidth;
        bars.forEach((b, i) => { b.style.transition = "left .45s ease-in " + (i * 0.05) + "s"; b.style.left = "-232px"; });
        await wait(650); render();
        bars.forEach((b, i) => { b.style.transition = "left .45s ease-out " + (i * 0.05) + "s"; b.style.left = "-1700px"; });
        await wait(700); w.style.display = "none";
        return;
      }
      case "CrossFade":
        render(true); return;
      case "Shake": render(); retrigger($("#shake"), "shake"); return;
      case "Earthquake": render(); retrigger($("#shake"), "quake"); return;
      case "Attack": render(); retrigger($("#shake"), "shake"); await fadeTo("#ff2a2a", 0.45, 0.08); await fadeTo("#ff2a2a", 0, 0.3); return;
      case "Heal": {
        // 原版 StoryHealEffectView：全畫面 80% 黑底（Background (Overlay80)），畫面中央以 0.5 倍播放
        // SpriteStudio 特效 effect_iyashi 的動畫「1」，播放速度 0.7 倍（AnimationTimeRate）
        render(); await wait((ep.delay || 0) * 300);
        await fadeTo("#000000", 0.8, ep.fadeIn || 0.1);
        if (healp) {
          await healp.play({ name: "effect_iyashi", anim: "1", pos: [0, 0], sc: [0.5, 0.5], n: 1, delay: 0, rate: 0.7 },
                           { base: ssBase("effect_iyashi"), lang, playerName: playerName() });
          await wait(healp.remaining());
          healp.stop();
        } else await wait(1400);
        await fadeTo("#000000", 0, ep.fadeOut || 0.1); return;
      }
      case "Summon":
        render(); await wait((ep.delay || 0) * 300);
        await fadeTo("#ffffff", 0.7, 0.25); await fadeTo("#ffffff", 0, 0.6); return;
      case "Scroll": {
        render();
        const im = bgFront; im.style.transition = "none"; im.style.objectPosition = ep.move === "Up" ? "50% 100%" : "50% 0%";
        await wait((ep.scrollStartDelay || 0) * 1000);
        im.style.transition = "object-position " + (ep.duration || 3) + "s linear"; im.style.objectPosition = ep.move === "Up" ? "50% 0%" : "50% 100%";
        return;
      }
      case "Movie": {
        const v = $("#movie"); const name = ep.movieAssetName;
        if (!name) { playBgm(s.bgm); render(); return; }
        v.src = "assets/movie/" + name; v.style.display = "block";
        stopBgm(0.5);
        // 原版：播放中點畫面會暫停並詢問「この動画をスキップしますか？」
        const ask = $("#mskip");
        await new Promise((res) => {
          v.onended = res; v.onerror = res;
          v.onclick = (e) => {
            e.stopPropagation(); v.pause();
            ask.querySelector(".msg").textContent = lang === "ja" ? "この動画をスキップしますか？" : "要略過這段影片嗎？";
            ask.classList.add("on");
          };
          ask.querySelector(".yes").onclick = (e) => { e.stopPropagation(); ask.classList.remove("on"); res(); };
          ask.querySelector(".no").onclick = (e) => { e.stopPropagation(); ask.classList.remove("on"); v.play().catch(res); };
          v.play().catch(res);
        });
        v.pause(); v.removeAttribute("src"); v.load(); v.style.display = "none";
        playBgm(s.bgm);
        render(); return;
      }
      default:
        render();
        if (s.tl && s.tl.length) await prepareTimeline(s.tl[0]);
    }
  }

  // ------------------------------------------------------------------ Timeline 演出（player/timeline.js）
  // 原版 StoryTimelineComponent：UI 標記 WaitTap 停住等點擊、NextScene 結束後進下一個場景；台詞以淡入淡出顯示在畫面下方
  let curTL = null, curTLInfo = null, tlPlaying = false, tlAsking = false;
  const tlLine = (t) => (t || "").replace(/\r\n?/g, "\n").replace(/\n+$/, "").replace(/<name>/g, playerName());
  function rgba(c, a) { return c ? "rgba(" + Math.round(c.r * 255) + "," + Math.round(c.g * 255) + "," + Math.round(c.b * 255) + "," + (a == null ? c.a : a) + ")" : ""; }
  function tlText(dir, p) {
    const el = $("#tltext");
    if (dir !== "in") { el.style.transition = "opacity " + (p.fadeOutSec || 0.5) + "s"; el.classList.remove("on"); return; }
    const ja = p.text, zh = curTLInfo && curTLInfo.tr ? curTLInfo.tr[ja] : "";
    const show = lang === "ja" || !zh ? ja : zh;
    el.innerHTML = esc(tlLine(show)) + (lang === "both" && zh ? '<span class="ja">' + esc(tlLine(ja).replace(/\n/g, " ")) + "</span>" : "");
    const pos = p.position || { x: 0, y: -110 };
    el.style.top = (320 - pos.y * 2) + "px";
    el.style.left = (pos.x * 2) + "px";
    el.style.color = rgba(p.color) || "#fff";
    // 原版 TextMeshPro 外框：用平滑的描邊（只畫在字外側），不用多方向硬陰影（會在字邊形成色塊）
    el.style.setProperty("--tl-outline", rgba(p.outlineColor, 0.85) || "rgba(70,56,47,.85)");
    el.style.transition = "opacity " + (p.fadeInSec || 0.5) + "s";
    el.classList.add("on");
    logItems.push({ n: "", t: tlLine(show), j: lang !== "ja" && zh ? tlLine(ja) : "" });
  }
  async function prepareTimeline(info) {
    if (curTL && !curTL.done && curTLInfo && curTLInfo.name === info.name) { curTLInfo = info; return true; }   // 同一個演出跨多個場景：沿用
    if (curTL) { curTL.stop(); curTL = null; }
    curTLInfo = info;
    try {
      curTL = await MercTimeline.create({
        name: info.name, base: tlBase(info.name), canvas: $("#tl"), textEl: $("#tltext"),
        tr: info.tr || {}, lang, playerName: playerName(),
        hooks: {
          text: tlText,
          bgm: (p) => { if (p.bgmEvent === 2) stopBgm(p.fadeSec || 1); else if (p.bgmEvent === 1 && p.inBgmId) playBgm({ name: "B-" + p.inBgmId, fi: p.fadeSec || 0 }); },
          se: (name) => playSe({ name, type: "Play", n: 1 }),
          wait: () => { if (autoLv) setTimeout(() => { if (curTL && autoLv && !tlAsking) curTL.tap(); }, AUTO_WAIT[autoLv][0]); },
        },
      });
      $("#tl").classList.add("on");
      return true;
    } catch (e) {
      console.warn("Timeline 無法播放：", info.name, e);
      curTL = null; curTLInfo = null;
      return false;
    }
  }
  function stopTimeline(keepFrame) {
    if (curTL) { curTL.stop(); curTL = null; }
    curTLInfo = null; tlPlaying = false; $("#tlskip").classList.remove("on");
    $("#tltext").classList.remove("on");
    if (!keepFrame) $("#tl").classList.remove("on");
    $("#ui").classList.remove("tl");
  }
  // 播放（或從暫停處繼續）場景上的 Timeline，直到下一個交給故事處理的事件：
  // "waittext"（WaitText：演出停在畫面上，故事顯示接下來的訊息框）、"next"／"end"（演出結束）、"skip"
  async function runTimelines(s) {
    let why = "end";
    for (let k = 0; k < s.tl.length; k++) {
      if (!(await prepareTimeline(s.tl[k]))) continue;
      tlPlaying = true; $("#ui").classList.add("tl"); $("#tlskip").classList.add("on");
      why = await curTL.play();
      tlPlaying = false; $("#ui").classList.remove("tl"); $("#tlskip").classList.remove("on");
      if (why === "waittext" || why === "skip") return why;
      if (k + 1 < s.tl.length) { curTL.stop(); curTL = null; }
    }
    return why;
  }
  const hasTL = (s, name) => (s.tl || []).some((t) => t.name === name);
  // 播放中點畫面：只有在等待點擊（WaitTap）時才繼續；略過演出用右上角的 SKIP 按鈕
  function tlTap() {
    if (!curTL || !tlPlaying || tlAsking) return;
    curTL.tap();
  }
  function tlAskSkip() {
    if (!curTL || !tlPlaying || tlAsking) return;
    const was = curTL.paused; curTL.paused = true; tlAsking = true;
    const ask = $("#mskip");
    ask.querySelector(".msg").textContent = lang === "ja" ? "この演出をスキップしますか？" : "要略過這段演出嗎？";
    ask.classList.add("on");
    const close = () => { ask.classList.remove("on"); tlAsking = false; ask.querySelector(".yes").onclick = ask.querySelector(".no").onclick = null; };
    ask.querySelector(".yes").onclick = (e) => { e.stopPropagation(); close(); if (curTL) curTL.finish("skip"); };
    ask.querySelector(".no").onclick = (e) => { e.stopPropagation(); close(); if (curTL) curTL.paused = was; };
  }

  // ------------------------------------------------------------------ 進行
  // laterSS：畫面被淡入淡出蓋住時先不播 SpriteStudio 特效，等畫面亮回來再播（不然短的特效會在蓋住時就播完）
  function stateAt(i, animate, slowBg, laterSS) {
    const s = D.scenes[i];
    if (curTL && curTLInfo && !hasTL(s, curTLInfo.name)) stopTimeline();
    setBg(s.bg, slowBg || s.fx === "CrossFade");
    renderChars(s, animate);
    setWeather(s.fxa);   // 由 SpriteStudio 播放器重現的特效已在匯出時從 fxa 去掉，其餘用 CSS 近似
    if (laterSS) { if (ssp) ssp.stop(); }
    else setSS(s, !animate);         // 往回翻頁：直接顯示動畫最後一格
  }

  async function go(i, opts) {
    opts = opts || {};
    if (i < 0) return;
    if (i >= D.scenes.length) { finishStory(); return; }
    clearTimeout(autoTimer);
    const s = D.scenes[i];
    idx = i;
    preload(i + 1, 4);
    if (!opts.back) {
      if (s.fx !== "Movie") playBgm(s.bgm);   // Movie：影片播完才換 BGM
      (s.se || []).forEach(playSe);
    } else if (s.bgm) playBgm(s.bgm);
    $("#btnBack").classList.toggle("off", i === 0);
    busy = true;
    if (opts.back || s.fx === "None") stateAt(i, !opts.back);
    else {
      const laterSS = !!s.ss && (s.fx === "Fade" || s.fx === "FadeStill");
      await playEffect(s, (slow) => stateAt(i, true, slow, laterSS));
      if (laterSS && idx === i) setSS(s, false);
    }
    const tlName = s.tl && s.tl.length ? s.tl[0].name : null;
    const sameTL = tlName && curTL && curTLInfo && curTLInfo.name === tlName;
    if (tlName && !opts.back && (s.wt || !sameTL)) {
      // Timeline 演出：waitTarget 場景開始或繼續播放；WaitText 時演出停在畫面上，故事接著顯示訊息框；
      // NextScene／播完 → 下一個場景；略過 → 跳到這段演出之後的第一個場景
      const why = await runTimelines(s);
      busy = false;
      if (idx !== i || !started) return;
      if (why === "skip") {
        let j = i + 1;
        while (j < D.scenes.length && hasTL(D.scenes[j], tlName)) j++;
        stopTimeline();
        go(j);
      } else go(i + 1);
      return;
    }
    busy = false;
    showText(s);
    if (!opts.back) addLog(s);
  }

  function next() {
    if (curTL && tlPlaying) { tlTap(); return; }
    if (!started || busy) return;
    if (typing) { typing.finish(); return; }
    go(idx + 1);
  }
  function back() { if (!started || busy || idx <= 0) return; go(idx - 1, { back: true }); }
  // 按住不放時的連續前進：打字中直接顯示全文並進到下一句（不用按兩下）
  function fastNext() {
    if (!started || busy || $("#end").classList.contains("on")) return;
    if (typing) typing.finish();
    go(idx + 1);
  }

  // 按住 BACK／NEXT：先動作一次，0.4 秒後開始連續動作，而且越按越快（最快每秒約 16 次）
  function holdRepeat(btn, first, repeat) {
    let timer = null, delay = 0;
    const stop = () => { clearTimeout(timer); timer = null; };
    btn.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      try { btn.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      stop(); first();
      delay = 400;
      const step = () => { repeat(); delay = Math.max(60, delay * 0.75); timer = setTimeout(step, delay); };
      timer = setTimeout(step, delay);
    });
    ["pointerup", "pointercancel", "lostpointercapture"].forEach((ev) => btn.addEventListener(ev, stop));
    btn.addEventListener("click", (e) => e.stopPropagation());   // 動作已在 pointerdown 執行
  }

  function addLog(s) {
    const t = sceneText(s);
    if (!t) return;
    logItems.push({ n: speakerName(s), t: richText(t).map((c) => c.ch).join(""), j: lang !== "ja" && s.text_zh ? richText(s.text).map((c) => c.ch).join("") : "" });
  }
  function openLog() {
    const list = $("#log .list"); list.innerHTML = "";
    for (const it of logItems) {
      const d = document.createElement("div"); d.className = "item";
      d.innerHTML = (it.n ? '<div class="n">' + esc(it.n) + "</div>" : "") + '<div class="t">' + esc(it.t) + "</div>" + (it.j && lang === "both" ? '<div class="j">' + esc(it.j) + "</div>" : "");
      list.appendChild(d);
    }
    $("#log").classList.add("on"); list.scrollTop = list.scrollHeight;
  }

  // 每一段速度：文字顯示完後等待「基本毫秒＋每字毫秒×字數」
  const AUTO_WAIT = [null, [2600, 90], [1500, 55], [700, 28]];
  function scheduleAuto(n) {
    clearTimeout(autoTimer);
    if (!autoLv) return;
    const w = AUTO_WAIT[autoLv];
    // 特效動畫（例如文字逐行出現）播完才計時
    autoTimer = setTimeout(() => { if (autoLv && !typing && !busy) go(idx + 1); }, w[0] + n * w[1] + (ssp ? ssp.remaining() : 0));
  }
  function setAuto(lv) {
    autoLv = lv;
    const b = $("#btnAuto");
    b.classList.remove("on", "lv1", "lv2", "lv3");
    if (lv) b.classList.add("on", "lv" + lv);
    b.setAttribute("aria-label", lv ? "オート ×" + lv : "オート");
    const ind = $("#autoInd");
    ind.classList.remove("lv1", "lv2", "lv3");
    ind.classList.toggle("on", !!lv);
    if (lv) ind.classList.add("lv" + lv);
    if (lv && !typing) scheduleAuto(0); else clearTimeout(autoTimer);
  }
  const cycleAuto = () => setAuto((autoLv + 1) % 4);   // ▶ → ▶▶ → ▶▶▶ → 關閉

  function finishStory() {
    if (curTL) { const t = curTL; curTL = null; curTLInfo = null; tlPlaying = false; $("#tlskip").classList.remove("on"); t.stop(); $("#ui").classList.remove("tl"); $("#tltext").classList.remove("on"); }
    setAuto(0);
    stopAllSe();
    stopBgm(1.5);
    const n = $("#endNext"), note = $("#endNote");
    const nx = D && D.next ? String(D.next) : "";
    // catalog.js 列出已匯出的話；沒有 catalog 時仍顯示按鈕（載入失敗會再提示）
    const ok = nx && (!exported.size || exported.has(nx));
    if (ok) { n.href = "player.html?id=" + nx; n.classList.remove("hide"); } else n.classList.add("hide");
    note.textContent = !D ? "找不到這一話的故事資料" : nx && !ok ? "下一話尚未匯出，請先用翻譯器匯出" : !nx ? "本章已全部結束" : "";
    $("#end").classList.add("on");
  }

  function updateLangBtn() {
    const b = $("#btnLang");
    b.textContent = lang === "ja" ? "日" : lang === "both" ? "雙" : "中";
    b.style.display = D && D.translated ? "" : "none";
  }

  // ------------------------------------------------------------------ 輸入
  $("#title").addEventListener("click", () => {
    if ($("#title").classList.contains("loading")) return;     // 素材還在讀取
    if (!D) { location.href = "index.html"; return; }
    $("#title").classList.add("gone");
    started = true;
    const at = Math.max(0, Math.min(D.scenes.length - 1, parseInt(qs.get("at") || "0", 10) || 0));
    go(at);
  });
  $("#stage").addEventListener("click", (e) => {
    if (e.target.closest("button,#log,#end,#title,a")) return;
    if ($("#ui").classList.contains("hidden")) { $("#ui").classList.remove("hidden"); return; }
    if ($("#menu").classList.contains("open")) { $("#menu").classList.remove("open"); return; }
    next();
  });
  $("#tlskip").addEventListener("click", (e) => { e.stopPropagation(); tlAskSkip(); });
  holdRepeat($("#btnNext"), next, fastNext);
  holdRepeat($("#btnBack"), back, back);
  $("#btnMenu").addEventListener("click", () => $("#menu").classList.toggle("open"));
  $("#btnSkip").addEventListener("click", () => { $("#menu").classList.remove("open"); finishStory(); });
  $("#btnLog").addEventListener("click", () => { $("#menu").classList.remove("open"); openLog(); });
  $("#btnAuto").addEventListener("click", cycleAuto);
  $("#btnHide").addEventListener("click", () => { $("#menu").classList.remove("open"); $("#ui").classList.add("hidden"); });
  $("#btnLang").addEventListener("click", () => {
    lang = lang === "zh" ? "both" : lang === "both" ? "ja" : "zh";
    store.set("lang", lang); updateLangBtn();
    if (idx >= 0) showText(D.scenes[idx]);
    if (ssp && ssp.cur) ssp.setText(ssp.cur.tr, lang, playerName());
  });
  $("#log .close").addEventListener("click", () => $("#log").classList.remove("on"));
  addEventListener("keydown", (e) => {
    if ($("#log").classList.contains("on")) { if (e.key === "Escape") $("#log").classList.remove("on"); return; }
    if (!started) {
      if (e.key === "Enter" || e.key === " ") $("#title").click();
      else if (e.key === "Escape" && !D) location.href = "index.html";
      return;
    }
    if (e.key === "Enter" || e.key === " " || e.key === "ArrowRight") { e.preventDefault(); e.repeat ? fastNext() : next(); }
    else if (e.key === "ArrowLeft") back();
    else if (e.key === "a" || e.key === "A") cycleAuto();
    else if (e.key === "l" || e.key === "L") openLog();
    else if (e.key === "h" || e.key === "H") $("#ui").classList.toggle("hidden");
  });
  addEventListener("wheel", (e) => { if (started && e.deltaY < 0 && !$("#log").classList.contains("on")) openLog(); });
})();
