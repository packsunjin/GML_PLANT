/* 초록말 대시보드 - 화면 전환 + 라즈베리파이 실시간 데이터 연결
 *
 * 백엔드(src/web_dashboard.py)의 /data 를 주기적으로 읽어 "실시간 상태" 화면을
 * 갱신합니다. 나머지 화면(홈/랭킹/기록/내 식물/마켓/설정)은 아직 정적 화면입니다.
 */
(function () {
  "use strict";

  // ── 상태별 색/문구 ───────────────────────────────────────────────
  var STATE = {
    "정상":     { c: "var(--primary)",     d: "var(--primary-hover)",     s: "var(--primary-soft)",
                  tip: "지금이 좋아요",   body: "물주기 간격을 그대로 유지해 주세요" },
    "수분부족": { c: "var(--water)",       d: "var(--water-deep)",        s: "var(--water-soft)",
                  tip: "수분부족 TIP",     body: "흙이 마르면 흠뻑 주세요" },
    "자극":     { c: "var(--warning)",     d: "var(--warning-foreground)", s: "var(--warning-soft)",
                  tip: "자극 감지 TIP",   body: "잎을 건드리지 말고 잠시 두세요" },
    "꺾임":     { c: "var(--destructive)", d: "var(--destructive)",       s: "var(--destructive-soft)",
                  tip: "꺾임 TIP",         body: "손상된 줄기를 정리해 주세요" }
  };
  var FALLBACK = STATE["정상"];
  var RING_LEN = 108.7;   // r=17.3 원의 둘레

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var live = function (name) { return $('[data-live="' + name + '"]'); };

  // ── 화면 전환 ────────────────────────────────────────────────────
  var LABEL = {
    home: ["2026년 8월 4일 · 화요일", "좋은 저녁이에요, 선진님 👋"],
    status: ["거실 몬스테라 · 센서 연결됨", "실시간 상태"],
    ranking: ["케어 스트릭 · 이번 주", "케어 랭킹"],
    history: ["지난 7일 · 거실 몬스테라", "상태 기록"],
    plants: ["총 3개 식물 모니터링 중", "내 식물"],
    market: ["화훼시장 · 원예몰 가격 수집 결과", "전국 최저가 식물 마켓"],
    settings: ["초록말 · 무료 플랜", "설정 및 계정"]
  };

  function go(name) {
    $$(".screen").forEach(function (s) { s.classList.toggle("is-on", s.dataset.screen === name); });
    $$("aside nav [data-go]").forEach(function (el) {
      var on = el.dataset.go === name;
      el.classList.toggle("bg-primary", on);
      el.classList.toggle("text-primary-foreground", on);
      el.classList.toggle("shadow-soft", on);
      el.classList.toggle("font-bold", on);
      el.classList.toggle("font-medium", !on);
      el.classList.toggle("text-muted-foreground", !on);
      el.classList.toggle("hover:bg-foreground/5", !on);
    });
    $$("nav.fixed [data-go]").forEach(function (el) {
      var on = el.dataset.go === name;
      el.classList.toggle("text-primary", on);
      el.classList.toggle("text-subtle", !on);
    });
    var head = LABEL[name];
    if (head) {
      var ey = $("header .md\\:block p"), ti = $("header .md\\:block h2");
      if (ey) ey.textContent = head[0];
      if (ti) ti.textContent = head[1];
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  document.addEventListener("click", function (e) {
    var nav = e.target.closest("[data-go]");
    if (nav) { e.preventDefault(); go(nav.dataset.go); return; }
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
  }

  // ── 그리기 ───────────────────────────────────────────────────────
  function drawWave(signal) {
    if (!signal || !signal.length) return;
    var lo = Math.min.apply(null, signal), hi = Math.max.apply(null, signal);
    var span = (hi - lo) || 1e-6;
    var step = 288 / (signal.length - 1);
    var pts = signal.map(function (v, i) {
      // 위아래 8% 여백을 두고 80 높이에 맞춤
      var y = 74 - ((v - lo) / span) * 68;
      return (i * step).toFixed(1) + "," + y.toFixed(2);
    }).join(" ");
    $$('[data-live="wave"], #graphModal [data-wave] polyline').forEach(function (el) {
      el.setAttribute("points", pts);
    });
  }

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
    $$("canvas[data-spec]").forEach(function (cv) {
      var ctx = cv.getContext("2d"), w = cv.width, h = cv.height;
      var cw = w / cols, ch = h / rows;
      for (var r2 = 0; r2 < rows; r2++) {
        for (var c2 = 0; c2 < cols; c2++) {
          ctx.fillStyle = ramp((spec[r2][c2] - lo) / span);
          // 주파수 축을 위로 향하게 뒤집어 그린다
          ctx.fillRect(c2 * cw, h - (r2 + 1) * ch, Math.ceil(cw), Math.ceil(ch));
        }
      }
    });
  }

  // ── 실시간 갱신 ──────────────────────────────────────────────────
  function apply(d) {
    if (!d || !d.ready) return;

    var st = STATE[d.state] || FALLBACK;
    var scope = $("[data-state-scope]");
    if (scope) {
      scope.style.setProperty("--state", st.c);
      scope.style.setProperty("--state-deep", st.d);
      scope.style.setProperty("--state-soft", st.s);
    }

    var nameEl = live("state");
    if (nameEl) nameEl.textContent = d.state;

    var conf = (d.proba == null) ? null : d.proba * 100;
    var confEl = live("conf");
    if (confEl) confEl.textContent = conf == null ? "확신도 —" : "확신도 " + conf.toFixed(1) + "%";

    var ring = live("ring");
    if (ring) {
      var filled = (conf == null ? 0 : conf) / 100 * RING_LEN;
      ring.setAttribute("stroke-dasharray", filled.toFixed(1) + " " + RING_LEN);
    }

    if (d.probs) {
      $$("[data-cls]").forEach(function (row) {
        var p = d.probs[row.dataset.cls];
        var pct = (p == null ? 0 : p * 100);
        var bar = $("[data-bar]", row), val = $("[data-val]", row);
        if (bar) bar.style.width = pct.toFixed(1) + "%";
        if (val) val.textContent = pct.toFixed(1) + "%";
      });
    }

    var tipT = live("tipTitle"), tipB = live("tipBody");
    if (tipT) tipT.textContent = st.tip;
    if (tipB) tipB.textContent = st.body;

    var alert1 = live("alert1");
    if (alert1) alert1.innerHTML = '방금 · <b class="text-foreground">' + d.state + "</b> 감지";

    var src = live("source");
    if (src) src.textContent = d.source || "입력 소스 확인 중";

    drawWave(d.signal);
    drawSpec(d.spec);
  }

  function poll() {
    fetch("/data", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(apply)
      .catch(function () {
        var src = live("source");
        if (src) src.textContent = "서버 연결 끊김 — 재시도 중";
      });
  }

  go("home");
  poll();
  setInterval(poll, 700);
})();
