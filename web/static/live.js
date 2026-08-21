/* 초록말 - 실시간 식물 상태 화면
 *
 * 백엔드(src/web_dashboard.py)의 /data 를 주기적으로 읽어 화면을 갱신합니다.
 * 백엔드가 없으면(파일만 열었을 때) 데모 값으로 대체해 화면을 확인할 수 있습니다.
 */
(function () {
  "use strict";

  var POLL_MS = 500;
  var WAVE_MAX = 1500;           // 파형 롤링 버퍼 길이

  var STATE = {
    "정상":     { alert: null },
    "수분부족": { alert: { icon: "💧", text: "수분부족 신호가 이어지고 있어요. 겉흙이 말랐다면 물을 주세요." } },
    "자극":     { alert: { icon: "⚡", text: "잎에 닿는 자극이 감지됐어요. 순간적인 반응이라 곧 회복됩니다." } }
  };

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var live = function (n) { return $('[data-live="' + n + '"]'); };

  // 지금 선택된 버튼 표시: 색 대신 aria-pressed(굵게+밑줄, ui.css에서 정의)만 사용
  function setActive(btn, on) {
    if (!btn) return;
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }
  var setText = function (n, v) { var el = live(n); if (el) el.textContent = v; };

  // 화면 상태
  var paused = false;
  var waveBuf = [];              // 파형 롤링 버퍼
  // 파형 세로축의 최소 범위(V). 이보다 조용하면 축을 더 좁히지 않는다.
  // 바탕잡음 표준편차가 약 0.006V이므로 0.2V면 조용할 때 화면의 10% 안쪽에 머문다.
  var WAVE_MIN_SPAN = 0.2;
  // 스펙트로그램 밝기의 최소 dB 범위. 같은 이유로 조용한 창이 과장되지 않게 한다.
  var SPEC_MIN_SPAN = 20;
  var lastState = null;
  var stateSince = Date.now();
  var lastSeen = 0;
  var pageLoad = Date.now();     // lastSeen이 한 번도 안 찍혔을 때(첫 판정 전 실패)의 기준 시각
  var lastWorkerError = null;    // 실시간 루프가 보낸 마지막 에러 메시지(있으면 일반 문구보다 우선)

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
    $$('[data-live="wave"]').forEach(function (cv) {
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
      // 세로축에 최소 범위를 둔다. 매 프레임 min/max로만 맞추면 바탕잡음
      // (실측 표준편차 약 0.006V)까지 화면을 꽉 채워, 아무 일도 없을 때조차
      // 큰 신호가 잡히는 것처럼 보인다. 실제로 이보다 크게 흔들릴 때만 축이 넓어진다.
      var mid = (hi + lo) / 2;
      if (hi - lo < WAVE_MIN_SPAN) {
        lo = mid - WAVE_MIN_SPAN / 2;
        hi = mid + WAVE_MIN_SPAN / 2;
      }
      var span = (hi - lo) || 1e-6;
      setText("wave-scale", "세로축 ±" + (span / 2).toFixed(3) + " V");
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
  // 픽셀 방식 모델은 이 이미지로 직접 분류하고, 특징 방식도 여기서 특징을 뽑는다 —
  // 장식이 아니라 실제 분류 입력이라 화면에 그대로 보여준다.
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
    // 파형과 같은 이유로 밝기에도 최소 범위를 둔다. 창마다 min–max로 정규화하면
    // 조용한 창도 밝은 무늬가 가득 차 실제로 뭔가 잡힌 것처럼 보인다.
    if (hi - lo < SPEC_MIN_SPAN) lo = hi - SPEC_MIN_SPAN;
    var span = (hi - lo) || 1e-6;
    $$('[data-live="spec"]').forEach(function (cv) {
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

  // ── 적용 ─────────────────────────────────────────────────────────
  function apply(d) {
    if (!d || !d.ready) {
      // 실시간 루프가 죽지는 않았지만 매 반복에서 계속 실패하는 중이면(예: 방금
      // 갈아끼운 모델이 안 맞음) 서버가 이유를 같이 보낸다. 그냥 '대기 중'만 뜨는 것보다
      // 훨씬 도움이 되므로 바로 보여준다.
      if (!demo && d && d.worker_error) {
        lastWorkerError = d.worker_error;
        setText("source", "⚠️ " + d.worker_error);
      }
      return;
    }
    lastWorkerError = null;
    lastSeen = Date.now();

    // 실측 샘플레이트. 명목의 90% 미만이면 주파수축이 어긋나므로 눈에 띄게 표시한다.
    var rateEl = $('[data-live="rate"]');
    if (rateEl) {
      var nominal = d.sample_rate || 250;
      if (typeof d.actual_rate === "number" && isFinite(d.actual_rate)) {
        var low = d.actual_rate < nominal * 0.9;
        rateEl.textContent = (low ? "⚠ " : "") + d.actual_rate.toFixed(0) + " Hz";
      } else {
        rateEl.textContent = "— Hz";
      }
    }

    var st = STATE[d.state] || STATE["정상"];

    if (d.state !== lastState) {
      lastState = d.state;
      stateSince = Date.now();
    }

    setText("state", d.state);
    var conf = d.proba == null ? null : d.proba * 100;
    setText("conf", conf == null ? "확신도 —" : "확신도 " + conf.toFixed(1) + "%");
    setText("duration", since(stateSince));

    // 확률
    if (d.probs) {
      $$("[data-cls]").forEach(function (row) {
        var p = d.probs[row.dataset.cls];
        var pct = p == null ? 0 : p * 100;
        var bar = $("[data-bar]", row), val = $("[data-val]", row);
        if (bar) bar.value = pct;
        if (val) val.textContent = pct.toFixed(1) + "%";
      });
    }

    // 경고 배너
    var banner = live("alert-banner");
    if (banner) {
      if (st.alert) {
        banner.hidden = false;
        setText("alert-icon", st.alert.icon);
        setText("alert-text", st.alert.text);
      } else {
        banner.hidden = true;
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

    // 신호 통계
    var f = function (v, unit) { return v == null ? "—" : v + (unit || ""); };
    setText("std", f(d.std, " V"));
    setText("p2p", f(d.p2p, " V"));
    setText("wave-peak", d.p2p == null ? "PEAK —" : "PEAK " + d.p2p + " V");

    setText("source", d.source || "입력 소스 확인 중");
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
             std: +std.toFixed(4),
             p2p: +(Math.max.apply(null, arr) - Math.min.apply(null, arr)).toFixed(4),
             sample_rate: 250, actual_rate: 250,
             source: "데모 데이터 (백엔드 없음)" };
  }

  function poll() {
    if (demo) { apply(demoPayload()); return; }
    fetch("/data", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(apply)
      .catch(function () {
        if (!demo) enterDemo();
        apply(demoPayload());
      });
  }

  // 백엔드가 없으면(파일만 열었거나 배포된 미리보기) 수집·학습·파일 기능은 동작할 수
  // 없으므로 버튼 자체를 감춘다. 눌러도 안 되는 버튼을 남겨 두면 고장난 것처럼 보인다.
  function enterDemo() {
    demo = true;
    ['[data-auth="button"]', '[data-admin="panel"]'].forEach(function (sel) {
      var el = $(sel);
      if (el) el.style.display = "none";
    });
    var hint = $('[data-mode="hint"]');
    if (hint) hint.textContent = "미리보기 — 실제 측정이 아니라 예시 데이터로 도는 화면입니다";
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
      if (hint) hint.textContent = "미리보기 — 예시 데이터로 도는 화면입니다 (실제 측정 아님)";
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
        if (m.ok) { renderMode(m); waveBuf = []; }
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
          var last = remembered("collect", null);
          selectCollect(o.states.indexOf(last) >= 0 ? last : o.states[0]);
        }
        // 전처리 버튼: 상태별 + 전체. 변환이 끝난 상태에는 이미지 장수를 붙여준다.
        var pbox = jobEl("prep-buttons");
        if (pbox) {
          pbox.innerHTML = "";
          o.states.forEach(function (s) {
            var b = document.createElement("button");
            b.type = "button";
            b.dataset.jobPrep = s;
            b.className = "rounded-xl bg-muted px-3.5 py-1.5 text-xs font-bold text-secondary-foreground transition hover:bg-border";
            var n = (o.converted || {})[s] || 0;
            b.textContent = n ? s + " (" + n + "장)" : s;
            b.disabled = o.collected.indexOf(s) < 0;
            b.style.opacity = b.disabled ? "0.4" : "";
            b.title = b.disabled ? "먼저 수집하세요" : "";
            pbox.appendChild(b);
          });
          var all = document.createElement("button");
          all.type = "button";
          all.dataset.jobPrep = "";
          all.className = "rounded-xl bg-muted px-3.5 py-1.5 text-xs font-bold text-secondary-foreground transition hover:bg-border";
          all.textContent = "전체";
          pbox.appendChild(all);
        }
        // 상태별 변환 현황: 학습 탭에서 어떤 과제를 고를 수 있는지 한눈에 보여준다.
        var statusBox = jobEl("train-status");
        if (statusBox) {
          statusBox.innerHTML = "";
          o.states.forEach(function (s) {
            var collected = o.collected.indexOf(s) >= 0;
            var n = (o.converted || {})[s] || 0;
            var span = document.createElement("span");
            span.textContent = s + ": " + (n > 0 ? "✓ 변환됨 " + n + "장"
                                           : collected ? "수집됨 · 변환 필요" : "미수집") + "  ·  ";
            statusBox.appendChild(span);
          });
        }

        // 과제 버튼: 필요한 클래스가 다 변환돼 있지 않으면 고를 수 없게 잠근다.
        var taskBox = jobEl("task-buttons");
        if (taskBox) {
          taskBox.innerHTML = "";
          o.tasks.forEach(function (t) {
            var ready = (o.task_ready || {})[t];
            var b = document.createElement("button");
            b.type = "button";
            b.dataset.jobTask = t;
            b.className = "rounded-xl bg-muted px-3.5 py-1.5 text-xs font-bold text-secondary-foreground transition hover:bg-border";
            b.textContent = t;
            b.disabled = !ready;
            b.style.opacity = ready ? "" : "0.4";
            b.title = ready ? "" : ((o.task_reason || {})[t] || "데이터가 부족합니다");
            taskBox.appendChild(b);
          });
          var readyTasks = o.tasks.filter(function (t) { return (o.task_ready || {})[t]; });
          var lastTask = remembered("task", null);
          selectTrainTask(readyTasks.indexOf(lastTask) >= 0 ? lastTask : (readyTasks[0] || null));
        }

        // 방식 버튼 (픽셀 / 특징 / 둘다)
        var modeBox = jobEl("mode-buttons");
        if (modeBox) {
          modeBox.innerHTML = "";
          o.modes.forEach(function (m) {
            var b = document.createElement("button");
            b.type = "button";
            b.dataset.jobModeBtn = m;
            b.className = "rounded-xl bg-muted px-3.5 py-1.5 text-xs font-bold text-secondary-foreground transition hover:bg-border";
            b.textContent = m;
            modeBox.appendChild(b);
          });
          selectTrainMode(remembered("mode", "둘다"));
        }

        loadTrainHistory();
        restoreSettings();
        // 어느 탭에 있든 전체 진행 상황이 보이게 요약을 띄운다.
        var sum = $('[data-admin="summary"]');
        if (sum) {
          var done = o.states.filter(function (s2) { return o.collected.indexOf(s2) >= 0; }).length;
          var conv = o.states.filter(function (s2) { return (o.converted || {})[s2] > 0; }).length;
          sum.textContent = "수집 " + done + "/" + o.states.length +
                            " · 변환 " + conv + "/" + o.states.length +
                            " · 모델 " + (o.model || "—");
        }
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

  // ── 설정 기억 (수집 길이, 무제한 여부, 학습 옵션) ────────────────
  function remembered(key, fallback) {
    try {
      var v = localStorage.getItem("gml." + key);
      return v === null ? fallback : v;
    } catch (e) { return fallback; }
  }

  function remember(key, value) {
    try { localStorage.setItem("gml." + key, value); } catch (e) {}
  }

  function restoreSettings() {
    var dur = jobEl("duration"), un = jobEl("unlimited");
    if (dur) dur.value = remembered("duration", dur.value);
    if (un) {
      un.checked = remembered("unlimited", "") === "1";
      if (dur) { dur.disabled = un.checked; dur.style.opacity = un.checked ? "0.4" : ""; }
    }
  }

  // 수집: 상태를 먼저 고르고 '측정 시작'을 눌러야 실제로 시작한다.
  // (버튼 한 번에 바로 측정되면 실수로 기존 데이터를 덮어쓰기 쉽다)
  var collectPick = null;

  function selectCollect(state) {
    collectPick = state;
    remember("collect", state);
    $$("[data-job-collect]").forEach(function (b) {
      setActive(b, b.dataset.jobCollect === state);
    });
    renderCollectHint();
  }

  function renderCollectHint() {
    var hint = jobEl("collect-hint"), btn = jobEl("collect-start");
    if (!hint || !btn) return;
    if (!collectPick) {
      hint.textContent = "상태를 먼저 골라주세요";
      btn.disabled = true;
      btn.style.opacity = "0.45";
      return;
    }
    var unlimited = (jobEl("unlimited") || {}).checked;
    var dur = parseFloat((jobEl("duration") || {}).value) || 30;
    hint.textContent = unlimited
      ? "'" + collectPick + "' 을(를) 중지할 때까지 측정합니다"
      : "'" + collectPick + "' 을(를) " + dur + "초 동안 측정합니다";
    if (!jobRunning) { btn.disabled = false; btn.style.opacity = ""; }
  }

  function startCollect() {
    if (!collectPick) return;
    var unlimited = (jobEl("unlimited") || {}).checked;
    var dur = unlimited ? 0 : (parseFloat((jobEl("duration") || {}).value) || 30);
    postJob("/api/collect", { state: collectPick, duration: dur },
            collectPick + (unlimited ? " 수집 (중지할 때까지)" : " " + dur + "초 수집"));
  }

  // 학습: 과제(2종/3종)와 방식(픽셀/특징/둘다)을 고른다. 데이터가 없는 과제는
  // loadJobOptions에서 버튼 자체를 잠가 놓으므로 여기서는 고른 값만 기억하면 된다.
  var trainTaskPick = null, trainModePick = null;

  function selectTrainTask(t) {
    trainTaskPick = t;
    if (t) remember("task", t);
    $$("[data-job-task]").forEach(function (b) { setActive(b, b.dataset.jobTask === t); });
    renderTrainHint();
  }

  function selectTrainMode(m) {
    trainModePick = m;
    remember("mode", m);
    $$("[data-job-mode-btn]").forEach(function (b) { setActive(b, b.dataset.jobModeBtn === m); });
  }

  function renderTrainHint() {
    var hint = jobEl("train-hint"), btn = jobEl("train");
    if (!hint || !btn) return;
    if (!trainTaskPick) {
      hint.textContent = "변환된 데이터가 없어요. 2 · 변환을 먼저 하세요";
      btn.disabled = true;
      btn.style.opacity = "0.45";
      return;
    }
    hint.textContent = "'" + trainTaskPick + "' 과제로 학습합니다";
    if (!jobRunning) { btn.disabled = false; btn.style.opacity = ""; }
  }

  function startTrain() {
    if (!trainTaskPick) return;
    postJob("/api/train", { task: trainTaskPick, mode: trainModePick || "둘다" },
            trainTaskPick + " 학습");
  }

  // 최근 학습 결과: 과제/방식/정확도를 최신순으로 보여준다.
  function loadTrainHistory() {
    if (demo) return;
    var box = jobEl("train-history");
    if (!box) return;
    fetch("/api/train/history", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var hist = d.history || [];
        if (!hist.length) {
          box.innerHTML = "<p>아직 학습 기록이 없어요</p>";
          return;
        }
        box.innerHTML = "";
        var ul = document.createElement("ul");
        hist.forEach(function (h) {
          var acc = h.accuracy != null ? Math.round(h.accuracy * 1000) / 10 + "%" : "—";
          var ok = h.accuracy != null && h.accuracy >= 0.7;
          var when = h.trained_at ? new Date(h.trained_at * 1000).toLocaleString("ko-KR",
                       { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
          var li = document.createElement("li");
          li.textContent = (h.task || "") + " — " + (h.mode || "") + " · " + (h.best_name || "") +
                            " · train " + (h.train_n != null ? h.train_n : "?") +
                            " test " + (h.test_n != null ? h.test_n : "?") +
                            (h.duration_sec != null ? " · " + Math.round(h.duration_sec) + "초 걸림" : "") +
                            " · 정확도 " + (ok ? "" : "⚠ ") + acc +
                            (when ? " · " + when : "");
          ul.appendChild(li);
        });
        box.appendChild(ul);
      })
      .catch(function () {});
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
    $$("[data-job-prep]").concat([jobEl("train"), jobEl("collect-start")]).forEach(function (b) {
      if (b) { b.disabled = j.running; b.style.opacity = j.running ? "0.5" : ""; }
    });
    if (!j.running) renderCollectHint();
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
        L.push("온·습도 센서    : " + (s.env_sensor || "없음 — " + (s.env_error || "확인 불가")) +
                                      (s.env_sensor === "DHT22" ? "  (GPIO " + s.dht_pin + ")" : ""));
        if (s.env_read_error) L.push("  └ 읽기 실패    : " + s.env_read_error);
        L.push("온도            : " + (s.temp == null ? "— (온·습도 센서 필요)" : s.temp + " °C"));
        L.push("습도            : " + (s.humidity == null ? "—" : s.humidity + " %") +
                                      (s.humidity_source ? "  (" + s.humidity_source + ")" : ""));
        if (s.env_method) L.push("읽는 방식        : " +
          (s.env_method === "_IIODht" ? "커널 드라이버 (" + s.iio_device + ")" : "파이썬 (" + s.env_method + ")"));
        if (!s.env_sensor) {
          L.push("");
          L.push("※ DHT22 는 라즈베리파이 5에서 커널 드라이버로 읽는 게 확실합니다.");
          L.push("   (파이 5는 GPIO가 RP1 칩 뒤에 있어 파이썬 비트뱅잉 타이밍이 잘 안 맞습니다)");
          L.push("");
          L.push("   1) sudo nano /boot/firmware/config.txt   ← 맨 아래에 한 줄 추가");
          L.push("        dtoverlay=dht11,gpiopin=4");
          L.push("   2) sudo reboot");
          L.push("   3) 다시 '진단하기' → '읽는 방식: 커널 드라이버' 로 뜨면 성공");
          L.push("");
          L.push("   ※ 오버레이 이름은 dht11 이지만 DHT22 도 이걸로 동작합니다.");
          L.push("   ※ DATA 는 파이 GPIO4(물리 7번). ADS1115 에 꽂으면 안 됩니다.");
          L.push("   ※ 자세한 진단:  cd ~/project/src && python3 check_sensors.py");
        } else if (s.env_sensor === "DHT22" && s.temp == null) {
          L.push("");
          L.push("※ 커널 드라이버는 올라왔는데 센서가 응답하지 않습니다(timed out).");
          L.push("   = 오버레이/장치는 정상, 배선이나 센서 쪽 문제입니다.");
          L.push("");
          L.push("   1) config.txt 의 gpiopin= 번호와 실제로 꽂은 핀이 같나요?");
          L.push("      dtoverlay=dht11,gpiopin=4  →  DATA 는 GPIO4 = 물리 7번");
          L.push("   2) VCC 는 3V3(물리 1번), GND 는 공통(물리 6번) 인가요?");
          L.push("   3) 맨 센서(4핀)면 DATA–VCC 사이에 10kΩ 풀업 저항이 필요합니다.");
          L.push("   4) 첫 몇 번은 원래 실패합니다. 계속 재시도 중이니 10초쯤 두고 보세요.");
          L.push("   5) 그래도 안 되면:  cd ~/project/src && python3 check_sensors.py");
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
        else if (handleAuthError(res.j)) { if (hint) hint.textContent = "🔒 관리자 로그인이 필요합니다"; }
        else if (hint) hint.textContent = "❌ " + (res.j.error || "실패");
      })
      .catch(function () { if (hint) hint.textContent = "❌ 요청을 보내지 못했습니다"; });
  }

  // ── 관리자 로그인 ────────────────────────────────────────────────
  // 데이터를 바꾸는 동작만 잠근다. 화면 보기와 모드 전환은 열려 있다.
  var auth = { configured: false, authed: false };

  function aEl(n) { return $('[data-auth="' + n + '"]'); }

  var adminTab = null;

  function renderAuth() {
    var btn = aEl("button");
    if (btn) {
      btn.textContent = auth.authed ? "🔓 관리자" : "🔒 관리자";
      btn.title = auth.authed ? "누르면 로그아웃합니다"
                              : (auth.configured ? "로그인" : "비밀번호를 처음 설정합니다");
      setActive(btn, auth.authed);
    }
    // 관리자 기능은 로그인해야 화면에 나타난다(잠긴 버튼을 보여주지 않는다).
    var panel = $('[data-admin="panel"]');
    if (panel) panel.style.display = auth.authed ? "block" : "none";
    if (auth.authed) {
      if (!adminTab) showAdminTab(localStorage.getItem("gml.tab") || "collect");
      loadJobOptions();
      loadFiles();
    }
  }

  function showAdminTab(name) {
    adminTab = name;
    try { localStorage.setItem("gml.tab", name); } catch (e) {}
    $$("[data-admin-tab]").forEach(function (b) {
      setActive(b, b.dataset.adminTab === name);
    });
    $$("[data-admin-pane]").forEach(function (p) {
      p.style.display = p.dataset.adminPane === name ? "block" : "none";
    });
    if (name === "files") loadFiles();
  }

  function loadAuth() {
    if (demo) return;
    fetch("/api/auth", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (a) { auth = a; renderAuth(); })
      .catch(function () {});
  }

  function openAuth() {
    var m = aEl("modal");
    aEl("title").textContent = auth.configured ? "관리자 로그인" : "관리자 비밀번호 설정";
    aEl("desc").textContent = auth.configured
      ? "수집 · 변환 · 학습 · 삭제는 관리자만 할 수 있어요."
      : "처음이라 비밀번호를 정해야 해요. 4자 이상으로 정해주세요.";
    aEl("pw").value = "";
    aEl("msg").textContent = "";
    m.hidden = false;
    setTimeout(function () { aEl("pw").focus(); }, 50);
  }

  function submitAuth(e) {
    e.preventDefault();
    var pw = aEl("pw").value, msg = aEl("msg");
    var url = auth.configured ? "/api/auth/login" : "/api/auth/setup";
    msg.textContent = "확인 중…";
    fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                 body: JSON.stringify({ password: pw }) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) {
          msg.textContent = "❌ " + (d.error || "실패했습니다");
          return;
        }
        auth = { configured: d.configured, authed: d.authed };
        aEl("modal").hidden = true;
        renderAuth();
        loadJobOptions();
      })
      .catch(function () {
        msg.textContent = "❌ 요청을 보내지 못했습니다";
      });
  }

  // 잠긴 상태에서 서버가 401을 주면 로그인 창을 띄운다(세션이 만료된 경우 등).
  function handleAuthError(d) {
    if (d && d.need_auth) { auth.authed = false; renderAuth(); openAuth(); return true; }
    return false;
  }

  // ── 학습 자료 (보기 / 내려받기 / 지우기) ─────────────────────────
  var filesData = null, filesTab = null, specState = null;

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

    // 스펙트로그램은 상태(정상/수분부족/자극)별로 나눠 볼 수 있게 필터 줄을 띄운다.
    var sub = fEl("subtabs");
    if (sub) {
      if (grp.states) {
        var keys = Object.keys(grp.states);
        if (specState !== null && keys.indexOf(specState) < 0) specState = null;
        sub.style.display = "block";
        sub.innerHTML = "";
        [["", "전체 " + grp.total]].concat(keys.map(function (k) {
          return [k, k + " " + grp.states[k]];
        })).forEach(function (pair) {
          var b = document.createElement("button");
          b.type = "button";
          b.dataset.specState = pair[0];
          b.textContent = pair[1];
          setActive(b, (specState || "") === pair[0]);
          sub.appendChild(b);
        });
      } else {
        sub.style.display = "none";
        sub.innerHTML = "";
      }
    }
    if (grp.states && specState) {
      grp = Object.assign({}, grp, {
        files: grp.files.filter(function (f) { return f.state === specState; })
      });
    }

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
        // 모델 파일이면서 지금 쓰는 게 아니면, 이걸로 바로 갈아끼울 수 있는 버튼을 붙인다.
        // (svm_model/rf_model은 비교용 raw 분류기라 번들 정보가 없어 바로 못 씀)
        (!isTrash && grp.kind === "model" && !inUse && f.name.indexOf("best_model") === 0 ?
          '<button type="button" data-model-activate="' + f.path + '" ' +
          'class="shrink-0 rounded-lg bg-primary px-2.5 py-1 text-[10px] font-bold text-primary-foreground">사용하기</button>' : '') +
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
    if (!path) { box.hidden = true; return; }
    fEl("preview-img").src = dlUrl(path, 1);
    fEl("preview-name").textContent = path;
    fEl("preview-dl").href = dlUrl(path);
    box.hidden = false;
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
        if (handleAuthError(d)) { hint.textContent = "🔒 관리자 로그인이 필요합니다"; return; }
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

  // 자료 탭에서 예전에 학습해 둔 모델을 골라 지금 쓰는 모델로 바로 바꾼다.
  function activateModel(path) {
    var hint = fEl("hint");
    hint.textContent = "모델 적용하는 중…";
    fetch("/api/model/activate", { method: "POST", headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify({ path: path }) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (handleAuthError(d)) { hint.textContent = "🔒 관리자 로그인이 필요합니다"; return; }
        if (!d.ok) { hint.textContent = "❌ " + (d.error || "모델을 바꾸지 못했습니다"); return; }
        hint.textContent = "✅ 모델 적용됨: " + d.name + " · 클래스 " + d.classes.join("/");
        loadFiles();
      })
      .catch(function () { hint.textContent = "❌ 요청을 보내지 못했습니다"; });
  }

  // ── 조작 ─────────────────────────────────────────────────────────
  document.addEventListener("click", function (e) {
    var mode = e.target.closest("[data-mode-set]");
    if (mode) { setMode(mode.dataset.modeSet); return; }

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
      selectCollect(col.dataset.jobCollect);
      return;
    }
    if (e.target.closest('[data-job="collect-start"]') && !jobEl("collect-start").disabled) {
      startCollect();
      return;
    }
    var prep = e.target.closest("[data-job-prep]");
    if (prep && !prep.disabled) {
      // data-job-prep 이 비어 있으면 전체 변환.
      var pstate = prep.dataset.jobPrep || null;
      postJob("/api/preprocess", pstate ? { state: pstate } : {},
              (pstate || "전체") + " 스펙트로그램 변환");
      return;
    }
    if (e.target.closest('[data-job="train"]') && !jobEl("train").disabled) {
      startTrain();
      return;
    }
    var tsel = e.target.closest("[data-job-task]");
    if (tsel && !tsel.disabled) { selectTrainTask(tsel.dataset.jobTask); return; }
    var msel = e.target.closest("[data-job-mode-btn]");
    if (msel) { selectTrainMode(msel.dataset.jobModeBtn); return; }
    var atab = e.target.closest("[data-admin-tab]");
    if (atab) { showAdminTab(atab.dataset.adminTab); return; }
    var abtn = e.target.closest('[data-auth="button"]');
    if (abtn) {
      if (auth.authed) {
        fetch("/api/auth/logout", { method: "POST" })
          .then(function (r) { return r.json(); })
          .then(function (d) { auth = { configured: d.configured, authed: d.authed }; renderAuth(); })
          .catch(function () {});
      } else { openAuth(); }
      return;
    }
    if (e.target.closest('[data-auth="cancel"]')) { aEl("modal").hidden = true; return; }
    if (e.target.closest('[data-job="sensors"]')) { loadSensors(); return; }

    // ── 학습 자료 ──
    if (e.target.closest('[data-files="refresh"]')) { loadFiles(); return; }
    var tab = e.target.closest("[data-files-tab]");
    if (tab) { filesTab = tab.dataset.filesTab; renderFiles(); return; }
    var sst = e.target.closest("[data-spec-state]");
    if (sst) { specState = sst.dataset.specState || null; renderFiles(); return; }
    if (e.target.closest('[data-files="delete"]')) { deleteChecked(); return; }
    if (e.target.closest('[data-files="restore"]')) { restoreChecked(); return; }
    var actBtn = e.target.closest("[data-model-activate]");
    if (actBtn) { activateModel(actBtn.dataset.modelActivate); return; }
    var openImg = e.target.closest("[data-files-open]");
    if (openImg) { previewFile(openImg.dataset.filesOpen); return; }
    if (e.target.closest('[data-files="preview-close"]')) { previewFile(null); return; }

    var pause = e.target.closest('[data-act="pause"]');
    if (pause) {
      paused = !paused;
      pause.textContent = paused ? "다시 재생" : "일시정지";
      setActive(pause, paused);
      return;
    }
  });
  document.addEventListener("submit", function (e) {
    if (e.target.matches('[data-auth="form"]')) submitAuth(e);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      var am = aEl("modal");
      if (am) am.hidden = true;
    }
  });

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
      remember("unlimited", e.target.checked ? "1" : "");
      renderCollectHint();
    }
    if (e.target.matches('[data-job="duration"]')) {
      remember("duration", e.target.value);
      renderCollectHint();
    }
  });

  // 지속 시간·연결 상태는 데이터가 안 와도 계속 갱신
  setInterval(function () {
    if (lastState) setText("duration", since(stateSince));
    // lastSeen은 ready:true를 한 번도 못 받으면 0으로 남으므로, 그 경우 페이지를 연
    // 시각을 기준으로 삼는다(안 그러면 시작부터 실패해도 이 경고가 영영 안 뜬다).
    if (!demo && Date.now() - (lastSeen || pageLoad) > 4000) {
      setText("source", lastWorkerError ? "⚠️ " + lastWorkerError : "신호 끊김 — 재시도 중");
    }
  }, 1000);

  poll();
  setInterval(poll, POLL_MS);
  loadMode();
  setInterval(loadMode, 5000);
  loadAuth();

  // 다른 기기(폰/노트북)에서 시작한 작업도 헤더 배지에 보이도록, 패널을 열지 않아도
  // 처음 한 번은 상태를 확인하고 도는 중이면 폴링을 이어간다.
  if (!demo) {
    fetch("/api/job", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (j) { renderJob(j); if (j.running) watchJob(); })
      .catch(function () {});
  }
})();
