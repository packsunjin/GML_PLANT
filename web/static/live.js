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
                           bg: "var(--warning-soft)", border: "var(--warning-soft-border)" } }
  };

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var live = function (n) { return $('[data-live="' + n + '"]'); };

  // 활성/비활성 버튼 색. bg-primary만 켜고 bg-muted/text-secondary-foreground를 그대로 두면
  // 초록 배경 위에 초록 글자가 남아 글씨가 안 보인다. 네 클래스를 항상 같이 뒤집는다.
  function setActive(btn, on) {
    if (!btn) return;
    btn.classList.toggle("bg-primary", on);
    btn.classList.toggle("text-primary-foreground", on);
    btn.classList.toggle("bg-muted", !on);
    btn.classList.toggle("text-secondary-foreground", !on);
  }
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

    // 실측 샘플레이트. 명목의 90% 미만이면 주파수축이 어긋나므로 눈에 띄게 표시한다.
    var rateEl = $('[data-live="rate"]');
    if (rateEl) {
      var nominal = d.sample_rate || 250;
      if (typeof d.actual_rate === "number" && isFinite(d.actual_rate)) {
        rateEl.textContent = d.actual_rate.toFixed(0) + " Hz";
        rateEl.style.color = d.actual_rate < nominal * 0.9 ? "var(--destructive)" : "";
      } else {
        rateEl.textContent = "— Hz";
        rateEl.style.color = "";
      }
    }

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
  var DEMO = ["정상", "수분부족", "자극"];

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
             sample_rate: 250, actual_rate: 250,
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

  // ── 모드 전환 (SSH 없이 브라우저에서) ────────────────────────────
  var modeState = null;

  function renderMode(m) {
    modeState = m;
    $$("[data-mode-set]").forEach(function (b) {
      var on = b.dataset.modeSet === m.sim_state;
      setActive(b, on && m.editable);
      b.disabled = !m.editable;
      b.style.opacity = m.editable ? "1" : ".45";
      b.style.cursor = m.editable ? "pointer" : "not-allowed";
    });
    var hint = $('[data-mode="hint"]');
    if (hint) {
      hint.textContent = m.editable
        ? "시뮬레이션 입력이라 여기서 상태를 바꿀 수 있어요"
        : (m.source_kind === "hardware"
            ? "실제 센서로 측정 중이라 모드를 바꿀 수 없어요"
            : "CSV 재생 중이라 모드를 바꿀 수 없어요");
    }
  }

  function loadMode() {
    if (demo) {
      renderMode({ source_kind: "sim", sim_state: "순환", editable: false });
      var hint = $('[data-mode="hint"]');
      if (hint) hint.textContent = "데모 화면이라 모드 전환은 백엔드에서만 됩니다";
      return;
    }
    fetch("/api/mode", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(renderMode)
      .catch(function () {});
  }

  function setMode(state) {
    if (!modeState || !modeState.editable) return;
    fetch("/api/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sim_state: state })
    })
      .then(function (r) { return r.json(); })
      .then(function (m) {
        if (m.ok) { renderMode(m); trend = []; waveBuf = []; }
      })
      .catch(function () {});
  }

  // ── 수집 · 학습 (SSH 없이 브라우저에서) ──────────────────────────
  var jobOpts = null, jobTimer = null, jobRunning = false, jobFinished = false;

  function jobEl(name) { return $('[data-job="' + name + '"]'); }

  function loadJobOptions() {
    if (demo) return;
    fetch("/api/train/options", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (o) {
        jobOpts = o;
        var box = jobEl("collect-buttons");
        if (box) {
          box.innerHTML = "";
          o.states.forEach(function (s) {
            var b = document.createElement("button");
            b.type = "button";
            b.dataset.jobCollect = s;
            b.className = "rounded-xl bg-muted px-3.5 py-1.5 text-xs font-bold text-secondary-foreground transition hover:bg-border";
            // 이미 수집된 상태는 표시해 준다(다시 누르면 덮어쓰기).
            b.textContent = o.collected.indexOf(s) >= 0 ? s + " ✓" : s;
            box.appendChild(b);
          });
        }
        fillSelect(jobEl("task"), o.tasks, "3종");
        fillSelect(jobEl("mode"), o.modes, "둘다");
        // 방금 끝난 작업의 결과 문구는 덮어쓰지 않는다(완료/실패 표시가 사라지지 않도록).
        var hint = jobEl("hint");
        if (hint && !jobRunning && !jobFinished) {
          hint.textContent = o.hardware
            ? "현재 모델: " + o.model
            : "센서가 없어 수집하면 시뮬레이션 신호가 저장됩니다";
        }
      })
      .catch(function () {});
  }

  function fillSelect(sel, values, pick) {
    if (!sel || sel.options.length) return;
    values.forEach(function (v) {
      var o = document.createElement("option");
      o.value = v; o.textContent = v;
      if (v === pick) o.selected = true;
      sel.appendChild(o);
    });
  }

  function renderJob(j) {
    var log = jobEl("log"), hint = jobEl("hint");
    jobRunning = !!j.running;
    jobFinished = !j.running && j.ok !== null && j.ok !== undefined;

    // 패널을 닫아 놔도 작업이 도는지 알 수 있게 헤더에 배지를 띄운다.
    var badge = jobEl("badge");
    if (badge) {
      badge.style.display = j.running ? "inline-block" : "none";
      // 단계 이름은 길어서 배지에서 잘리므로 짧은 라벨만 쓴다(자세한 단계는 패널에서).
      if (j.running) badge.textContent = "▶ " + (j.label || "작업") + " 중";
    }
    if (log && j.log && j.log.length) {
      log.style.display = "block";
      var atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
      log.textContent = j.log.join("\n");
      if (atBottom) log.scrollTop = log.scrollHeight;
    }
    if (hint) {
      if (j.running) {
        var sec = j.started ? Math.round(Date.now() / 1000 - j.started) : 0;
        hint.textContent = "▶ " + (j.label || "") + " · " + (j.step || "") + " (" + sec + "초)";
      } else if (j.ok === true) {
        hint.textContent = j.stopped ? "⏹ " + (j.label || "") + " 중지됨 (모은 데이터는 저장됨)"
                                     : "✅ " + (j.label || "") + " 완료";
      } else if (j.ok === false) {
        hint.textContent = "❌ " + (j.label || "") + " 실패 — 아래 로그 확인";
      }
    }
    // 실행 중에는 시작 버튼을 잠그고 중지 버튼을 띄운다.
    $$("[data-job-collect]").concat([jobEl("train"), jobEl("pipeline")]).forEach(function (b) {
      if (b) { b.disabled = j.running; b.style.opacity = j.running ? "0.5" : ""; }
    });
    $$('[data-job="stop"]').forEach(function (b) {
      b.style.display = j.running ? "inline-block" : "none";
      b.disabled = !!j.stopping;
      b.textContent = j.stopping ? "중지하는 중…" : "■ 중지";
      b.style.opacity = j.stopping ? "0.6" : "";
    });
    if (!j.running && jobTimer) {
      clearInterval(jobTimer); jobTimer = null;
      loadJobOptions();   // 수집 후 ✓ 표시, 학습 후 모델명 갱신
    }
  }

  // 센서 진단: 무엇이 잡혔고 왜 안 잡혔는지 사람이 읽을 수 있게 풀어준다.
  function loadSensors() {
    var box = jobEl("sensor-log"), hint = jobEl("sensor-hint");
    if (demo) { hint.textContent = "데모 화면이라 센서 진단은 백엔드에서만 됩니다"; return; }
    hint.textContent = "확인 중…";
    fetch("/api/sensors", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        var L = [];
        L.push("I2C 버스        : " + (s.i2c ? "OK" : "없음 — " + (s.i2c_error || "확인 불가")));
        L.push("ADC(ADS1115)    : " + (s.adc || "없음"));
        L.push("온·습도 센서    : " + (s.env_sensor || "없음 — " + (s.env_error || "확인 불가")));
        L.push("온도            : " + (s.temp == null ? "— (온·습도 센서 필요)" : s.temp + " °C"));
        L.push("습도            : " + (s.humidity == null ? "—" : s.humidity + " %") +
                                      (s.humidity_source ? "  (" + s.humidity_source + ")" : ""));
        L.push("토양수분 전압   : " + (s.moisture_volts == null ? "—" : s.moisture_volts + " V") +
                                      (s.moisture_error ? "  ← " + s.moisture_error : ""));
        L.push("보정값          : 젖음 " + s.calibration.wet_v + "V / 마름 " + s.calibration.dry_v + "V");
        if (!s.env_sensor) {
          L.push("");
          L.push("※ 온도를 재려면 AHT20(I2C 0x38) 또는 DHT22가 있어야 합니다.");
          L.push("   AHT20: pip install adafruit-circuitpython-ahtx0  → 3V3/GND/SDA/SCL 에 연결");
          L.push("   DHT22: pip install adafruit-circuitpython-dht    → GML_DHT_PIN 환경변수로 핀 지정 (지금 " + s.dht_pin + ")");
          L.push("   3핀(+/-/OUT) 아날로그 센서는 흙 수분만 재고 온도는 못 잽니다.");
        }
        box.style.display = "block";
        box.textContent = L.join("\n");
        hint.textContent = s.env_sensor ? "✅ " + s.env_sensor + " 인식됨"
                                        : (s.i2c ? "⚠️ 온·습도 센서가 안 잡혔습니다" : "⚠️ I2C가 꺼져 있습니다");
      })
      .catch(function () { hint.textContent = "❌ 진단 요청 실패"; });
  }

  function watchJob() {
    if (jobTimer) return;
    jobTimer = setInterval(function () {
      fetch("/api/job", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(renderJob)
        .catch(function () {});
    }, 1000);
  }

  function postJob(url, body, startedText) {
    var hint = jobEl("hint"), log = jobEl("log");
    // 요청이 왕복하는 동안 아무 반응이 없으면 "안 눌린다"고 느끼므로 즉시 표시한다.
    jobFinished = false;
    if (hint) hint.textContent = "▶ " + startedText + " 시작하는 중…";
    if (log) { log.style.display = "block"; log.textContent = "시작하는 중…"; }
    fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                 body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { return { s: r.status, j: j }; }); })
      .then(function (res) {
        if (res.j.ok) { renderJob(res.j.job); watchJob(); }
        else if (hint) hint.textContent = "❌ " + (res.j.error || "실패");
      })
      .catch(function () { if (hint) hint.textContent = "❌ 요청을 보내지 못했습니다"; });
  }

  // ── 학습 자료 (보기 / 내려받기 / 지우기) ─────────────────────────
  var filesData = null, filesTab = null;

  function fEl(n) { return $('[data-files="' + n + '"]'); }
  function human(b) {
    if (b < 1024) return b + " B";
    if (b < 1024 * 1024) return (b / 1024).toFixed(0) + " KB";
    return (b / 1024 / 1024).toFixed(1) + " MB";
  }
  function dlUrl(p, view) {
    return "/api/files/download?path=" + encodeURIComponent(p) + (view ? "&view=1" : "");
  }

  var TRASH_KEY = "__trash__";

  function loadFiles() {
    if (demo) { fEl("hint").textContent = "데모 화면이라 파일 목록은 백엔드에서만 보입니다"; return; }
    Promise.all([
      fetch("/api/files", { cache: "no-store" }).then(function (r) { return r.json(); }),
      fetch("/api/trash", { cache: "no-store" }).then(function (r) { return r.json(); })
    ]).then(function (res) {
      var d = res[0];
      // 휴지통을 마지막 탭으로 붙인다(파일 그룹과 같은 모양으로 다루기 위해).
      d.groups = d.groups.concat([{
        key: TRASH_KEY, title: "휴지통", kind: "trash",
        note: "지운 파일이 여기 있어요. 되돌리거나 완전히 지울 수 있습니다.",
        files: (res[1].items || []).map(function (it) {
          return { path: it.id, name: it.name, size: it.size,
                   mtime: it.deleted_at, from: it.path };
        }),
        total: (res[1].items || []).length
      }]);
      filesData = d;
      if (!filesTab || !d.groups.some(function (g) { return g.key === filesTab; })) {
        filesTab = d.groups.length ? d.groups[0].key : null;
      }
      renderFiles();
    }).catch(function () { fEl("hint").textContent = "목록을 불러오지 못했습니다"; });
  }

  // 체크 상태에 따라 "모두 선택"과 선택 개수를 갱신한다.
  function syncPicks() {
    var boxes = $$("[data-files-pick]").filter(function (c) { return !c.disabled; });
    var on = boxes.filter(function (c) { return c.checked; });
    var all = fEl("all"), cnt = fEl("count");
    if (all) {
      all.checked = boxes.length > 0 && on.length === boxes.length;
      all.indeterminate = on.length > 0 && on.length < boxes.length;
      all.disabled = boxes.length === 0;
    }
    if (cnt) cnt.textContent = on.length + "개 선택";
  }

  function renderFiles() {
    if (!filesData) return;
    var tabs = fEl("tabs"), list = fEl("list"), note = fEl("note");
    tabs.innerHTML = "";
    filesData.groups.forEach(function (g) {
      var b = document.createElement("button");
      b.type = "button";
      b.dataset.filesTab = g.key;
      var on = g.key === filesTab;
      b.className = "rounded-xl px-3 py-1.5 text-[11px] font-bold transition " +
        (on ? "bg-primary text-primary-foreground" : "bg-muted text-secondary-foreground hover:bg-border");
      b.textContent = g.title + " " + g.total;
      tabs.appendChild(b);
    });

    var grp = filesData.groups.filter(function (g) { return g.key === filesTab; })[0];
    if (!grp) { list.innerHTML = ""; return; }
    note.textContent = grp.note +
      (grp.total > grp.files.length ? "  (" + grp.total + "개 중 " + grp.files.length + "개만 표시)" : "");

    // 휴지통 탭에서만 '되돌리기'가 나오고, 삭제 버튼은 '완전 삭제'로 바뀐다.
    var isTrash = grp.kind === "trash";
    var restoreBtn = fEl("restore"), delBtn = fEl("delete");
    if (restoreBtn) restoreBtn.style.display = isTrash ? "inline-block" : "none";
    if (delBtn) delBtn.textContent = isTrash ? "완전 삭제" : "휴지통으로";

    if (!grp.files.length) {
      list.style.display = "block";
      list.innerHTML = '<p class="py-6 text-center text-xs text-subtle">' +
        (isTrash ? "휴지통이 비어 있습니다." : "아직 파일이 없습니다.") + "</p>";
      syncPicks();
      return;
    }
    // 이미지는 뭔지 보고 지울 수 있어야 하므로 목록이 아니라 사진 그리드로 보여준다.
    if (grp.kind === "image") { renderImageGrid(grp, list); syncPicks(); return; }
    renderFileList(grp, list);
    if (grp.kind === "csv") grp.files.forEach(drawCsvPreview);
    syncPicks();
  }

  function renderImageGrid(grp, list) {
    list.style.display = "grid";
    list.style.gridTemplateColumns = "repeat(auto-fill,minmax(132px,1fr))";
    list.style.gap = "10px";
    list.innerHTML = grp.files.map(function (f) {
      return '<figure class="rounded-2xl bg-card p-2" style="margin:0">' +
        '<div style="position:relative">' +
          '<img src="' + dlUrl(f.path, 1) + '" alt="' + f.name + '" data-files-open="' + f.path + '"' +
            ' style="width:100%;aspect-ratio:1;object-fit:contain;background:#0d140f;' +
                   'border-radius:10px;cursor:zoom-in;image-rendering:pixelated">' +
          '<input type="checkbox" data-files-pick="' + f.path + '"' +
            ' style="position:absolute;top:6px;left:6px;width:18px;height:18px;cursor:pointer">' +
        '</div>' +
        '<figcaption class="mt-1.5">' +
          '<span class="block text-[10px] font-bold text-foreground" style="word-break:break-all">' +
            f.name + '</span>' +
          '<span class="mt-0.5 flex items-center gap-1.5">' +
            '<span class="text-[10px] text-subtle">' + human(f.size) + '</span>' +
            '<a href="' + dlUrl(f.path) + '" download ' +
              'class="rounded-md bg-muted px-1.5 py-0.5 text-[10px] font-bold text-secondary-foreground">받기</a>' +
          '</span>' +
        '</figcaption>' +
      '</figure>';
    }).join("");
  }

  function renderFileList(grp, list) {
    list.style.display = "block";
    list.style.gridTemplateColumns = "";
    var isTrash = grp.kind === "trash";
    list.innerHTML = grp.files.map(function (f) {
      var inUse = !isTrash && f.path === filesData.current_model;
      var isCsv = grp.kind === "csv";
      return '<div class="flex items-center gap-2 border-b border-border py-2 last:border-b-0">' +
        '<input type="checkbox" data-files-pick="' + f.path + '"' + (inUse ? " disabled" : "") + '>' +
        (isCsv ? '<canvas data-files-wave="' + f.path + '" width="150" height="40" ' +
                 'style="width:75px;height:40px;background:#0d140f;border-radius:6px;flex:none"></canvas>' : '') +
        '<div class="min-w-0 flex-1">' +
          // 모델/혼동행렬 파일명은 접미사(_특징_정상-자극 등)로만 구분되므로 자르지 않는다.
          '<span class="block text-xs font-bold text-foreground" style="word-break:break-all">' + f.name +
            (inUse ? ' <span class="text-[10px] font-medium text-subtle">· 사용 중</span>' : '') + '</span>' +
          '<span class="block text-[10px] text-subtle" data-files-meta="' + f.path + '">' +
            human(f.size) + ' · ' +
            (isTrash ? hhmm(f.mtime * 1000) + " 삭제 · 원래 위치 " + f.from
                     : hhmm(f.mtime * 1000)) + '</span>' +
        '</div>' +
        // 휴지통 파일은 내려받기 경로가 없다(복원한 뒤에 받으면 된다).
        (isTrash ? '' :
          '<a href="' + dlUrl(f.path) + '" download ' +
          'class="shrink-0 rounded-lg bg-card px-2.5 py-1 text-[10px] font-bold text-secondary-foreground">내려받기</a>') +
      '</div>';
    }).join("");
  }

  // CSV는 그림이 없으니 실제 파형을 작게 그려 어떤 파일인지 보이게 한다.
  function drawCsvPreview(f) {
    fetch("/api/files/preview?path=" + encodeURIComponent(f.path), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var meta = $('[data-files-meta="' + f.path + '"]');
        var cv = $('[data-files-wave="' + f.path + '"]');
        if (!d.ok) return;
        if (d.kind === "table") {
          if (cv) cv.style.display = "none";
          if (meta) {
            var lab = Object.keys(d.labels || {}).map(function (k) { return k + " " + d.labels[k]; });
            meta.textContent = human(f.size) + " · " + d.rows + "행" +
              (lab.length ? " · " + lab.join(" / ") : "");
          }
          return;
        }
        if (meta) {
          meta.textContent = human(f.size) + " · " + d.rows + "샘플" +
            (d.duration ? " · " + d.duration + "초" : "") +
            " · " + d.min + "~" + d.max + " V";
        }
        if (!cv || !d.signal || d.signal.length < 2) return;
        var ctx = cv.getContext("2d"), w = cv.width, h = cv.height;
        var lo = Math.min.apply(null, d.signal), hi = Math.max.apply(null, d.signal);
        var span = (hi - lo) || 1e-6, step = w / (d.signal.length - 1);
        ctx.clearRect(0, 0, w, h);
        ctx.beginPath();
        d.signal.forEach(function (v, i) {
          var y = h - 3 - ((v - lo) / span) * (h - 6);
          if (i === 0) ctx.moveTo(0, y); else ctx.lineTo(i * step, y);
        });
        ctx.strokeStyle = cssVal("--signal") || "#5fce7a";
        ctx.lineWidth = 1.2;
        ctx.stroke();
      })
      .catch(function () {});
  }

  function previewFile(path) {
    var box = fEl("preview");
    if (!box) return;
    if (!path) { box.style.display = "none"; return; }
    fEl("preview-img").src = dlUrl(path, 1);
    fEl("preview-name").textContent = path;
    fEl("preview-dl").href = dlUrl(path);
    box.style.display = "flex";
  }

  function picked() {
    return $$("[data-files-pick]").filter(function (c) { return c.checked && !c.disabled; })
                                  .map(function (c) { return c.dataset.filesPick; });
  }

  function filesPost(url, body, done) {
    var hint = fEl("hint");
    fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                 body: JSON.stringify(body) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { hint.textContent = "❌ " + d.error; return; }
        hint.textContent = done(d) +
          ((d.failed || []).length ? " · 실패 " + d.failed.length + "개: " + d.failed[0].error : "");
        loadFiles();
      })
      .catch(function () { hint.textContent = "❌ 요청을 보내지 못했습니다"; });
  }

  function deleteChecked() {
    var picks = picked(), hint = fEl("hint");
    if (!picks.length) { hint.textContent = "지울 파일을 먼저 선택하세요"; return; }

    if (filesTab === TRASH_KEY) {
      if (!window.confirm(picks.length + "개를 완전히 지웁니다. 되돌릴 수 없어요. 계속할까요?")) return;
      filesPost("/api/trash/empty", { ids: picks }, function (d) {
        return "🔥 " + (d.removed || []).length + "개 완전 삭제";
      });
      return;
    }
    // 일반 탭에서는 바로 지우지 않고 휴지통으로 옮긴다(되돌릴 수 있음).
    filesPost("/api/files/delete", { paths: picks }, function (d) {
      return "🗑 " + (d.deleted || []).length + "개를 휴지통으로 옮겼어요 (되돌리기 가능)";
    });
  }

  function restoreChecked() {
    var picks = picked(), hint = fEl("hint");
    if (!picks.length) { hint.textContent = "되돌릴 항목을 먼저 선택하세요"; return; }
    filesPost("/api/trash/restore", { ids: picks }, function (d) {
      return "↩ " + (d.restored || []).length + "개 되돌렸어요";
    });
  }

  // ── 조작 ─────────────────────────────────────────────────────────
  document.addEventListener("click", function (e) {
    var mode = e.target.closest("[data-mode-set]");
    if (mode) { setMode(mode.dataset.modeSet); return; }

    var toggle = e.target.closest('[data-job="toggle"]');
    if (toggle) {
      var panel = jobEl("panel");
      var open = panel.style.display === "none";
      panel.style.display = open ? "flex" : "none";
      setActive(toggle, open);
      if (open && !jobOpts) { loadJobOptions(); watchJob(); }
      return;
    }
    var stopBtn = e.target.closest('[data-job="stop"]');
    if (stopBtn && !stopBtn.disabled) {
      stopBtn.disabled = true;
      stopBtn.textContent = "중지하는 중…";
      fetch("/api/job/stop", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function () { watchJob(); })
        .catch(function () {});
      return;
    }
    var col = e.target.closest("[data-job-collect]");
    if (col && !col.disabled) {
      // '중지할 때까지'를 켜면 duration=0 -> 서버가 무제한 수집으로 받는다.
      var unlimited = (jobEl("unlimited") || {}).checked;
      var dur = unlimited ? 0 : (parseFloat((jobEl("duration") || {}).value) || 30);
      postJob("/api/collect", { state: col.dataset.jobCollect, duration: dur },
              col.dataset.jobCollect + (unlimited ? " 수집 (중지할 때까지)" : " " + dur + "초 수집"));
      return;
    }
    if (e.target.closest('[data-job="train"]') && !jobEl("train").disabled) {
      postJob("/api/train", { task: jobEl("task").value, mode: jobEl("mode").value },
              jobEl("task").value + " 재학습");
      return;
    }
    if (e.target.closest('[data-job="pipeline"]') && !jobEl("pipeline").disabled) {
      var pd = parseFloat((jobEl("duration") || {}).value) || 30;
      if (!window.confirm("정상·수분부족·자극을 각각 " + pd + "초씩 새로 모으고 학습합니다.\n" +
                          "기존 원시 데이터는 덮어써집니다. 계속할까요?")) return;
      postJob("/api/pipeline", { duration: pd, task: jobEl("task").value, mode: jobEl("mode").value },
              "전체 자동 (3종 × " + pd + "초 → 학습)");
      return;
    }
    if (e.target.closest('[data-job="sensors"]')) { loadSensors(); return; }

    // ── 학습 자료 패널 ──
    var ftog = e.target.closest('[data-files="toggle"]');
    if (ftog) {
      var fp = $('[data-files="panel"]');
      var fopen = fp.style.display === "none";
      fp.style.display = fopen ? "flex" : "none";
      setActive(ftog, fopen);
      if (fopen) loadFiles();
      return;
    }
    if (e.target.closest('[data-files="refresh"]')) { loadFiles(); return; }
    var tab = e.target.closest("[data-files-tab]");
    if (tab) { filesTab = tab.dataset.filesTab; renderFiles(); return; }
    if (e.target.closest('[data-files="delete"]')) { deleteChecked(); return; }
    if (e.target.closest('[data-files="restore"]')) { restoreChecked(); return; }
    var openImg = e.target.closest("[data-files-open]");
    if (openImg) { previewFile(openImg.dataset.filesOpen); return; }
    if (e.target.closest('[data-files="preview-close"]')) { previewFile(null); return; }
    var pv = $('[data-files="preview"]');
    if (pv && pv.style.display === "flex" && e.target === pv) { previewFile(null); return; }

    var pause = e.target.closest('[data-act="pause"]');
    if (pause) {
      paused = !paused;
      pause.textContent = paused ? "다시 재생" : "일시정지";
      setActive(pause, paused);
      return;
    }
    if (e.target.closest("[data-graph-open]")) { modal(true); return; }
    if (e.target.closest("[data-graph-close]")) { modal(false); return; }
    var m = document.getElementById("graphModal");
    if (m && m.style.display === "flex" && e.target === m) modal(false);
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") modal(false); });

  // 체크박스는 click이 아니라 change로 잡아야 키보드 조작도 반영된다.
  document.addEventListener("change", function (e) {
    if (e.target.matches('[data-files="all"]')) {
      $$("[data-files-pick]").forEach(function (c) {
        if (!c.disabled) c.checked = e.target.checked;
      });
      syncPicks();
      return;
    }
    if (e.target.matches("[data-files-pick]")) syncPicks();
    // '중지할 때까지'를 켜면 길이 입력은 의미가 없으므로 잠근다.
    if (e.target.matches('[data-job="unlimited"]')) {
      var dur = jobEl("duration");
      if (dur) { dur.disabled = e.target.checked; dur.style.opacity = e.target.checked ? "0.4" : ""; }
    }
  });

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
  loadMode();
  setInterval(loadMode, 5000);

  // 다른 기기(폰/노트북)에서 시작한 작업도 헤더 배지에 보이도록, 패널을 열지 않아도
  // 처음 한 번은 상태를 확인하고 도는 중이면 폴링을 이어간다.
  if (!demo) {
    fetch("/api/job", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (j) { renderJob(j); if (j.running) watchJob(); })
      .catch(function () {});
  }
})();
