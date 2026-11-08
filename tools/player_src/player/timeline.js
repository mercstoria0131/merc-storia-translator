/* Story Timeline（Unity Timeline 演出）重現：讀 assets/timeline/<名稱>/tl.js（tools/timeline.py 匯出）
 * 以 WebGL 繪製 prefab 的物件階層，依 Timeline 軌道、Animator、粒子與 UI/BGM 標記播放。
 * 座標：正交攝影機，畫面中心為原點，可見 568 × 320 單位，z 越小越靠前。 */
(function () {
  "use strict";
  const VW = 568, VH = 320;

  // ------------------------------------------------------------------ 數學
  function qmul(a, b) {
    return [a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
      a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
      a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
      a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2]];
  }
  function qeuler(x, y, z) {        // Unity：Quaternion.Euler = Y * X * Z
    const r = Math.PI / 360;
    const qx = [Math.sin(x * r), 0, 0, Math.cos(x * r)], qy = [0, Math.sin(y * r), 0, Math.cos(y * r)], qz = [0, 0, Math.sin(z * r), Math.cos(z * r)];
    return qmul(qmul(qy, qx), qz);
  }
  function qnorm(q) { const l = Math.hypot(q[0], q[1], q[2], q[3]) || 1; return [q[0] / l, q[1] / l, q[2] / l, q[3] / l]; }
  // TRS → 3×4（列優先：[r00 r01 r02 tx | r10 r11 r12 ty | r20 r21 r22 tz]）
  function trs(p, q, s) {
    const [x, y, z, w] = q;
    const xx = x * x, yy = y * y, zz = z * z, xy = x * y, xz = x * z, yz = y * z, wx = w * x, wy = w * y, wz = w * z;
    return [
      (1 - 2 * (yy + zz)) * s[0], 2 * (xy - wz) * s[1], 2 * (xz + wy) * s[2], p[0],
      2 * (xy + wz) * s[0], (1 - 2 * (xx + zz)) * s[1], 2 * (yz - wx) * s[2], p[1],
      2 * (xz - wy) * s[0], 2 * (yz + wx) * s[1], (1 - 2 * (xx + yy)) * s[2], p[2]];
  }
  function mmul(a, b) {
    const o = new Array(12);
    for (let r = 0; r < 3; r++) {
      for (let c = 0; c < 3; c++) o[r * 4 + c] = a[r * 4] * b[c] + a[r * 4 + 1] * b[4 + c] + a[r * 4 + 2] * b[8 + c];
      o[r * 4 + 3] = a[r * 4] * b[3] + a[r * 4 + 1] * b[7] + a[r * 4 + 2] * b[11] + a[r * 4 + 3];
    }
    return o;
  }
  const xf = (m, x, y, z) => [m[0] * x + m[1] * y + m[2] * z + m[3], m[4] * x + m[5] * y + m[6] * z + m[7], m[8] * x + m[9] * y + m[10] * z + m[11]];
  const clamp = (v, a, b) => v < a ? a : v > b ? b : v;
  const lerp = (a, b, t) => a + (b - a) * t;

  // 可重現的亂數（粒子用）
  function rng(seed) {
    let s = (seed >>> 0) || 1;
    return () => { s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; };
  }

  // ------------------------------------------------------------------ 曲線
  function bsearch(keys, t) {       // 最後一個 keys[i][0] <= t
    let lo = 0, hi = keys.length - 1;
    if (t < keys[0][0]) return -1;
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (keys[m][0] <= t) lo = m; else hi = m - 1; }
    return lo;
  }
  function evalCurve(cv, t) {
    if (cv.k) {
      const k = cv.k; if (!k.length) return null;
      let i = bsearch(k, t); if (i < 0) i = 0;
      const e = k[i], dt = Math.max(0, t - e[0]);
      if (e[0] < -1e8) return e[4];
      return ((e[1] * dt + e[2]) * dt + e[3]) * dt + e[4];
    }
    if (cv.dense) {
      const d = cv.dense, f = (t - d.t0) * d.rate, n = d.v.length;
      if (f <= 0) return d.v[0];
      if (f >= n - 1) return d.v[n - 1];
      const i = Math.floor(f); return lerp(d.v[i], d.v[i + 1], f - i);
    }
    if (cv.step) { const i = bsearch(cv.step, t); return cv.step[Math.max(0, i)][1]; }
    return cv.const;
  }
  // MinMaxCurve：mode 0 常數、1 曲線、2 兩曲線、3 兩常數
  function mm(c, t, r) {
    if (!c) return 0;
    const s = (arr) => { const f = clamp(t, 0, 1) * (arr.length - 1), i = Math.floor(f); return i >= arr.length - 1 ? arr[arr.length - 1] : lerp(arr[i], arr[i + 1], f - i); };
    switch (c.mode) {
      case 1: return c.s * s(c.c);
      case 2: return c.s * lerp(s(c.cm), s(c.c), r);
      case 3: return lerp(c.mn, c.s, r);
      default: return c.s;
    }
  }
  function grad(g, t) {
    if (!g) return [1, 1, 1, 1];
    const pick = (keys, n) => {
      if (!keys.length) return n === 3 ? [1, 1, 1] : [1];
      const tt = clamp(t, 0, 1);
      let a = keys[0], b = keys[keys.length - 1];
      for (let i = 0; i < keys.length - 1; i++) if (keys[i][n] <= tt && tt <= keys[i + 1][n]) { a = keys[i]; b = keys[i + 1]; break; }
      if (tt <= keys[0][n]) a = b = keys[0];
      if (tt >= keys[keys.length - 1][n]) a = b = keys[keys.length - 1];
      const d = b[n] - a[n], u = d > 0 ? (tt - a[n]) / d : 0;
      const out = []; for (let i = 0; i < n; i++) out.push(lerp(a[i], b[i], u)); return out;
    };
    const c = pick(g.c, 3), a = pick(g.a, 1);
    return [c[0], c[1], c[2], a[0]];
  }
  function mmgrad(c, t, r, r2) {
    if (!c) return [1, 1, 1, 1];
    switch (c.mode) {
      case 1: return grad(c.gmax, t);
      case 2: return c.min.map((v, i) => lerp(v, c.max[i], r));
      case 3: { const a = grad(c.gmin, t), b = grad(c.gmax, t); return a.map((v, i) => lerp(v, b[i], r)); }
      case 4: return grad(c.gmax, r2);
      default: return c.max.slice();
    }
  }

  // ------------------------------------------------------------------ WebGL
  const VS = `#version 300 es
in vec2 aPos; in vec2 aUV; out vec2 vUV;
void main(){ vUV = aUV; gl_Position = vec4(aPos, 0.0, 1.0); }`;
  const FS = `#version 300 es
precision mediump float;
in vec2 vUV; out vec4 o;
uniform sampler2D uTex, uRule, uNoise;
uniform vec4 uColor; uniform int uMode; uniform float uProgress, uSat, uOver;
void main(){
  vec4 c = texture(uTex, vUV);
  if (uMode == 1) {                 // ArtisticFade：依規則圖與雜訊做擦除式淡入
    float r = texture(uRule, vUV).r + (texture(uNoise, vUV).r - 0.5) * 0.15;
    float e = 0.12;
    c *= clamp((uProgress * (1.0 + 2.0 * e) - e - r) / e + 0.5, 0.0, 1.0);
  }
  c *= uColor;
  if (uSat != 1.0) { float g = dot(c.rgb, vec3(0.299, 0.587, 0.114)); c.rgb = mix(vec3(g), c.rgb, uSat); }
  if (uOver > 0.0) c.rgb *= 2.0;     // overlay 近似：dst × 2s
  o = c;
}`;

  class GL {
    constructor(canvas) {
      const gl = canvas.getContext("webgl2", { premultipliedAlpha: true, alpha: true, antialias: true });
      if (!gl) throw new Error("瀏覽器不支援 WebGL2");
      this.gl = gl;
      const sh = (t, s) => { const x = gl.createShader(t); gl.shaderSource(x, s); gl.compileShader(x); if (!gl.getShaderParameter(x, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(x)); return x; };
      const p = gl.createProgram();
      gl.attachShader(p, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
      this.p = p; gl.useProgram(p);
      this.u = {};
      for (const n of ["uTex", "uRule", "uNoise", "uColor", "uMode", "uProgress", "uSat", "uOver"]) this.u[n] = gl.getUniformLocation(p, n);
      gl.uniform1i(this.u.uTex, 0); gl.uniform1i(this.u.uRule, 1); gl.uniform1i(this.u.uNoise, 2);
      this.buf = gl.createBuffer(); this.ibuf = gl.createBuffer();
      const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buf);
      const aPos = gl.getAttribLocation(p, "aPos"), aUV = gl.getAttribLocation(p, "aUV");
      gl.enableVertexAttribArray(aPos); gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 16, 0);
      gl.enableVertexAttribArray(aUV); gl.vertexAttribPointer(aUV, 2, gl.FLOAT, false, 16, 8);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.ibuf);
      gl.enable(gl.BLEND);
      this.white = this.texFromPixels(new Uint8Array([255, 255, 255, 255]), 1, 1);
      // Unity 預設粒子貼圖：柔邊圓點
      const n = 64, px = new Uint8Array(n * n * 4);
      for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
        const d = Math.hypot(x - n / 2 + 0.5, y - n / 2 + 0.5) / (n / 2), a = Math.round(255 * clamp(1 - d, 0, 1) ** 1.5);
        px.set([a, a, a, a], (y * n + x) * 4);
      }
      this.dot = this.texFromPixels(px, n, n);
    }
    texFromPixels(px, w, h) {
      const gl = this.gl, t = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, px);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      return t;
    }
    texFromImage(img) {
      const gl = this.gl, t = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      return t;
    }
    // RenderTexture 攝影機用的畫布（大小同主畫面）
    target(w, h) {
      const gl = this.gl, tex = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      const fb = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      return { fb, tex, w, h };
    }
    bind(t, bg) {       // t = null → 主畫面
      const gl = this.gl;
      gl.bindFramebuffer(gl.FRAMEBUFFER, t ? t.fb : null);
      gl.viewport(0, 0, t ? t.w : gl.canvas.width, t ? t.h : gl.canvas.height);
      const c = bg || [0, 0, 0, 0];
      gl.clearColor(c[0] * c[3], c[1] * c[3], c[2] * c[3], c[3]); gl.clear(gl.COLOR_BUFFER_BIT);
    }
    clear() { this.bind(null); }
    blend(mode) {
      const gl = this.gl;
      switch (mode) {
        case "add": gl.blendFunc(gl.ONE, gl.ONE); break;
        case "mul": case "overlay": gl.blendFunc(gl.DST_COLOR, gl.ONE_MINUS_SRC_ALPHA); break;
        case "screen": gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_COLOR); break;
        default: gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      }
    }
    // verts: [x,y,u,v,...]（x,y 為世界座標）；idx：三角形索引
    draw(verts, idx, d) {
      const gl = this.gl;
      const v = this.view || [0, 0];
      for (let i = 0; i < verts.length; i += 4) { verts[i] = (verts[i] - v[0]) / (VW / 2); verts[i + 1] = (verts[i + 1] - v[1]) / (VH / 2); }
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buf); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(verts), gl.STREAM_DRAW);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.ibuf); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(idx), gl.STREAM_DRAW);
      this.blend(d.blend);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, d.tex || this.white);
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, d.rule || this.white);
      gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, d.noise || this.white);
      const c = d.color, a = c[3];
      gl.uniform4f(this.u.uColor, c[0] * a, c[1] * a, c[2] * a, a);
      gl.uniform1i(this.u.uMode, d.mode || 0);
      gl.uniform1f(this.u.uProgress, d.progress == null ? 1 : d.progress);
      gl.uniform1f(this.u.uSat, d.sat == null ? 1 : d.sat);
      gl.uniform1f(this.u.uOver, d.blend === "overlay" ? 1 : 0);
      gl.drawElements(gl.TRIANGLES, idx.length, gl.UNSIGNED_SHORT, 0);
    }
  }

  // 文字貼圖：outline＝白字深色描邊、mind＝米黃字寬字距、glow＝橘色光暈
  // S.tbox：原圖中文字所佔範圍（比例）；字高、垂直位置與左右留白依原圖，寬度依譯文長度
  function textCanvas(text, S) {
    const style = S.style, H = 128;
    const tb = S.tbox || [0.05, 0.15, 0.95, 0.85];
    const ink = Math.max(0.2, tb[3] - tb[1]);
    const fs = Math.round(H * ink / (style === "glow" ? 1.15 : 0.98));
    const cy = H * (tb[1] + tb[3]) / 2;
    const aspect = S.iw && S.ih ? S.iw / S.ih : 4;
    const pad = Math.round(Math.min(tb[0], 1 - tb[2]) * aspect * H);
    const c = document.createElement("canvas"), x = c.getContext("2d");
    const font = "bold " + fs + 'px "Huninn","RocknRoll One","PingFang TC","Hiragino Maru Gothic ProN",sans-serif';
    x.font = font;
    const chars = Array.from(text), sp = style === "mind" || style === "glow" ? fs * 0.38 : fs * 0.02;
    const ws = chars.map((ch) => x.measureText(ch).width);
    const tw = ws.reduce((a, b) => a + b, 0) + sp * Math.max(0, chars.length - 1);
    // 上下各留邊：描邊與光暈不會被畫布切掉（c.rowTop／c.rowH 記錄原圖那一行在畫布中的位置）
    const mv = Math.ceil(fs * (style === "glow" ? 0.6 : 0.3));
    c.width = Math.ceil(tw + pad * 2); c.height = H + mv * 2;
    c.rowTop = mv; c.rowH = H;
    x.font = font; x.textBaseline = "middle"; x.lineJoin = "round";
    const each = (fn) => { let px = pad; chars.forEach((ch, k) => { fn(ch, px); px += ws[k] + sp; }); };
    if (style === "glow") {
      x.shadowColor = "rgba(255,160,50,1)"; x.shadowBlur = fs * 0.45; x.fillStyle = "rgba(255,190,90,.8)";
      for (let k = 0; k < 2; k++) each((ch, px) => x.fillText(ch, px, cy + mv));
      return c;
    }
    x.strokeStyle = style === "mind" ? "#5b3a1f" : "#3b2a22";
    x.lineWidth = fs * (style === "mind" ? 0.16 : 0.2);
    each((ch, px) => x.strokeText(ch, px, cy + mv));
    x.fillStyle = style === "mind" ? "#fff0c0" : "#ffffff";
    each((ch, px) => x.fillText(ch, px, cy + mv));
    return c;
  }

  // ------------------------------------------------------------------ 粒子
  class Emitter {
    constructor(ps, seed) { this.ps = ps; this.seed = seed; this.reset(); }
    reset() { this.t = 0; this.parts = []; this.acc = 0; this.r = rng(this.seed * 7919 + 13); this.bursted = new Set(); this.prewarmed = false; }
    spawn(n, M, scale) {
      const ps = this.ps, r = this.r, sh = ps.shape;
      const tn = ps.dur > 0 ? (this.t % ps.dur) / ps.dur : 0;
      for (let k = 0; k < n && this.parts.length < (ps.max || 1000); k++) {
        let p = [0, 0, 0], d = [0, 0, 1];
        if (sh.on) {
          const ang = (r() * sh.arc) * Math.PI / 180;
          switch (sh.type) {
            case 0: case 1: { const u = r() * 2 - 1, th = r() * Math.PI * 2, s = Math.sqrt(1 - u * u); d = [s * Math.cos(th), s * Math.sin(th), u]; const rr = sh.r * (sh.type === 1 ? 1 : (1 - sh.thick * r())); p = d.map((v) => v * rr); break; }
            case 2: case 3: { const u = r(), th = r() * Math.PI * 2, s = Math.sqrt(1 - u * u); d = [s * Math.cos(th), s * Math.sin(th), u]; p = d.map((v) => v * sh.r * (1 - sh.thick * r())); break; }
            case 4: case 7: case 8: case 9: {           // 圓錐：底面圓內往外張開
              const rr = sh.r * Math.sqrt(1 - sh.thick * r()), th = ang;
              p = [Math.cos(th) * rr, Math.sin(th) * rr, 0];
              const a = sh.angle * Math.PI / 180 * (sh.r > 0 ? rr / sh.r : 0);
              d = [Math.cos(th) * Math.sin(a), Math.sin(th) * Math.sin(a), Math.cos(a)];
              break;
            }
            case 5: case 15: case 16: {                 // 盒子
              p = [(r() - 0.5) * sh.box[0], (r() - 0.5) * sh.box[1], (r() - 0.5) * sh.box[2]]; d = [0, 0, 1];
              if (sh.type === 16) { const e = Math.floor(r() * 4); if (e < 2) p[0] = (e ? 0.5 : -0.5) * sh.box[0]; else p[1] = (e === 3 ? 0.5 : -0.5) * sh.box[1]; }
              break;
            }
            case 10: case 11: { const rr = sh.type === 11 ? sh.r : sh.r * Math.sqrt(1 - sh.thick * r()); p = [Math.cos(ang) * rr, Math.sin(ang) * rr, 0]; d = [Math.cos(ang), Math.sin(ang), 0]; break; }
            case 12: p = [(r() * 2 - 1) * sh.r, 0, 0]; d = [0, 1, 0]; break;
            case 18: p = [(r() - 0.5) * sh.box[0], (r() - 0.5) * sh.box[1], 0]; d = [0, 0, 1]; break;
            default: { const u = r() * 2 - 1, th = r() * Math.PI * 2, s = Math.sqrt(1 - u * u); d = [s * Math.cos(th), s * Math.sin(th), u]; }
          }
          if (sh.rdir > 0) { const u = r() * 2 - 1, th = r() * Math.PI * 2, s = Math.sqrt(1 - u * u); const rd = [s * Math.cos(th), s * Math.sin(th), u]; d = d.map((v, i) => lerp(v, rd[i], sh.rdir)); }
          if (sh.rot[0] || sh.rot[1] || sh.rot[2]) { const Q = trs([0, 0, 0], qeuler(sh.rot[0], sh.rot[1], sh.rot[2]), [1, 1, 1]); p = xf(Q, p[0], p[1], p[2]); d = xf(Q, d[0], d[1], d[2]); }
          p = [p[0] + sh.pos[0], p[1] + sh.pos[1], p[2] + sh.pos[2]];
        }
        const rr = [r(), r(), r(), r(), r(), r(), r()];
        const speed = mm(ps.v0, tn, rr[0]), life = Math.max(0.01, mm(ps.life, tn, rr[1]));
        let vel = d.map((v) => v * speed);
        if (ps.space === 1 && M) {                     // World：生成時就換到世界座標
          p = xf(M, p[0], p[1], p[2]);
          const o = xf(M, 0, 0, 0), v2 = xf(M, vel[0], vel[1], vel[2]); vel = [v2[0] - o[0], v2[1] - o[1], v2[2] - o[2]];
        }
        this.parts.push({ age: 0, life, p, v: vel, size: mm(ps.size, tn, rr[2]) * (ps.space === 1 ? scale : 1), rot: mm(ps.rot0, tn, rr[3]), rs: rr[4], col: mmgrad(ps.color, tn, rr[5], rr[6]), r: rr });
      }
    }
    step(dt, M, scale) {
      const ps = this.ps;
      if (dt <= 0) return;
      dt *= ps.speed || 1;
      const prevT = this.t; this.t += dt;
      const playing = ps.loop || this.t <= ps.dur;
      const tn = ps.dur > 0 ? (this.t % ps.dur) / ps.dur : 0;
      if (playing) {
        if (ps.rate) { this.acc += mm(ps.rate, tn, this.r()) * dt; const n = Math.floor(this.acc); this.acc -= n; if (n > 0) this.spawn(n, M, scale); }
        for (let bi = 0; bi < ps.bursts.length; bi++) {
          const b = ps.bursts[bi];
          const cycle = ps.dur > 0 ? Math.floor(this.t / ps.dur) : 0;
          for (let c = 0; c < Math.max(1, b.cycles); c++) {
            const bt = cycle * (ps.dur || 0) + b.t + c * (b.iv || 0);
            const key = cycle + ":" + bi + ":" + c;
            if (bt <= this.t && bt > prevT - 1e-6 && !this.bursted.has(key)) { this.bursted.add(key); this.spawn(Math.round(mm(b.n, tn, this.r())), M, scale); }
          }
        }
      }
      const g = mm(ps.grav, tn, 0.5) * 9.81;
      for (const q of this.parts) {
        q.age += dt;
        const lt = q.age / q.life;
        if (ps.vel) { /* 速度模組：加上額外速度（以位移方式近似） */
          const vx = mm(ps.vel[0], lt, q.r[0]), vy = mm(ps.vel[1], lt, q.r[1]), vz = mm(ps.vel[2], lt, q.r[2]);
          q.p[0] += vx * dt; q.p[1] += vy * dt; q.p[2] += vz * dt;
        }
        if (ps.force) { q.v[0] += mm(ps.force[0], lt, q.r[0]) * dt; q.v[1] += mm(ps.force[1], lt, q.r[1]) * dt; q.v[2] += mm(ps.force[2], lt, q.r[2]) * dt; }
        if (ps.drag) { const k = Math.max(0, 1 - mm(ps.drag, lt, q.r[3]) * dt); q.v = q.v.map((v) => v * k); }
        if (ps.damp && ps.limit) {
          const lim = mm(ps.limit, lt, q.r[3]), sp = Math.hypot(q.v[0], q.v[1], q.v[2]);
          if (sp > lim && sp > 0) { const ns = lerp(sp, lim, ps.damp); q.v = q.v.map((v) => v * ns / sp); }
        }
        q.v[1] -= g * dt;
        q.p[0] += q.v[0] * dt; q.p[1] += q.v[1] * dt; q.p[2] += q.v[2] * dt;
        if (ps.rs) q.rot += mm(ps.rs, lt, q.rs) * dt * 180 / Math.PI;
      }
      this.parts = this.parts.filter((q) => q.age < q.life);
    }
  }

  // ------------------------------------------------------------------ 播放器
  class Timeline {
    constructor(opts) {
      this.o = opts;           // {canvas, textEl, base, data, hooks:{text, bgm, se, log}, name()}
      this.D = opts.data;
      this.gl = new GL(opts.canvas);
      this.part = 0;
      this.t = 0;
      this.wall = 0;
      this.paused = false;
      this.waiting = false;
      this.done = false;
      this.mi = 0;             // 下一個要處理的標記
      this.emitters = {};
      this.animStart = {};     // animancer：節點 → 開始的牆鐘時間
      this.prevActive = [];
    }
    async load() {
      const D = this.D;
      // 外插（Hold／Loop…）只填補到同一軌下一個片段開始前：片段依開始時間排序
      for (const tl of D.timeline) for (const tr of tl.tracks) tr.clips.sort((a, b) => a.start - b.start);
      const ld = (src) => new Promise((res) => { const im = new Image(); im.onload = () => res(im); im.onerror = () => res(null); im.src = src; });
      const sp = await Promise.all(D.sprites.map((s) => ld(s.src)));
      const tx = await Promise.all(D.textures.map((s) => ld(s.src)));
      this.spriteTex = sp.map((im) => im ? this.gl.texFromImage(im) : null);
      // 畫成圖片的日文台詞：有譯文時用 Canvas 畫成同樣風格的貼圖取代（尺寸依譯文長度調整）
      this.sprites = D.sprites.map((x) => Object.assign({}, x));
      const tr = this.o.tr || {};
      if (this.o.lang !== "ja" && this.sprites.some((x) => x.txt && tr[x.txt])) {
        try { await document.fonts.ready; } catch (e) { /* ignore */ }
        this.sprites.forEach((S, i) => {
          const zh = S.txt && tr[S.txt];
          if (!zh) return;
          let c = textCanvas(zh.replace(/\r?\n/g, " ").replace(/<name>/g, this.o.playerName || ""), S);
          const H = c.rowH, Wc = c.width;
          let R = c.rowTop, ox = 0;           // 原圖那一行在最終畫布中的上緣與左緣
          if (this.o.lang === "both" && sp[i] && S.style !== "glow") {
            // 雙語：中文在上（位置與大小同原圖），原本的日文圖片（只取有字的範圍）縮小接在下方；發光層只發中文的光
            const im = sp[i], tb = S.tbox || [0, 0, 1, 1];
            const fs = H * Math.max(0.2, tb[3] - tb[1]) / 0.98, cy = R + H * (tb[1] + tb[3]) / 2;
            const m = 0.02;
            const sx = Math.max(0, tb[0] - m) * im.width, sy = Math.max(0, tb[1] - m) * im.height;
            const sw = Math.min(1, tb[2] + m) * im.width - sx, sh = Math.min(1, tb[3] + m) * im.height - sy;
            const Hj = fs * 0.55, Wj = Hj * sw / sh, top = cy + fs * 0.62;
            const tw = Math.ceil(Math.max(Wc, Wj + 8)), th = Math.ceil(Math.max(c.height, top + Hj + fs * 0.12));
            const k = document.createElement("canvas"); k.width = tw; k.height = th;
            const x = k.getContext("2d");
            ox = Math.round((tw - Wc) / 2);
            x.drawImage(c, ox, 0);
            x.drawImage(im, sx, sy, sw, sh, Math.round((tw - Wj) / 2), Math.round(top), Wj, Hj);
            c = k;
          }
          // 樞紐點與尺寸：讓原圖那一行（高 H、寬 Wc）維持在原本的位置與大小
          S.px = (ox + S.px * Wc) / c.width;
          S.py = (c.height - (R + H) + S.py * H) / c.height;
          S.h = S.h * c.height / H;
          this.spriteTex[i] = this.gl.texFromImage(c);
          S.canvas = c;                       // 除錯用
          S.w = S.h * c.width / c.height;
        });
      }
      this.texTex = tx.map((im) => im ? this.gl.texFromImage(im) : null);
      this.rt = (D.cameras || []).map(() => this.gl.target(this.o.canvas.width, this.o.canvas.height));
      this.camMask = (D.cameras || []).reduce((m, c) => m | c.mask, 0);
      this.sm = D.animators.map((a) => ({ cur: a.start || 0, t: 0, prev: -1, prevT: 0, fadeT: 0, fadeDur: 0, trig: new Set() }));
      this.base = D.nodes.map((n) => ({
        pos: n.pos, rot: n.rot, scale: n.scale, active: n.active,
        color: n.r && n.r.color ? n.r.color : [1, 1, 1, 1], sprite: n.r ? n.r.sprite : -1, enabled: n.r ? n.r.enabled : false,
        flip: n.r && n.r.flip ? n.r.flip : [false, false],
      }));
    }
    get tl() { return this.D.timeline[this.part]; }

    // 播放到下一個需要故事處理的事件為止：回傳 "waittext"（暫停，讓故事顯示訊息框）、"next"、"end" 或 "skip"
    play() {
      this.paused = false; this.waiting = false;
      if (!this.raf) {
        let last = performance.now();
        const frame = (now) => {
          if (this.done) return;
          const dt = Math.min(0.1, (now - last) / 1000); last = now;
          this.update(dt);
          if (!this.done) this.render();
          this.raf = requestAnimationFrame(frame);
        };
        this.raf = requestAnimationFrame(frame);
      }
      return new Promise((resolve) => { this.resolve = resolve; });
    }
    emit(why) { const r = this.resolve; this.resolve = null; if (r) r(why); }
    stop() {
      this.done = true; cancelAnimationFrame(this.raf); this.raf = 0;
      if (this.o.textEl) { this.o.textEl.classList.remove("on"); this.o.textEl.innerHTML = ""; }
    }
    finish(why) { if (this.done) return; this.stop(); this.emit(why || "end"); }
    tap() { if (this.waiting) { this.waiting = false; this.paused = false; return true; } return false; }

    // ---------------------------------------------------------------- 標記
    markers(from, to) {
      const M = this.tl.markers;
      while (this.mi < M.length && M[this.mi].t <= to) {
        const m = M[this.mi++];
        if (m.t < from - 1e-6) continue;
        const p = m.parameter || {};
        if (typeof p.text === "string" && "fadeInSec" in p) this.o.hooks.text("in", p);
        else if ("fadeOutSec" in p) this.o.hooks.text("out", p);
        else if (m.parameters) {
          for (const e of m.parameters) {
            if (e.uiEvent === 1) {                                // WaitText：暫停，交給故事顯示下一句訊息框
              this.t = m.t; this.paused = true; this.emit("waittext");
              return true;
            }
            if (e.uiEvent === 2) {                                // WaitTap：停住等點擊
              this.t = m.t; this.paused = true; this.waiting = true;
              if (this.o.hooks.wait) this.o.hooks.wait();
              return true;
            }
            if (e.uiEvent === 3) {                                // NextScene
              if (this.part + 1 < this.D.timeline.length) { this.part++; this.t = 0; this.mi = 0; return true; }
              this.finish("next"); return true;
            }
          }
        } else if (p.animatorEvent && m.anim != null) {            // [UI] Animator操作：1＝SetTrigger、2＝隨機播放
          const S = this.sm[m.anim], A = this.D.animators[m.anim];
          if (p.animatorEvent === 1) S.trig.add(p.strParameter);
          else if (p.animatorEvent === 2 && p.randomAnimationNames && p.randomAnimationNames.length) {
            const nm = p.randomAnimationNames[Math.floor(Math.random() * p.randomAnimationNames.length)];
            const k = A.states.findIndex((x) => x.name === nm);
            if (k >= 0) this.goState(m.anim, k, 0.1);
          }
        } else if (p.bgmEvent) this.o.hooks.bgm(p);
        else if (m.seName) this.o.hooks.se(m.seName);
      }
      return false;
    }

    update(dt) {
      this.wall += dt;
      if (!this.paused) {
        const from = this.t;
        this.t += dt;
        if (this.markers(from, this.t)) { /* 停在標記上 */ }
        if (!this.done && this.t >= this.tl.duration && !this.paused) {
          if (this.part + 1 < this.D.timeline.length) { this.part++; this.t = 0; this.mi = 0; } else { this.finish("end"); return; }
        }
      }
      this.evaluate(dt);
    }

    // ---------------------------------------------------------------- 取值
    evaluate(dt) {
      const D = this.D, N = D.nodes, st = this.base.map((b) => ({
        pos: b.pos.slice(), rot: b.rot.slice(), scale: b.scale.slice(), euler: null, active: b.active,
        color: b.color.slice(), sprite: b.sprite, enabled: b.enabled, flip: b.flip.slice(), mat: {},
      }));
      this.st = st;
      const apply = (clip, lt, w) => {
        const C = D.clips[clip]; if (!C) return;
        const tt = clamp(lt, C.start, C.stop);
        for (const cv of C.curves) {
          const v = evalCurve(cv, tt); if (v == null) continue;
          const s = st[cv.n]; if (!s) continue;
          const p = cv.p;
          if (p === "pos" || p === "scale") s[p][cv.i] = w >= 1 ? v : lerp(s[p][cv.i], v, w);
          else if (p === "rot") { if (!s._q) s._q = s.rot.slice(); s._q[cv.i] = v; s._qw = w; }
          else if (p === "euler") { if (!s.euler) s.euler = [0, 0, 0]; s.euler[cv.i] = v; s._ew = w; }
          else if (p === "go:m_IsActive") { if (w >= 0.5) s.active = v > 0.5; }
          else if (p === "sr:m_Enabled" || p === "mr:m_Enabled") { if (w >= 0.5) s.enabled = v > 0.5; }
          else if (p.startsWith("sr:m_Color.")) { const k = "rgba".indexOf(p.slice(-1)); s.color[k] = w >= 1 ? v : lerp(s.color[k], v, w); }
          else if (p === "pptr:m_Sprite") { if (w >= 0.5 && v >= 0) s.sprite = v; }
          else if (p === "sr:m_FlipX") s.flip[0] = v > 0.5;
          else if (p === "sr:m_FlipY") s.flip[1] = v > 0.5;
          else if (p.startsWith("mat:")) { const k = p.slice(4); s.mat[k] = s.mat[k] == null || w >= 1 ? v : lerp(s.mat[k], v, w); }
          else if (p.startsWith("ps:")) { s.psProp = s.psProp || {}; s.psProp[p.slice(3)] = v; }
        }
        for (const s of st) {
          if (s._q) { s.rot = s._qw >= 1 ? qnorm(s._q) : qnorm(s.rot.map((v, i) => lerp(v, s._q[i], s._qw))); s._q = null; }
          if (s.euler) { const q = qeuler(s.euler[0], s.euler[1], s.euler[2]); s.rot = s._ew >= 1 ? q : qnorm(s.rot.map((v, i) => lerp(v, q[i], s._ew))); s.euler = null; }
        }
      };
      const bound = new Set();
      for (const tr of this.tl.tracks) if (tr.node >= 0 && (tr.kind === "anim" || tr.kind === "animancer")) bound.add(tr.node);
      // 1) Timeline 沒有綁定的 Animator：狀態機（預設狀態、Trigger、Exit Time），依牆鐘時間
      D.animators.forEach((a, ai) => {
        if (bound.has(a.node) || !a.states || !a.states.length) return;
        const S = this.sm[ai];
        this.stepState(ai, dt);
        const local = (k, t) => {
          const st = a.states[k]; if (!st || st.clip < 0) return null;
          const C = D.clips[st.clip], len = C.stop - C.start;
          let lt = t * (st.speed || 1);
          if ((st.loop || C.loop) && len > 0) lt = lt % len;
          return [st.clip, C.start + lt];
        };
        if (S.prev >= 0 && S.fadeT < S.fadeDur) {
          const p = local(S.prev, S.prevT); if (p) apply(p[0], p[1], 1);
          const c = local(S.cur, S.t); if (c) apply(c[0], c[1], clamp(S.fadeT / S.fadeDur, 0, 1));
        } else { const c = local(S.cur, S.t); if (c) apply(c[0], c[1], 1); }
      });
      // 2) 動畫軌：依 Timeline 時間
      const t = this.t;
      for (const tr of this.tl.tracks) {
        if (tr.muted || tr.node < 0) continue;
        if (tr.kind === "anim") {
          if (tr.infinite != null) apply(tr.infinite, t, 1);
          const cs = tr.clips;
          let any = false;
          for (let i = 0; i < cs.length; i++) {
            const c = cs[i]; if (c.clip == null || c.clip < 0) continue;
            const end = c.start + c.dur;
            let lt = null, w = 1;
            if (t >= c.start && t < end) {
              lt = (t - c.start) * c.scale + c.in;
              const ein = Math.max(c.easeIn, c.blendIn), eout = Math.max(c.easeOut, c.blendOut);
              if (ein > 0 && t - c.start < ein) w = (t - c.start) / ein;
              if (eout > 0 && end - t < eout) w = Math.min(w, (end - t) / eout);
            } else {
              const nextStart = i + 1 < cs.length ? cs[i + 1].start : Infinity;
              if (t >= end && t < nextStart && c.post) lt = this.extrap(c, t, c.post);
              else if (i === 0 && t < c.start && c.pre) lt = this.extrap(c, t, c.pre);
            }
            if (lt == null) continue;
            const len = c.clipLen || (D.clips[c.clip].stop - D.clips[c.clip].start);
            const loop = c.loop === 1 || (c.loop === 0 && c.clipLoop);
            if (loop && len > 0) lt = ((lt % len) + len) % len;
            apply(c.clip, D.clips[c.clip].start + lt, w);
            any = true;
          }
          void any;
        } else if (tr.kind === "animancer") {
          // Animancer（TimelineAnimationClipClip）：進入片段時開始播，依牆鐘時間（等待點擊時仍會動）
          let cur = null;
          for (const c of tr.clips) if (t >= c.start && t < c.start + c.dur) cur = c;
          const key = tr.node;
          if (cur) {
            if (!this.animStart[key] || this.animStart[key].c !== cur) this.animStart[key] = { c: cur, w: this.wall };
            // 片段序列（第一段＋_Others）：依序各播一次，最後一段循環；例如「下一頁」圖示在等待點擊時持續循環
            const segs = [{ clip: cur.clip, speed: cur.speed || 1 }].concat(cur.seq || []);
            let el = this.wall - this.animStart[key].w;
            for (let k = 0; k < segs.length; k++) {
              const C = D.clips[segs[k].clip]; if (!C) break;
              const len = (C.stop - C.start) / (segs[k].speed || 1);
              if (k < segs.length - 1 && el >= len) { el -= len; continue; }
              let lt = el * (segs[k].speed || 1);
              if (len > 0) lt = lt % (C.stop - C.start);
              apply(segs[k].clip, C.start + lt, 1);
              break;
            }
          } else delete this.animStart[key];
        }
      }
      // 3) 啟用軌：片段期間啟用、其餘時間停用
      for (const tr of this.tl.tracks) {
        if (tr.kind !== "activation" || tr.muted || tr.node < 0) continue;
        st[tr.node].active = tr.clips.some((c) => t >= c.start && t < c.start + c.dur);
      }
      // 4) 世界矩陣與實際啟用狀態
      const W = new Array(N.length), A = new Array(N.length);
      for (let i = 0; i < N.length; i++) {
        const s = st[i], par = N[i].parent;
        const L = trs(s.pos, s.rot, s.scale);
        W[i] = par >= 0 ? mmul(W[par], L) : L;
        A[i] = s.active && (par < 0 || A[par]);
      }
      this.W = W; this.A = A;
      // 5) 粒子
      for (let i = 0; i < N.length; i++) {
        const ps = N[i].ps; if (!ps) continue;
        let e = this.emitters[i];
        if (A[i] && !this.prevActive[i]) { if (!e) e = this.emitters[i] = new Emitter(ps, i + 1); else e.reset(); if (!ps.awake) e.off = true; }
        if (!A[i]) { if (e) e.parts = []; continue; }
        if (e && !e.off) e.step(dt, W[i], Math.hypot(W[i][0], W[i][4]));
      }
      this.prevActive = A;
    }
    goState(ai, k, dur) {
      const S = this.sm[ai];
      S.prev = S.cur; S.prevT = S.t; S.cur = k; S.t = 0; S.fadeT = 0; S.fadeDur = dur || 0;
    }
    stepState(ai, dt) {
      const a = this.D.animators[ai], S = this.sm[ai];
      S.t += dt; S.prevT += dt; S.fadeT += dt;
      const st = a.states[S.cur];
      const clipLen = (k) => { const x = a.states[k]; if (!x || x.clip < 0) return 1; const C = this.D.clips[x.clip]; return Math.max(1e-3, (C.stop - C.start) / (x.speed || 1)); };
      const ok = (tr) => tr.cond.every(([mode, name]) => mode === 1 ? S.trig.has(name) : mode === 2 ? !S.trig.has(name) : false);
      const fire = (tr) => {
        tr.cond.forEach(([mode, name]) => { if (mode === 1) S.trig.delete(name); });
        const dur = tr.fixed ? tr.dur : tr.dur * clipLen(S.cur);
        this.goState(ai, tr.to, dur);
        if (tr.off) S.t = tr.off * clipLen(tr.to);
      };
      for (const tr of a.any || []) if (tr.cond.length && ok(tr)) { fire(tr); return; }
      if (!st) return;
      const nt = S.t / clipLen(S.cur);
      for (const tr of st.tr || []) {
        if (tr.exit != null && nt < tr.exit) continue;
        if (tr.exit == null && !tr.cond.length) continue;
        if (ok(tr)) { fire(tr); return; }
      }
    }
    extrap(c, t, mode) {
      const len = c.dur * c.scale, end = c.start + c.dur;
      if (mode === 1) return t >= end ? c.in + len : c.in;                       // Hold
      const lt = (t - c.start) * c.scale;
      if (mode === 2) return c.in + (((lt % len) + len) % len);                 // Loop
      if (mode === 3) { const k = ((lt % (2 * len)) + 2 * len) % (2 * len); return c.in + (k < len ? k : 2 * len - k); }  // PingPong
      if (mode === 4) return c.in + lt;                                         // Continue
      return null;
    }

    // ---------------------------------------------------------------- 繪製
    render() {
      const D = this.D, gl = this.gl;
      this.rtDone = new Set();
      // 先依相依順序畫好所有 RenderTexture 攝影機，再畫主畫面
      for (let ci = 0; ci < (D.cameras || []).length; ci++) this.renderCamera(ci);
      gl.view = [0, 0];
      gl.bind(null);
      this.drawPass(null);
    }
    // 一次繪製：mask == null 為主畫面（不畫給 RenderTexture 攝影機拍的圖層），否則只畫 mask 內的圖層
    drawPass(mask) {
      const N = this.D.nodes, st = this.st, W = this.W, A = this.A;
      const list = [];
      for (let i = 0; i < N.length; i++) {
        if (!A[i]) continue;
        const n = N[i], bit = 1 << (n.layer || 0);
        if (mask == null ? (this.camMask & bit) : !(mask & bit)) continue;
        const r = n.r, s = st[i];
        if (r && s.enabled) {
          if (r.kind === "sprite" && s.sprite >= 0 && s.color[3] > 0.001) list.push({ i, z: W[i][11], layer: r.layer, order: r.order, kind: "sprite" });
          else if (r.kind === "mesh") list.push({ i, z: W[i][11], layer: r.layer, order: r.order, kind: "mesh" });
        }
        if (n.ps && this.emitters[i] && this.emitters[i].parts.length && (!n.ps.render || n.ps.render.enabled)) {
          list.push({ i, z: W[i][11], layer: 0, order: n.ps.render ? n.ps.render.order : 0, kind: "ps" });
        }
      }
      list.sort((a, b) => a.layer - b.layer || a.order - b.order || b.z - a.z || a.i - b.i);
      for (const it of list) {
        if (it.kind === "sprite") this.drawSprite(it.i);
        else if (it.kind === "mesh") this.drawMesh(it.i);
        else this.drawParticles(it.i);
      }
    }
    // 把 RenderTexture 攝影機的畫面畫到它的畫布（遞迴處理攝影機串接）
    renderCamera(ci) {
      if (this.rtDone.has(ci)) return;
      this.rtDone.add(ci);
      const c = this.D.cameras[ci], gl = this.gl;
      if (!this.A[c.node]) return;                   // 攝影機停用：保留上一次的畫面
      for (let k = 0; k < this.D.cameras.length; k++) {
        const q = this.D.nodes[this.D.cameras[k].quad];
        if (k !== ci && (c.mask & (1 << (q.layer || 0)))) this.renderCamera(k);
      }
      const saved = gl.view, W = this.W[c.node];
      gl.view = [W[3], W[7]];
      gl.bind(this.rt[ci], c.clear === 2 || c.clear === 1 ? c.bg : [0, 0, 0, 0]);
      this.drawPass(c.mask);
      gl.view = saved;
    }
    matInfo(mi, s) {
      const M = this.D.materials[mi];
      if (!M) return { blend: "alpha" };
      const get = (k, d) => (s.mat[k] != null ? s.mat[k] : M.floats[k] != null ? M.floats[k] : d);
      const info = { blend: M.blend, M };
      if (/ArtisticFade/.test(M.shader)) {
        info.mode = 1;
        info.progress = get("_Progress", 1);
        info.alpha = get("_Alpha", 1);
        info.sat = get("_Saturation", 1);
        info.rule = M.tex._RuleTex ? this.texTex[M.tex._RuleTex.tex] : null;
        info.noise = M.tex._NoiseTex ? this.texTex[M.tex._NoiseTex.tex] : null;
      } else if (M.floats._Alpha != null || s.mat._Alpha != null) info.alpha = get("_Alpha", 1);
      const col = M.colors._Color || M.colors._TintColor;
      info.tint = col ? ["r", "g", "b", "a"].map((c, k) => (s.mat["_Color." + c] != null ? s.mat["_Color." + c] : col[k])) : [1, 1, 1, 1];
      return info;
    }
    drawSprite(i) {
      const s = this.st[i], r = this.D.nodes[i].r, S = this.sprites[s.sprite], M = this.W[i];
      const tex = this.spriteTex[s.sprite]; if (!S || !tex) return;
      let x0 = -S.px * S.w, x1 = (1 - S.px) * S.w, y0 = -S.py * S.h, y1 = (1 - S.py) * S.h;
      if (s.flip[0]) { const a = -x1; x1 = -x0; x0 = a; }
      if (s.flip[1]) { const a = -y1; y1 = -y0; y0 = a; }
      let u0 = 0, u1 = 1; if (s.flip[0]) { u0 = 1; u1 = 0; }
      let v0 = 1, v1 = 0; if (s.flip[1]) { v0 = 0; v1 = 1; }
      const c = [[x0, y0, u0, v0], [x1, y0, u1, v0], [x1, y1, u1, v1], [x0, y1, u0, v1]];
      const verts = [];
      for (const [x, y, u, v] of c) { const p = xf(M, x, y, 0); verts.push(p[0], p[1], u, v); }
      const mi = this.matInfo(r.mat, s);
      const color = s.color.slice();
      color[3] *= mi.alpha == null ? 1 : mi.alpha;
      for (let k = 0; k < 4; k++) color[k] *= mi.tint[k];
      this.gl.draw(verts, [0, 1, 2, 0, 2, 3], { tex, blend: mi.blend, color, mode: mi.mode, progress: mi.progress, rule: mi.rule, noise: mi.noise, sat: mi.sat });
    }
    drawMesh(i) {
      const s = this.st[i], r = this.D.nodes[i].r, M = this.W[i];
      const mi = this.matInfo(r.mat, s);
      const Mt = mi.M; if (!Mt) return;
      let tk = Mt.tex._MainTex || Mt.tex._OverlayTex || Object.values(Mt.tex)[0];
      let tex = tk ? this.texTex[tk.tex] : null;
      const isRT = r.rt != null && this.rt[r.rt];
      if (isRT) { tex = this.rt[r.rt].tex; tk = null; }   // RenderTexture 攝影機的輸出平面（render() 已先畫好）
      const sc = tk ? tk.scale : [1, 1], of = tk ? tk.offset : [0, 0];
      let pos, uv, idx;
      if (r.mesh >= 0) { const m = this.D.meshes[r.mesh]; pos = m.pos; uv = m.uv; idx = m.idx; }
      else { pos = [[-0.5, -0.5, 0], [0.5, -0.5, 0], [0.5, 0.5, 0], [-0.5, 0.5, 0]]; uv = [[0, 0], [1, 0], [1, 1], [0, 1]]; idx = [0, 1, 2, 0, 2, 3]; }
      const verts = [];
      for (let k = 0; k < pos.length; k++) {
        const p = xf(M, pos[k][0], pos[k][1], pos[k][2]), t = uv[k] || [0, 0];
        verts.push(p[0], p[1], t[0] * sc[0] + of[0], isRT ? t[1] : 1 - (t[1] * sc[1] + of[1]));
      }
      const color = mi.tint.slice(); color[3] *= mi.alpha == null ? 1 : mi.alpha;
      if (isRT) { const g = (k) => (s.mat[k] != null ? s.mat[k] : Mt.floats[k] != null ? Mt.floats[k] : 1); color[0] *= g("_MultiR"); color[1] *= g("_MultiG"); color[2] *= g("_MultiB"); }
      this.gl.draw(verts, idx, { tex, blend: isRT ? "alpha" : mi.blend, color, mode: isRT ? 0 : mi.mode, progress: mi.progress, rule: mi.rule, noise: mi.noise, sat: mi.sat });
    }
    drawParticles(i) {
      const n = this.D.nodes[i], ps = n.ps, e = this.emitters[i], M = this.W[i], s = this.st[i];
      const R = ps.render || { mat: -1 };
      const mi = this.matInfo(R.mat, s);
      const Mt = mi.M;
      const tk = Mt && (Mt.tex._MainTex || Object.values(Mt.tex)[0]);
      const tex = tk ? this.texTex[tk.tex] : this.gl.dot;
      const scale = ps.scaling === 2 ? 1 : Math.hypot(M[0], M[4]);
      const sheet = ps.sheet;
      for (const q of e.parts) {
        const lt = q.age / q.life;
        let p = q.p;
        if (ps.space !== 1) p = xf(M, p[0], p[1], p[2]);
        const size = q.size * (ps.sol ? mm(ps.sol, lt, q.r[2]) : 1) * (ps.space === 1 ? 1 : scale) / 2;
        let col = q.col.slice();
        if (ps.col) { const c2 = mmgrad(ps.col, lt, q.r[5], lt); col = col.map((v, k) => v * c2[k]); }
        for (let k = 0; k < 4; k++) col[k] *= mi.tint[k];
        if (col[3] <= 0.002 || size <= 0) continue;
        const a = q.rot * Math.PI / 180, ca = Math.cos(a) * size, sa = Math.sin(a) * size;
        let u0 = 0, v0 = 0, u1 = 1, v1 = 1;
        if (sheet && (sheet.x > 1 || sheet.y > 1)) {
          const nf = sheet.x * sheet.y, f = Math.min(nf - 1, Math.floor(mm(sheet.frame, (lt * (sheet.cycles || 1)) % 1, q.r[6]) * nf));
          const fx = f % sheet.x, fy = Math.floor(f / sheet.x);
          u0 = fx / sheet.x; u1 = (fx + 1) / sheet.x; v0 = fy / sheet.y; v1 = (fy + 1) / sheet.y;
        }
        const verts = [p[0] - ca + sa, p[1] - sa - ca, u0, v1, p[0] + ca + sa, p[1] + sa - ca, u1, v1, p[0] + ca - sa, p[1] + sa + ca, u1, v0, p[0] - ca - sa, p[1] - sa + ca, u0, v0];
        this.gl.draw(verts, [0, 1, 2, 0, 2, 3], { tex, blend: mi.blend, color: col });
      }
    }
  }

  // 資料以 <script src="assets/timeline/<名稱>/tl.js"> 載入（file:// 也能用），檔案內呼叫 MercTimeline.register
  const registry = {}, waiting = {};
  function loadData(name, base) {
    if (registry[name]) return Promise.resolve(registry[name]);
    return new Promise((resolve, reject) => {
      (waiting[name] = waiting[name] || []).push(resolve);
      if (waiting[name].length > 1) return;
      const sc = document.createElement("script");
      sc.src = base + "tl.js";
      sc.onerror = () => reject(new Error("找不到 Timeline 資料：" + name));
      document.head.appendChild(sc);
    });
  }
  window.MercTimeline = {
    register(name, data) { registry[name] = data; (waiting[name] || []).forEach((f) => f(data)); delete waiting[name]; },
    preload(name, base) { return loadData(name, base).catch(() => null); },
    async create(opts) {
      opts.data = await loadData(opts.name, opts.base);
      const t = new Timeline(opts);
      window.MercTimeline.last = t;      // 除錯用
      await t.load();
      t.evaluate(0); t.render();
      return t;
    },
  };
})();
