// source/sha256.js
var K = new Uint32Array([
  1116352408,
  1899447441,
  3049323471,
  3921009573,
  961987163,
  1508970993,
  2453635748,
  2870763221,
  3624381080,
  310598401,
  607225278,
  1426881987,
  1925078388,
  2162078206,
  2614888103,
  3248222580,
  3835390401,
  4022224774,
  264347078,
  604807628,
  770255983,
  1249150122,
  1555081692,
  1996064986,
  2554220882,
  2821834349,
  2952996808,
  3210313671,
  3336571891,
  3584528711,
  113926993,
  338241895,
  666307205,
  773529912,
  1294757372,
  1396182291,
  1695183700,
  1986661051,
  2177026350,
  2456956037,
  2730485921,
  2820302411,
  3259730800,
  3345764771,
  3516065817,
  3600352804,
  4094571909,
  275423344,
  430227734,
  506948616,
  659060556,
  883997877,
  958139571,
  1322822218,
  1537002063,
  1747873779,
  1955562222,
  2024104815,
  2227730452,
  2361852424,
  2428436474,
  2756734187,
  3204031479,
  3329325298
]);
var rotate = (value, bits) => value >>> bits | value << 32 - bits;
async function sha256(content) {
  const bytes = content instanceof Uint8Array ? content : new Uint8Array(content);
  if (globalThis.crypto?.subtle) {
    const result = await crypto.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(result)].map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  const padded = new Uint8Array(Math.ceil((bytes.length + 9) / 64) * 64);
  padded.set(bytes);
  padded[bytes.length] = 128;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, Math.floor(bytes.length / 536870912));
  view.setUint32(padded.length - 4, bytes.length * 8);
  const hash = new Uint32Array([
    1779033703,
    3144134277,
    1013904242,
    2773480762,
    1359893119,
    2600822924,
    528734635,
    1541459225
  ]);
  const words = new Uint32Array(64);
  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let i = 0; i < 16; i++) words[i] = view.getUint32(offset + i * 4);
    for (let i = 16; i < 64; i++) {
      const x = words[i - 15], y = words[i - 2];
      const s0 = rotate(x, 7) ^ rotate(x, 18) ^ x >>> 3;
      const s1 = rotate(y, 17) ^ rotate(y, 19) ^ y >>> 10;
      words[i] = words[i - 16] + s0 + words[i - 7] + s1;
    }
    let [a, b, c, d, e, f, g, h] = hash;
    for (let i = 0; i < 64; i++) {
      const s1 = rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25);
      const t1 = h + s1 + (e & f ^ ~e & g) + K[i] + words[i] >>> 0;
      const s0 = rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22);
      const t22 = s0 + (a & b ^ a & c ^ b & c) >>> 0;
      [h, g, f, e, d, c, b, a] = [g, f, e, d + t1 >>> 0, c, b, a, t1 + t22 >>> 0];
    }
    [a, b, c, d, e, f, g, h].forEach((word, i) => {
      hash[i] += word;
    });
  }
  return [...hash].map((word) => word.toString(16).padStart(8, "0")).join("");
}

// source/original-wall.js
var tt = Object.freeze({ mode: "smooth", gamma: 0.75 });
var P8 = 1024;
var w8 = 2;
var x8 = [3, 6, 14, 255];
var et = 4095;
var z8 = new Uint32Array(new Uint8Array(x8).buffer)[0];
var Y2 = [[0, 7, 9, 22], [0.08, 23, 15, 44], [0.2, 69, 51, 126], [0.32, 49, 104, 142], [0.5, 31, 149, 139], [0.68, 80, 185, 98], [0.84, 167, 213, 50], [1, 253, 231, 77]];
var _t = /* @__PURE__ */ new Map();
var Q2 = /* @__PURE__ */ new WeakMap();
var a5 = (L) => Math.max(0, Math.min(1, L));
var M8 = (L, E) => (L % E + E) % E;
function g1(L = {}, E = tt) {
  let R = { mode: L.mode ?? E.mode, gamma: L.gamma ?? E.gamma };
  if (!["smooth", "grid"].includes(R.mode)) throw new RangeError("\u663E\u793A\u91C7\u6837\u987B\u4E3A smooth \u6216 grid");
  if (!Number.isFinite(R.gamma) || R.gamma < 0.35 || R.gamma > 2.5) throw new RangeError("\u663E\u793A gamma \u987B\u5728 0.35\u20132.5 \u4E4B\u95F4");
  return R;
}
function J2(L) {
  if (!Array.isArray(L) || L.length !== 2 || !L.every(Number.isFinite) || L[1] <= L[0]) throw new RangeError("\u5BC6\u5EA6\u8272\u6807\u987B\u6709\u56FA\u5B9A\u4E14\u9012\u589E\u7684\u4E0A\u4E0B\u754C");
  return L;
}
function r5(L) {
  if (_t.has(L)) return _t.get(L);
  let E = new Uint8ClampedArray((et + 1) * 4);
  for (let R = 0; R <= et; R++) {
    let H = (R / et) ** L, C = Y2.findIndex((b) => b[0] >= H), s = Y2[Math.max(1, C)], T = Y2[Math.max(0, C - 1)], a = (H - T[0]) / (s[0] - T[0]);
    for (let b = 0; b < 3; b++) E[R * 4 + b] = Math.round(T[b + 1] + a * (s[b + 1] - T[b + 1]));
    E[R * 4 + 3] = 255;
  }
  return _t.size >= 16 && _t.delete(_t.keys().next().value), _t.set(L, E), E;
}
function Ft(L, { gamma: E = tt.gamma, domain: R = [0, 1] } = {}) {
  g1({ gamma: E });
  let [H, C] = J2(R), s = Math.round(a5((Number.isFinite(L) ? L - H : 0) / (C - H)) * et) * 4;
  return Array.from(r5(E).subarray(s, s + 4));
}
function E1({ gamma: L = tt.gamma, domain: E = [0, 1] } = {}) {
  let [R, H] = J2(E);
  return `linear-gradient(90deg, ${Array.from({ length: 65 }, (C, s) => `rgb(${Ft(R + (H - R) * s / 64, { gamma: L, domain: E }).slice(0, 3).join(",")}) ${100 * s / 64}%`).join(", ")})`;
}
function at(L, E = 64) {
  let R = L.getBoundingClientRect?.(), H = R ? Math.min(R.width, R.height) : E, C = L.ownerDocument?.defaultView?.devicePixelRatio ?? 1, s = Math.min(w8, Math.max(1, C)), T = H > 0 ? Math.min(P8, Math.max(1, Math.round(H * s))) : 0;
  return { cssSize: H, dpr: s, pixels: T };
}
function f1(L, { frame: E, width: R, height: H, extent: C = Math.max(R, H), viewCells: s = C, shiftX: T = 0, shiftY: a = 0, valueScale: b = 1, valueOffset: f = 0, domain: e = [0, 1], display: t = tt }) {
  if (![R, H, C].every((W) => Number.isSafeInteger(W) && W > 0) || R > C || H > C || E?.length !== R * H) throw new RangeError("\u5BC6\u5EA6\u5E27\u3001\u7F51\u683C\u548C\u4E16\u754C\u89C6\u91CE\u4E0D\u4E00\u81F4");
  if (!(s > 0 && s <= C) || !Number.isInteger(T) || !Number.isInteger(a)) throw new RangeError("\u76F8\u673A\u987B\u4F7F\u7528\u6574\u6570\u5468\u671F\u5E73\u79FB\u53CA\u6709\u6548\u89C6\u91CE");
  let n = g1(t), [o, l] = J2(e), v = at(L, C), h = v.pixels;
  if (Object.assign(L.dataset, { grid: `${R}x${H}`, render: `${h}x${h}`, renderDpr: String(v.dpr), sampling: n.mode === "smooth" ? "cubic-bspline" : "nearest", gamma: String(n.gamma), densityDomain: e.join(",") }), !h) return { ...v, scale: 0, painted: false };
  L.width !== h && (L.width = h), L.height !== h && (L.height = h), L.style && (L.style.imageRendering = n.mode === "grid" ? "pixelated" : "auto");
  let r = L.getContext("2d"), A = Q2.get(L);
  if (!A || A.n !== h) {
    let W = r.createImageData(h, h);
    A = { n: h, image: W, pixels: new Uint32Array(W.data.buffer) }, Q2.set(L, A);
  }
  let _ = `${R},${H},${C},${s},${T},${a},${n.mode}`;
  if (A.key !== _) {
    let W = Math.floor((C - s) / 2), U = Math.floor((C - R) / 2), F = Math.floor((C - H) / 2), I = (N, Z, q) => {
      let $2 = Array.from({ length: 4 }, () => new Int32Array(h)), r0 = Array.from({ length: 4 }, () => new Float64Array(h)), Y = new Uint8Array(h);
      for (let a0 = 0; a0 < h; a0++) {
        let V = W + (a0 + 0.5) * s / h - Z;
        Y[a0] = V >= 0 && V < N ? 1 : 0;
        let Q = V + q - (n.mode === "smooth" ? 0.5 : 0), J = Math.floor(Q), B = Q - J, G = B * B, t0 = G * B, K2 = n.mode === "smooth" ? [(1 - B) ** 3 / 6, (3 * t0 - 6 * G + 4) / 6, (-3 * t0 + 3 * G + 3 * B + 1) / 6, t0 / 6] : [0, 1, 0, 0];
        for (let s0 = 0; s0 < 4; s0++) $2[s0][a0] = M8(J + s0 - 1, N), r0[s0][a0] = K2[s0];
      }
      return { indices: $2, weights: r0, inside: Y, start: Y.indexOf(1), end: Y.lastIndexOf(1) + 1 };
    };
    A.x = I(R, U, T), A.y = I(H, F, a), A.key = _, A.rows = new Float64Array(H * h), A.rowStart = new Int32Array(H), A.rowEnd = new Int32Array(H);
  }
  let { x: p, y: d, pixels: i, rowStart: y, rowEnd: w } = A, z = new Uint32Array(r5(n.gamma).buffer), M = b / (l - o), P = (f - o) / (l - o), c = A.rows, [g, u, m, k] = p.indices, [x, j, O, S] = p.weights;
  for (let W = 0; W < H; W++) {
    let U = W * R, F = W * h, I = h, N = 0;
    for (let Z = Math.max(0, p.start); Z < p.end; Z++) {
      let q = E[U + g[Z]] * x[Z] + E[U + u[Z]] * j[Z] + E[U + m[Z]] * O[Z] + E[U + k[Z]] * S[Z];
      c[F + Z] = q, q !== 0 && (I = Math.min(I, Z), N = Z + 1);
    }
    y[W] = I, w[W] = N;
  }
  i.fill(z8);
  let D = z[Math.round(a5(P) * et)];
  for (let W = Math.max(0, d.start); W < d.end && p.start >= 0; W++) {
    let U = d.indices[0][W], F = d.indices[1][W], I = d.indices[2][W], N = d.indices[3][W], Z = U * h, q = F * h, $2 = I * h, r0 = N * h, Y = W * h, a0 = d.weights[0][W], V = d.weights[1][W], Q = d.weights[2][W], J = d.weights[3][W];
    i.fill(D, Y + p.start, Y + p.end);
    let B = Math.min(y[U], y[F], y[I], y[N]), G = Math.max(w[U], w[F], w[I], w[N]);
    for (let t0 = B; t0 < G; t0++) {
      let K2 = (c[Z + t0] * a0 + c[q + t0] * V + c[$2 + t0] * Q + c[r0 + t0] * J) * M + P;
      i[Y + t0] = z[K2 > 0 ? K2 < 1 ? Math.round(K2 * et) : et : 0];
    }
  }
  return r.putImageData(A.image, 0, 0), { ...v, scale: h / C, painted: true };
}
function qe(L) {
  Q2.delete(L);
}
function De(L, E) {
  let R = 0, H = L.length - 1;
  for (; R < H; ) {
    let C = Math.ceil((R + H) / 2);
    L[C] <= E ? R = C : H = C - 1;
  }
  return R;
}
function m3(L, E, R) {
  if (!L.length) return null;
  if (R === "Home") return L[0];
  if (R === "End") return L.at(-1);
  let H = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1, PageUp: 10, PageDown: -10 }[R];
  return H == null ? null : L[Math.max(0, Math.min(L.length - 1, De(L, E) + H))];
}
function u7(L, E, R, H) {
  let C = E.steps[0], s = E.steps.at(-1);
  s !== C && (L.step >= s ? (L.hold += R, L.hold >= 1.2 && (L.step = C, L.hold = 0, L.loops++)) : L.step = Math.min(s, L.step + R * H));
}
function qt(L, E) {
  return L.parentRelation !== "recorded" || !L.parentId ? null : E.find((R) => R.id === L.parentId) || null;
}
function g3(L, E, R) {
  let H = 0, C = 0, s = 0, T = 0, a = 0;
  for (let b = 0; b < R; b++) for (let f = 0; f < E; f++) {
    let e = L[b * E + f];
    a += e, H += e * Math.sin(2 * Math.PI * f / E), C += e * Math.cos(2 * Math.PI * f / E), s += e * Math.sin(2 * Math.PI * b / R), T += e * Math.cos(2 * Math.PI * b / R);
  }
  return !a || Math.hypot(H, C) / a < 0.02 || Math.hypot(s, T) / a < 0.02 ? null : { x: (Math.atan2(H, C) / (2 * Math.PI) + 1) % 1 * E, y: (Math.atan2(s, T) / (2 * Math.PI) + 1) % 1 * R };
}
function zt(L, { frame: E, width: R, height: H, extent: C = 64, follow: s = false, zoom: T = 1, display: a, densityDomain: b = [0, 1] }) {
  if (![R, H, C].every((p) => Number.isSafeInteger(p) && p > 0) || R > C || H > C) throw new RangeError("\u4E16\u754C\u5C3A\u5BF8\u987B\u4E3A\u6B63\u6574\u6570\uFF0C\u4E14\u4E0D\u80FD\u8D85\u51FA\u5B8C\u6574\u89C6\u91CE extent");
  if (!E || E.length !== R * H) throw new RangeError("\u5BC6\u5EA6\u5E27\u957F\u5EA6\u4E0E\u4E16\u754C\u5C3A\u5BF8\u4E0D\u7B26");
  if (![1, 2].includes(T) || C % T) throw new RangeError("\u89C2\u5BDF\u500D\u7387\u987B\u4E3A 1 \u6216 2\uFF0C\u4E14 extent \u987B\u80FD\u6574\u9664\u500D\u7387");
  let f = g3(E, R, H), e = !!(s && f), t = !!(s && !f), n = t ? 1 : T, o = C / n, l = e ? Math.round(f.x - R / 2) : 0, v = e ? Math.round(f.y - H / 2) : 0, h = Math.floor((C - o) / 2), r = Math.floor((C - R) / 2), A = Math.floor((C - H) / 2), _ = f1(L, { frame: E, width: R, height: H, extent: C, viewCells: o, shiftX: l, shiftY: v, valueScale: (b[1] - b[0]) / 255, valueOffset: b[0], domain: b, display: a });
  if (_.painted) {
    let p = L.getContext("2d"), d = _.scale;
    (R < C || H < C) && (p.strokeStyle = "#49616a", p.lineWidth = 0.5 * n * d, p.strokeRect((r - h - 0.25) * n * d, (A - h - 0.25) * n * d, (R + 0.5) * n * d, (H + 0.5) * n * d)), p.fillStyle = "#d0e3dd", p.fillRect(5 * d, (C - 6) * d, 8 * n * d, Math.max(1, d));
  }
  return Object.assign(L.dataset, { world: `${R}x${H}`, view: `${o}x${o}`, shift: `${l},${v}`, center: f ? `${f.x.toFixed(2)},${f.y.toFixed(2)}` : "undefined", zoom: String(n), camera: e ? "follow" : "fixed", fallback: t ? "unreliable-center" : "" }), { shiftX: l, shiftY: v, center: f, viewCells: o, zoom: n };
}
var Wt = "budgeted-morphogenesis";
var Mt = (L) => L.collectionId === Wt;
var Vt = [["bodyMass", "\u5F53\u524D\u7EC4\u7EC7"], ["energy", "\u5F53\u524D\u80FD\u91CF E"], ["precursor", "\u5269\u4F59\u524D\u4F53 P"], ["fuel", "\u5269\u4F59\u71C3\u6599 F"], ["cumulativeBirths", "\u7D2F\u8BA1\u65B0\u751F"], ["founderMass", "\u5B58\u7559\u521D\u59CB\u7EC4\u7EC7"], ["newbornMass", "\u5B58\u7559\u65B0\u751F\u7EC4\u7EC7"], ["capacityCells", "\u8FBE\u5BB9\u91CF\u683C\u6570"], ["cumulativeRejectedPrecursor", "\u7D2F\u8BA1\u524D\u4F53\u4E0D\u8DB3\u62D2\u7EDD"]];
function C1(L, E, R = Vt) {
  let H = L.formation?.series;
  if (!H?.steps?.length) return null;
  let C = De(H.steps, E);
  return { step: H.steps[C], ...Object.fromEntries(R.map(([s]) => [s, H[s]?.[C] ?? null])) };
}
var p7 = ["\u524D\u4E24\u9879\u4E3A\u4E09\u56E2\u5757\u6765\u6E90 r4\uFF0C\u540E\u4E24\u9879\u4E3A\u6210\u5BF9\u6765\u6E90 r2\uFF1B\u6BCF\u5BF9\u4F9D\u6B21\u4E3A\u201C\u4ED8\u8D39\u751F\u6210 \u2192 \u5173\u95ED\u751F\u6210\u201D\u3002\u56DB\u6BB5\u662F\u771F\u5B9E\u6761\u4EF6\u8BB0\u5F55\uFF0C\u4E0D\u662F\u56DB\u79CD\u65B0\u751F\u7269\u3002", "\u6709\u9650\u4E16\u754C\uFF0C\u4E0D\u8865\u5145\u5E93\u5B58\u3002\u6BCF\u751F\u6210 1 \u5355\u4F4D\u7EC4\u7EC7\uFF0C\u5B9E\u9645\u6D88\u8017 1 \u5355\u4F4D\u524D\u4F53 P \u548C 0.4 \u5355\u4F4D\u71C3\u6599 F\uFF0C\u5F62\u6210 0.2 \u5355\u4F4D\u80FD\u91CF E \u4E0E 0.2 \u5355\u4F4D\u70ED\u3002\u03C1\u3001P\u3001F \u7684 0\u20131 \u662F\u672C\u6A21\u578B\u7684\u663E\u5F0F\u5BB9\u91CF\u3002", "\u8EAB\u4F53\u7684\u79FB\u52A8\u4E0E\u53D8\u5F62\u6765\u81EA\u5404\u683C\u751F\u6210\u548C\u6D88\u5931\uFF1B\u8FD9\u91CC\u6CA1\u6709\u5B88\u6052\u7684\u7269\u8D28\u642C\u8FD0\u3002\u7D2F\u8BA1\u65B0\u751F\u8BB0\u5F55\u603B\u5468\u8F6C\uFF0C\u5B58\u7559\u65B0\u751F\u7EC4\u7EC7\u53EA\u8BB0\u5F55\u5F53\u524D\u4ECD\u5728\u7684\u90E8\u5206\uFF0C\u4E24\u8005\u4E0D\u80FD\u76F8\u52A0\u3002", "r4 \u5173\u95ED\u751F\u6210\u540E\u9010\u6B65\u8BB0\u5F55\u5728 36 \u6B65\u5F52\u96F6\uFF0C\u663E\u793A\u91C7\u6837\u5230 40 \u6B65\u9996\u6B21\u5168\u96F6\uFF1Br2 \u5206\u522B\u4E3A 25 / 32 \u6B65\u3002\u5F52\u96F6\u6765\u81EA\u8D1F\u89C4\u5219\u635F\u5931\uFF0C\u9965\u997F\u4E0E\u7EF4\u6301\u6B20\u4ED8\u5747\u4E3A\u96F6\u3002", "\u6BCF 8 \u6B65\u4E00\u5E45\u771F\u5B9E\u8EAB\u4F53\u5E27\uFF0C\u6A21\u578B\u65F6\u95F4 = \u6B65\u6570 \xD7 0.05\u3002\u672B\u5E27\u505C\u7559\u540E\u91CD\u64AD\uFF1B\u672C\u7EC4\u4E0D\u5EF6\u957F\u89C2\u5BDF\u3001\u4E0D\u63D2\u5165\u8EAB\u4F53\u5E27\u3002\u9AD8\u5468\u8F6C\u3001\u5BB9\u91CF\u9971\u548C\u4E0E\u5C40\u90E8\u524D\u4F53\u62D2\u7EDD\u4E0D\u4EE3\u8868\u5B66\u4E60\u3001\u518D\u751F\u6216\u957F\u671F\u7EF4\u6301\u3002"];
var P3 = "developmental-ecologies-batch1";
var m7 = (L) => L.collectionId === P3;
var Ke = (L) => L.stateLineage?.kind === "lifetime_environment_branch";
function E7(L) {
  return [L.environment?.firstPulse, L.environment?.secondPulse].filter(Boolean).map((E) => ({ ...E, stop: E.endExclusive }));
}
function K3(L, E) {
  return E7(L).find((R) => E >= R.start && E < R.stop) || null;
}
function C7(L, E) {
  let R = L.environment.sampling.clipIntervalUpdates;
  return E7(L).some((H) => E >= H.start - R && E < H.stop + R) ? 8 : 96;
}
function $3(L, E) {
  let R = L.environment, H = E7(L), C = [];
  E[0] < H[0].start && C.push(["\u751F\u547D\u671F\u8D77\u70B9", E[0]]), H.forEach((b, f) => {
    C.push([f ? "\u518D\u6B21\u5206\u53C9" : "\u9996\u6B21\u5206\u53C9", b.start]), C.push([f ? "\u518D\u6B21\u540E\u7EED\u5E27" : "\u9996\u6B21\u540E\u7EED\u5E27", b.firstFollowingClipFrameStep]), !f && R.freeInterval?.[0] > b.firstFollowingClipFrameStep && R.freeInterval[0] < H[1].start && C.push(["\u81EA\u7531\u5EF6\u7EED", R.freeInterval[0]]);
  }), C.push(["\u8BB0\u5F55\u672B\u5E27", E.at(-1)]);
  let s = H.map((b) => `${b.start} \u662F\u523A\u6FC0\u524D\u8EAB\u4F53\uFF1B${b.firstFollowingClipFrameStep} \u5DF2\u542B ${b.updates} \u6B21\u8BA1\u5212\u523A\u6FC0\u7A97\u66F4\u65B0\u548C ${b.firstFollowingClipFrameStep - b.stop} \u6B21\u65E0\u523A\u6FC0\u66F4\u65B0`).join("\u3002"), T = H.map((b) => `[${b.start},${b.stop})`).join("\u3001"), a = H.map((b) => `${b.transitionToSteps[0]}\u2013${b.transitionToSteps[1]}`).join("\u3001");
  return { phases: C, sampling: `\u659C\u7EB9\u8868\u793A\u5F53\u524D\u65F6\u949F\u7684\u523A\u6FC0\u8303\u56F4\uFF1B\u8EAB\u4F53\u6BCF ${R.sampling.clipIntervalUpdates} \u6B65\u91C7\u6837\uFF0C\u5E27\u95F4\u4FDD\u6301\u4E0A\u4E00\u5E27\u3002${s}\u3002\u4E0D\u8865\u753B\u8109\u51B2\u5185\u90E8\u8FC7\u7A0B\u3002\u5173\u952E\u9636\u6BB5 8 \u6B65/\u79D2\uFF0C\u5176\u4F59 96 \u6B65/\u79D2\u3002`, update: R.operator.fullUpdate, intensity: `\u5168\u573A\u7EDF\u4E00\u7CFB\u6570 I=${R.intensityConvention.nonzeroValue}\uFF08\u96F6\u5F3A\u5EA6\u5206\u652F\u4E3A0\uFF09\uFF0C\u662F\u672A\u6807\u5B9A\u7269\u7406\u5355\u4F4D\u7684\u6A21\u578B\u635F\u8017\u7CFB\u6570\u3002\u76F4\u63A5\u4F5C\u7528\u9879\u4E3A \u2212dt\xB7I\xB7A\uFF1B\u81EA\u7136\u589E\u957F\u4E0E\u622A\u65AD\u540C\u65F6\u53D1\u751F\uFF0CI \u6216 dt\xB7I \u5747\u4E0D\u662F\u5B9E\u9645\u51C0\u4E0B\u964D\u6BD4\u4F8B\u3002`, windows: `\u4E24\u6B21\u8F93\u5165\u6B65\u7A97\u53E3\u4E3A ${T}\uFF0C\u8F93\u51FA\u72B6\u6001\u4E3A ${a}\uFF1B\u672C\u7247\u6BB5\u6CA1\u6709\u8109\u51B2\u5185\u90E8\u6216\u672B\u8109\u51B2\u8EAB\u4F53\u5E27\u3002\u56DB\u8F68\u5171\u7528\u540C\u4E00\u56FA\u5B9A\u89C4\u5219\uFF0C\u6BD4\u8F83\u4E0D\u6D89\u53CA\u9057\u4F20\u53D8\u5316\u3002`, interpretation: R.interpretation || ["\u6709\u9650\u65F6\u7A97\u5185\u7684\u8EAB\u4F53\u72B6\u6001\u4F9D\u8D56\uFF0C\u4E0D\u7B49\u4E8E\u6062\u590D\u3001\u5B66\u4E60\u6216\u6297\u6270\u6536\u76CA\u3002\u56DB\u8F68\u4E0D\u662F\u72EC\u7ACB\u91CD\u590D\u3002"] };
}
function e6(L) {
  let E = L.outcome;
  return `\u751F\u547D\u671F ${E.lifetimeStep}\uFF1A\u8D28\u91CF ${E.mass.toFixed(2)}\uFF0C\u9608\u503C\u7EC4\u5206 ${E.components}\uFF08\u4E0D\u7B49\u540C\u751F\u7269\u6570\u91CF\uFF09\u3002${E.statusMeaning}`;
}
var e2 = "budgeted-offspring-g1";
var w1 = (L) => L.collectionId === e2;
var O7 = ["\u7236\u4F53\u590D\u7528 bf30 \u7684\u771F\u5B9E\u4ED8\u8D39\u8BB0\u5F55\uFF1B\u56DB\u4E2A\u5B50\u4EE3\u6765\u81EA\u552F\u4E00\u4F5C\u4E1A 10f94857\u3002\u6765\u6E90\u5916\u5C42\u8C31\u7CFB\u7B2C 4 \u2192 5 \u4EE3\uFF0C\u672C\u6B21\u4ED8\u8D39\u9636\u6BB5\u4E3A\u9996\u4EE3\uFF1B\u56DB\u4E2A\u54CD\u5E94\u53C2\u6570\u5171\u540C\u53D8\u52A8\uFF0C\u5404\u6BB5\u751F\u547D\u671F\u5185\u89C4\u5219\u56FA\u5B9A\u3002", "\u4E94\u8DEF\u4ECE\u540C\u4E00\u5B8C\u6574\u8EAB\u4F53\u3001\u80FD\u91CF\u548C\u6709\u9650\u4E16\u754C\u51FA\u53D1\uFF0Cm\u3001z\u3001p \u521D\u59CB\u4E3A\u96F6\u3002\u5B50\u4EE3\u91CD\u65B0\u4ECE\u521D\u6001\u8FD0\u884C\uFF0C\u4E0D\u662F\u7236\u4F53\u672B\u5E27\u8FDE\u7EED\u53D8\u5F62\uFF0C\u4E5F\u4E0D\u662F\u4E2A\u4F53\u5B66\u4E60\u6216\u81EA\u7136\u7E41\u6B96\u3002", "c01 \u8F6C\u5411\u5E76\u5728\u672B\u671F\u95ED\u5408\uFF0C\u672B\u7EC4\u7EC7 24.776 \u4F4E\u4E8E\u7236\u4F53 28.019\u3002c04 \u53CD\u590D\u8F6C\u5411\u4E0E\u5F3A\u53D8\u5F62\uFF0C\u7D2F\u8BA1\u6D88\u8017 P 926.960 / F 506.782\uFF0C\u4F4E\u4E8E\u7236\u4F53 972.438 / 525.902\uFF1B\u4F46\u665A\u671F\u5E73\u5747\u7EC4\u7EC7\u66F4\u4F4E\u3001\u6CE2\u52A8\u66F4\u5927\u3002\u5B83\u4EEC\u662F\u89C2\u770B\u4EE3\u8868\uFF0C\u6CA1\u6709\u603B\u51A0\u519B\u3002", "c02\u3001c03 \u4ECD\u53EF\u7FFB\u9875\u548C\u4ECE\u7236\u4EE3\u5C55\u5F00\uFF1B\u4E94\u8DEF\u5728 1024 \u6B65\u90FD\u4ECD\u6D3B\u52A8\u3002\u65B9\u5411\u6216\u901F\u5EA6\u4E0D\u540C\u4E0D\u6309\u65B0\u7269\u79CD\u8BA1\u6570\u3002\u5F62\u6001\u4F4D\u79FB\u6765\u81EA\u5C40\u90E8\u751F\u6210\u4E0E\u6D88\u5931\uFF0C\u4E0D\u662F\u7EC4\u7EC7\u8F93\u8FD0\uFF1B\u4E3B\u8FC7\u7A0B\u59CB\u7EC8\u91C7\u7528\u56FA\u5B9A\u539F\u5750\u6807\u3002", "\u03C1\u3001P\u3001F \u663E\u5F0F\u5BB9\u91CF\u4E3A 0\u20131\u3002\u6BCF\u751F\u6210 1 \u5355\u4F4D\u7EC4\u7EC7\u6D88\u8017 1 P \u548C 0.4 F\uFF0C\u5F62\u6210 0.2 E \u4E0E 0.2 \u70ED\uFF1B\u71C3\u6599\u7D2F\u8BA1\u6D88\u8017\u8FD8\u5305\u542B\u6444\u53D6\u3002\u5E93\u5B58\u4E0D\u8865\u5145\uFF0C\u4F4E\u6D88\u8017\u6216\u79FB\u52A8\u5FEB\u4E0D\u7B49\u4E8E\u9002\u5E94\u3002", "\u6BCF 8 \u6B65\u4E00\u5E45\u771F\u5B9E\u8EAB\u4F53\u5E27\uFF0C\u6A21\u578B\u65F6\u95F4 = \u6B65\u6570 \xD7 0.05\uFF0C\u8BB0\u5F55 0\u20131024 \u6B65\uFF1B\u672B\u5E27\u505C\u7559\u540E\u91CD\u64AD\u3002\u6CA1\u6709\u4E0B\u4E00\u4EE3\u3001\u5EF6\u957F\u7A97\u53E3\u6216\u72EC\u7ACB\u4E16\u754C\u9A8C\u8BC1\uFF1B\u8DEF\u5F84\u548C\u5C45\u4E2D\u5F62\u72B6\u56FE\u4EC5\u4F5C\u8BCA\u65AD\uFF0C\u4E0D\u4EE3\u66FF\u539F\u5750\u6807\u4E3B\u8FC7\u7A0B\u3002"];
var a6 = "developmental-rule-diversity";
var X2 = (L) => L.collectionId === a6;
function r6(L) {
  let E = L.presentation.moments.map((R) => R.step === 128 ? { ...R, label: L.id === "drd-r2-triad-fast" ? "\u5FEB\u652F\u5DF2\u6D88\u4EA1 \xB7 \u6162\u652F\u53CC\u5F2F\u6708" : "\u6162\u652F\u53CC\u5F2F\u6708" } : R);
  return L.id === "drd-r2-triad-fast" && E.splice(2, 0, { step: 112, label: "\u5FEB\u652F\u9996\u4E2A\u96F6\u5E27" }), { ...L.presentation, moments: E };
}
var i6 = "learned-action-timing";
var t2 = (L) => L.collectionId === i6;
var o6 = { learned: "\u9884\u7B97\u5B66\u4E60", frozen: "\u56FA\u5B9A\u4F30\u8BA1", immediate: "\u5373\u65F6\u63A7\u5236" };
var c6 = (L) => ({ ...L.presentation, title: o6[L.timing.mode] });
var f6 = "accessible-development";
var L7 = (L) => L.collectionId === f6;
var v6 = "memory-under-turnover-phase-a";
var F7 = (L) => L.collectionId === v6;
var x6 = "memory-under-turnover-phase-b";
var z6 = (L) => L.collectionId === x6;
var T6 = "developmental-excision";
var S6 = (L) => L.collectionId === T6;
var C6 = "excision-fuel-layout";
var O6 = (L) => L.collectionId === C6;
var F6 = "paid-intent-pulse";
var R6 = (L) => L.collectionId === F6;
var W6 = "embodied-action-learning";
var V6 = (L) => L.collectionId === W6;
var O9 = { "m2026101607-g1-0055": "\u7D27\u51D1\u8EAB\u4F53\u6CBF\u5468\u671F\u4E16\u754C\u6E38\u52A8\u3002", "m2026101607-g1-0060": "\u4E24\u4E2A\u5E73\u6ED1\u8EAB\u4F53\u5171\u540C\u6F02\u79FB\u3002", "m2026101607-g2-0133": "\u5C40\u90E8\u521D\u6001\u94FA\u5C55\u6210\u7EC6\u73AF\u6591\u70B9\u3002", "m2026101607-g1-0075": "\u73AF\u72B6\u521D\u6001\u5C55\u5F00\u4E3A\u66F2\u6298\u957F\u7EB9\u3002", "m2026101607-g2-0128": "\u79FB\u52A8\u4F53\u7684\u53D8\u5F02\u5206\u652F\u5F62\u6210\u73AF\u4E0E\u6761\u7EB9\u3002", "ls-12d8620a-b04-i036": "\u73AF\u5E26\u62C6\u5F00\uFF0C\u9010\u6E10\u53D8\u6210\u6591\u70B9\u3002", "ls-12d8620a-b05-i016": "\u8FD9\u6B21\u4F18\u5316\u7559\u4E0B\u8FD1\u9759\u6001\u9971\u548C\u6591\u70B9\u3002", "dk2026101619-r2-v5": "\u591A\u4E2A\u7ED3\u6784\u6301\u7EED\u5F62\u53D8\u3001\u5206\u79BB\u4E0E\u6F02\u79FB\u3002", "dk2026101619-r2-v4": "\u65E9\u671F\u591A\u4F53\u6D3B\u52A8\uFF0C\u968F\u540E\u9010\u6E10\u6D88\u5931\u3002", "dk2026101619-r4-v5": "\u4E09\u56E2\u521D\u6001\u5728\u73AF\u5F62\u4E0E\u79FB\u52A8\u4E4B\u95F4\u53D8\u5316\u3002" };
function G6(L) {
  return t2(L) ? c6(L) : X2(L) ? r6(L) : { title: L.presentation?.title || L.title.replace(/^r\d+-v\d+ · /, "").replace(/ · (?:\d{4}|b\d+\/i\d+)$/, ""), discovery: L.presentation?.discovery || O9[L.id] || (L.watchNote || L.summary).split(/[。；]/)[0] + "\u3002", moments: L.presentation?.moments || [] };
}
function s2(L) {
  let E = L.origin;
  return w1(L) ? `\u6765\u6E90\u5916\u5C42\u7B2C ${L.offspring.sourceOuterGeneration} \u4EE3 \xB7 ${L.offspring.role === "parent" ? "\u4ED8\u8D39\u7236\u4EE3" : "\u4ED8\u8D39\u9996\u4EE3"}` : Ke(L) ? "\u751F\u547D\u671F\u73AF\u5883\u5206\u652F" : E?.kind === "lifetime" ? "\u751F\u547D\u671F\u8BB0\u5F55" : E?.index == null ? "\u6765\u6E90\u9636\u6BB5\u672A\u6807\u5B9A" : E.kind === "optimization" ? `\u4F18\u5316\u8FED\u4EE3 ${E.index}` : E.kind === "generation" ? `\u5916\u5C42\u7B2C ${E.index} \u4EE3` : `\u6765\u6E90\u8BB0\u5F55 ${E.index}`;
}
function ht(L) {
  return L.morphology?.coordinateMode === "fixed" ? false : ["localized motile", "multiple smooth bodies", "localized reference"].includes(L.morphology?.sourceLabel);
}
function i2(L) {
  let E = L.outcome;
  if (!E) return "";
  if (F7(L)) return `\u751F\u547D\u671F ${E.observationEnd}\uFF1A${E.statusMeaning}`;
  if (Ke(L)) return e6(L);
  let R = L.rule?.schema === "lenia.fixed-dual-response.v1", H = [Mt(L) || w1(L) || X2(L) || t2(L) || L7(L) || z6(L) || S6(L) || O6(L) || R6(L) || V6(L) || m7(L) ? `\u751F\u547D\u671F ${E.observationEnd}\uFF1A\u7EC4\u7EC7 ${E.mass.toFixed(3)}\uFF0C\u80FD\u91CF ${E.energy.toFixed(3)}\u3002${E.statusMeaning}` : R ? `\u751F\u547D\u671F ${E.observationEnd}\uFF1A\u8D28\u91CF ${E.mass.toFixed(2)}\u3002${E.statusMeaning}` : E.label || (E.finiteWindowPass ? "\u6709\u9650\u7A97\u53E3\u901A\u8FC7" : "\u6709\u9650\u7A97\u53E3\u672A\u901A\u8FC7")], C = E.firstZeroInFreeRecordStep ?? E.exactZeroAtLifetimeStep, s = E.firstZeroDisplayFrameStep ?? E.firstDisplayedZeroFrameStep;
  return E.alreadyZeroAtLife64 ? H.push("\u5230\u751F\u547D\u671F 64 \u6B65\u5DF2\u5F52\u96F6\uFF0C\u4E0D\u80FD\u7528\u81EA\u7531\u8BB0\u5F55\u9996\u5E27\u63A8\u65AD\u6B7B\u4EA1\u65F6\u523B") : C != null && H.push(`\u9010\u6B65\u8D28\u91CF\u8BB0\u5F55\uFF1A${C} \u6B65\u5F52\u96F6`), s != null && H.push(`\u663E\u793A\u91C7\u6837\uFF1A${s} \u6B65\u9996\u6B21\u5168\u96F6`), E.finiteWindowPass && H.push("\u6709\u9650\u7A97\u53E3\u901A\u8FC7\u4E0D\u4EE3\u8868\u7A33\u5B9A\u65B0\u751F\u7269\u6216\u9002\u5E94\u6536\u76CA"), H.join("\uFF1B");
}
async function Y6(L, { sdk: E, signal: R, onObserve: H, onLearn: C, display: s, loadCatalog: T = h3 } = {}) {
  let a = L.ownerDocument, b = a.defaultView || globalThis, f = g1(s), e = 0, t = b.devicePixelRatio || 1, n = /* @__PURE__ */ new WeakMap(), o = (M0, S0 = "", X0 = "") => {
    let ee = a.createElement(M0);
    return ee.className = S0, ee.textContent = X0, ee;
  }, l = (M0, S0) => {
    let X0 = o("button", "portalButton", M0);
    return X0.type = "button", X0.addEventListener("click", S0), X0;
  }, v = false, h = false, r = true, A = 1, _ = false, p = 0, d = 0, i = null, y, w = /* @__PURE__ */ new Map(), z = /* @__PURE__ */ new Map(), M = [], P = "overview", c = 0, g = 0, u = /* @__PURE__ */ new Set(), m = null, k = 0, x = "", j = "", O = "", S = null, D = /* @__PURE__ */ new Map(), W = 3, U = (M0) => M0.clip?.steps[0] ?? M0.entry.time?.clipWindow?.start ?? 0, F = (M0) => M0.clip?.steps.at(-1) ?? M0.entry.time?.clipWindow?.end ?? 1 / 0, I = o("section", "morphology-wall");
  I.setAttribute("aria-label", "\u771F\u5B9E\u5F62\u6001\u5899");
  let N = o("div", "morphology-heading"), Z = o("div");
  Z.append(o("p", "morphology-eyebrow", "\u4E0D\u540C\u63A2\u7D22 \xB7 \u540C\u4E00\u523B\u89C2\u770B"), o("h2", "", "\u751F\u547D\u7684\u5F62\u72B6"));
  let q = o("div", "morphology-tools"), $2 = l("\u5168\u90E8\u6682\u505C", () => {
    r = !r, $2.textContent = r ? "\u5168\u90E8\u6682\u505C" : "\u7EE7\u7EED\u64AD\u653E";
  });
  $2.dataset.action = "wall-pause";
  let r0 = o("select");
  r0.setAttribute("aria-label", "\u5F62\u6001\u5899\u64AD\u653E\u500D\u7387");
  for (let M0 of [0.25, 0.5, 1, 2]) {
    let S0 = o("option", "", `${M0}\xD7`);
    S0.value = String(M0), r0.append(S0);
  }
  r0.value = "1", r0.addEventListener("change", () => {
    A = Number(r0.value), B0();
  });
  let Y = l("\u56FA\u5B9A\u5750\u6807", () => {
    _ = !_, Y.textContent = _ ? "\u73AF\u9762\u8DDF\u968F" : "\u56FA\u5B9A\u5750\u6807", Y.setAttribute("aria-pressed", String(_));
    for (let M0 of w.values()) M0.dirty = true;
    B0(), Z0();
  });
  Y.setAttribute("aria-pressed", "false"), q.append($2, r0, Y), N.append(Z, q);
  let a0 = o("div", "morphology-legend"), V = o("i", "morphology-density");
  V.style.background = E1(f);
  let Q = o("span", "", "1"), J = o("span", "morphology-scale", "\u540C\u8272\u6807 \xB7 \u56FA\u5B9A\u7A7A\u95F4\u5C3A\u5EA6 \xB7 \u5DF2\u8BB0\u5F55\u5E27");
  a0.append(o("span", "", "\u5BC6\u5EA6 0"), V, Q, J);
  let B = o("p", "morphology-status", "\u6B63\u5728\u8BFB\u53D6\u771F\u5B9E\u5F62\u6001\u8BB0\u5F55\u2026");
  B.setAttribute("role", "status");
  let G = o("div", "morphology-grid"), t0 = o("div", "morphology-browse"), K2 = o("select");
  K2.setAttribute("aria-label", "\u63A2\u7D22\u5206\u652F"), K2.addEventListener("change", () => v0(K2.value));
  let s0 = l("\u4E0A\u4E00\u7EC4", () => {
    c--, G0(), se();
  }), e0 = l("\u4E0B\u4E00\u7EC4", () => {
    c++, G0(), se();
  }), n0 = o("span", "morphology-page"), i0 = l("\u672C\u7EC4\u4ECE\u8D77\u70B9\u64AD\u653E", () => {
    let M0 = M.find((X0) => X0.id === P), S0 = M0?.synchronized ? M0.entries.map((X0) => w.get(X0.id)) : [...w.values()].filter((X0) => !X0.card.hidden);
    if (M0?.synchronized && S0.length) k0(S0[0], M0.start ?? U(S0[0]));
    else for (let X0 of S0) X0.step = U(X0);
    for (let X0 of S0) X0.hold = 0, X0.loops = 0;
    M0?.clock && S0.length && Object.assign(M0.clock, { step: S0[0].step, hold: 0, loops: 0 }), r = true, $2.textContent = "\u5168\u90E8\u6682\u505C", d = 0, Z0();
  });
  t0.append(K2, s0, n0, e0, i0);
  let c0 = o("section", "morphology-environment");
  c0.hidden = true;
  let d0, o0, f0, h0 = o("section", "morphology-detail");
  h0.hidden = true, h0.setAttribute("aria-label", "\u9009\u4E2D\u5F62\u6001\u4E0E\u771F\u5B9E\u524D\u8EAB"), I.append(N, a0, t0, B, c0, h0, G), L.append(I), I.dataset.display = f.mode;
  let u0 = 64, p0 = [], y0, w0, x0, z0, A0 = 1, N0 = true, E0, F0, V0, H0 = (M0) => Ke(M0) ? M.find((S0) => S0.kind === "environment" && S0.sourceId === M0.sourceCandidateId) : void 0;
  function U0(M0) {
    let S0 = M.find((X0) => X0.id === P);
    return S0?.synchronized && S0.entries.some((X0) => X0.id === M0.id) ? S0 : z.get(M0.id);
  }
  function Y0(M0) {
    let S0 = M0.entries.map((X0) => w.get(X0.id));
    return S0.find((X0) => !X0.card.hidden && X0.clip) || S0.find((X0) => X0.clip) || S0[0];
  }
  function R0(M0) {
    let S0 = M.find((X0) => X0.id === P);
    return y?.prepareEntries && !w.get(M0.id)?.prepared ? { title: M0.title || M0.id, discovery: "\u5F85\u9009\u4E2D\u540E\u8BFB\u53D6\u5B8C\u6574\u8BB0\u5F55", moments: [] } : { ...G6(M0), ...S0?.comparison?.entryPresentation?.(M0) };
  }
  function D0(M0) {
    if (M0?.clock && M0.clock.step != null) for (let S0 of M0.entries) Object.assign(w.get(S0.id), M0.clock);
  }
  function k0(M0, S0) {
    let X0 = U0(M0.entry), ee = X0?.entries.map((L0) => w.get(L0.id)) || [M0];
    for (let L0 of ee) L0.step = Math.max(X0?.start ?? U(L0), Math.min(S0, X0?.end ?? F(L0))), L0.hold = 0;
    X0?.clock && Object.assign(X0.clock, { step: ee[0].step, hold: 0 });
  }
  function v0(M0, { step: S0 } = {}) {
    if (v || !M.some((ee) => ee.id === M0)) return;
    P = M0, c = 0, G0();
    let X0 = M.find((ee) => ee.id === M0);
    (X0?.comparison || X0?.synchronized) && Number.isFinite(S0) && (X0.opened = true, k0(w.get(X0.entries[0].id), S0), d = 0), X0?.kind === "environment" && !X0.opened && (X0.opened = true, k0(w.get(X0.entries[0].id), X0.secondFork), r = false, $2.textContent = "\u7EE7\u7EED\u64AD\u653E"), X0?.comparison && !X0.opened && (X0.opened = true, k0(w.get(X0.entries[0].id), 0), r = true, $2.textContent = "\u5168\u90E8\u6682\u505C", d = 0), se();
  }
  function T0(M0) {
    let S0 = a.createElementNS("http://www.w3.org/2000/svg", "svg");
    S0.setAttribute("viewBox", `0 0 ${u0} ${u0}`), S0.setAttribute("class", "morphology-stimulus-layer"), S0.setAttribute("aria-hidden", "true"), S0.setAttribute("hidden", "");
    let X0 = a.createElementNS(S0.namespaceURI, "path"), ee = "";
    for (let L0 = 0; L0 < u0 * 2; L0 += 7) ee += L0 <= u0 ? `M${L0} 0L0 ${L0}` : `M${u0} ${L0 - u0}L${L0 - u0} ${u0}`;
    return X0.setAttribute("d", ee), X0.setAttribute("stroke", "#ffc97a"), X0.setAttribute("stroke-width", "1"), X0.setAttribute("opacity", ".28"), S0.append(X0), M0.append(S0), S0;
  }
  function _0(M0, S0, X0, ee) {
    if (!M0) return;
    let L0 = K3(X0, ee), q0 = X0.environment.spatialScope?.kind === "full_grid" && X0.environment.spatialScope.coefficientUniform;
    M0.toggleAttribute("hidden", !(q0 && L0?.intensity > 0)), S0.dataset.active = String(L0?.intensity > 0), S0.textContent = L0 ? `${q0 ? "\u5747\u5300\u5168\u573A" : "\u8303\u56F4\u672A\u6807\u5B9A"} \xB7 I=${L0.intensity} \xB7 [${L0.start}, ${L0.stop})${L0.intensity === 0 ? " \xB7 \u96F6\u5F3A\u5EA6\u5BF9\u7167" : " \xB7 \u5BC6\u5EA6\u6BD4\u4F8B\u635F\u8017"}` : "\u5F53\u524D I=0 \xB7 \u65E0\u523A\u6FC0";
  }
  function j0(M0) {
    f0?.setVisible?.(false), f0?.destroy?.(), f0 = null, c0.replaceChildren(), d0 = o0 = null;
    let S0 = M0.kind === "environment";
    if (c0.hidden = !S0, I.dataset.stateBranches = String(S0), I.dataset.columns = String(M0.columns || 3), I.dataset.collectionKind = M0.kind || "records", I.dataset.view = M0.view || "cards", Q.textContent = String(M0.densityRange?.[1] ?? 1), J.textContent = M0.legendNote || "\u540C\u8272\u6807 \xB7 \u56FA\u5B9A\u7A7A\u95F4\u5C3A\u5EA6 \xB7 \u5DF2\u8BB0\u5F55\u5E27", M0.contextReady === false || (M0.comparison || S0) && !Y0(M0)?.clip) {
      c0.hidden = false, c0.append(o("p", "", "\u672C\u7EC4\u7247\u6BB5\u6309\u9700\u8BFB\u53D6\u4E2D\uFF1B\u5206\u7C7B\u3001\u5206\u9875\u4ECD\u53EF\u4F7F\u7528\u3002"));
      return;
    }
    if (M0.comparison) {
      g0(M0);
      return;
    }
    if (!S0) return;
    let X0 = Y0(M0), ee = M0.firstFork, L0 = M0.secondFork, q0 = $3(X0.entry, X0.clip.steps), le = o("p", "", `\u540C\u4E00\u8EAB\u4F53\uFF0C\u4ECE ${ee} \u6B65\u5206\u6210\u4E24\u79CD\u7ECF\u5386\uFF0C\u5728 ${L0} \u6B65\u5404\u81EA\u63A5\u53D7\u518D\u6B21\u523A\u6FC0\u6216\u96F6\u5F3A\u5EA6\u5BF9\u7167\u3002`), ge = o("div", "morphology-tools");
    for (let [Oe, Ee] of q0.phases) ge.append(l(`${Oe} \xB7 ${Ee}`, () => {
      k0(X0, Ee), r = false, $2.textContent = "\u7EE7\u7EED\u64AD\u653E", Z0();
    }));
    ge.append(l(`\u8FD4\u56DE ${M0.sourceLabel} \u5019\u9009`, () => Ze(M0.sourceId, ee))), d0 = o("input"), d0.type = "range", d0.min = String(X0.clip.steps[0]), d0.max = String(X0.clip.steps.at(-1)), d0.step = "1", d0.setAttribute("aria-label", "\u73AF\u5883\u5206\u652F\u5171\u540C\u751F\u547D\u671F\u65F6\u95F4"), d0.addEventListener("input", () => {
      k0(X0, Number(d0.value)), r = false, $2.textContent = "\u7EE7\u7EED\u64AD\u653E", Z0();
    }), m0(d0, X0), o0 = o("p", "morphology-environment-clock");
    let Re = o("p", "morphology-environment-note", q0.sampling), xe = o("details", "morphology-environment-protocol");
    xe.append(o("summary", "", "\u523A\u6FC0\u542B\u4E49\u4E0E\u6709\u9650\u89C2\u5BDF\u7ED3\u8BBA")), xe.append(o("p", "", "\u56DB\u8F68\u5171\u4EAB\u524D\u7F00\uFF0C\u662F\u540C\u4E00\u8EAB\u4F53\u7684\u53CD\u4E8B\u5B9E\u5EF6\u7EED\uFF0C\u5E76\u975E\u56DB\u4E2A\u72EC\u7ACB\u6837\u672C\u3002"), Re, o("p", "", q0.intensity), o("p", "", q0.update), o("p", "", q0.windows));
    for (let Oe of q0.interpretation) xe.append(o("p", "", Oe));
    c0.append(le, ge, d0, o0, xe);
  }
  function m0(M0, S0) {
    M0.addEventListener("keydown", (X0) => {
      if (X0.ctrlKey || X0.metaKey || X0.altKey) return;
      let ee = m3(S0.clip.steps, Number(M0.value), X0.key);
      ee != null && (X0.preventDefault(), k0(S0, ee), r = false, $2.textContent = "\u7EE7\u7EED\u64AD\u653E", Z0());
    });
  }
  function g0(M0) {
    c0.hidden = false;
    let S0 = Y0(M0), X0 = M0.comparison, ee = o("p", "", X0.intro), L0 = o("div", "morphology-tools");
    for (let [le, ge] of X0.moments) L0.append(l(`${le} \xB7 ${ge}`, () => {
      k0(S0, ge), r = false, $2.textContent = "\u7EE7\u7EED\u64AD\u653E", Z0();
    }));
    for (let le of X0.relatedCollections || []) L0.append(l(le.label, () => v0(le.id, Number.isFinite(le.step) ? { step: le.step } : le.preserveClock ? { step: S0.step } : {})));
    if (X0.relatedChoices?.items.length) {
      let le = X0.relatedChoices, ge = o("select");
      ge.setAttribute("aria-label", le.label), ge.style.width = "min(100%, 25rem)";
      let Re = o("option", "", le.label);
      Re.value = "", ge.append(Re);
      for (let xe of le.items) {
        let Oe = o("option", "", xe.label);
        Oe.value = xe.id, ge.append(Oe);
      }
      ge.addEventListener("change", () => {
        let xe = le.items.find((Oe) => Oe.id === ge.value);
        xe && v0(xe.id, Number.isFinite(xe.step) ? { step: xe.step } : xe.preserveClock ? { step: S0.step } : {});
      }), L0.append(ge);
    }
    d0 = o("input"), d0.type = "range", d0.min = String(M0.start ?? S0.clip.steps[0]), d0.max = String(M0.end ?? S0.clip.steps.at(-1)), d0.step = "1", d0.setAttribute("aria-label", X0.timelineLabel), d0.addEventListener("input", () => {
      k0(S0, Number(d0.value)), r = false, $2.textContent = "\u7EE7\u7EED\u64AD\u653E", Z0();
    }), m0(d0, S0), o0 = o("p", "morphology-environment-clock");
    let q0 = o("details", "morphology-environment-protocol");
    q0.append(o("summary", "", X0.detailsTitle));
    for (let le of X0.explanation) q0.append(o("p", "", le));
    c0.append(ee, L0, d0, o0, q0), M0.entries.filter((le) => !w.get(le.id).card.hidden).every((le) => w.get(le.id).clip) && (f0 = X0.mountReadout?.(c0, { sdk: E, signal: R, getRecording: (le) => {
      let ge = w.get(le);
      return ge?.clip ? { entry: ge.entry, clip: ge.clip } : void 0;
    }, getDisplay: () => f, visibleEntries: M0.entries.filter((le) => !w.get(le.id).card.hidden) })), f0?.setVisible?.(h);
  }
  function C0(M0, S0, X0, ee = false, L0 = false) {
    let { clip: q0 } = S0, le = q0.width * q0.height, ge = !ee && (L0 ? N0 : _) && ht(S0.entry), Re = L0 && !ee && ht(S0.entry) ? A0 : 1, xe = n.get(M0);
    if (xe && xe.record === S0 && xe.frameIndex === X0 && xe.following === ge && xe.zoom === Re && xe.revision === e) return xe.result;
    let Oe = q0.frames.subarray(X0 * le, (X0 + 1) * le), Ee = U0(S0.entry), Ne = zt(M0, { frame: Oe, width: q0.width, height: q0.height, extent: Ee?.displayExtent || u0, follow: ge, zoom: Re, display: f, densityDomain: S0.entry.displayRange || [0, 1] });
    return Ee?.comparison?.paintOverlay?.(M0, { entry: S0.entry, step: q0.steps[X0], camera: Ne }), M0.dataset.step = String(q0.steps[X0]), n.set(M0, { record: S0, frameIndex: X0, following: ge, zoom: Re, revision: e, result: Ne }), Ne;
  }
  function Z0() {
    let M0 = b.devicePixelRatio || 1;
    M0 !== t && (t = M0, be());
    for (let S0 of w.values()) {
      if (!S0.clip || S0.card.hidden && S0.entry.id !== i) continue;
      let X0 = De(S0.clip.steps, S0.step);
      if (S0.frame !== X0 || S0.dirty) {
        S0.frame = X0, S0.dirty = false, C0(S0.canvas, S0, X0);
        let ee = U0(S0.entry)?.comparison?.frameNote?.(S0.entry, S0.clip.steps[X0]);
        S0.time.textContent = `${S0.clip.steps[X0]} ${S0.unit} \xB7 ${ht(S0.entry) && _ ? "\u8DDF\u968F\u89C6\u56FE" : "\u539F\u5750\u6807"}${ee ? " \xB7 " + ee : ""}${S0.loops ? " \xB7 \u91CD\u64AD" : ""}`;
      }
      _0(S0.overlay, S0.pulseLabel, S0.entry, S0.step);
    }
    if (i) {
      let S0 = w.get(i);
      for (let { canvas: L0, record: q0, label: le, overlay: ge, pulseLabel: Re, world: xe, viewLabel: Oe, metrics: Ee } of p0) {
        let Ne = S0.step, a1 = De(q0.clip.steps, Ne), o1 = C0(L0, q0, a1, p0.length > 1, true), h1 = U0(q0.entry)?.comparison?.detailSummary?.(q0.entry, q0.clip.steps[a1]);
        if (le.textContent = `${p0.length > 1 && !Ke(q0.entry) ? s2(q0.entry) + " \xB7 " : ""}${R0(q0.entry).title} \xB7 ${q0.clip.steps[a1]} ${q0.unit}${h1 ? `
` + h1 : ""}`, xe && (C0(xe, q0, a1, true), Oe.textContent = L0.dataset.fallback ? "\u4E2D\u5FC3\u4E0D\u53EF\u9760 \xB7 \u5B8C\u6574\u4E16\u754C 1\xD7" : `${N0 ? "\u8DDF\u968F\u89C6\u56FE" : "\u56FA\u5B9A\u89C6\u56FE"} \xB7 ${o1.zoom}\xD7 \xB7 \u89C6\u91CE ${o1.viewCells} \u683C`), _0(ge, Re, q0.entry, Ne), Ee) {
          let Je = Ee.sample(q0.entry, q0.clip.steps[a1], Ee.fields);
          Ee.root.dataset.step = String(Je?.step ?? "");
          for (let [_1, $e] of Ee.values) $e.textContent = Ee.format ? Ee.format(Je?.[_1]) : Number.isFinite(Je?.[_1]) ? Je[_1].toFixed(3).replace(/^-0\.000$/, "0.000") : "\u672A\u8BB0\u5F55";
        }
      }
      if (F0) {
        let L0 = p0[0].record, q0 = L0.clip.steps[De(L0.clip.steps, S0.step)], le = S0.clip.steps[De(S0.clip.steps, S0.step)];
        F0.textContent = q0 === le ? `${w1(S0.entry) ? "\u5916\u5C42\u7236\u4EE3 \u2192 \u5B50\u4EE3 \xB7 \u5404\u81EA\u4ECE\u5171\u540C\u521D\u6001\u8FD0\u884C" : "\u771F\u5B9E\u524D\u8EAB \u2192 \u5F53\u524D\u8BB0\u5F55"} \xB7 \u540C\u4E00\u751F\u547D\u671F\u6B65\u5BF9\u7167\uFF08${le} \u6B65\uFF09` : le > L0.clip.steps.at(-1) ? `\u524D\u8EAB\u8BB0\u5F55\u6B62\u4E8E ${L0.clip.steps.at(-1)} \u6B65\uFF1B\u5F53\u524D ${le} \u6B65 \xB7 \u975E\u540C\u4E00\u6B65\u5BF9\u7167` : `\u524D\u8EAB\u5B9E\u9645\u91C7\u6837 ${q0} \u6B65\uFF1B\u5F53\u524D ${le} \u6B65 \xB7 \u975E\u540C\u4E00\u6B65\u5BF9\u7167`;
      }
      w0.value = String(S0.clip.steps[S0.frame]);
      let X0 = S0.entry.time?.dt || S0.entry.rule?.dt, ee = U0(S0.entry);
      y0.textContent = `\u751F\u547D\u671F ${S0.clip.steps[S0.frame]} ${S0.unit}${X0 ? ` \xB7 \u6A21\u578B\u65F6\u95F4 ${(S0.clip.steps[S0.frame] * X0).toFixed(2)}` : ""} \xB7 \u8BB0\u5F55 ${ee?.start ?? S0.clip.steps[0]}\u2013${ee?.end ?? S0.clip.steps.at(-1)} \xB7 \u672B\u5E27\u505C\u7559\u540E\u91CD\u64AD`;
    }
    if (o0) {
      let S0 = M.find((L0) => L0.id === P), X0 = Y0(S0);
      d0.value = String(Math.floor(X0.step));
      let ee = X0.clip.steps[De(X0.clip.steps, X0.step)];
      f0?.update(ee), o0.textContent = S0.comparison ? `\u5171\u540C\u751F\u547D\u671F ${ee} \u6B65 \xB7 \u6A21\u578B\u65F6\u95F4 ${(ee * X0.entry.time.dt).toFixed(2)} \xB7 ${A}\xD7 / ${(S0.stepsPerSecond || X0.rate) * A} \u6B65\u6BCF\u79D2 \xB7 \u56FA\u5B9A\u539F\u5750\u6807${X0.loops ? " \xB7 \u91CD\u64AD" : ""}${S0.comparison.phaseAt ? "\u3002" + S0.comparison.phaseAt(ee) : ""}` : `\u5171\u540C\u523A\u6FC0\u65F6\u949F ${Math.floor(X0.step)} \u6B65 \xB7 \u8EAB\u4F53\u91C7\u6837 ${ee} \u6B65 \xB7 ${A}\xD7 / ${C7(X0.entry, X0.step) * A} \u6B65\u6BCF\u79D2 \xB7 \u56FA\u5B9A\u539F\u59CB\u5750\u6807`;
    }
  }
  function B0() {
    if (!x0 || !i || !w.get(i)?.clip) return;
    let M0 = w.get(i), S0 = U0(M0.entry), X0 = S0?.comparison;
    if (V0 || X0?.detailLabel) {
      x0.textContent = `${V0 || X0.detailLabel} \xB7 ${((S0.stepsPerSecond || M0.rate) * A).toFixed(1)} \u6B65/\u79D2 \xB7 \u6807\u5C3A 8 \u683C`;
      return;
    }
    if (Mt(M0.entry)) {
      x0.textContent = `\u4ED8\u8D39\u751F\u6210 / \u5173\u95ED\u751F\u6210 \xB7 \u540C\u4E00\u751F\u547D\u671F\u6B65 \xB7 ${(M0.rate * A).toFixed(1)} \u6B65/\u79D2 \xB7 \u4E24\u4FA7\u56FA\u5B9A\u539F\u5750\u6807\u3001\u540C\u5C3A\u540C\u8272 \xB7 \u6807\u5C3A 8 \u683C`;
      return;
    }
    x0.textContent = `\u64AD\u653E ${A}\xD7${Ke(M0.entry) ? " \xB7 \u5171\u7528\u73AF\u5883\u65F6\u95F4\u8F74" : ` \xB7 ${(M0.rate * A).toFixed(1)} ${M0.unit}/\u79D2`} \xB7 \u6807\u5C3A 8 \u683C \xB7 ${p0.length > 1 ? Ke(M0.entry) ? "\u540C\u53F2\u63A2\u6D4B/\u5BF9\u7167 \xB7 \u56FA\u5B9A\u539F\u59CB\u5750\u6807" : "\u524D\u8EAB\u5BF9\u7167\u4E24\u4FA7\u56FA\u5B9A\u539F\u59CB\u5750\u6807" : N0 && ht(M0.entry) ? "\u8DDF\u968F\u4EC5\u5E73\u79FB\u89C6\u91CE\uFF0C\u539F\u4E16\u754C\u89C1\u5C0F\u56FE" : "\u56FA\u5B9A\u539F\u59CB\u5750\u6807"}`;
  }
  function G0() {
    i && w.get(i)?.select.setAttribute("aria-pressed", "false"), m = null;
    for (let M0 of p0) qe(M0.canvas), M0.world && qe(M0.world);
    i = null, z0 = null, p0 = [], F0 = null, h0.hidden = true, x0 = null, queueMicrotask($0);
  }
  function se() {
    let M0 = M.find((L0) => L0.id === P);
    if (!M0) return;
    D0(M0), be();
    let S0 = M0.pageSize || 6, X0 = Math.max(1, Math.ceil(M0.entries.length / S0));
    c = Math.max(0, Math.min(X0 - 1, c));
    let ee = new Set(M0.entries.slice(c * S0, c * S0 + S0).map((L0) => L0.id));
    for (let [L0, q0] of w) q0.card.hidden = !ee.has(L0), q0.dirty = true;
    for (let L0 of ee) {
      let q0 = w.get(L0);
      if (G.append(q0.card), !Ke(q0.entry)) {
        let le = R0(q0.entry);
        q0.card.querySelector(".morphology-caption h3").textContent = le.title, q0.card.querySelector(".morphology-highlight").textContent = le.discovery;
      }
    }
    K2.value = P, s0.disabled = c === 0, e0.disabled = c === X0 - 1, s0.hidden = e0.hidden = X0 === 1, s0.textContent = M0.kind === "offspring" ? "\u7236\u4F53\u4E0E\u4EE3\u8868\u5B50\u4EE3" : "\u4E0A\u4E00\u7EC4", e0.textContent = M0.kind === "offspring" ? "\u5176\u4F59\u5B50\u4EE3 c02 / c03" : "\u4E0B\u4E00\u7EC4", n0.textContent = `${c + 1} / ${X0} \xB7 ${M0.entries.length} \u6BB5`, G.style.setProperty("--wall-rows", Math.ceil(ee.size / (M0.columns || 3))), W0(M0), re(M0), $0(), Z0();
  }
  function re(M0) {
    if (M0.contextReady === false) {
      B.textContent = "\u6309\u76EE\u5F55\u6D4F\u89C8 \xB7 \u4EC5\u5728\u9009\u4E2D\u65F6\u8BFB\u53D6\u5B8C\u6574\u8BB0\u5F55\u4E0E\u6BD4\u8F83\u4E0A\u4E0B\u6587";
      return;
    }
    B.textContent = M0.comparison?.wallNote || (M0.kind === "offspring" ? c === 0 ? "\u4F9D\u6B21\u4E3A\u4ED8\u8D39\u7236\u4F53\u3001c01\u8F6C\u5411\u95ED\u5408\u3001c04\u53CD\u590D\u5F62\u53D8\u3002\u70B9\u9009\u5B50\u4EE3\u4E0E\u771F\u5B9E\u7236\u4EE3\u5E76\u6392\u89C2\u770B\u3002" : "c02\u3001c03\u5B8C\u6574\u4FDD\u7559\uFF1B\u4E0E\u7236\u4F53\u76F8\u8FD1\u7684\u79FB\u52A8\u5206\u652F\u3002\u4E94\u8DEF\u5171\u7528\u751F\u547D\u671F\u65F6\u95F4\u3002" : M0.kind === "formation" ? "\u524D\u4E24\u9879\u4E09\u56E2\u5757\u6765\u6E90 r4\uFF0C\u540E\u4E24\u9879\u6210\u5BF9\u6765\u6E90 r2\uFF1B\u6BCF\u5BF9\u4F9D\u6B21\u4E3A\u4ED8\u8D39\u751F\u6210\u3001\u5173\u95ED\u751F\u6210\u3002\u70B9\u9009\u5E76\u6392\u7EC6\u770B\u3002" : M0.kind === "environment" ? "\u524D\u4E24\u9879\u65E0\u9996\u523A\u6FC0\uFF0C\u540E\u4E24\u9879\u6709\u9996\u523A\u6FC0\uFF1B\u6BCF\u5BF9\u6309\u201C\u96F6\u5F3A\u5EA6\u5BF9\u7167 \u2192 \u518D\u6B21\u523A\u6FC0\u201D\u6392\u5217" : M0.id === "overview" ? "\u4ECE\u521D\u6001\u770B\u5F62\u6210\uFF0C\u518D\u770B\u5B83\u5982\u4F55\u8FD0\u52A8\u3002\u70B9\u9009\u753B\u9762\uFF0C\u5C55\u5F00\u8FC7\u7A0B\u3002" : M0.id === "all" ? "\u5168\u90E8\u52A8\u753B\u8BB0\u5F55 \xB7 \u6BCF\u6BB5\u8BB0\u5F55\u53EA\u5217\u4E00\u6B21 \xB7 \u4E0A\u4E00\u7EC4\u3001\u4E0B\u4E00\u7EC4\u7FFB\u9875\u6D4F\u89C8" : M0.entries.every((S0) => S0.origin?.kind === "optimization") ? "\u6BCF\u683C\u662F\u4E00\u8F6E\u771F\u5B9E\u4F18\u5316\u7559\u4E0B\u7684\u89C4\u5219\uFF0C\u5404\u81EA\u64AD\u653E\u4ECE\u521D\u6001\u5F00\u59CB\u7684\u751F\u547D\u671F\u3002" : "\u6BCF\u683C\u64AD\u653E\u4E00\u6BB5\u771F\u5B9E\u751F\u547D\u671F \xB7 \u70B9\u9009\u89C2\u770B\u6765\u6E90\u4E0E\u5173\u952E\u53D8\u5316");
  }
  function K0(M0, S0) {
    if (v || !h || S0 !== k) return;
    p = 0;
    let X0 = d ? Math.min((M0 - d) / 1e3, 0.2) : 0;
    if (d = M0, r) {
      let ee = M.find((L0) => L0.id === P);
      if (ee?.synchronized) {
        let L0 = Y0(ee);
        if (L0.clip) {
          let q0 = ee.kind === "environment" ? C7(L0.entry, ee.clock.step) : ee.stepsPerSecond || L0.rate;
          u7(ee.clock, ee.clockClip || L0.clip, X0, q0 * A), D0(ee);
        }
      } else for (let L0 of w.values()) L0.clip && (!L0.card.hidden || L0.entry.id === i) && u7(L0, L0.clip, X0, L0.rate * A);
    }
    Z0(), h && !v && S0 === k && (p = requestAnimationFrame((ee) => K0(ee, S0)));
  }
  function de() {
    k++, cancelAnimationFrame(p), p = 0, d = 0;
  }
  function ue() {
    let M0 = a.fullscreenElement;
    if (I.dataset.collectionKind !== "regenerative-branches" || !M0?.contains(I) || b.innerWidth < 760) {
      G.style.removeProperty("--branch-grid-height");
      return;
    }
    let S0 = G.getBoundingClientRect().top + M0.scrollTop, X0 = `${Math.max(300, Math.floor(b.innerHeight - S0 - 30))}px`;
    G.style.getPropertyValue("--branch-grid-height") !== X0 && G.style.setProperty("--branch-grid-height", X0);
  }
  function be() {
    ue(), e++;
    for (let M0 of w.values()) M0.dirty = true;
  }
  function oe(M0) {
    v || (f = g1(M0, f), I.dataset.display = f.mode, V.style.background = E1(f), be(), h && Z0());
  }
  let ce = b.ResizeObserver ? new b.ResizeObserver(be) : null;
  ce?.observe(I), ce?.observe(h0), ce?.observe(c0), b.addEventListener?.("resize", be), a.addEventListener("fullscreenchange", be);
  function J0() {
    if (!v && (h = true, f0?.setVisible?.(true), be(), $0(), i1(), !p)) {
      let M0 = k;
      p = requestAnimationFrame((S0) => K0(S0, M0));
    }
  }
  function Ae() {
    h = false, f0?.setVisible?.(false), de(), $0();
  }
  function ae() {
    if (!v) {
      v = true, Ae(), ce?.disconnect(), f0?.setVisible?.(false), f0?.destroy?.(), f0 = null, b.removeEventListener?.("resize", be), a.removeEventListener("fullscreenchange", be);
      for (let M0 of w.values()) qe(M0.canvas);
      for (let M0 of p0) qe(M0.canvas), M0.world && qe(M0.world);
      y?.destroy(), D.clear(), w.clear(), z.clear(), R?.removeEventListener("abort", ae), I.remove();
    }
  }
  R?.addEventListener("abort", ae, { once: true }), R?.aborted && ae();
  function fe() {
    if (!y.prepareEntries) return;
    let M0 = M.find((Ee) => Ee.id === P), S0 = q7(y.preparedEntries || y.entries.filter((Ee) => !Ee.metadataOnly), y).find((Ee) => Ee.id === M0?.id);
    if (!S0) return;
    let { id: X0, title: ee, entries: L0, default: q0, pageSize: le, clock: ge, clockClip: Re, opened: xe, ...Oe } = S0;
    if (Object.assign(M0, Oe, { contextReady: true }), M0.contextRevision = (M0.contextRevision || 0) + 1, M0.synchronized) {
      M0.clock ||= { step: M0.start ?? null, hold: 0, loops: 0 }, M0.start != null && M0.end != null && (M0.clockClip = { steps: [M0.start, M0.end] });
      for (let Ee of M0.entries) z.has(Ee.id) || z.set(Ee.id, M0);
    }
  }
  function W0(M0) {
    let S0 = M0.entries.map((ee) => w.get(ee.id)).filter((ee) => !ee.card.hidden), X0 = `${M0.id}/${M0.contextRevision || 0}/${c}/${Y0(M0)?.entry.id}/${!!Y0(M0)?.clip}/${S0.every((ee) => ee.clip)}`;
    X0 !== x && (x = X0, j0(M0));
  }
  function te(M0) {
    let S0 = w.get(M0.id), X0 = M.find((xe) => xe.id === P);
    if (!S0) return [];
    if (!S0.prepared) return [S0];
    let ee = X0?.comparison, L0 = ee?.detailModes?.(S0.entry) || [], q0 = L0.find((xe) => xe.id === M0.viewId) || L0[0], le = ee?.pairEntries?.(S0.entry), ge = Ke(S0.entry) && w.get(S0.entry.stateLineage.sharedPrefix.peerEntryId), Re = qt(S0.entry, Array.from(w.values(), (xe) => xe.entry));
    return [S0, ...q0 ? q0.entries.map((xe) => w.get(xe.id)) : le ? le.map((xe) => w.get(xe.id)) : ge ? [ge] : Re && M0.compare ? [w.get(Re.id)] : []].filter(Boolean);
  }
  function $0() {
    if (u = /* @__PURE__ */ new Set(), h && !v) {
      for (let M0 of w.values()) M0.card.hidden || u.add(M0.entry.id);
      for (let M0 of m ? te(m) : p0.map((S0) => S0.record)) u.add(M0.entry.id);
    }
    for (let M0 of w.values()) M0.job && !u.has(M0.entry.id) && M0.job.abort();
    S && (!h || v || S.key !== ve()) && S.controller.abort(), we();
  }
  function pe(M0) {
    if (!(!M0 || v)) {
      if (M0.errorPhase === "prepare") {
        O = "";
        for (let S0 of w.values()) S0.errorPhase === "prepare" && (S0.error = null, S0.errorPhase = null, S0.retry.hidden = true);
      }
      M0.error = null, M0.retry.hidden = true, $0(), i1();
    }
  }
  function ve() {
    return `${P}/${[...u].join(",")}`;
  }
  function we() {
    if (!(!h || v)) {
      if (y.prepareEntries && j !== ve()) {
        if (S || g >= W || O === ve()) return;
        let M0 = M.find((L0) => L0.id === P), S0 = [.../* @__PURE__ */ new Set([...M0?.id === "all" ? [] : M0?.entries.map((L0) => L0.id) || [], ...u])], X0 = new AbortController(), ee = ve();
        if (!S0.length) return;
        S = { controller: X0, key: ee }, g++, ye(S0, ee, X0).finally(() => {
          g--, S = null, v || (i1(), we());
        });
        return;
      }
      for (let M0 of u) {
        if (g >= W) break;
        let S0 = w.get(M0);
        if (!S0 || S0.clip || S0.job || S0.error) continue;
        let X0 = new AbortController();
        S0.job = X0, g++, S0.time.textContent = "\u8BFB\u53D6\u6B64\u7247\u6BB5\u2026", S0.card.setAttribute("aria-busy", "true"), Se(S0, X0).finally(() => {
          if (g--, S0.job = null, !v) {
            if (S0.card.setAttribute("aria-busy", "false"), !S0.clip && !S0.error && (S0.time.textContent = "\u5F85\u663E\u793A\u65F6\u8BFB\u53D6"), h && u.has(M0)) {
              let ee = M.find((L0) => L0.id === P);
              W0(ee), re(ee), i1(), Z0();
            }
            we();
          }
        });
      }
    }
  }
  async function ye(M0, S0, X0) {
    try {
      if (await y.prepareEntries(M0, { signal: X0.signal }), v || X0.signal.aborted || S0 !== ve()) return;
      for (let L0 of M0) {
        let q0 = w.get(L0);
        q0.prepared = true, q0.errorPhase === "prepare" && (q0.error = null, q0.errorPhase = null, q0.retry.hidden = true), q0.rate = q0.entry.playback?.stepsPerSecond || q0.entry.playback?.recommendedStepsPerSecond || (q0.entry.kind === "lifetime" ? q0.entry.origin?.kind === "optimization" ? 20 : 60 : 1), q0.unit = q0.entry.kind === "lifetime" ? "\u6B65" : q0.entry.kind === "generation" ? "\u4EE3" : "\u6B21", Ke(q0.entry) && !q0.overlay && (q0.overlay = T0(q0.select), q0.card.querySelector(".morphology-caption").append(q0.pulseLabel));
      }
      j = S0, O = "", fe();
      let ee = M.find((L0) => L0.id === P);
      W0(ee), re(ee);
    } catch (ee) {
      if (v || X0.signal.aborted || S0 !== ve()) return;
      O = S0;
      for (let L0 of M0) {
        let q0 = w.get(L0);
        q0.error = ee, q0.errorPhase = "prepare", q0.time.textContent = `\u8BB0\u5F55\u8BF4\u660E\u4E0D\u53EF\u8BFB\uFF1A${ee.message}`, q0.retry.hidden = false;
      }
    }
  }
  async function Se(M0, S0) {
    try {
      if (!M0.resolved && M0.entry.sourceRef && E) {
        let L0 = M0.entry.sourceRef;
        if (!D.has(L0)) {
          let le = (async () => {
            let ge = await E.resource("resolve", { address: L0 });
            if (!ge.readable) throw new Error("\u5F53\u524D\u65E0\u6743\u8BFB\u53D6\u6765\u6E90");
            return ge;
          })().catch((ge) => {
            throw D.get(L0) === le && D.delete(L0), ge;
          });
          D.set(L0, le);
        }
        let q0 = await D.get(L0);
        if (S0.signal.aborted || v) return;
        M0.portalUrl = q0.portal_url, M0.resolved = true;
      }
      if (S0.signal.aborted || v) return;
      let X0 = await y.loadClip(M0.entry.id, { signal: S0.signal });
      if (v) return;
      M0.clip = X0, M0.error = null, M0.retry.hidden = true, M0.step ??= X0.steps[0], M0.canvas.width = M0.canvas.height = u0;
      let ee = U0(M0.entry);
      if (ee?.clock && (ee.clock.step ??= X0.steps[0], Object.assign(M0, ee.clock)), M0.dirty = true, h && u.has(M0.entry.id)) {
        let L0 = R0(M0.entry);
        M0.card.querySelector(".morphology-caption h3").textContent = Ke(M0.entry) ? `${M0.entry.environment.firstPulse.intensity ? "\u6709\u9996\u523A\u6FC0" : "\u65E0\u9996\u523A\u6FC0"} \u2192 ${M0.entry.environment.secondPulse.intensity ? "\u518D\u6B21\u523A\u6FC0" : "\u96F6\u5F3A\u5EA6\u5BF9\u7167"}` : L0.title, M0.card.querySelector(".morphology-highlight").textContent = L0.discovery;
      }
    } catch (X0) {
      if (v || S0.signal.aborted) return;
      M0.error = X0, M0.time.textContent = `\u7247\u6BB5\u4E0D\u53EF\u8BFB\uFF1A${X0.message}`, M0.retry.hidden = false;
    }
  }
  function Ze(M0, S0, X0 = true, ee) {
    let L0 = w.get(M0);
    if (!L0 || v) return;
    G0(), M.find((ge) => ge.id === P)?.entries.some((ge) => ge.id === M0) || (P = H0(L0.entry)?.id || M.find((ge) => ge.id === L0.entry.origin?.branchId)?.id || M.find((ge) => ge.id !== "overview" && ge.entries.some((Re) => Re.id === M0))?.id || "overview");
    let le = M.find((ge) => ge.id === P);
    c = Math.max(0, Math.floor(le.entries.findIndex((ge) => ge.id === M0) / (le.pageSize || 6))), m = { id: M0, lifetimeStep: S0, compare: X0, viewId: ee }, se(), i1();
  }
  function i1() {
    if (!m || !h || v) return;
    let M0 = m, S0 = te(M0);
    if ((!y.prepareEntries || j === ve()) && S0.every((X0) => X0.clip)) {
      m = null, B1(M0.id, M0.lifetimeStep, M0.compare, M0.viewId);
      return;
    }
    h0.hidden = false, h0.replaceChildren(), h0.append(o("h3", "", R0(w.get(M0.id).entry).title), l("\u6536\u8D77\u653E\u5927", G0), o("p", "", "\u6B63\u5728\u6309\u9700\u8BFB\u53D6\u5F53\u524D\u5F62\u6001\u4E0E\u5DF2\u9009\u62E9\u7684\u771F\u5B9E\u5BF9\u7167\u2026"));
    for (let X0 of S0.filter((ee) => ee.error)) h0.append(o("p", "", `${X0.entry.title}\uFF1A${X0.error.message}`), l(`\u91CD\u8BD5 \xB7 ${X0.entry.title}`, () => pe(X0)));
    $0();
  }
  function B1(M0, S0, X0 = true, ee) {
    let L0 = w.get(M0);
    if (!L0) return;
    M.find((ie) => ie.id === P)?.entries.some((ie) => ie.id === M0) || (P = H0(L0.entry)?.id || M.find((ie) => ie.id === L0.entry.origin?.branchId)?.id || M.find((ie) => ie.id !== "overview" && ie.entries.some((Me) => Me.id === M0))?.id || "overview");
    let le = M.find((ie) => ie.id === P);
    le.opened = true, c = Math.max(0, Math.floor(le.entries.findIndex((ie) => ie.id === M0) / (le.pageSize || 6))), S0 != null && k0(L0, S0), i = M0, z0 = L0.entry, h0.replaceChildren(), p0 = [], A0 = 1, N0 = true, h0.hidden = false;
    for (let [ie, Me] of w) Me.select.setAttribute("aria-pressed", String(ie === M0));
    let ge = R0(L0.entry), Re = o("div", "morphology-detail-heading");
    Re.append(o("h3", "", ge.title), l("\u6536\u8D77\u653E\u5927", () => {
      G0(), L0.select.focus();
    }));
    let xe = o("div", "morphology-comparison"), Oe = qt(L0.entry, Array.from(w.values(), (ie) => ie.entry)), Ee = Ke(L0.entry) && w.get(L0.entry.stateLineage.sharedPrefix.peerEntryId), Ne = le.comparison, a1 = Ne?.detailModes?.(L0.entry) || [], o1 = a1.find((ie) => ie.id === ee) || a1[0];
    V0 = o1?.description;
    let h1 = Ne?.pairEntries?.(L0.entry).map((ie) => w.get(ie.id)), Je = o1 ? o1.entries.map((ie) => w.get(ie.id)) : h1 || (Ee ? [L0, Ee].sort((ie, Me) => ie.entry.environment.secondPulse.intensity - Me.entry.environment.secondPulse.intensity) : Oe && X0 ? [w.get(Oe.id), L0] : [L0]);
    xe.dataset.columns = String(Je.length);
    for (let ie of Je) {
      let Me = o("figure"), e1 = o("canvas");
      e1.width = e1.height = u0, e1.setAttribute("aria-label", `${ie.entry.title}\u653E\u5927\u56DE\u653E`);
      let l1 = o("div", "morphology-frame");
      l1.append(e1);
      let bt = Ke(ie.entry) ? T0(l1) : null, q1 = o("figcaption"), vt = o("p", "morphology-pulse"), m1, K1;
      if (Je.length === 1 && ht(ie.entry)) {
        let X = o("div", "morphology-world");
        m1 = o("canvas"), m1.width = m1.height = u0, m1.setAttribute("aria-label", "\u5B8C\u6574\u4E16\u754C\u539F\u5750\u6807"), X.append(m1, o("span", "", "\u5B8C\u6574\u4E16\u754C \xB7 \u539F\u5750\u6807")), l1.append(X), K1 = o("p", "morphology-view-label"), Me.append(K1);
      }
      Me.append(l1, q1), bt && Me.append(vt);
      let D1, O0 = Ne?.metrics;
      if (O0) {
        let X = o("details", "morphology-formation-metrics");
        X.append(o("summary", "", Ne?.metricsTitle || "\u5E93\u5B58\u4E0E\u7EC4\u7EC7\u66F4\u65B0 \xB7 \u5F53\u524D\u771F\u5E27"));
        let b0 = /* @__PURE__ */ new Map(), I0 = o("dl");
        for (let [P0, l0] of O0) {
          let Q0 = o("dd");
          Q0.dataset.metric = P0, I0.append(o("dt", "", l0), Q0), b0.set(P0, Q0);
        }
        X.append(I0, o("p", "", Ne?.metricsNote || "\u5168\u4E16\u754C\u603B\u91CF \xB7 \u539F\u59CB\u7CBE\u5EA6\u7EDF\u8BA1\uFF0C\u4E0D\u4ECE\u663E\u793A\u989C\u8272\u4F30\u7B97")), Me.append(X), D1 = { root: X, values: b0, fields: O0, sample: Ne?.sample || C1, format: Ne?.formatMetric };
      }
      xe.append(Me), p0.push({ canvas: e1, record: ie, label: q1, overlay: bt, pulseLabel: vt, world: m1, viewLabel: K1, metrics: D1 });
    }
    let _1 = o("p", "morphology-lineage", Oe ? `${s2(Oe)} ${Oe.title} \u2192 ${s2(L0.entry)} ${L0.entry.title}\u3002${L0.entry.parentNote || ""} ${L0.entry.changeSummary || ""} \u4E24\u4FA7\u6309\u751F\u547D\u671F\u8BB0\u5F55\u6B65\u6570\u5B9A\u4F4D\uFF0C\u672A\u6821\u6B63\u8FD0\u52A8\u76F8\u4F4D\u3002` : Ee ? `\u540C\u4E00\u9996\u523A\u6FC0\u5386\u53F2\u7684\u4E24\u6761\u5206\u652F\uFF0C\u5728\u751F\u547D\u671F ${L0.entry.stateLineage.sharedPrefix.throughStep} \u6B65\u53CA\u4EE5\u524D\u5B8C\u5168\u76F8\u540C\uFF1B\u6B64\u540E\u4EC5\u518D\u6B21\u523A\u6FC0\u4E0D\u540C\u3002\u8FD9\u662F\u540C\u4E00\u8EAB\u4F53\u7684\u53CD\u4E8B\u5B9E\u5EF6\u7EED\uFF0C\u6CA1\u6709\u65B0\u589E\u9057\u4F20\u7236\u4EE3\u3002` : L0.entry.parentId?.startsWith("founder:") ? `\u521D\u6001\u6765\u6E90\u65CF\uFF1A${L0.entry.parentId.slice(8)}\u3002\u8FD9\u662F\u8D77\u70B9\u6807\u7B7E\uFF0C\u6CA1\u6709\u5BF9\u5E94\u7684\u7236\u4EE3\u5F71\u7247\u3002${L0.entry.changeSummary || ""}` : L0.entry.parentRelation === "recorded" ? `\u771F\u5B9E\u524D\u8EAB ${L0.entry.parentId} \u7684\u7247\u6BB5\u5C1A\u672A\u6536\u5F55\u3002${L0.entry.parentNote || ""} ${L0.entry.changeSummary || ""}` : L0.entry.parentNote || "\u672C\u6761\u6CA1\u6709\u5DF2\u8BB0\u5F55\u7684\u7EE7\u627F\u5173\u7CFB\uFF1B\u72EC\u7ACB\u5C55\u793A\u5176\u5B9E\u9645\u8F68\u8FF9\u3002");
    y0 = o("p", "morphology-detail-time"), x0 = o("p", "morphology-detail-rate"), w0 = o("input"), w0.type = "range", w0.min = String(le.start ?? L0.clip.steps[0]), w0.max = String(le.end ?? L0.clip.steps.at(-1)), w0.step = "1", w0.setAttribute("aria-label", "\u9009\u4E2D\u5F62\u6001\u7684\u5B9E\u9645\u8BB0\u5F55\u65F6\u95F4"), w0.addEventListener("input", () => {
      r = false, $2.textContent = "\u7EE7\u7EED\u64AD\u653E", k0(L0, Number(w0.value)), Z0();
    }), m0(w0, L0);
    let $e = o("div", "morphology-tools");
    for (let ie of a1) {
      let Me = l(ie.label, () => Ze(M0, void 0, X0, ie.id));
      Me.dataset.detailMode = ie.id, Me.setAttribute("aria-pressed", String(ie === o1)), $e.append(Me);
    }
    $e.append(l("\u4ECE\u8BB0\u5F55\u8D77\u70B9\u64AD\u653E", () => {
      k0(L0, L0.clip.steps[0]), r = true, $2.textContent = "\u5168\u90E8\u6682\u505C", Z0();
    }));
    let Xt = ge.moments.length ? ge.moments : [0.25, 0.5, 1].map((ie) => ({ step: L0.clip.steps[Math.round((L0.clip.steps.length - 1) * ie)], label: "\u8BB0\u5F55" }));
    for (let { step: ie, label: Me } of Xt) $e.append(l(`${Me} \xB7 ${ie} ${L0.unit}`, () => {
      k0(L0, ie), r = false, $2.textContent = "\u7EE7\u7EED\u64AD\u653E", Z0();
    }));
    if (Je.length === 1 && ht(L0.entry)) {
      E0 = l("\u8DDF\u968F\u8EAB\u4F53", () => {
        N0 = !N0, E0.textContent = N0 ? "\u8DDF\u968F\u8EAB\u4F53" : "\u56FA\u5B9A\u4E16\u754C", E0.setAttribute("aria-pressed", String(N0)), Z0(), B0();
      }), E0.setAttribute("aria-pressed", "true");
      let ie = l("\u89C2\u5BDF 2\xD7", () => {
        A0 = A0 === 1 ? 2 : 1, ie.textContent = A0 === 1 ? "\u89C2\u5BDF 2\xD7" : "\u8FD8\u539F 1\xD7", Z0();
      });
      ie.dataset.action = "morphology-zoom", $e.prepend(E0, ie);
    }
    Oe && $e.append(l(X0 ? "\u53EA\u770B\u5F53\u524D\u5F62\u6001" : w1(L0.entry) ? "\u4E0E\u5916\u5C42\u7236\u4EE3\u5BF9\u7167" : "\u4E0E\u771F\u5B9E\u524D\u8EAB\u5BF9\u7167", () => Ze(M0, L0.step, !X0))), Oe && $e.append(l(w1(L0.entry) ? "\u67E5\u770B\u5916\u5C42\u7236\u4EE3" : "\u67E5\u770B\u524D\u8EAB", () => Ze(Oe.id, L0.step)));
    for (let ie of w.values()) qt(ie.entry, [L0.entry]) && $e.append(l(`\u540E\u7EE7 \xB7 ${ie.entry.title}`, () => Ze(ie.entry.id, L0.step)));
    let mt = M.find((ie) => ie.kind === "environment" && ie.sourceId === M0);
    mt && $e.append(l("\u67E5\u770B\u540C\u57FA\u56E0\u7EC4\u73AF\u5883\u5206\u652F", () => v0(mt.id)));
    let c1 = o("nav", "morphology-chain");
    if (c1.setAttribute("aria-label", "\u771F\u5B9E\u4F18\u5316\u68C0\u67E5\u70B9"), Ne?.relatedEntries) {
      c1.setAttribute("aria-label", "\u771F\u5B9E\u751F\u547D\u671F\u5206\u652F");
      for (let ie of Ne.relatedEntries(L0.entry, L0.step)) {
        if (!w.has(ie.id)) continue;
        let Me = l(ie.label, () => {
          let e1 = Ne.relatedEntries(L0.entry, L0.step).find((l1) => l1.id === ie.id);
          e1 && Ze(e1.id, e1.step);
        });
        Me.dataset.relatedEntry = ie.id, c1.append(Me);
      }
    }
    if (F0 = Oe && X0 ? o("span", "morphology-alignment") : null, F0 && c1.append(F0), L0.entry.origin?.kind === "optimization") {
      let ie = Array.from(w.values()).filter((Me) => Me.entry.origin?.branchId === L0.entry.origin.branchId).sort((Me, e1) => Me.entry.origin.index - e1.entry.origin.index);
      ie.length > 1 && (c1.append(o("span", "", "\u5916\u5C42\u4F18\u5316\u68C0\u67E5\u70B9 \xB7 \u5404\u81EA\u4ECE\u521D\u6001\u5F00\u59CB")), ie.forEach((Me, e1) => {
        e1 && qt(Me.entry, [ie[e1 - 1].entry]) && c1.append(o("span", "morphology-chain-arrow", "\u2192"));
        let l1 = l(`${Me.entry.origin.index} \xB7 ${Me.entry.outcome?.finiteWindowPass ? "\u7A97\u53E3\u901A\u8FC7" : "\u672A\u901A\u8FC7"}`, () => Ze(Me.entry.id, L0.step));
        l1.dataset.checkpoint = Me.entry.id, l1.setAttribute("aria-current", String(Me.entry.id === M0)), c1.append(l1);
      }));
    }
    let gt = l("\u5728\u6C99\u76D8\u4E2D\u6DF1\u5165\u89C2\u5BDF\u6B64\u5F62\u6001", async () => {
      let ie = await H?.(L0.entry);
      ie?.opened === false && (B.textContent = ie.reason || "\u6B64\u5F62\u6001\u5C1A\u65E0\u53EF\u7EED\u7B97\u7684\u89C4\u5219\u4E0E\u521D\u6001\uFF0C\u7EE7\u7EED\u5C55\u793A\u771F\u5B9E\u8BB0\u5F55\u3002");
    });
    Ne || $e.append(gt);
    let Ve = o("details", "morphology-evidence");
    Ve.append(o("summary", "", "\u89C4\u5219\u3001\u6765\u6E90\u4E0E\u663E\u793A\u7CBE\u5EA6")), L0.entry.rule?.schema === "lenia.fixed-dual-response.v1" && Ve.append(o("p", "", "\u4E24\u5957\u7A7A\u95F4\u6838\u3001\u5404\u81EA\u7684\u589E\u957F\u54CD\u5E94\u53CA\u6DF7\u5408\u6743\u91CD\u5728\u8FD9\u6BB5\u751F\u547D\u671F\u5185\u56FA\u5B9A\uFF0C\u52A8\u6001\u72B6\u6001\u53EA\u6709\u8EAB\u4F53\u5BC6\u5EA6\uFF1B\u4E0D\u662F\u5FEB\u6162\u8BB0\u5FC6\u573A\u3002\u5173\u95ED\u5BF9\u7167\u7684\u539F\u59CB\u6807\u7B7E\u548C\u53C2\u6570\u53E6\u5217\uFF0C\u4E0D\u628A\u4E0D\u540C\u53C2\u6570\u7684\u53D8\u4F53\u76F4\u63A5\u5F53\u4F5C\u914D\u5BF9\u6D88\u878D\u3002"));
    for (let ie of [L0.entry.time?.description, L0.entry.sourceNote, L0.entry.origin?.description || L0.entry.origin?.method, L0.entry.quantization ? "\u663E\u793A\u5BC6\u5EA6\u91CF\u5316\u4E3A 256 \u7EA7\uFF1B\u79D1\u5B66\u6307\u6807\u4EE5\u539F\u59CB\u8BB0\u5F55\u4E3A\u51C6\u3002" : "", `\u5F53\u524D\u753B\u9762\u4E3A\u771F\u5B9E\u91C7\u6837\u5E27\uFF0C\u4E0D\u5BF9\u5E27\u4E4B\u95F4\u7684\u8EAB\u4F53\u8FDB\u884C\u63D2\u503C\u3002${L0.entry.cycleNote || L0.entry.recording?.description || "\u672C\u7247\u6BB5\u7684\u5468\u671F\u5B8C\u6574\u6027\u5C1A\u672A\u6807\u5B9A\u3002"}`]) ie && Ve.append(o("p", "", ie));
    if (L0.entry.rule && Ve.append(o("pre", "", JSON.stringify(L0.entry.rule, null, 2))), le.kind === "formation") {
      for (let Me of p7.slice(1)) Ve.append(o("p", "", Me));
      for (let Me of Je) Ve.append(o("p", "morphology-outcome", `${R0(Me.entry).title}\uFF1A${i2(Me.entry)}`));
      let ie = L0.entry.ruleReference?.sourceCandidateId;
      ie && w.has(ie) && $e.append(l("\u67E5\u770B\u65E7\u514D\u8D39\u89C4\u5219\u6765\u6E90", () => Ze(ie, 0))), Ve.append(o("p", "", "\u65E7\u514D\u8D39\u89C4\u5219\u5F71\u7247\u4EC5\u4E3A\u6765\u6E90\u53C2\u8003\uFF1B\u65F6\u95F4\u6B65\u957F\u3001\u6162\u53CD\u9988\u53CA\u4ED8\u8D39\u89C4\u5219\u5E76\u4E0D\u76F8\u540C\uFF0C\u4E0D\u4F5C\u5339\u914D\u5BF9\u7167\u6216\u8FDE\u7EED\u751F\u547D\u671F\u3002"));
    }
    if (w1(L0.entry)) {
      for (let ie of O7) Ve.append(o("p", "", ie));
      for (let ie of Je) {
        let Me = ie.entry.outcome;
        Ve.append(o("p", "morphology-outcome", `${R0(ie.entry).title}\uFF1A${i2(ie.entry)}`)), Number.isFinite(Me?.lateMeanBody) && Number.isFinite(Me?.lateBodyStd) && Ve.append(o("p", "", `\u9010\u6B65\u8BB0\u5F55 256\u20131023\uFF1A\u7EC4\u7EC7\u5747\u503C ${Me.lateMeanBody.toFixed(3)}\uFF0C\u6807\u51C6\u5DEE ${Me.lateBodyStd.toFixed(3)}\u3002`));
      }
    }
    if (Ne && le.kind !== "formation" && !w1(L0.entry)) {
      for (let ie of Ne.explanation) Ve.append(o("p", "", ie));
      for (let ie of Je) Ve.append(o("p", "morphology-outcome", `${R0(ie.entry).title}\uFF1A${i2(ie.entry)}`));
    }
    Ke(L0.entry) && Ve.append(o("pre", "", JSON.stringify({ spatialScope: L0.entry.environment.spatialScope, operator: L0.entry.environment.operator, intensityConvention: L0.entry.environment.intensityConvention, sampling: L0.entry.environment.sampling }, null, 2))), L0.portalUrl && $e.append(l("\u6253\u5F00\u6765\u6E90", () => E.openPage(`/resource?address=${encodeURIComponent(L0.entry.sourceRef)}`))), Ve.prepend(o("p", "morphology-identifier", `${L0.entry.id} \xB7 ${L0.entry.branch} \xB7 ${s2(L0.entry)}`)), Ve.append(_1, o("p", "", L0.entry.summary)), L0.entry.outcome && Ve.append(o("p", "morphology-outcome", i2(L0.entry))), h0.append(Re, o("p", "morphology-summary", ge.discovery), c1, xe), h0.append(w0, y0, x0, $e, Ve), B0(), se(), h0.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  try {
    if (v) return { show: J0, hide: Ae, destroy: ae, setDisplayOptions: oe, openCollection: v0 };
    if (y = await T({ sdk: E, signal: R }), v) return y.destroy(), { show: J0, hide: Ae, destroy: ae };
    y.warnings?.length && B.after(o("p", "morphology-resource-warning", y.warnings.join(`
`)));
    let M0 = y.entries;
    if (u0 = Math.max(64, ...M0.map((q0) => Math.max(q0.width || 0, q0.height || 0))), u0 > 512) throw new Error("\u5F62\u6001\u7A7A\u95F4\u8D85\u51FA\u5F53\u524D\u663E\u793A\u8303\u56F4");
    for (let q0 of M0) {
      let le = o("article", "morphology-card");
      le.dataset.candidate = q0.id;
      let ge = l("", () => Ze(q0.id));
      ge.className = "morphology-select", ge.setAttribute("aria-label", `\u653E\u5927 ${q0.title}`), ge.setAttribute("aria-pressed", "false");
      let Re = o("canvas");
      Re.width = Re.height = 1, Re.setAttribute("aria-label", `${q0.title}\u5B9E\u9645\u8BB0\u5F55`), ge.append(Re);
      let xe = !y.prepareEntries && Ke(q0) ? T0(ge) : null, Oe = R0(q0), Ee = !y.prepareEntries && Ke(q0) ? `${q0.environment.firstPulse.intensity ? "\u6709\u9996\u523A\u6FC0" : "\u65E0\u9996\u523A\u6FC0"} \u2192 ${q0.environment.secondPulse.intensity ? "\u518D\u6B21\u523A\u6FC0" : "\u96F6\u5F3A\u5EA6\u5BF9\u7167"}` : Oe.title, Ne = o("div", "morphology-caption");
      q0.origin?.kind === "optimization" && Ne.append(o("span", "morphology-branch", `\u4F18\u5316\u8FED\u4EE3 ${q0.origin.index}`)), Ne.append(o("h3", "", Ee), o("p", "morphology-highlight", Oe.discovery));
      let a1 = o("p", "morphology-time", "\u5F85\u663E\u793A\u65F6\u8BFB\u53D6");
      le.append(ge, Ne, a1), G.append(le);
      let o1 = o("p", "morphology-pulse");
      xe && Ne.append(o1);
      let h1 = l("\u91CD\u8BD5\u6B64\u7247\u6BB5", () => pe(w.get(q0.id)));
      h1.hidden = true, h1.dataset.retry = q0.id, le.append(h1);
      let Je = q0.playback?.stepsPerSecond || q0.playback?.recommendedStepsPerSecond || (q0.kind === "lifetime" ? q0.origin?.kind === "optimization" ? 20 : 60 : 1);
      w.set(q0.id, { entry: q0, card: le, canvas: Re, overlay: xe, pulseLabel: o1, select: ge, time: a1, retry: h1, rate: Je, unit: q0.kind === "lifetime" ? "\u6B65" : q0.kind === "generation" ? "\u4EE3" : "\u6B21", step: null, hold: 0, loops: 0, dirty: true, clip: null, job: null, error: null, prepared: !y.prepareEntries });
    }
    B.textContent = w.size ? `${w.size} \u6BB5\u771F\u5B9E\u8BB0\u5F55 \xB7 \u70B9\u51FB\u753B\u9762\u653E\u5927 \xB7 \u5404\u6BB5\u663E\u793A\u5B9E\u9645\u6B65\u6570\uFF0C\u5FAA\u73AF\u5904\u76F4\u63A5\u56DE\u5230\u8D77\u70B9` : "\u6682\u65E0\u53EF\u5C55\u793A\u7684\u771F\u5B9E\u5019\u9009\u3002\u6765\u6E90\u8BB0\u5F55\u5230\u8FBE\u540E\u63A5\u5165\uFF0C\u4E0D\u7528\u91CD\u590D\u5F62\u6001\u586B\u5145\u3002", a0.querySelector(".morphology-scale").textContent = `\u7EDF\u4E00\u5BC6\u5EA6\u8272\u6807 \xB7 \u89C6\u91CE ${u0}\xD7${u0} \u683C \xB7 \u6807\u5C3A 8 \u683C \xB7 \u65E0\u5E27\u95F4\u63D2\u503C`, M = y.collections ? y.collections.map((q0) => ({ ...q0, comparison: y.prepareEntries ? void 0 : q0.comparison, contextReady: !y.prepareEntries })) : q7(Array.from(w.values(), (q0) => q0.entry), y);
    for (let q0 of M.filter((le) => le.synchronized)) {
      q0.clock = { step: q0.start ?? null, hold: 0, loops: 0 }, q0.start != null && q0.end != null && (q0.clockClip = { steps: [q0.start, q0.end] });
      for (let le of q0.entries) z.has(le.id) || z.set(le.id, q0);
    }
    for (let q0 of M) {
      let le = o("option", "", `${q0.title} \xB7 ${q0.entries.length}`);
      le.value = q0.id, K2.append(le);
    }
    let S0 = o("nav", "morphology-stories");
    S0.setAttribute("aria-label", "\u89C2\u770B\u7EBF\u7D22"), M.some((q0) => q0.id === Wt) && S0.append(l("\u751F\u6210\u4E5F\u8981\u4ED8\u8D39", () => v0(Wt))), M.some((q0) => q0.id === e2) && S0.append(l("\u9996\u4EE3\uFF1A\u8F6C\u5411\u4E0E\u4EE3\u4EF7", () => v0(e2)));
    for (let q0 of M.filter((le) => le.storyLabel || le.comparison?.storyLabel)) S0.append(l(q0.storyLabel || q0.comparison.storyLabel, () => v0(q0.id)));
    w.has("m2026101607-g2-0128") && S0.append(l("\u53D8\u5F02\u524D\u540E", () => Ze("m2026101607-g2-0128", 0))), M.some((q0) => q0.id === "ls-12d8620a-b05") && S0.append(l("\u4F18\u5316\u7684\u8F6C\u6298", () => v0("ls-12d8620a-b05")));
    let X0 = M.find((q0) => q0.kind === "environment" && q0.sourceId === "dk2026101619-r4-v5");
    X0 && S0.append(l("\u540C\u4E00\u8EAB\u4F53\uFF0C\u4E0D\u540C\u523A\u6FC0", () => v0(X0.id))), C && S0.append(l("\u4ED8\u6B3E\u600E\u6837\u6539\u53D8\u9009\u62E9 \xB7 \u7B97\u6CD5\u52A8\u753B", C));
    let ee = o("details", "morphology-shortcuts");
    ee.append(o("summary", "", "\u5206\u7C7B\u5FEB\u6377\u5165\u53E3"), S0), t0.after(ee);
    let L0 = M.find((q0) => q0.default);
    L0 && (P = L0.id), se();
  } catch (M0) {
    v || (B.textContent = `\u5F62\u6001\u8BB0\u5F55\u8BFB\u53D6\u5931\u8D25\uFF1A${M0.message}`);
  }
  return { show: J0, hide: Ae, destroy: ae, setDisplayOptions: oe, openCollection: v0, getScene: () => {
    let M0 = M.find((ee) => ee.id === P), S0 = M0?.kind === "environment", X0 = i ? w.get(i) : M0?.synchronized ? Y0(M0) : null;
    return { mode: "morphology-wall", candidate_id: S0 ? M0.sourceId : i, selected_track_id: S0 ? i : null, source: z0?.sourceRef || null, step: X0?.clip ? X0.clip.steps[De(X0.clip.steps, X0.step)] : null, ...S0 ? { environment_clock: X0?.step == null ? null : Math.floor(X0.step), tracks: M0.entries.map((ee) => ee.id), independent_replicates: false } : {}, ...M0?.comparison ? { tracks: M0.entries.map((ee) => ee.id), model_family: X0?.entry.rule?.schema, independent_replicates: false } : {}, collection: P, page: c + 1, display_only: true, speed: A, playing: r };
  } };
}
var S1 = { schema: "learning-algorithm-records.v1", jobId: "f91fe62e-9e26-4b32-8434-b49f266ecd60", write: { action: 17, cell: [16, 12], receipt: { uptake: 0, basal: 20385502787760026e-20, motor: 3955442275538904e-19, energyLost: 0, massLost: 0, reward: -5993992554314907e-19 }, thetaMixed: [0, 0, 0, 0, 0], thetaAfter: [18459374600179493e-21, 19979975181049698e-21, 19979975181049698e-21, 19979975181049698e-21, -7839930014332859e-20], thetaProposed: [18459374600179493e-21, 19979975181049698e-21, 19979975181049698e-21, 19979975181049698e-21, -7839930014332859e-20], thetaRaw: [1845937460017949e-20, 19979975181049694e-21, 19979975181049694e-21, 19979975181049694e-21, -7839930014332859e-20], traceMixed: [-0.18477875405658725, -0.20000000000000004, -0.20000000000000004, -0.20000000000000004, 0.7847787540565875], rawDeltaTheta: [1845937460017949e-20, 19979975181049694e-21, 19979975181049694e-21, 19979975181049694e-21, -7839930014332859e-20], afterBody: 0.16987918989800024, derivedNote: "traceMixed/thetaMixed = saved transported_load / saved transported_body; differences are read-only derived quantities" }, samples: [{ step: 17, cell: [16, 12], update: { old_theta: [0, 0, 0, 0, 0], next_theta: [18459374600179493e-21, 19979975181049698e-21, 19979975181049698e-21, 19979975181049698e-21, -7839930014332859e-20], old_pi: [0.2, 0.2, 0.2, 0.2, 0.2], next_trace: [-0.18477875405658725, -0.20000000000000004, -0.20000000000000004, -0.20000000000000004, 0.7847787540565875], old_body: 0.17233009902777977, fresh: 1, used_draw: 0.8422450136769793, sampled_intent: 4, movement_paid: 3955442275538904e-19, basal_paid: 20385502787760026e-20, association_reward: -5993992554314907e-19 }, no_update: { old_theta: [0, 0, 0, 0, 0], next_theta: [0, 0, 0, 0, 0], old_pi: [0.2, 0.2, 0.2, 0.2, 0.2], next_trace: [-0.18477875405658725, -0.20000000000000004, -0.20000000000000004, -0.20000000000000004, 0.7847787540565875], old_body: 0.17233009902777977, fresh: 1, used_draw: 0.8422450136769793, sampled_intent: 4, movement_paid: 3955442275538904e-19, basal_paid: 20385502787760026e-20, association_reward: -5993992554314907e-19 } }, { step: 25, cell: [16, 12], update: { old_theta: [8780188925423245e-20, 1340929217621658e-19, 13398926951661136e-20, 13415776048170426e-20, -4900418410147138e-19], next_theta: [9983296620525055e-20, 17054569829301597e-20, 17264872357921617e-20, 6557332430755972e-20, -5086007123850425e-19], old_pi: [0.2000175551133422, 0.2000268143467962, 0.20002679361356882, 0.20002682731627913, 0.19990200961001367], next_trace: [-0.2716498128082885, -0.35509280184949193, -0.37574932427355856, 0.6022673277493489, 0.40022461118199026], old_body: 0.1523621859788042, fresh: 1, used_draw: 0.7423910086657423, sampled_intent: 3, movement_paid: 47320178988244707e-20, basal_paid: 18529879742283404e-20, association_reward: -6585005873052811e-19 }, no_update: { old_theta: [0, 0, 0, 0, 0], next_theta: [0, 0, 0, 0, 0], old_pi: [0.2, 0.2, 0.2, 0.2, 0.2], next_trace: [-0.2716358135838435, -0.3550664911121418, -0.37572304634550696, 0.6022936580877598, 0.4001316929537329], old_body: 0.1523621859788042, fresh: 1, used_draw: 0.7423910086657423, sampled_intent: 3, movement_paid: 47320178988244707e-20, basal_paid: 18529879742283404e-20, association_reward: -6585005873052811e-19 } }, { step: 65, cell: [16, 12], update: { old_theta: [0.0013970650323843212, -8860645641833809e-19, 30564754662087913e-20, -5902821519017329e-19, -2263658629200867e-19], next_theta: [0.0013374290043743704, -8705280431057341e-19, 3241440211413192e-19, -5788309239551163e-19, -21221405845483934e-20], old_pi: [0.20027954357395056, 0.1998228010200556, 0.2000610742203761, 0.1998819138320043, 0.19995466735361345], next_trace: [0.3849695459115688, -0.05668412363854825, -0.04149843333801702, -0.20247126087521108, -0.08431572805979318], old_body: 0.1288899605476707, fresh: 1, used_draw: 0.20002680455460298, sampled_intent: 0, movement_paid: 20679461554869809e-20, basal_paid: 15499346573812967e-20, association_reward: -3617880812868278e-19 }, no_update: { old_theta: [0, 0, 0, 0, 0], next_theta: [0, 0, 0, 0, 0], old_pi: [0.2, 0.2, 0.2, 0.2, 0.2], next_trace: [-0.5917467325095015, 0.920467430910553, -0.04143430620228735, -0.2028925332681684, -0.084393858930596], old_body: 0.1288899605476707, fresh: 1, used_draw: 0.20002680455460298, sampled_intent: 1, movement_paid: 41673223639272604e-20, basal_paid: 15163811876589848e-20, association_reward: -5683703551586245e-19 } }], choice: { y: 16, x: 12, old_rho: 0.1288899605476707, old_E: 0.044449124144560447, draw: 0.20002680455460298, pi_update: [0.20027954357395056, 0.1998228010200556, 0.2000610742203761, 0.1998819138320043, 0.19995466735361345], pi_no_update: [0.2, 0.2, 0.2, 0.2, 0.2], intent_update: 0, intent_no_update: 1 }, openings: [{ label: "65\u201380", update: 1.4127918145351375, control: 1.4127994962405912 }, { label: "129\u2013144", update: 1.1846571516052187, control: 1.1847787155998857 }, { label: "193\u2013208", update: 0.8190204670048138, control: 0.8191467826972071 }, { label: "257\u2013272", update: 0.6583282485786557, control: 0.6584585117421804 }], endpoints: { update: { id: "update", new_body_updates: 256, start: 16, end: 272, mass: 14.43717633418671, energy: 2.0511187586044413, patch_mass: 1.9628926164708427, remaining_stock: 608.9537999281481, resource_consumed: 4.074797681723825, mass_injury: 0, mass_starvation: 9.56282366581327, energy_spent: 15.98621224152663, energy_lost_with_tissue: 0.16423799823975677, movement_requested: 9.340482841081988, movement_paid: 9.340482841081988, movement_unpaid: 0, movement_intended_source: 9.43676182457113, movement_denied_source: 0.09627898348914157, basal_requested: 6.941246615316232, basal_paid: 6.645729400444642, basal_unpaid: 0.2955172148715907, reward: -12.840678451307623, patch_inflow: 7.423775281280254, patch_outflow: 10.203998702292608, patch_death: 0, transport_amount: 116.75603551352485, first_exact_extinction: null, max_balance: 1325606291402437e-28 }, no_update: { id: "no_update", new_body_updates: 256, start: 16, end: 272, mass: 14.434864822732944, energy: 2.0514495823099153, patch_mass: 1.9633194107786558, remaining_stock: 608.9534141035921, resource_consumed: 4.075183506279864, mass_injury: 0, mass_starvation: 9.565135177267054, energy_spent: 15.986276161637504, energy_lost_with_tissue: 0.1642290789794488, movement_requested: 9.340988894793485, movement_paid: 9.340988894793485, movement_unpaid: 0, movement_intended_source: 9.436775922090366, movement_denied_source: 0.0957870272968811, basal_requested: 6.9409169585491135, basal_paid: 6.645287266844019, basal_unpaid: 0.29562969170509484, reward: -12.840532548518452, patch_inflow: 7.424271462636856, patch_outflow: 10.204068089341398, patch_death: 0, transport_amount: 116.76236118491856, first_exact_extinction: null, max_balance: 10658141036401503e-29 } }, provenance: { evidence: "/share/Projects/\u5143\u80DE\u81EA\u52A8\u673A\u7814\u7A76/knowledge_base/agents/lineage_75e3399f4877/residents/resident_32388f7d2980/workspace/algorithm-f91/evidence.json", evidenceSha: "fa42c1f435b880d62867698e3a0db4e8eb6b671f8ee333466683b460f10ae3a7", action17: "/share/Projects/\u5143\u80DE\u81EA\u52A8\u673A\u7814\u7A76/knowledge_base/agents/lineage_75e3399f4877/residents/resident_112dd56b5808/workspace/algorithm-learning-f91/action17.json", action17Sha: "292ce4e5af31b082820013086afea47e780d9b760fb58bec59dfff3b0c8b59c8", sourceFiles: [{ path: "/share/Projects/\u5143\u80DE\u81EA\u52A8\u673A\u7814\u7A76/src/local_learning/results_20261017/embodied_action_learning_pilot/handoff.json", sha256: "ec191f9787a8d7a56e1d209ab0b095890723d45e3a0f56b29d1a4ac34533ef17" }, { path: "/share/Projects/\u5143\u80DE\u81EA\u52A8\u673A\u7814\u7A76/src/local_learning/results_20261017/embodied_action_learning_pilot/first_realized_choice.json", sha256: "226ca427ad66fcbec974c4f71365cae7d1504430ac965d21567f924c8957e5d9" }, { path: "/share/Projects/\u5143\u80DE\u81EA\u52A8\u673A\u7814\u7A76/src/local_learning/results_20261017/embodied_action_learning_pilot/pre_first_choice_feedback.json", sha256: "af9d41a25f96db54a947e8d24e2b205474b3a49bd668f325a2e97aa0382aa81f" }, { path: "/share/Projects/\u5143\u80DE\u81EA\u52A8\u673A\u7814\u7A76/src/local_learning/results_20261017/embodied_action_learning_pilot/summary.json", sha256: "b6eb80dfd2704357e3b0e6fe6e2daef1bcb1ceb13e80fbcb979a37a2f74fdc32" }, { path: "/share/Projects/\u5143\u80DE\u81EA\u52A8\u673A\u7814\u7A76/src/runs/jobs/f91fe62e-9e26-4b32-8434-b49f266ecd60/update/actions.npz", sha256: "d1257adfa2c6e8ff1b8cf96bb02f90c78413854ab8a4c24d776a8c37a134df5b" }, { path: "/share/Projects/\u5143\u80DE\u81EA\u52A8\u673A\u7814\u7A76/src/runs/jobs/f91fe62e-9e26-4b32-8434-b49f266ecd60/no_update/actions.npz", sha256: "faaea0433e7387bdf5114ea20a2675eeff9a2ddef06dad841379d431555cb4c1" }], transaction17: { path: "/share/Projects/\u5143\u80DE\u81EA\u52A8\u673A\u7814\u7A76/src/runs/jobs/f91fe62e-9e26-4b32-8434-b49f266ecd60/update/transactions/0017.npz", sha256: "717f6f751994dcaf56dc845763086774720ee7f513d2a4bc39ddcafd615b18ff", manifestStep: 16, tree: "manifest_json.metadata.tree", arrayLocators: { "old_fields.body": "field__a0", "old_fields.energy": "field__a1", "old_fields.memory": "field__a2", "old_fields.eligibility": "field__a3", "old_fields.polarity": "field__a4", "old_fields.timing_interval": "field__a5", "old_fields.timing_age": "field__a6", "old_fields.timing_previous_contact": "field__a7", "old_fields.timing_seen_contact": "field__a8", "old_fields.resource_stock": "field__a9", "old_fields.cue": "field__a10", "old_fields.hazard": "field__a11", "old_control.theta": "field__a12", "old_control.trace": "field__a13", "old_control.intent_mixture": "field__a14", "old_control.remaining": "field__a15", draw: "field__a16", "pending.old_pi": "field__a17", "pending.fresh": "field__a18", "pending.sampled_intent": "field__a19", "pending.score": "field__a20", "pending.proxy_p": "field__a21", "pending.payload.theta": "field__a22", "pending.payload.trace": "field__a23", "pending.payload.intent_mixture": "field__a24", "pending.payload.remaining": "field__a25", "pending.used_draw": "field__a26", "envelope.after_fields.body": "field__a27", "envelope.after_fields.energy": "field__a28", "envelope.after_fields.memory": "field__a29", "envelope.after_fields.eligibility": "field__a30", "envelope.after_fields.polarity": "field__a31", "envelope.after_fields.timing_interval": "field__a32", "envelope.after_fields.timing_age": "field__a33", "envelope.after_fields.timing_previous_contact": "field__a34", "envelope.after_fields.timing_seen_contact": "field__a35", "envelope.after_fields.resource_stock": "field__a36", "envelope.after_fields.cue": "field__a37", "envelope.after_fields.hazard": "field__a38", "envelope.metrics.resource_consumed": "field__a39", "envelope.metrics.mass_injury": "field__a40", "envelope.metrics.mass_starvation": "field__a41", "envelope.metrics.energy_spent": "field__a42", "envelope.metrics.energy_lost_with_tissue": "field__a43", "envelope.metrics.movement_requested": "field__a44", "envelope.metrics.movement_paid": "field__a45", "envelope.metrics.movement_unpaid": "field__a46", "envelope.metrics.basal_requested": "field__a47", "envelope.metrics.basal_paid": "field__a48", "envelope.metrics.basal_unpaid": "field__a49", "envelope.metrics.movement_intended_source": "field__a50", "envelope.metrics.movement_denied_source": "field__a51", "envelope.metrics.transport_weights": "field__a52", "envelope.metrics.source_budget_scale": "field__a53", "envelope.timing_diagnostics.native_rates": "field__a54", "envelope.timing_diagnostics.gate": "field__a55", "envelope.timing_diagnostics.remaining": "field__a56", "envelope.timing_diagnostics.predicted_interval": "field__a57", "envelope.timing_diagnostics.elapsed": "field__a58", "envelope.timing_diagnostics.polarity_readout": "field__a59", "envelope.timing_diagnostics.physical_polarity": "field__a60", "envelope.timing_diagnostics.budget_full_motor_rate": "field__a61", "envelope.timing_diagnostics.contact": "field__a62", "envelope.timing_diagnostics.onset": "field__a63", "envelope.timing_diagnostics.valid_target": "field__a64", "envelope.timing_diagnostics.target_interval": "field__a65", "envelope.timing_diagnostics.interval_error": "field__a66", "envelope.timing_diagnostics.interval_update": "field__a67", "envelope.timing_diagnostics.local_uptake_rate": "field__a68", "envelope.timing_diagnostics.mixed_interval": "field__a69", "envelope.timing_diagnostics.transported_body": "field__a70", "next_control.theta": "field__a71", "next_control.trace": "field__a72", "next_control.intent_mixture": "field__a73", "next_control.remaining": "field__a74", "audit.theta_transported_load": "field__a75", "audit.theta_source_write_load": "field__a76", "audit.trace_transported_load": "field__a77", "audit.trace_source_write_load": "field__a78", "audit.intent_mixture_transported_load": "field__a79", "audit.intent_mixture_source_write_load": "field__a80", "audit.remaining_transported_load": "field__a81", "audit.remaining_source_write_load": "field__a82", "audit.reward": "field__a83", "audit.theta_raw": "field__a84", "audit.theta_proposed": "field__a85", "audit.transported_body": "field__a86", "audit.theta_write_load": "field__a87", "audit.theta_deleted_load": "field__a88", "audit.theta_retained_load": "field__a89", "audit.trace_deleted_load": "field__a90", "audit.trace_retained_load": "field__a91", "audit.intent_mixture_deleted_load": "field__a92", "audit.intent_mixture_retained_load": "field__a93", "audit.remaining_deleted_load": "field__a94", "audit.remaining_retained_load": "field__a95" } } }, newBodyUpdates: 0 };
var y1 = [16e3, 14e3, 18e3, 14e3];
var ta = (L) => Math.max(0, Math.min(1, L));
function G2(L, E) {
  let R = 0;
  for (let H = 0; H < L.length; H++) if (R += L[H], E < R) return H;
  return L.length - 1;
}
function d8(L) {
  let E = L * 1e3;
  for (let R = 0; R < y1.length; R++) {
    if (E < y1[R] || R === y1.length - 1) return { index: R, progress: ta(E / y1[R]) };
    E -= y1[R];
  }
}
var z1 = ["#b2a2ff", "#5ad7c7", "#ffbd77", "#7ebaff", "#fa8bab"];
var Ct = ["off", "+y", "\u2212y", "+x", "\u2212x"];
var f8 = ["\u4E00\u7B14\u4ED8\u6B3E\uFF0C\u7559\u4E0B\u4EC0\u4E48\uFF1F", "\u504F\u597D\u6539\u53D8\uFF0C\u884C\u52A8\u672A\u5FC5\u7ACB\u523B\u53D8", "\u540C\u4E00\u62BD\u6837\uFF0C\u8DE8\u8FC7\u4E00\u6761\u8FB9\u754C", "\u9009\u62E9\u6539\u53D8\u4E86\uFF0C\u53D6\u5F97\u66F4\u591A\u4E86\u5417\uFF1F"];
var u8 = (L) => Math.max(0, Math.min(1, L));
var l2 = (L) => (L = u8(L), L * L * (3 - 2 * L));
var H1 = (L, E = 6) => Number(L).toFixed(E);
var aa = (L) => `${L >= 0 ? "+" : ""}${H1(L)}`;
var Ce = (L, E, R, H = 16, C = "#eaf2fa", s = "start") => `<text x="${L}" y="${E}" font-size="${H}" fill="${C}" text-anchor="${s}">${R}</text>`;
var Z1 = (L, E, R, H, C, s = 5, T = "") => `<rect x="${L}" y="${E}" width="${Math.max(0, R)}" height="${Math.max(0, H)}" rx="${s}" fill="${C}" ${T}/>`;
var Ot = (L, E, R, H, C = "#68788f", s = "") => `<path d="M${L},${E} L${R},${H}" stroke="${C}" fill="none" ${s}/>`;
var Lt = (L, E, R, H, C = "") => `<circle cx="${L}" cy="${E}" r="${R}" fill="${H}" ${C}/>`;
function p8(L, { signal: E, onObserve: R } = {}) {
  let H = L.ownerDocument, C = H.defaultView, s = (n0, i0, c0) => {
    let d0 = H.createElement(n0);
    return d0.className = i0 || "", c0 != null && (d0.textContent = c0), d0;
  }, T = false, a = false, b = true, f = 0, e = 0, t = 0, n = 1, o = -1, l = false, v = S1.choice.draw, h = s("article", "learning-algorithm");
  h.dataset.method = "f91-held-intent";
  let r = s("header", "algorithm-heading");
  r.append(s("p", "algorithm-eyebrow", "\u5B66\u4E60\u7B97\u6CD5 \xB7 01 / \u5C40\u90E8\u4ED8\u6B3E\u53CD\u9988"), s("h1", "", "\u4ED8\u6B3E\u5982\u4F55\u6539\u53D8\u4E0B\u4E00\u6B21\u9009\u62E9")), r.append(s("p", "algorithm-deck", "\u4ECE\u4E00\u683C\u7684\u4ED8\u6B3E\u56DE\u6267\uFF0C\u5230\u4E94\u79CD\u884C\u52A8\u7684\u504F\u597D\uFF0C\u518D\u5230\u4E00\u6B21\u771F\u5B9E\u9009\u62E9\u3002\u8FD9\u91CC\u5B66\u7684\u662F\u751F\u547D\u671F\u5185\u7684\u5C40\u90E8\u7B56\u7565\u53C2\u6570\u3002"));
  let A = s("div", "algorithm-scales");
  A.append(s("span", "active", "\u672C\u7247\uFF1A\u751F\u547D\u671F\u5185 \u03B8 \u968F\u4ED8\u6B3E\u66F4\u65B0"), s("span", "", "\u5916\u5C42\uFF1A\u672C\u6B21\u89C4\u5219\u4E0E\u5B66\u4E60\u7387\u56FA\u5B9A\uFF0C\u672A\u4F5C\u641C\u7D22\u66F4\u65B0"));
  let _ = s("nav", "algorithm-chapters");
  _.setAttribute("aria-label", "\u7B97\u6CD5\u52A8\u753B\u7AE0\u8282");
  let p = f8.map((n0, i0) => {
    let c0 = s("button", "", `${String(i0 + 1).padStart(2, "0")}  ${["\u4ED8\u6B3E\u5199\u5165", "\u7B49\u5F85\u4E0B\u4E00\u6B21\u62BD\u6837", "\u5B9E\u9645\u9009\u62E9", "\u884C\u4E3A\u7ED3\u679C"][i0]}`);
    return c0.type = "button", c0.onclick = () => Y(i0, 0), _.append(c0), c0;
  }), d = s("section", "algorithm-theatre"), i = s("div", "algorithm-stage-head"), y = s("span", "algorithm-clock"), w = s("h2"), z = s("span", "algorithm-badge", "\u771F\u5B9E\u7AEF\u70B9 \xB7 \u673A\u5236\u52A8\u753B");
  i.append(y, w, z);
  let M = H.createElementNS("http://www.w3.org/2000/svg", "svg");
  M.classList.add("algorithm-canvas"), M.setAttribute("role", "img"), M.setAttribute("aria-label", "\u4ED8\u6B3E\u3001\u504F\u597D\u4E0E\u62BD\u6837\u7684\u52A8\u6001\u673A\u5236\u56FE");
  let P = s("p", "algorithm-narration");
  P.setAttribute("aria-live", "off");
  let c = s("div", "algorithm-controls"), g = (n0, i0) => {
    let c0 = s("button", "portalButton", n0);
    return c0.type = "button", c0.onclick = i0, c0;
  }, u = g("\u6682\u505C\u52A8\u753B", () => {
    b = !b, e = 0, a0(), G();
  }), m = [0, 6500, 13500, 16e3, 24e3, 29500, 3e4, 4e4, 47e3, 48e3, 61500], k = g("\u4E0A\u4E00\u6B65", () => {
    f = [...m].reverse().find((n0) => n0 < f - 1) ?? 0, b = false, l = false, a0();
  }), x = g("\u4E0B\u4E00\u6B65", () => {
    f = m.find((n0) => n0 > f + 1) ?? 62e3, b = false, l = false, a0();
  }), j = g("\u4ECE\u5934\u56DE\u770B", () => {
    f = 0, b = true, l = false, a0(), G();
  }), O = s("select");
  O.setAttribute("aria-label", "\u7B97\u6CD5\u8BB2\u89E3\u901F\u5EA6");
  for (let [n0, i0] of [[0.5, "0.5\xD7"], [1, "1\xD7"], [2, "2\xD7"]]) {
    let c0 = s("option", "", i0);
    c0.value = n0, O.append(c0);
  }
  O.value = "1", O.onchange = () => {
    n = Number(O.value);
  };
  let S = s("input");
  S.type = "range", S.min = "0", S.max = String(y1.reduce((n0, i0) => n0 + i0) / 1e3), S.step = ".1", S.value = "0", S.setAttribute("aria-label", "\u7B97\u6CD5\u8BB2\u89E3\u8FDB\u5EA6\uFF08\u4E0D\u662F\u8EAB\u4F53\u65F6\u95F4\uFF09"), S.oninput = () => {
    f = Number(S.value) * 1e3, b = false, l = false, a0();
  };
  let D = s("output", "algorithm-counter");
  c.append(k, u, x, j, O, D, S);
  let W = s("div", "algorithm-readout"), U = s("details", "algorithm-lab");
  U.append(s("summary", "", "\u8BD5\u4E00\u8BD5\uFF1A\u53EA\u6539\u53D8\u62BD\u6837\u6570\uFF0C\u4F1A\u9009\u4EC0\u4E48\uFF1F")), U.append(s("p", "", "\u4EA4\u4E92\u793A\u610F\uFF1A\u4F7F\u7528\u52A8\u4F5C65\u5DF2\u8BB0\u5F55\u7684\u4E24\u7EC4\u6982\u7387\uFF0C\u53EA\u91CD\u505A\u4E00\u6B21\u5206\u7C7B\u62BD\u6837\uFF1B\u4E0D\u8FD0\u884C\u8EAB\u4F53\u3001\u4E0D\u5199\u5165\u53C2\u6570\uFF0C\u4E5F\u4E0D\u9884\u6D4B\u6536\u76CA\u3002"));
  let F = s("input");
  F.type = "range", F.min = "0", F.max = ".999999", F.step = ".000001", F.value = String(v), F.setAttribute("aria-label", "\u793A\u610F\u62BD\u6837\u6570");
  let I = s("input");
  I.type = "number", I.min = "0", I.max = ".999999999", I.step = ".000001", I.value = String(v), I.setAttribute("aria-label", "\u793A\u610F\u62BD\u6837\u6570\u7CBE\u786E\u503C");
  let N = s("p", "algorithm-experiment-result");
  function Z(n0) {
    v = Math.min(0.999999999, Math.max(0, Number(n0) || 0)), l = true, b = false, f = y1[0] + y1[1] + y1[2] * 0.8, F.value = String(v), I.value = String(v), a0();
  }
  F.oninput = () => Z(F.value), I.oninput = () => Z(I.value), U.append(F, I, g("\u56DE\u5230\u771F\u5B9E\u62BD\u6837", () => {
    l = false, v = S1.choice.draw, F.value = String(v), I.value = String(v), Y(2, 0.8);
  }), N);
  let q = s("details", "algorithm-method");
  q.append(s("summary", "", "\u5B9E\u9645\u66F4\u65B0\u3001\u65F6\u95F4\u5C3A\u5EA6\u4E0E\u6570\u636E\u51FA\u5904"));
  for (let n0 of ["\u5148\u4ECE\u65E7\u03B8\u5F97\u5230softmax\u6982\u7387\uFF0C\u518D\u62BD\u6837\u5E76\u4FDD\u6301\u610F\u56FE\u3002off\u53EA\u5173\u95ED\u81EA\u4E3Bp\u8BFB\u51FA\uFF1B\u5F62\u6001\u52BF\u9A71\u52A8\u4ECD\u53EF\u8FD0\u52A8\u548C\u4ED8\u8D39\u3002\u4E00\u6B21\u52A8\u4F5C\u5DF2\u7ECF\u9009\u5B9A\u540E\uFF0C\u5F53\u524D\u56DE\u6267\u4E0D\u80FD\u5012\u6539\u8FD9\u6B21\u9009\u62E9\u3002", "\u62BD\u6837\u5206\u6570 onehot(intent)\u2212\u65E7\u03C0 \u8FDB\u5165\u8870\u51CFtrace\u3002\u5B9E\u9645\u63A5\u7EB3\u7684\u8F93\u8FD0K\u643A\u5E26\u5E76\u6DF7\u5408\u03B8\u3001trace\u3001\u610F\u56FE\u4E0E\u4F59\u65F6\uFF1B\u56FE\u4E2D\u9009\u62E9\u4E00\u4E2A\u63A5\u53D7\u683C\uFF0C\u4E0D\u628A\u56FA\u5B9A\u683C\u5F53\u6210\u540C\u4E00\u5757\u7269\u8D28\u3002", "\u5C40\u90E8\u56DE\u6267 r=\u6444\u53D6\u2212\u7EF4\u6301\u5B9E\u4ED8\u2212\u8FD0\u52A8\u5B9E\u4ED8\u2212\u968F\u6B7B\u4EA1\u4E22\u5931\u80FD\u91CF\u22120.08\xD7\u5931\u4F53\u3002\u66F4\u65B0\u63D0\u8BAE \u03B8*=\u03B8\u6DF7\u5408+(0.1/0.6)r\xD7trace\u6DF7\u5408\uFF0C\u968F\u540E\u4E2D\u5FC3\u5316\u3001\u6309\u6700\u5927\u7EDD\u5BF9\u5206\u91CF\u7B49\u6BD4\u4F8B\u7F29\u653E\u81F3\u4E0D\u8D85\u8FC72\uFF0C\u518D\u6309\u5B9E\u9645\u5B58\u6D3B\u8EAB\u4F53\u4FDD\u7559\u3002\u56DE\u6267\u5DF2\u542B\u672C\u6B65dt\uFF0C\u4E0D\u518D\u4E58\u4E00\u6B21\u3002", "\u03B8\u662F\u751F\u547D\u671F\u5185\u53EF\u6539\u53D8\u7684\u5C40\u90E8\u7B56\u7565\u53C2\u6570\uFF1Btrace\u662F\u77ED\u65F6\u4FE1\u7528\u75D5\u8FF9\uFF0CT/m/z\u4ECD\u5728\u7EBF\u3002\u5B66\u4E60\u7387\u3001\u53CD\u9988\u5F0F\u3001\u52A8\u4F5C\u96C6\u5408\u4E0E\u8EAB\u4F53\u751F\u6210\u89C4\u5219\u5728\u672C\u6B21\u914D\u5BF9\u4E2D\u56FA\u5B9A\u3002\u5916\u5C42\u89C4\u5219\u641C\u7D22\u4F1A\u8DE8\u5019\u9009\u6539\u53D8\u89C4\u5219\uFF0C\u672C\u7247\u6CA1\u6709\u628A\u751F\u547D\u671F\u5E27\u5F53\u4F5C\u5916\u5C42\u8FED\u4EE3\u3002", "\u793A\u610F\u52A8\u6548\u53EA\u5728\u5DF2\u8BB0\u5F55\u7AEF\u70B9\u95F4\u5C55\u5F00\u8BB2\u89E3\uFF0C\u4E0D\u662F\u8865\u9020\u8EAB\u4F53\u5E27\u6216\u65B0\u79D1\u5B66\u8BA1\u7B97\u3002\u539F\u59CB\u8EAB\u4F53\u6BCF8\u6B65\u4FDD\u5B58\uFF1B\u52A8\u4F5C17/25/65\u5206\u522B\u662F\u5199\u5165\u3001fresh\u6982\u7387\u3001\u5B9E\u9645\u9009\u62E9\u7684\u65F6\u949F\u3002\u673A\u5236\u56FE\u7684\u7BAD\u5934\u3001\u9897\u7C92\u548C\u653E\u5927\u5C3A\u5EA6\u4E0D\u662F\u7269\u8D28\u642C\u8FD0\u6216\u65B0\u7684\u8F68\u8FF9\u3002", "\u8F93\u8FD0\u6DF7\u5408\u3001\u9009\u62E9\u6027\u6B7B\u4EA1\u4E0E\u53D8\u5316\u4E2D\u7684\u53C2\u6570\u4F7F\u4FE1\u7528\u5F52\u56E0\u6709\u504F\uFF0C\u4E0D\u58F0\u79F0\u65E0\u504F\u7B56\u7565\u68AF\u5EA6\u3002\u5355\u6B21\u914D\u5BF9\u3001\u540C\u4E00\u968F\u673A\u5E8F\u5217\uFF1B\u4E00\u4E2A\u9009\u62E9\u4E0D\u7B49\u4E8E\u7EC8\u70B9\u5DEE\u5F02\u7684\u552F\u4E00\u539F\u56E0\uFF0C\u6CA1\u6709\u8BC1\u660E\u5B66\u4F1A\u8D44\u6E90\u65B9\u5411\u6216\u7A33\u5B9A\u9002\u5E94\u3002", "\u6765\u6E90\uFF1Af91fe62e \u5DF2\u5B8C\u6210\u8BD5\u8FD0\u884C\uFF1B\u539F\u751F\u4E8B\u52A1\u3001first_realized_choice\u3001pre_first_choice_feedback\u3001\u5F00\u653E\u7A97\u8D26\u672C\u3002\u8FD9\u91CC\u53EA\u8BFB\u65E2\u6709\u8BB0\u5F55\uFF1B\u4E24\u6761state16\u7EED\u63A5\uFF0C\u65B0\u589Elife0\u3001\u7269\u79CD\u4E0E\u4EE3\u9645\u5747\u4E3A0\u3002"]) q.append(s("p", "", n0));
  let $2 = g("\u770B\u8FD9\u4E24\u652F\u771F\u5B9E\u8EAB\u4F53\u4E0E\u5B8C\u6574\u7ED3\u679C", () => R?.());
  $2.classList.add("is-primary"), d.append(i, M, P, c), h.append(r, A, _, d, W, U, $2, q), L.append(h);
  let r0 = new C.ResizeObserver(() => a0());
  r0.observe(h);
  function Y(n0, i0 = 0) {
    f = y1.slice(0, n0).reduce((c0, d0) => c0 + d0, 0) + i0 * y1[n0], b = false, l = false, a0();
  }
  function a0() {
    if (a) return;
    let n0 = d8(f / 1e3);
    o = n0.index;
    let i0 = n0.progress, c0 = h.clientWidth < 650, d0 = c0 ? 360 : 960, o0 = c0 ? 600 : 420;
    M.setAttribute("viewBox", `0 0 ${d0} ${o0}`), M.dataset.chapter = String(o), M.dataset.progress = i0.toFixed(4), h.dataset.playing = String(b), h.dataset.time = (f / 1e3).toFixed(3), p.forEach((p0, y0) => p0.setAttribute("aria-pressed", String(y0 === o))), w.textContent = f8[o], y.textContent = ["\u52A8\u4F5C17 \xB7 \u63A5\u53D7\u683C\u7684\u4E00\u6B21\u5199\u5165", "\u540C\u4E00\u52A8\u4F5C\u8F74 \xB7 17 / 25 / 65 \u4E09\u4E2A\u4E8B\u4EF6", "\u52A8\u4F5C65 \xB7 \u8BFBstate64", "\u56DB\u6B21\u5F00\u653E \xB7 \u5230state272"][o], z.textContent = l ? "\u4EA4\u4E92\u793A\u610F \xB7 \u4E0D\u8FD0\u884C\u8EAB\u4F53" : "\u771F\u5B9E\u8BB0\u5F55\u7AEF\u70B9 \xB7 \u52A8\u6548\u4E3A\u673A\u5236\u793A\u610F", u.textContent = b ? "\u6682\u505C\u52A8\u753B" : "\u7EE7\u7EED\u52A8\u753B", D.textContent = `${H1(f / 1e3, 1)} / 62 \u79D2\u8BB2\u89E3`, S.value = String(f / 1e3), M.innerHTML = [V, Q, J, B][o](i0, c0, d0, o0), P.textContent = ["\u8D44\u6E90\u5C1A\u672A\u5F00\u653E\uFF0C\u6CA1\u6709\u6444\u53D6\uFF1B\u4ED8\u6B3E\u5F62\u6210\u8D1F\u53CD\u9988\u3002\u5B83\u4E58\u4E0A\u5DF2\u968F\u7EC4\u7EC7\u6DF7\u5408\u7684\u4FE1\u7528\u75D5\u8FF9\uFF0C\u6539\u53D8\u4E94\u4E2A\u504F\u597D\uFF0C\u800C\u4E0D\u662F\u76F4\u63A5\u547D\u4EE4\u8EAB\u4F53\u671D\u8D44\u6E90\u8D70\u3002", "\u5199\u5165\u3001\u4E0B\u4E00\u6B21\u62BD\u6837\u3001\u771F\u6B63\u4E0D\u540C\u7684\u52A8\u4F5C\uFF0C\u662F\u540C\u4E00\u52A8\u4F5C\u8F74\u4E0A\u7684\u4E09\u4E2A\u4E8B\u4EF6\u3002\u03B8\u5DF2\u7ECF\u53D8\u4E86\uFF0C\u540C\u4E00\u4E2A\u968F\u673A\u6570\u4ECD\u5E38\u843D\u5728\u540C\u4E00\u6982\u7387\u533A\u95F4\uFF1B\u4FDD\u6301\u4E2D\u7684\u610F\u56FE\u4E5F\u4E0D\u4F1A\u88AB\u4E8B\u540E\u6539\u5199\u3002", "\u52A8\u4F5C65\u524D\u4E24\u652F\u7269\u7406\u573A\u76F8\u540C\u3001\u03B8\u4E0D\u540C\uFF0C\u6CBF\u540C\u4E00\u968F\u673A\u5E26\u62BD\u6837\u3002off\u8FB9\u754C\u53EA\u79FB\u52A8\u7EA60.000280\uFF0C\u5374\u6070\u597D\u8DE8\u8FC7\u8FD9\u4E2A\u6570\uFF1A\u66F4\u65B0\u652F\u9009off\uFF0C\u5BF9\u7167\u9009+y\u3002off\u4ECD\u53EF\u80FD\u53D1\u751F\u4ED8\u8D39\u8FD0\u52A8\u3002", "\u6539\u53D8\u9009\u62E9\u662F\u771F\u5B9E\u7684\uFF1B\u672C\u914D\u5BF9\u672A\u89C1\u660E\u786E\u603B\u4F53\u6444\u53D6\u6216\u76EE\u6807\u4F18\u52BF\uFF1A\u56DB\u6B21\u5F00\u653E\u7684\u6444\u53D6\u90FD\u7565\u4F4E\u3002\u672B\u7EC4\u7EC7\u7565\u591A\u3001\u80FD\u91CF\u7565\u5C11\uFF0C\u4E24\u652F\u90FD\u5931\u53BB\u7EA639.85%\u521D\u4F53\uFF1B\u4E0D\u5426\u8BA4\u4E2A\u522B\u65F6\u523B\u6307\u6807\u66FE\u7565\u9AD8\u3002"][o];
    let f0 = S1.write, h0 = S1.choice;
    W.textContent = [`\u63A5\u53D7\u683C(y,x)=(${f0.cell.join(",")}) \xB7 r=${H1(f0.receipt.reward, 9)} EU \xB7 \u6444\u53D6=${H1(f0.receipt.uptake, 9)} \xB7 \u8FD0\u52A8\u4ED8\u6B3E=${H1(f0.receipt.motor, 9)} \xB7 \u7EF4\u6301\u4ED8\u6B3E=${H1(f0.receipt.basal, 9)}\u3002\u6DF7\u5408trace\u662F\u539F\u8F7D\u8377/\u539F\u7EC4\u7EC7\u91CF\uFF1B\u6B64\u683C\u2212x\u75D5\u8FF9\u4E3A\u6B63\uFF0C\u8D1F\u56DE\u6267\u4F7F\u5176\u03B8\u4E0B\u8C03\uFF0C\u5176\u4F59\u504F\u597D\u7565\u5347\u3002`, `25\u7684\u771F\u5B9E\u62BD\u6837 ${S1.samples[1].update.used_draw.toFixed(10)}\uFF0C\u66F4\u65B0/\u5BF9\u7167\u90FD\u9009+x\u300217\u201364\u4E24\u652F\u7D2F\u8BA1\uFF1A\u6444\u53D60\uFF1B\u8FD0\u52A8\u5B9E\u4ED83.190740\u3001\u7EF4\u6301\u5B9E\u4ED81.382400 EU\uFF1B\u5B8C\u6574\u53CD\u9988\u22124.573140\u300265\u4E4B\u524D\u6CA1\u6709\u6765\u81EA\u6444\u53D6\u7684\u65B0\u53CD\u9988\u3002`, `\u89C2\u5BDF\u683C(y,x)=(16,12) \xB7 \u771F\u62BD\u6837 ${h0.draw.toFixed(15)} \xB7 \u66F4\u65B0\u03C0(off) ${h0.pi_update[0].toFixed(15)} / \u5BF9\u71670.2\u3002\u8BE5\u683C\u662F\u4E8B\u540E\u89C2\u5BDF\u4F4D\u7F6E\uFF0C\u4E0D\u4F5C\u4E3A\u63A7\u5236\u5668\u8F93\u5165\u3002`, "\u7EC8\u70B9 \u66F4\u65B0 / \u5BF9\u7167\uFF1AM 14.437176 / 14.434865\uFF1BE 2.051119 / 2.051450\u3002M\u591A0.002312\u3001E\u5C110.000331\uFF0C\u4E0D\u80FD\u53EA\u9009\u4E00\u4E2A\u91CF\u5BA3\u5E03\u8D62\u5BB6\u3002"][o];
    let u0 = l ? v : h0.draw;
    N.textContent = `${l ? "\u793A\u610F" : "\u5B9E\u9645\u8BB0\u5F55"}\u62BD\u6837 ${u0.toFixed(15)} \u2192 \u66F4\u65B0 ${Ct[G2(h0.pi_update, u0)]} / \u5BF9\u7167 ${Ct[G2(h0.pi_no_update, u0)]}`;
  }
  function V(n0, i0, c0, d0) {
    let o0 = S1.write, f0 = o0.receipt, h0 = l2((n0 - 0.05) / 0.4), u0 = l2((n0 - 0.45) / 0.4), p0 = i0 ? 145 : 178, y0 = i0 ? 180 : 340, w0 = i0 ? 45 : 100, x0 = i0 ? 180 : 660, z0 = Ce(24, 30, "\u4ED8\u6B3E\u4E0E\u6DF7\u5408\u4FE1\u7528\u75D5\u8FF9\uFF0C\u5171\u540C\u5199\u5165\u4E94\u4E2A\u504F\u597D", i0 ? 14 : 18, "#b9cfdf");
    [["\u6444\u53D6", f0.uptake, "#5ad7c7"], ["\u8FD0\u52A8", -f0.motor, "#ffbd77"], ["\u7EF4\u6301", -f0.basal, "#fa8bab"]].forEach(([v0, T0, _0], j0) => {
      let m0 = i0 ? 60 + j0 * 120 : w0, g0 = i0 ? 65 : 100 + j0 * 83;
      z0 += Lt(m0, g0, 23, _0, 'opacity=".15"') + Ce(m0, g0 + 5, v0, 15, _0, "middle");
      let C0 = p0 - 10;
      if (z0 += `<path d="M${m0},${g0 + 25} Q${m0},${p0} ${y0},${C0}" fill="none" stroke="${_0}" opacity=".2"/>`, T0 !== 0) for (let Z0 = 0; Z0 < 4; Z0++) {
        let B0 = u8(h0 * 1.5 - Z0 * 0.15);
        z0 += Lt(m0 + (y0 - m0) * B0, g0 + 25 + (C0 - g0 - 25) * B0, 3.8, _0, `opacity="${B0 > 0 && B0 < 1 ? 0.8 : 0}"`);
      }
    }), z0 += Lt(y0, p0, 40, "#ffbd77", 'opacity=".1"') + Lt(y0, p0, 34, "none", 'stroke="#ffbd77"') + Ce(y0, p0 - 2, "\u8D1F\u53CD\u9988 r", 15, "#ffbd77", "middle") + Ce(y0, p0 + 21, H1(f0.reward, 6), 13, "#fff", "middle"), i0 || (z0 += Ot(y0 + 44, p0, x0 - 80, p0, "#ffbd77", 'stroke-width="2" stroke-dasharray="5 5"'));
    let N0 = i0 ? 275 : 70, E0 = i0 ? 480 : 285, F0 = i0 ? 40 : 510, V0 = i0 ? 45 : 66, H0 = i0 ? 19 : 18, U0 = n0 < 0.73 ? 4 : 1, Y0 = i0 ? 218 : 345;
    z0 += Ce(i0 ? 16 : 100, i0 ? Y0 - 14 : Y0 + 7, "\u6DF7\u5408\u4FE1\u7528\u75D5\u8FF9 \xD7 \u8D1F\u53CD\u9988", i0 ? 13 : 15, "#b9cfdf"), o0.traceMixed.forEach((v0, T0) => {
      let _0 = F0 + V0 / 2 + T0 * (V0 + H0);
      z0 += Ce(_0, Y0 + 7, `${v0 >= 0 ? "+" : ""}${v0.toFixed(3)}`, i0 ? 12 : 14, z1[T0], "middle");
    }), z0 += Ce(F0, N0 - 17, "\u03B8 \u5199\u5165\u524D\uFF08\u8F6E\u5ED3\uFF09 \u2192 \u5199\u5165\u540E\uFF08\u5B9E\u8272\uFF09", i0 ? 14 : 17, "#d5e2ec");
    let R0 = Math.max(...o0.thetaAfter.map(Math.abs), ...o0.thetaMixed.map(Math.abs), 1e-9), D0 = (N0 + E0) / 2;
    z0 += Ot(F0 - 10, D0, F0 + 5 * (V0 + H0) - H0 + 8, D0, "#78859d"), z0 += Ce(F0 - 15, D0 + 4, "0", 12, "#a9bdd1", "end");
    for (let v0 = 0; v0 < 5; v0++) {
      let T0 = F0 + v0 * (V0 + H0), _0 = o0.thetaMixed[v0] / R0, j0 = o0.thetaAfter[v0] / R0, m0 = _0 + (j0 - _0) * u0, g0 = (E0 - N0) * 0.34;
      u0 > 0 && v0 === U0 && (z0 += Z1(T0 - 5, N0 - 3, V0 + 10, E0 - N0 + 50, z1[v0], 6, 'opacity=".10"')), z0 += Z1(T0, D0 - Math.max(0, _0) * g0, V0, Math.max(1, Math.abs(_0) * g0), "none", 3, `stroke="${z1[v0]}" stroke-dasharray="3 3"`), z0 += Z1(T0, D0 - Math.max(0, m0) * g0, V0, Math.max(1, Math.abs(m0) * g0), z1[v0], 3, 'opacity=".8"'), z0 += Ce(T0 + V0 / 2, E0 + 20, Ct[v0], 16, z1[v0], "middle"), z0 += Ce(T0 + V0 / 2, E0 + 40, H1(o0.thetaAfter[v0], 5), i0 ? 10 : 12, "#dbe6f5", "middle");
    }
    return z0 += Ce(i0 ? 180 : 720, i0 ? 554 : 382, U0 === 4 ? "\u8D1F r \xD7 \u6B63\u75D5\u8FF9\uFF08\u2212x\uFF09\u2192 \u03B8 \u4E0B\u964D" : "\u8D1F r \xD7 \u8D1F\u75D5\u8FF9\uFF08+y\uFF09\u2192 \u03B8 \u4E0A\u5347", i0 ? 17 : 19, z1[U0], "middle"), z0 += Ce(i0 ? 180 : 720, i0 ? 582 : 407, "\u504F\u597D\u523B\u5EA6\u5DF2\u653E\u5927\uFF1B\u6570\u5B57\u4E3A\u539F\u8BB0\u5F55\u3002", i0 ? 12 : 13, "#9eb2c8", "middle"), z0;
  }
  function Q(n0, i0, c0, d0) {
    let o0 = S1.samples.find((E0) => E0.step === 25), f0 = o0.update, h0 = o0.no_update, u0 = i0 ? 25 : 110, p0 = c0 - u0 * 2, y0 = [17, 25, 65], w0 = ["\u03B8 \u9996\u6B21\u5199\u5165", "fresh \u03C0 \u4E0D\u540C", "\u5B9E\u9645\u9009\u62E9\u4E0D\u540C"], x0 = "";
    y0.forEach((E0, F0) => {
      let V0 = u0 + F0 * p0 / 2, H0 = F0 === 0 ? "start" : F0 === 2 ? "end" : "middle";
      x0 += Lt(V0, 43, 7, F0 === 1 ? "#b2a2ff" : "#536b83") + Ce(V0, 70, String(E0), 16, "#d4e4f3", "middle") + Ce(V0, 96, w0[F0], i0 ? 11 : 15, "#a1b7ce", H0), F0 < 2 && (x0 += Ot(V0 + 10, 43, V0 + p0 / 2 - 10, 43, "#536b83"));
    }), x0 += Ce(u0, i0 ? 145 : 140, "\u52A8\u4F5C25\uFF1A\u6982\u7387\u5DF2\u4E0D\u540C\uFF0C\u62BD\u6837\u4ECD\u90FD\u843D\u5728 +x", i0 ? 15 : 21, "#e5d8ff");
    let z0 = i0 ? [205, 325] : [200, 295];
    [[f0, "\u4ED8\u6B3E\u53CD\u9988\u66F4\u65B0"], [h0, "\u5173\u95ED\u65B0\u03B8\u589E\u91CF"]].forEach(([E0, F0], V0) => {
      let H0 = z0[V0];
      x0 += Ce(u0, H0 - 17, F0, 15);
      let U0 = 0;
      E0.old_pi.forEach((R0, D0) => {
        x0 += Z1(u0 + U0 * p0, H0, R0 * p0, 27, z1[D0], 0, 'opacity=".8"') + Ce(u0 + (U0 + R0 / 2) * p0, H0 + 19, Ct[D0], 13, "#09192b", "middle"), U0 += R0;
      });
      let Y0 = u0 + f0.used_draw * p0;
      x0 += Ot(Y0, H0 - 8, Y0, H0 + 34, "#fff", 'stroke-width="2"'), x0 += Ce(u0, H0 + 62, `\u540C\u4E00\u62BD\u6837 ${f0.used_draw.toFixed(10)} \u2192 +x`, i0 ? 13 : 17, "#e5edf5");
    }), x0 += Ce(u0, i0 ? 440 : 390, "\u5185\u90E8\u504F\u597D\u53D8\u5316 \u2260 \u6BCF\u6B21\u884C\u52A8\u90FD\u6539\u53D8", i0 ? 16 : 18, "#ffbd77"), i0 && (x0 += Ce(u0, 485, "\u4FDD\u6301\u4E2D\u7684\u610F\u56FE\u4E0D\u5012\u6539\uFF1B\u4E0B\u6B21fresh\u624D\u91CD\u65B0\u62BD\u6837\u3002", 12, "#bacddd"));
    let N0 = l2(n0);
    return x0 += Lt(u0 + f0.used_draw * p0, z0[0] - 8, 5 + N0 * 6, "#fff", `opacity="${0.3 + 0.4 * (1 - N0)}"`), x0;
  }
  function J(n0, i0, c0, d0) {
    let o0 = S1.choice, f0 = l ? v : o0.draw, h0 = l ? 1 : l2(n0 / 0.6), u0 = i0 ? 28 : 90, p0 = c0 - 2 * u0, y0 = 28, w0 = Ce(24, 30, "\u4E94\u4E2A\u533A\u95F4\u603B\u548C = 1\uFF1B\u540C\u4E00\u4E2A\u6570\u843D\u5728\u54EA\u6BB5\uFF0C\u5C31\u9009\u54EA\u4E2A\u610F\u56FE", i0 ? 12 : 18, "#b9cfdf"), z0 = [[o0.pi_update.map((H0, U0) => o0.pi_no_update[U0] + h0 * (H0 - o0.pi_no_update[U0])), "\u4ED8\u6B3E\u53CD\u9988\u66F4\u65B0", i0 ? 95 : 100], [o0.pi_no_update, "\u5173\u95ED\u65B0\u03B8\u589E\u91CF", 200]];
    for (let [H0, U0, Y0] of z0) {
      w0 += Ce(u0, Y0 - 18, U0, 16);
      let R0 = 0;
      H0.forEach((k0, v0) => {
        w0 += Z1(u0 + R0 * p0, Y0, k0 * p0, y0, z1[v0], 0, 'opacity=".8"') + Ce(u0 + (R0 + k0 / 2) * p0, Y0 + 20, Ct[v0], 13, "#0b182b", "middle"), R0 += k0;
      });
      let D0 = u0 + f0 * p0;
      w0 += Ot(D0, Y0 - 9, D0, Y0 + y0 + 10, "#fff", 'stroke-width="2"'), h0 === 1 && (w0 += Ce(u0, Y0 + y0 + 34, `\u62BD\u6837 ${H1(f0, 9)} \u2192 ${Ct[G2(H0, f0)]}`, i0 ? 14 : 17, "#f3f6fa"));
    }
    let A0 = i0 ? 365 : 320, N0 = p0, E0 = 0.1998, F0 = 0.2004, V0 = (H0) => u0 + (H0 - E0) / (F0 - E0) * N0;
    return w0 += Ce(u0, A0 - 32, "off / +y \u8FB9\u754C\u5C40\u90E8\u653E\u5927", i0 ? 17 : 18, "#ffdaaa"), w0 += Ce(u0, A0 - 10, "\u8303\u56F40.1998\u20130.2004\uFF08\xD71667\uFF09\uFF1B\u4E24\u884C\u540C\u5C3A\u5EA6", i0 ? 12 : 14, "#9eb2c8"), z0.forEach(([H0, U0], Y0) => {
      let R0 = A0 + Y0 * (i0 ? 74 : 46), D0 = H0[0];
      w0 += Z1(u0, R0, V0(D0) - u0, 20, z1[0], 0) + Z1(V0(D0), R0, u0 + N0 - V0(D0), 20, z1[1], 0), w0 += Ce(V0(D0), R0 + 37, H1(D0, 9), i0 ? 11 : 13, z1[0], "middle"), f0 >= E0 && f0 <= F0 && (w0 += Ot(V0(f0), R0 - 6, V0(f0), R0 + 25, "#fff", 'stroke-width="3"'));
    }), i0 && (w0 += Ce(24, 570, "\u767D\u7EBF\u56FA\u5B9A\uFF1B\u8FB9\u754C\u8865\u95F4\u4E3A\u8BB2\u89E3\uFF0C\u4E0D\u662F\u65B0\u8FD0\u884C\u8BB0\u5F55\u3002", 12, "#b9cfdf")), w0;
  }
  function B(n0, i0, c0, d0) {
    let o0 = S1.openings, f0 = i0 ? 100 : 255, h0 = 1.6, u0 = i0 ? 225 : 600, p0 = Ce(24, 32, "\u6444\u53D6\u91CF EU \xB7 \u540C\u4E00\u7EDD\u5BF9\u5C3A\u5EA6\uFF0C\u4ECE0\u8D77", i0 ? 15 : 19, "#d7e6f3"), y0 = l2(n0 / 0.65);
    return o0.forEach((w0, x0) => {
      let z0 = (i0 ? 92 : 80) + x0 * (i0 ? 109 : 78);
      p0 += Ce(i0 ? 14 : 45, z0 + 15, w0.label, i0 ? 13 : 16, "#bdcde0"), p0 += Z1(f0, z0, w0.update / h0 * u0 * y0, 15, "#5ad7c7", 2) + Z1(f0, z0 + 22, w0.control / h0 * u0 * y0, 15, "#b2a2ff", 2), p0 += Ce(f0, z0 + 58, `\u66F4\u65B0 \u2212 \u5BF9\u7167 ${aa(w0.update - w0.control)}`, i0 ? 13 : 16, "#ffbd77");
    }), p0 += Ce(24, i0 ? 558 : 405, "\u9752\uFF1A\u4ED8\u6B3E\u53CD\u9988\u66F4\u65B0\u3000\u7D2B\uFF1A\u5173\u95ED\u65B0\u03B8\u589E\u91CF", i0 ? 13 : 16, "#c4d4e6"), p0;
  }
  function G() {
    !a && T && b && !t && (t = C.requestAnimationFrame(t0));
  }
  function t0(n0) {
    if (t = 0, !T || a || !b) {
      e = 0;
      return;
    }
    e && (f = Math.min(62e3, f + Math.min(100, n0 - e) * n)), e = n0, f >= 62e3 && (b = false), a0(), G();
  }
  function K2() {
    T = false, e = 0, C.cancelAnimationFrame(t), t = 0;
  }
  function s0() {
    T = true, e = 0, a0(), G();
  }
  function e0() {
    a || (K2(), a = true, r0.disconnect(), E?.removeEventListener("abort", e0), h.remove());
  }
  return E?.addEventListener("abort", e0, { once: true }), a0(), E?.aborted && e0(), { show: s0, hide: K2, destroy: e0, getScene: () => ({ mode: "learning-algorithm", method: "f91-held-intent", chapter: o, explanation_seconds: f / 1e3, representation: l ? "sampling-illustration" : "recorded-endpoints-mechanism-animation", display_only: true }) };
}

// source/viewer.js
var $ = (id) => document.getElementById(id);
var cache = /* @__PURE__ */ new Map();
async function json(path) {
  const asset = path.replace(/\.json$/, ".js");
  const worker = globalThis.Projection?.createWorker ? await Projection.createWorker(asset) : new Worker(new URL(asset, document.baseURI));
  return new Promise((resolve, reject) => {
    const finish = (error, data) => {
      clearTimeout(timer);
      worker.terminate();
      error ? reject(error) : resolve(data);
    };
    const timer = setTimeout(() => finish(Error("\u52A8\u753B\u6570\u636E\u52A0\u8F7D\u8D85\u65F6\uFF1A" + path)), 3e4);
    worker.onmessage = (event) => finish(null, event.data);
    worker.onerror = (event) => finish(Error("\u65E0\u6CD5\u8BFB\u53D6 " + path + "\uFF1A" + event.message));
    worker.onmessageerror = () => finish(Error("\u52A8\u753B\u6570\u636E\u683C\u5F0F\u9519\u8BEF\uFF1A" + path));
    worker.postMessage("read");
  });
}
async function start() {
  $("wall").textContent = "\u6B63\u5728\u52A0\u8F7D\u52A8\u753B\u5899\u2026";
  const entries = await json("catalog.json");
  const groups = [["overview", "\u7CBE\u9009\u6982\u89C8", "featured"], ["all", "\u5168\u9009", "all"], ...new Map(entries.filter((e) => e.deliveryGroup !== "featured").map((e) => [e.deliveryGroup, e.deliveryGroupLabel])).entries()].map(([id, title, key]) => ({ id, title, storyLabel: id === "overview" ? void 0 : title, entries: entries.filter((e) => (key || id) === "all" || e.deliveryGroup === (key || id)), pageSize: 6 }));
  async function loadClip(id) {
    if (!cache.has(id)) cache.set(id, (async () => {
      const entry = entries.find((e) => e.id === id), record = await json(entry.offlineClip), payload = await json(record.framesFile), frames = Uint8Array.from(atob(payload.data), (c) => c.charCodeAt(0));
      const hash = await sha256(frames);
      if (hash !== record.sha256 || frames.length !== record.width * record.height * record.steps.length) throw Error("\u56DE\u653E\u6821\u9A8C\u5931\u8D25\uFF1A" + id);
      return { ...record, frames };
    })().catch((error) => {
      cache.delete(id);
      throw error;
    }));
    return cache.get(id);
  }
  let wall, algorithm;
  function showWall() {
    $("learn").hidden = true;
    $("wall").hidden = false;
    algorithm?.hide();
    wall?.show();
  }
  function showLearning() {
    wall.hide();
    $("wall").hidden = true;
    $("learn").hidden = false;
    algorithm.show();
  }
  algorithm = p8($("algorithm"), { onObserve: () => {
    showWall();
    wall.openCollection("learning");
  } });
  $("wall").replaceChildren();
  wall = await Y6($("wall"), {
    loadCatalog: async () => ({ entries, collections: groups, loadClip, destroy() {
      cache.clear();
    } }),
    onLearn: showLearning,
    onObserve: openPaper
  });
  const learnButton = document.createElement("button");
  learnButton.type = "button";
  learnButton.className = "portalButton";
  learnButton.textContent = "\u5B66\u4E60\u7B97\u6CD5\u52A8\u753B";
  learnButton.setAttribute("aria-controls", "learn");
  learnButton.onclick = showLearning;
  document.querySelector(".morphology-heading .morphology-tools").prepend(learnButton);
  $("back").onclick = () => {
    showWall();
    learnButton.focus();
  };
  const nav = document.querySelector(".morphology-stories"), paper = document.createElement("a");
  paper.className = "portalButton";
  paper.textContent = "\u8BBA\u6587\u4E0E\u6210\u679C";
  paper.href = "/resource/article?item=article_0565c33e0c03";
  paper.target = "_blank";
  paper.rel = "noopener";
  nav?.append(paper);
  paper.onclick = (event) => {
    event.preventDefault();
    openPaper();
  };
  wall.show();
  document.body.dataset.ready = "true";
  window.addEventListener("pagehide", () => {
    wall.destroy();
    algorithm.destroy();
  }, { once: true });
}
function openPaper() {
  const url = "/resource/article?item=article_0565c33e0c03";
  if (globalThis.Projection?.openPage) return Projection.openPage(url);
  window.open(url, "_blank", "noopener");
}
start().catch((error) => {
  const message = document.createElement("p");
  message.setAttribute("role", "alert");
  message.textContent = "\u52A8\u753B\u5899\u52A0\u8F7D\u5931\u8D25\uFF1A" + error.message + "\u3002\u8BF7\u5237\u65B0\u91CD\u8BD5\u3002";
  $("wall").replaceChildren(message);
  console.error(error);
});
