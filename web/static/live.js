/* 초록말 - 실시간 식물 상태 화면
 *
 * 백엔드(src/web_dashboard.py)의 /data 를 주기적으로 읽어 화면을 갱신합니다.
 * 백엔드가 없으면(파일만 열었을 때) 데모 값으로 대체해 화면을 확인할 수 있습니다.
 */
(function () {
  "use strict";

  var POLL_MS = 500;
  var RING_LEN = 108.7;          // r=17.3 원의 둘레
  var TREND_MAX = 120;           // 확신도 추이 보관 개수 (약 1분)
  var WAVE_MAX = 1500;           // 파형 롤링 버퍼 길이

  var STATE = {
    "정상":     { c: "var(--primary)", d: "var(--primary-hover)", s: "var(--primary-soft)",
                  hex: "#2f7d45", alert: null },
    "수분부족": { c: "var(--water)", d: "var(--water-deep)", s: "var(--water-soft)",
                  hex: "#2f6fd0",
                  alert: { icon: "💧", text: "수분부족 신호가 이어지고 있어요. 겉흙이 말랐다면 물을 주세요.",
                           bg: "var(--water-soft)", border: "var(--water-soft-border)" } },
    "자극":     { c: "var(--warning)", d: "var(--warning-foreground)", s: "var(--warning-soft)",
                  hex: "#b8860b",
                  alert: { icon: "⚡", text: "잎에 닿는 자극이 감지됐어요. 순간적인 반응이라 곧 회복됩니다.",
                           bg: "var(--warning-soft)", border: "var(--warning-soft-border)" } },
    "꺾임":     { c: "var(--destructive)", d: "var(--destructive)", s: "var(--destructive-soft)",
                  hex: "#b5453a",
                  alert: { icon: "🥀", text: "손상으로 보이는 신호예요. 줄기와 잎을 확인해 주세요.",
                           bg: "var(--destructive-soft)", border: "color-mix(in srgb, var(--destructive) 25%, transparent)" } }
  };

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var live = function (n) { return $('[data-live="' + n + '"]'); };
  var setText = function (n, v) { var el = live(n); if (el) el.textContent = v; };

  // 화면 상태
  var paused = false;
  var waveBuf = [];              // 파형 롤링 버퍼
  var trend = [];                // {conf, hex}
  var lastState = null;
  var stateSince = Date.now();
  var changes = [];              // {ts, state, proba}
  var lastSeen = 0;

  // ── 유틸 ─────────────────────────────────────────────────────────
  function since(ms) {
    var s = Math.max(0, Math.round((Date.now() - ms) / 1000));
    if (s < 60) return s + "초째";
    var m = Math.floor(s / 60);
    if (m < 60) return m + "분째";
    return Math.floor(m / 60) + "시간 " + (m % 60) + "분째";
  }

  function hhmm(ts) {
    var d = new Date(ts);
    return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2) +
           ":" + ("0" + d.getSeconds()).slice(-2);
  }

  function cssVal(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // ── 그리기: 파형 ─────────────────────────────────────────────────
  function drawWave() {
    $$('[data-live="wave"], [data-live="wave-big"]').forEach(function (cv) {
      var ctx = cv.getContext("2d"), w = cv.width, h = cv.height;
      ctx.clearRect(0, 0, w, h);
      if (waveBuf.length < 2) return;

      // 기준선
      ctx.strokeStyle = "rgba(120,200,140,.16)";
      ctx.lineWidth = 1;
      for (var g = 1; g < 4; g++) {
        var y = h * g / 4;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
      }

      var lo = Infinity, hi = -Infinity;
      for (var i = 0; i < waveBuf.length; i++) {
        if (waveBuf[i] < lo) lo = waveBuf[i];
        if (waveBuf[i] > hi) hi = waveBuf[i];
      }
      var span = (hi - lo) || 1e-6;
      var step = w / (waveBuf.length - 1);

      ctx.beginPath();
      for (var j = 0; j < waveBuf.length; j++) {
        var yy = h - 12 - ((waveBuf[j] - lo) / span) * (h - 24);
        if (j === 0) ctx.moveTo(0, yy); else ctx.lineTo(j * step, yy);
      }
      ctx.strokeStyle = cssVal("--signal") || "#5fce7a";
      ctx.lineWidth = Math.max(1.4, w / 700);
      ctx.lineJoin = "round";
      ctx.stroke();

      // 최신 지점 표시
      var lastY = h - 12 - ((waveBuf[waveBuf.length - 1] - lo) / span) * (h - 24);
      ctx.beginPath();
      ctx.arc(w - 2, lastY, Math.max(3, w / 320), 0, Math.PI * 2);
      ctx.fillStyle = cssVal("--signal") || "#5fce7a";
      ctx.fill();
    });
  }

  // ── 그리기: 스펙트로그램 ─────────────────────────────────────────
  var VIRIDIS = [[16, 24, 20], [24, 70, 60], [32, 130, 96], [120, 190, 110], [224, 232, 150]];
  function ramp(t) {
    t = Math.max(0, Math.min(1, t)) * 4;
    var k = Math.floor(t), f = t - k, a = VIRIDIS[k], b = VIRIDIS[Math.min(4, k + 1)];
    return "rgb(" + ((a[0] + (b[0] - a[0]) * f) | 0) + "," +
                    ((a[1] + (b[1] - a[1]) * f) | 0) + "," +
                    ((a[2] + (b[2] - a[2]) * f) | 0) + ")";
  }

  function drawSpec(spec) {
    if (!spec || !spec.length) return;
    var rows = spec.length, cols = spec[0].length;
    var lo = Infinity, hi = -Infinity;
    for (var r = 0; r < rows; r++) {
      for (var c = 0; c < cols; c++) {
        var v = spec[r][c];
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
    }
    var span = (hi - lo) || 1e-6;
    $$('[data-live="spec"], [data-live="spec-big"]').forEach(function (cv) {
      var ctx = cv.getContext("2d"), w = cv.width, h = cv.height;
      var cw = w / cols, ch = h / rows;
      for (var r2 = 0; r2 < rows; r2++) {
        for (var c2 = 0; c2 < cols; c2++) {
          ctx.fillStyle = ramp((spec[r2][c2] - lo) / span);
          ctx.fillRect(c2 * cw, h - (r2 + 1) * ch, Math.ceil(cw), Math.ceil(ch));
        }
      }
    });
  }

  // ── 그리기: 확신도 추이 ──────────────────────────────────────────
  function drawTrend() {
    var cv = live("trend");
    if (!cv) return;
    var ctx = cv.getContext("2d"), w = cv.width, h = cv.height;
    ctx.clearRect(0, 0, w, h);

    ctx.strokeStyle = cssVal("--border") || "#e4eae5";
    ctx.lineWidth = 1;
    [0.25, 0.5, 0.75].forEach(function (p) {
      ctx.beginPath(); ctx.moveTo(0, h * p); ctx.lineTo(w, h * p); ctx.stroke();
    });
    if (trend.length < 2) return;

    var step = w / (TREND_MAX - 1);
    // 구간별 색(상태가 바뀌면 색도 바뀜)
    for (var i = 1; i < trend.length; i++) {
      ctx.beginPath();
      ctx.moveTo((i - 1) * step, h - trend[i - 1].conf * h);
      ctx.lineTo(i * step, h - trend[i].conf * h);
      ctx.strokeStyle = trend[i].hex;
      ctx.lineWidth = 2;
      ctx.lineCap = "round";
      ctx.stroke();
    }
  }

  // ── 로그 ─────────────────────────────────────────────────────────
  function renderLog() {
    var box = live("log");
    if (!box) return;
    if (!changes.length) {
      box.innerHTML = '<p class="py-6 text-center text-xs text-subtle">상태가 바뀌면 여기에 쌓입니다.</p>';
      return;
    }
    box.innerHTML = changes.slice(0, 10).map(function (c, i) {
      var st = STATE[c.state] || STATE["정상"];
      var held = i === 0 ? since(c.ts) : "";
      return '<div class="flex items-center gap-3 border-b border-border py-2.5 last:border-b-0">' +
               '<span style="width:9px;height:9px;border-radius:50%;background:' + st.c + ';flex:none"></span>' +
               '<div class="min-w-0 flex-1">' +
                 '<p class="text-sm font-bold text-foreground">' + c.state + ' 감지' +
                   (held ? ' <span class="text-[11px] font-medium text-subtle">' + held + '</span>' : '') + '</p>' +
                 '<p class="text-[11px] text-muted-foreground">확신도 ' +
                   (c.proba == null ? "—" : (c.proba * 100).toFixed(1) + "%") + '</p>' +
               '</div>' +
               '<span class="shrink-0 text-[11px] font-medium tabular-nums text-subtle">' + hhmm(c.ts) + '</span>' +
             '</div>';
    }).join("");
  }

  // ── 적용 ─────────────────────────────────────────────────────────
  function apply(d) {
    if (!d || !d.ready) return;
    lastSeen = Date.now();

    var st = STATE[d.state] || STATE["정상"];
    var scope = $("[data-state-scope]");
    if (scope) {
      scope.style.setProperty("--state", st.c);
      scope.style.setProperty("--state-deep", st.d);
      scope.style.setProperty("--state-soft", st.s);
    }

    if (d.state !== lastState) {
      if (lastState !== null) {
        changes.unshift({ ts: Date.now(), state: d.state, proba: d.proba });
        changes = changes.slice(0, 40);
      } else {
        changes.unshift({ ts: Date.now(), state: d.state, proba: d.proba });
      }
      lastState = d.state;
      stateSince = Date.now();
      renderLog();
    }

    setText("state", d.state);
    var conf = d.proba == null ? null : d.proba * 100;
    setText("conf", conf == null ? "확신도 —" : "확신도 " + conf.toFixed(1) + "%");
    setText("duration", since(stateSince));

    var ring = live("ring");
    if (ring) {
      ring.setAttribute("stroke-dasharray",
        ((conf || 0) / 100 * RING_LEN).toFixed(1) + " " + RING_LEN);
    }

    // 확률 막대
    if (d.probs) {
      $$("[data-cls]").forEach(function (row) {
        var p = d.probs[row.dataset.cls];
        var pct = p == null ? 0 : p * 100;
        var bar = $("[data-bar]", row), val = $("[data-val]", row);
        if (bar) bar.style.width = pct.toFixed(1) + "%";
        if (val) val.textContent = pct.toFixed(1) + "%";
      });
    }

    // 경고 배너
    var banner = live("alert-banner");
    if (banner) {
      if (st.alert) {
        banner.style.display = "flex";
        banner.style.background = st.alert.bg;
        banner.style.borderColor = st.alert.border;
        setText("alert-icon", st.alert.icon);
        setText("alert-text", st.alert.text);
      } else {
        banner.style.display = "none";
      }
    }

    // 파형 버퍼 (뒤쪽 일부만 이어붙여 흐르게)
    if (!paused && d.signal && d.signal.length) {
      var take = d.signal.slice(-Math.max(4, Math.round(d.signal.length / 6)));
      waveBuf = waveBuf.concat(take);
      if (waveBuf.length > WAVE_MAX) waveBuf = waveBuf.slice(-WAVE_MAX);
      drawWave();
    }
    if (!paused) drawSpec(d.spec);

    // 확신도 추이
    if (!paused && conf != null) {
      trend.push({ conf: conf / 100, hex: st.hex });
      if (trend.length > TREND_MAX) trend.shift();
      drawTrend();
    }

    // 신호 통계
    var f = function (v, unit) { return v == null ? "—" : v + (unit || ""); };
    setText("mean", f(d.mean, " V"));
    setText("std", f(d.std, " V"));
    setText("p2p", f(d.p2p, " V"));
    setText("wave-peak", d.p2p == null ? "PEAK —" : "PEAK " + d.p2p + " V");

    // 스파이크: 표준편차의 3배를 넘는 샘플 수
    if (d.signal && d.signal.length && d.std) {
      var thr = d.std * 3, mean = d.mean || 0, n = 0;
      for (var i = 0; i < d.signal.length; i++) {
        if (Math.abs(d.signal[i] - mean) > thr) n++;
      }
      setText("spikes", n + "개");
    }

    // 안정도 = 최근 추이에서 현재 상태가 차지한 비율
    var same = changes.length ? 1 : 0;
    var recent = trend.slice(-60);
    if (recent.length) {
      var hit = recent.filter(function (t) { return t.hex === st.hex; }).length;
      setText("stability", Math.round(hit / recent.length * 100) + "%");
    }
    setText("changes", changes.length + "회");
    if (conf != null) setText("quality", conf >= 70 ? "좋음" : conf >= 45 ? "보통" : "낮음");

    setText("source", d.source || "입력 소스 확인 중");
    applyEnv(d);
  }

  // ── 온·습도 게이지 ───────────────────────────────────────────────
  // 백엔드가 temp / humidity 를 보내주면 자동으로 채워집니다. 센서가 아직 없으면
  // 값 없음(—)으로 두고 점을 회색으로 남겨 실제 측정이 아님을 드러냅니다.
  var TEMP_SCALE = [10, 35], HUM_SCALE = [0, 100];

  function gauge(value, scale, valEls, dotEls, unit, color) {
    var has = typeof value === "number" && isFinite(value);
    valEls.forEach(function (n) { setText(n, has ? value.toFixed(1) + unit : "—"); });
    dotEls.forEach(function (n) {
      var dot = live(n);
      if (!dot) return;
      if (!has) {
        dot.style.background = "var(--border)";
        dot.style.left = "50%";
        return;
      }
      var pct = (value - scale[0]) / (scale[1] - scale[0]) * 100;
      dot.style.left = Math.max(0, Math.min(100, pct)).toFixed(1) + "%";
      dot.style.background = color;
    });
  }

  function applyEnv(d) {
    gauge(d.temp, TEMP_SCALE, ["temp", "temp-m"], ["temp-dot", "temp-dot-m"], "°C", "var(--warning)");
    gauge(d.humidity, HUM_SCALE, ["humidity", "humidity-m"], ["hum-dot", "hum-dot-m"], "%", "var(--water)");

    var missing = !(typeof d.temp === "number") && !(typeof d.humidity === "number");
    var note = live("temp-range");
    if (note) note.textContent = missing ? "적정 18–24°C · 센서 대기 중" : "적정 18–24°C";
    var note2 = live("hum-range");
    if (note2) note2.textContent = missing ? "적정 45–70% · 센서 대기 중" : "적정 45–70%";
  }

  // ── 데모 (백엔드 없이 파일만 열었을 때) ──────────────────────────
  var demo = false, demoStart = Date.now();
  var DEMO = ["정상", "수분부족", "자극", "꺾임"];

  function demoPayload() {
    var t = (Date.now() - demoStart) / 1000;
    var state = DEMO[Math.floor(t / 10) % DEMO.length];
    var top = 0.68 + Math.sin(t / 3) * 0.12;
    var probs = {};
    DEMO.forEach(function (s) { probs[s] = (1 - top) / 3; });
    probs[state] = top;

    var signal = [], v = 0;
    for (var i = 0; i < 120; i++) {
      v = v * 0.72 + (Math.random() - 0.5) * 0.014;
      var s = Math.sin(t * 2 + i / 8) * 0.016 + v;
      if (state === "자극" && i % 37 === 3) s += 0.085;
      if (state === "꺾임") s -= 0.026;
      signal.push(s);
    }
    var spec = [];
    for (var r = 0; r < 26; r++) {
      var row = [];
      for (var c = 0; c < 22; c++) row.push(-18 - r * 2.1 + Math.sin(c / 3 + t) * 3 + Math.random() * 4);
      spec.push(row);
    }
    var arr = signal.slice();
    var mean = arr.reduce(function (a, b) { return a + b; }, 0) / arr.length;
    var std = Math.sqrt(arr.reduce(function (a, b) { return a + (b - mean) * (b - mean); }, 0) / arr.length);
    return { ready: true, state: state, proba: top, probs: probs, signal: signal, spec: spec,
             mean: +mean.toFixed(4), std: +std.toFixed(4),
             p2p: +(Math.max.apply(null, arr) - Math.min.apply(null, arr)).toFixed(4),
             temp: 22.5 + Math.sin(t / 20) * 3,
             humidity: 45 + Math.sin(t / 13) * 18,
             source: "데모 데이터 (백엔드 없음)" };
  }

  function poll() {
    if (demo) { apply(demoPayload()); return; }
    var t0 = performance.now();
    fetch("/data", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        setText("latency", Math.round(performance.now() - t0) + " ms");
        apply(d);
      })
      .catch(function () {
        demo = true;
        setText("latency", "— ms");
        apply(demoPayload());
      });
  }

  // ── 조작 ─────────────────────────────────────────────────────────
  document.addEventListener("click", function (e) {
    var pause = e.target.closest('[data-act="pause"]');
    if (pause) {
      paused = !paused;
      pause.textContent = paused ? "다시 재생" : "일시정지";
      pause.classList.toggle("bg-primary", paused);
      pause.classList.toggle("text-primary-foreground", paused);
      return;
    }
    if (e.target.closest("[data-graph-open]")) { modal(true); return; }
    if (e.target.closest("[data-graph-close]")) { modal(false); return; }
    var m = document.getElementById("graphModal");
    if (m && m.style.display === "flex" && e.target === m) modal(false);
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") modal(false); });

  function modal(open) {
    var m = document.getElementById("graphModal");
    if (!m) return;
    m.style.display = open ? "flex" : "none";
    document.body.style.overflow = open ? "hidden" : "";
    if (open) { drawWave(); }
  }

  // 지속 시간·연결 상태는 데이터가 안 와도 계속 갱신
  setInterval(function () {
    if (lastState) setText("duration", since(stateSince));
    if (!demo && lastSeen && Date.now() - lastSeen > 4000) {
      setText("source", "신호 끊김 — 재시도 중");
    }
    if (changes.length) renderLog();
  }, 1000);

  poll();
  setInterval(poll, POLL_MS);
})();
