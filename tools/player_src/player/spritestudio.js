/* SpriteStudio 特效播放（資料由 tools/spritestudio.py 匯出：assets/ss/<名稱>/ss.js＋tex0.png…）
 * 每一格是已經算好世界矩陣的繪製清單 [零件, cell, a, b, c, d, e, f, 不透明度, (混色), (零件顏色)]（y 向上），
 * 混色 1＝加算；零件顏色 [運算, r, g, b, 比例]（運算 0＝混合、1＝加算、2＝減算、3＝乘算）。
 * 遮罩零件範圍內的「受遮罩影響」零件不顯示（文字由左到右出現）。
 * 畫成圖片的日文台詞（cell.txt）在中文模式下改用 Canvas 畫譯文，位置與每一行的寬度對齊原圖，讓遮罩的時機不變。 */
(function () {
  "use strict";
  const W = 1136, H = 640;
  const loading = {};

  function loadScript(src) {
    return new Promise((res, rej) => { const s = document.createElement("script"); s.src = src; s.onload = res; s.onerror = rej; document.head.appendChild(s); });
  }
  function loadImage(src) {
    return new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = src; });
  }
  function load(name, base) {
    if (!loading[name]) {
      loading[name] = (async () => {
        if (!(window.MERC_SS && window.MERC_SS[name])) await loadScript(base + "ss.js");
        const d = window.MERC_SS[name];
        const tex = Array.isArray(d.tex) ? d.tex : [d.tex];
        d.imgs = await Promise.all(tex.map((t) => loadImage(base + t)));
        for (const k in d.anims) d.anims[k].thruSet = new Set(d.anims[k].thru || []);
        return d;
      })();
      loading[name].catch(() => { delete loading[name]; });
    }
    return loading[name];
  }

  const FONT = '"Huninn", "RocknRoll One", "PingFang TC", sans-serif';
  const fontOf = (t) => (t.font ? t.font + ", " : "") + FONT;
  const weightOf = (t) => (t.weight ? t.weight + " " : "");

  function cellImage(d, cl) {
    const r = cl.r, cv = document.createElement("canvas");
    cv.width = Math.ceil(r[2]); cv.height = Math.ceil(r[3]);
    cv.getContext("2d").drawImage(d.imgs[cl.t || 0], r[0], r[1], r[2], r[3], 0, 0, r[2], r[3]);
    return cv;
  }

  // 用譯文重畫一個文字 cell：每一行對齊原圖該行墨跡的位置，字高相同，太寬就水平壓縮
  // txt.rot＝90：這個 cell 在貼圖裡是順時針轉 90° 存放的（行框是轉正後的座標）
  // txt.keep：保留原圖，只清掉每一行（或 txt.clear 指定的範圍）再寫字，用在字旁邊有花框的圖
  function textCanvas(d, cell, text) {
    const r = cell.r, t = cell.txt, boxes = t.lines || [];
    const cw = Math.ceil(r[2]), ch = Math.ceil(r[3]);
    const rot = t.rot === 90;
    const tw = rot ? ch : cw, th = rot ? cw : ch;     // 轉正後的寬高
    const tc = document.createElement("canvas");
    tc.width = tw; tc.height = th;
    const x = tc.getContext("2d");
    let lines = text.split("\n");
    let bx = boxes;
    if (lines.length !== boxes.length) {
      // 行數不同：依原圖行距平均排列
      const pitch = boxes.length > 1 ? (boxes[boxes.length - 1][2] - boxes[0][2]) / (boxes.length - 1) : (boxes.length ? (boxes[0][3] - boxes[0][2]) * 1.6 : 50);
      const ink = boxes.length ? boxes[0][3] - boxes[0][2] : 28;
      const top = (th - pitch * (lines.length - 1) - ink) / 2;
      const x0 = boxes.length ? Math.min(...boxes.map((b) => b[0])) : 0;
      const x1 = boxes.length ? Math.max(...boxes.map((b) => b[1])) : tw;
      bx = lines.map((_, i) => [x0, x1, top + i * pitch, top + i * pitch + ink]);
    }
    if (t.keep) {
      // 原圖（轉正）再清掉字的位置
      const src = cellImage(d, cell);
      if (rot) { x.save(); x.translate(0, th); x.rotate(-Math.PI / 2); x.drawImage(src, 0, 0); x.restore(); }
      else x.drawImage(src, 0, 0);
      for (const c of t.clear || boxes.map((b) => [b[0] - 3, b[2] - 3, b[1] + 3, b[3] + 3])) x.clearRect(c[0], c[1], c[2] - c[0], c[3] - c[1]);
    }
    // 對齊：和最左邊的行同一起點→靠左；和最右邊的行同一終點→靠右；其他置中
    const minL = Math.min(...bx.map((b) => b[0])), maxR = Math.max(...bx.map((b) => b[1]));
    const multi = bx.length > 1;
    x.fillStyle = t.color || "#ffffff";
    x.textAlign = "left"; x.textBaseline = "alphabetic";
    if (t.glow) { x.shadowColor = t.glowColor || t.color || "#ffffff"; x.shadowBlur = t.glow; }
    lines.forEach((ln, i) => {
      const b = bx[i]; if (!b || !ln) return;
      // 字的實際墨跡不能超出原圖這一行的範圍（遮罩只蓋住原圖的字），超出就縮小／水平壓縮
      // 上下左右各留 2 px：遮罩邊緣是半像素，字的反鋸齒邊緣碰到會露出一條淡線
      const ink = b[3] - b[2] - 4;
      let fs = ink / 0.88;
      x.font = weightOf(t) + fs + "px " + fontOf(t);
      let m = x.measureText(ln);
      const gh = m.actualBoundingBoxAscent + m.actualBoundingBoxDescent;
      if (gh > ink) { fs *= ink / gh; x.font = weightOf(t) + fs + "px " + fontOf(t); m = x.measureText(ln); }
      const cy = (b[2] + b[3]) / 2 + (m.actualBoundingBoxAscent - m.actualBoundingBoxDescent) / 2;
      // blur：只畫光暈（字本身畫在畫面外，用陰影做出模糊的字）
      const OFF = t.blur ? 10000 : 0;
      if (t.blur) { x.shadowColor = t.color || "#ffffff"; x.shadowBlur = t.blur; x.shadowOffsetX = OFF; }
      if (t.spread) {
        // 字平均分散在整行（原圖是字距很寬的一行字）
        const chars = [...ln], n = chars.length, step = (b[1] - b[0]) / n;
        chars.forEach((chh, k) => {
          const mm = x.measureText(chh);
          const cw2 = mm.actualBoundingBoxRight + mm.actualBoundingBoxLeft;
          const s2 = cw2 > step - 2 ? (step - 2) / cw2 : 1;
          x.save(); x.translate(b[0] + step * (k + 0.5) - OFF, cy); x.scale(s2, 1);
          x.fillText(chh, mm.actualBoundingBoxLeft - cw2 / 2, 0); x.restore();
        });
        return;
      }
      const left = -m.actualBoundingBoxLeft, gw = m.actualBoundingBoxRight + m.actualBoundingBoxLeft;
      const alignL = !multi || Math.abs(b[0] - minL) <= 4;
      const alignR = !alignL && Math.abs(b[1] - maxR) <= 4;
      // wrap＝"cell"：可以用到整個 cell 的寬度（沒有遮罩的特效）；否則限制在原本那一行的寬度
      let maxW = b[1] - b[0] - 4;
      if (t.wrap === "cell") maxW = alignL ? tw - b[0] - 4 : alignR ? b[1] - 4 : tw - 8;
      const sx = gw > maxW ? maxW / gw : 1;
      const w = gw * sx;
      const cx = alignL ? b[0] + 2 + w / 2 : alignR ? b[1] - 2 - w / 2 : (b[0] + b[1]) / 2;
      x.save();
      x.translate(Math.max(w / 2 + 2, Math.min(tw - w / 2 - 2, cx)) - OFF, cy);
      x.scale(sx, 1);
      x.fillText(ln, -(left + gw / 2), 0);
      x.restore();
    });
    if (!rot) return tc;
    // 轉回貼圖裡的存放方向（順時針 90°）
    const cv = document.createElement("canvas");
    cv.width = cw; cv.height = ch;
    const y = cv.getContext("2d");
    y.translate(cw, 0); y.rotate(Math.PI / 2); y.drawImage(tc, 0, 0);
    return cv;
  }

  // 零件顏色：把 cell 圖（原圖或譯文）染色後的結果快取起來
  function tinted(src, sr, col) {
    const [op, r, g, b, rate] = col;
    const cv = document.createElement("canvas");
    cv.width = Math.ceil(sr[2]); cv.height = Math.ceil(sr[3]);
    const x = cv.getContext("2d");
    x.drawImage(src, sr[0], sr[1], sr[2], sr[3], 0, 0, sr[2], sr[3]);
    const rgb = (k) => "rgb(" + Math.round(r * 255 * k) + "," + Math.round(g * 255 * k) + "," + Math.round(b * 255 * k) + ")";
    if (op === 0) {               // 混合：原色往指定色靠，比例 rate
      x.globalCompositeOperation = "source-atop"; x.globalAlpha = Math.min(1, rate); x.fillStyle = rgb(1); x.fillRect(0, 0, cv.width, cv.height);
    } else if (op === 3) {        // 乘算
      x.globalCompositeOperation = "multiply"; x.globalAlpha = Math.min(1, rate); x.fillStyle = rgb(1); x.fillRect(0, 0, cv.width, cv.height);
    } else if (op === 1) {        // 加算
      x.globalCompositeOperation = "lighter"; x.globalAlpha = 1; x.fillStyle = rgb(rate); x.fillRect(0, 0, cv.width, cv.height);
    } else {                      // 減算（近似）
      x.globalCompositeOperation = "difference"; x.globalAlpha = Math.min(1, rate); x.fillStyle = rgb(1); x.fillRect(0, 0, cv.width, cv.height);
    }
    // 還原原本的透明度
    x.globalAlpha = 1; x.globalCompositeOperation = "destination-in";
    x.drawImage(src, sr[0], sr[1], sr[2], sr[3], 0, 0, sr[2], sr[3]);
    return cv;
  }

  class SSPlayer {
    constructor(canvas) {
      this.cv = canvas; this.ctx = canvas.getContext("2d");
      this.tmp = document.createElement("canvas"); this.tmp.width = W; this.tmp.height = H;
      this.tctx = this.tmp.getContext("2d");
      this.cur = null; this.raf = 0; this.token = 0;
    }
    // info：{name, anim, pos, sc, n（播放次數，0＝無限）, delay}；opts：{base, tr, lang, playerName, toEnd}
    // 回傳這個動畫顯示的台詞 [{ja, zh}]（給對話紀錄用）
    async play(info, opts) {
      const tok = ++this.token;
      let d;
      try { d = await load(info.name, opts.base); } catch (e) { console.warn("SpriteStudio 特效載入失敗：", info.name, e); this.stop(); return []; }
      if (tok !== this.token) return [];
      const a = d.anims[info.anim];
      if (!a) { console.warn("找不到 SpriteStudio 動畫：", info.name, info.anim); this.stop(); return []; }
      this.cur = { d, a, info, t0: performance.now(), toEnd: !!opts.toEnd, cells: [], tint: new Map() };
      await this.setText(opts.tr || {}, opts.lang, opts.playerName);
      if (tok !== this.token) return [];
      this.cv.classList.add("on");
      cancelAnimationFrame(this.raf);
      this.cur.t0 = performance.now();
      const loop = () => {
        if (!this.cur || tok !== this.token) return;
        const f = this.frameAt(performance.now());
        this.draw(f.i);
        if (!f.done) this.raf = requestAnimationFrame(loop);
      };
      loop();
      // 這個動畫顯示的台詞
      const seen = new Set(), out = [];
      for (const fr of a.f) for (const e of fr) {
        const c = d.cells[e[1]];
        if (c.txt && !seen.has(c.txt.text)) { seen.add(c.txt.text); out.push({ ja: c.txt.text, zh: (opts.tr || {})[c.txt.text] || "" }); }
      }
      return out;
    }
    async setText(tr, lang, playerName) {
      const c = this.cur; if (!c) return;
      c.tr = tr; c.lang = lang;
      const name = (s) => s.replace(/<name>/g, playerName || "");
      const want = c.d.cells.map((cl) => cl.txt && lang !== "ja" && tr[cl.txt.text] ? name(tr[cl.txt.text]) : null);
      if (want.some(Boolean) && document.fonts && document.fonts.load) {
        const fams = new Set(['30px "Huninn"']);
        c.d.cells.forEach((cl, i) => { if (want[i] && cl.txt.font) fams.add(weightOf(cl.txt) + "30px " + cl.txt.font.split(",")[0].trim()); });
        const s = want.filter(Boolean).join("");
        try { await Promise.all([...fams].map((f) => document.fonts.load(f, s))); } catch (e) { /* 字型載入失敗就用後備字型 */ }
      }
      if (this.cur !== c) return;
      c.cells = want.map((t, i) => (t ? textCanvas(c.d, c.d.cells[i], t) : null));
      c.tint = new Map();
      if (!this.raf || this.frameAt(performance.now()).done) this.draw(this.frameAt(performance.now()).i);
    }
    frameAt(now) {
      const c = this.cur, a = c.a, N = a.n, times = c.info.n | 0;
      if (c.toEnd) return { i: N - 1, done: true };
      const t = (now - c.t0) / 1000 - (c.info.delay || 0);
      const f = Math.max(0, Math.floor(t * a.fps * (c.info.rate || 1)));
      if (times <= 0) return { i: f % N, done: false };
      if (f >= N * times) return { i: N - 1, done: true };
      return { i: f % N, done: false };
    }
    remaining() {
      const c = this.cur; if (!c || c.toEnd) return 0;
      const times = c.info.n | 0; if (times <= 0) return 0;
      const end = c.t0 + ((c.info.delay || 0) + c.a.n * times / (c.a.fps * (c.info.rate || 1))) * 1000;
      return Math.max(0, end - performance.now());
    }
    // 遮罩：不透明度是門檻，遮罩貼圖的 alpha 大於門檻的地方才遮住（不透明度 0＝整個 cell 都遮住；
    // 漸層的遮罩貼圖配合不透明度變化，就是由一側慢慢擦出來的效果）
    maskImage(d, ci, thr) {
      const c = this.cur, q = Math.round(Math.max(0, Math.min(1, thr)) * 64);
      const k = "m" + ci + "|" + q;
      let cv = c.tint.get(k);
      if (cv) return cv;
      const cl = d.cells[ci], r = cl.r;
      cv = cellImage(d, cl);
      const x = cv.getContext("2d"), im = x.getImageData(0, 0, cv.width, cv.height), px = im.data;
      const lim = q / 64 * 255;
      for (let i = 3; i < px.length; i += 4) { const on = px[i] > lim || (q === 0 && px[i] > 0); px[i - 3] = px[i - 2] = px[i - 1] = 0; px[i] = on ? 255 : 0; }
      x.putImageData(im, 0, 0);
      c.tint.set(k, cv);
      return cv;
    }
    finish() { if (this.cur) { this.cur.toEnd = true; this.draw(this.cur.a.n - 1); } }
    stop() {
      this.token++; cancelAnimationFrame(this.raf); this.raf = 0; this.cur = null;
      this.ctx.setTransform(1, 0, 0, 1, 0, 0); this.ctx.clearRect(0, 0, W, H);
      this.cv.classList.remove("on");
    }
    draw(fi) {
      const c = this.cur; if (!c) return;
      const { d, a, info } = c, g = this.ctx, fr = a.f[fi] || [];
      const kx = (info.sc ? info.sc[0] : 1) * 2, ky = (info.sc ? info.sc[1] : 1) * 2;
      const tx = W / 2 + (info.pos ? info.pos[0] : 0) * 2, ty = H / 2 - (info.pos ? info.pos[1] : 0) * 2;
      // 螢幕 = S × 世界 × 翻轉（cell 以貼圖的 y 向下座標繪製）
      const setT = (x, e) => x.setTransform(kx * e[2], -ky * e[3], -kx * e[4], ky * e[5], kx * e[6] + tx, -ky * e[7] + ty);
      const drawCell = (x, e) => {
        const ci = e[1], cl = d.cells[ci], r = cl.r, p = cl.p, alt = c.cells[ci];
        let src = alt || d.imgs[cl.t || 0];
        let sr = alt ? [0, 0, alt.width, alt.height] : r;
        const col = e[10];
        if (col) {
          const k = ci + "|" + col.join(",");
          let tc = c.tint.get(k);
          if (!tc) { tc = tinted(src, sr, col); c.tint.set(k, tc); }
          src = tc; sr = [0, 0, tc.width, tc.height];
        }
        x.drawImage(src, sr[0], sr[1], sr[2], sr[3], -p[0], -p[1], r[2], r[3]);
      };
      const blendOf = (e) => (e[9] === 1 ? "lighter" : "source-over");
      g.setTransform(1, 0, 0, 1, 0, 0); g.globalAlpha = 1; g.globalCompositeOperation = "source-over"; g.clearRect(0, 0, W, H);
      const masks = fr.filter((e) => d.parts[e[0]].mask);
      for (const e of fr) {
        if (d.parts[e[0]].mask) continue;
        if (masks.length && !a.thruSet.has(e[0])) {
          const t = this.tctx;
          t.setTransform(1, 0, 0, 1, 0, 0); t.globalAlpha = 1; t.globalCompositeOperation = "source-over"; t.clearRect(0, 0, W, H);
          t.globalAlpha = e[8]; setT(t, e); drawCell(t, e);
          t.globalAlpha = 1; t.globalCompositeOperation = "destination-out";
          for (const m of masks) { const cl = d.cells[m[1]]; setT(t, m); t.drawImage(this.maskImage(d, m[1], m[8]), -cl.p[0], -cl.p[1], cl.r[2], cl.r[3]); }
          t.globalCompositeOperation = "source-over";
          g.setTransform(1, 0, 0, 1, 0, 0); g.globalAlpha = 1; g.globalCompositeOperation = blendOf(e); g.drawImage(this.tmp, 0, 0);
        } else {
          g.globalAlpha = e[8]; g.globalCompositeOperation = blendOf(e); setT(g, e); drawCell(g, e);
        }
      }
      g.setTransform(1, 0, 0, 1, 0, 0); g.globalAlpha = 1; g.globalCompositeOperation = "source-over";
    }
  }

  window.MercSS = { load, SSPlayer };
})();
