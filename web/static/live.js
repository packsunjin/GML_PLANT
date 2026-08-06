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
    applyHome(d);
  }

  // ── 데모 모드 ────────────────────────────────────────────────────
  // 파이(백엔드) 없이 파일만 열었을 때도 화면을 볼 수 있도록, /data 가 없으면
  // 브라우저에서 직접 만들어낸 값으로 돌립니다. 실제 측정값이 아닙니다.
  var demo = false;
  var DEMO_STATES = ["정상", "수분부족", "자극", "꺾임"];
  var demoStart = Date.now();
  var demoLog = loadDemoLog();

  function loadDemoLog() {
    try {
      var raw = localStorage.getItem("chorokmal-demo-log");
      if (raw) return JSON.parse(raw);
    } catch (err) { /* 저장소를 못 쓰면 메모리에만 둔다 */ }
    return [];
  }

  function saveDemoLog() {
    try { localStorage.setItem("chorokmal-demo-log", JSON.stringify(demoLog.slice(-300))); }
    catch (err) { /* 무시 */ }
  }

  function demoPayload() {
    var t = (Date.now() - demoStart) / 1000;
    var state = DEMO_STATES[Math.floor(t / 8) % DEMO_STATES.length];
    var probs = {}, top = 0.62 + Math.sin(t) * 0.08;
    DEMO_STATES.forEach(function (s) { probs[s] = (1 - top) / 3; });
    probs[state] = top;

    var signal = [], v = 0;
    for (var i = 0; i < 140; i++) {
      v = v * 0.7 + (Math.random() - 0.5) * 0.02;
      var wave = Math.sin((t * 2 + i / 9)) * 0.02;
      if (state === "자극" && i % 34 === 5) wave += 0.09;
      if (state === "꺾임") wave -= 0.03;
      signal.push(v + wave);
    }
    var spec = [];
    for (var r = 0; r < 24; r++) {
      var row = [];
      for (var c = 0; c < 20; c++) {
        row.push(-20 - r * 2.2 + Math.sin(c / 3 + t) * 3 + Math.random() * 4);
      }
      spec.push(row);
    }
    // 데모에서도 상태가 바뀌면 기록에 남겨 타임라인이 채워지게 한다
    var last = demoLog[demoLog.length - 1];
    if (!last || last.state !== state) {
      demoLog.push({ ts: new Date().toISOString().slice(0, 19), kind: "state",
                     state: state, proba: top });
      saveDemoLog();
    }
    return { ready: true, state: state, proba: top, probs: probs,
             signal: signal, spec: spec, source: "데모 데이터 (백엔드 없음)" };
  }

  function demoHistory() {
    var byDay = {}, today = new Date();
    var daily = [];
    for (var i = 6; i >= 0; i--) {
      var d = new Date(today.getTime() - i * 86400000);
      var key = d.toISOString().slice(0, 10);
      byDay[key] = { date: key, weekday: "일월화수목금토"[d.getDay()],
                     states: {}, water: 0, top: null };
      daily.push(byDay[key]);
    }
    demoLog.forEach(function (e) {
      var b = byDay[(e.ts || "").slice(0, 10)];
      if (!b) return;
      if (e.kind === "water") b.water += 1;
      else { b.states[e.state] = (b.states[e.state] || 0) + 1; }
    });
    daily.forEach(function (b) {
      var best = null;
      Object.keys(b.states).forEach(function (k) {
        if (!best || b.states[k] > b.states[best]) best = k;
      });
      b.top = best;
    });
    var states = demoLog.filter(function (e) { return e.kind === "state"; });
    var water = demoLog.filter(function (e) { return e.kind === "water"; });
    var healthy = states.filter(function (e) { return e.state === "정상"; }).length;
    return {
      daily: daily,
      events: demoLog.slice().reverse(),
      stats: {
        records: demoLog.length,
        water_count: water.length,
        state_count: states.length,
        healthy_ratio: states.length ? Math.round(healthy / states.length * 100) : null,
        active_days: Object.keys(byDay).filter(function (k) {
          return Object.keys(byDay[k].states).length || byDay[k].water;
        }).length
      }
    };
  }

  function poll() {
    if (demo) { apply(demoPayload()); return; }
    fetch("/data", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(apply)
      .catch(function () {
        demo = true;                 // 백엔드가 없으면 데모로 전환
        apply(demoPayload());
        loadHistory();
      });
  }

  // ── 홈 화면 요약 ─────────────────────────────────────────────────
  var MIND = {
    "정상": ["“지금 좋아요”", "수분과 온도 모두 안정 구간이에요. 그대로 유지해 주세요."],
    "수분부족": ["“목이 말라요”", "수분부족 신호가 감지됐어요. 흙을 촉촉하게 적셔주세요."],
    "자극": ["“방금 놀랐어요”", "잎에 닿는 자극이 감지됐어요. 잠시 그대로 두세요."],
    "꺾임": ["“어딘가 아파요”", "손상으로 보이는 신호예요. 줄기와 잎을 확인해 주세요."]
  };

  function applyHome(d) {
    var m = MIND[d.state];
    var mind = $('[data-home="mind"]'), desc = $('[data-home="desc"]');
    if (m && mind) mind.textContent = m[0];
    if (m && desc) desc.textContent = m[1];

    var line = $('[data-home="wave"]');
    if (line && d.signal && d.signal.length) {
      var lo = Math.min.apply(null, d.signal), hi = Math.max.apply(null, d.signal);
      var span = (hi - lo) || 1e-6, step = 288 / (d.signal.length - 1);
      line.setAttribute("points", d.signal.map(function (v, i) {
        return (i * step).toFixed(1) + "," + (72 - ((v - lo) / span) * 62).toFixed(2);
      }).join(" "));
    }
  }

  // ── 기록 화면 ────────────────────────────────────────────────────
  var BAR_COLOR = {
    "정상": "var(--primary)", "수분부족": "var(--water)",
    "자극": "var(--warning)", "꺾임": "var(--destructive)"
  };
  var histDays = 7;

  function renderHistory(h) {
    var bars = $('[data-hist="bars"]');
    if (bars) {
      var daily = h.daily || [];
      var peak = daily.reduce(function (mx, d) {
        var n = Object.values(d.states || {}).reduce(function (a, b) { return a + b; }, 0);
        return Math.max(mx, n);
      }, 0) || 1;
      bars.innerHTML = daily.map(function (d) {
        var total = Object.values(d.states || {}).reduce(function (a, b) { return a + b; }, 0);
        var pct = total ? Math.max(8, Math.round(total / peak * 100)) : 5;
        var color = total ? (BAR_COLOR[d.top] || "var(--primary)") : "var(--border)";
        return '<div class="flex flex-1 flex-col items-center justify-end" style="height:100%">' +
                 '<span class="mb-1.5 text-[10px] font-bold tabular-nums text-muted-foreground">' +
                   (total || "") + '</span>' +
                 '<div title="' + d.date + (d.top ? " · " + d.top : "") + '" ' +
                      'style="width:100%;max-width:56px;height:' + pct + '%;border-radius:12px 12px 5px 5px;background:' + color + '"></div>' +
                 '<span class="mt-2 text-[11px] font-bold text-muted-foreground">' + d.weekday + '</span>' +
                 (d.water ? '<span class="text-[10px] text-subtle">물 ' + d.water + '</span>' : '') +
               '</div>';
      }).join("") || '<p class="w-full text-center text-xs text-subtle">기록이 없어요.</p>';
    }

    var s = h.stats || {};
    var set = function (k, v) { var el = $('[data-hist="' + k + '"]'); if (el) el.textContent = v; };
    set("records", s.records != null ? s.records + "회" : "—");
    set("water", s.water_count != null ? s.water_count + "회" : "—");
    set("healthy", s.healthy_ratio != null ? s.healthy_ratio + "%" : "—");
    set("days", s.active_days != null ? s.active_days + "일" : "—");

    var tl = $('[data-hist="timeline"]');
    if (tl) {
      // 상태가 자주 바뀌면 목록이 길어지므로 최근 것만 보여준다
      var evs = (h.events || []).slice(0, 12);
      tl.innerHTML = evs.length ? evs.map(function (e) {
        var when = (e.ts || "").replace("T", " ").slice(5, 16);
        if (e.kind === "water") {
          return row("💧", "물주기 기록", (e.ml || 280) + "ml 급수 완료", when, "var(--water)");
        }
        var conf = e.proba == null ? "" : " · 확신도 " + (e.proba * 100).toFixed(1) + "%";
        return row("", e.state + " 감지", "전위신호 기반 분류" + conf, when, BAR_COLOR[e.state] || "var(--primary)");
      }).join("") : '<p class="py-6 text-center text-xs text-subtle">아직 기록이 없어요.</p>';
    }
  }

  function row(icon, title, sub, when, color) {
    return '<div class="flex items-center gap-3 border-b border-border py-3 last:border-b-0">' +
             '<span style="width:9px;height:9px;border-radius:50%;background:' + color + ';flex:none"></span>' +
             '<div class="min-w-0 flex-1">' +
               '<p class="text-sm font-bold text-foreground">' + (icon ? icon + " " : "") + title + '</p>' +
               '<p class="text-[11px] text-muted-foreground">' + sub + '</p>' +
             '</div>' +
             '<span class="shrink-0 text-[11px] font-medium text-subtle tabular-nums">' + when + '</span>' +
           '</div>';
  }

  function loadHistory() {
    if (demo) { renderHistory(demoHistory()); return; }
    fetch("/api/history?days=" + histDays, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(renderHistory)
      .catch(function () { demo = true; renderHistory(demoHistory()); });
  }

  function logWater() {
    if (demo) {
      demoLog.push({ ts: new Date().toISOString().slice(0, 19), kind: "water", ml: 280 });
      saveDemoLog();
      toast("물주기를 기록했어요 💧 (데모)");
      renderHistory(demoHistory());
      return;
    }
    fetch("/api/water", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plant: "monstera", ml: 280 })
    })
      .then(function (r) { return r.json(); })
      .then(function () { toast("물주기를 기록했어요 💧"); loadHistory(); })
      .catch(function () { toast("기록에 실패했어요"); });
  }

  // ── 설정 저장 ────────────────────────────────────────────────────
  function loadSettings() {
    if (demo) return;
    fetch("/api/settings", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        $$("#settings .settings-row, [data-screen='settings'] button[role='switch']");
        Object.keys(d.settings || {}).forEach(function (key) {
          var el = findToggleByLabel(key);
          if (el) setToggle(el, d.settings[key]);
        });
      })
      .catch(function () {});
  }

  function findToggleByLabel(label) {
    var hit = null;
    $$("[data-screen='settings'] p, [data-screen='settings'] div").some(function (el) {
      if (el.children.length === 0 && el.textContent.trim() === label) {
        var box = el.closest("div.flex");
        while (box && !box.querySelector("button, [role='switch'], input[type='checkbox']")) box = box.parentElement;
        hit = box && box.querySelector("button, [role='switch'], input[type='checkbox']");
        return !!hit;
      }
      return false;
    });
    return hit;
  }

  function setToggle(el, on) {
    el.dataset.on = on ? "1" : "0";
    if (el.tagName === "INPUT") el.checked = !!on;
    else el.setAttribute("aria-checked", on ? "true" : "false");
  }

  // ── 최저가 마켓 + 식물 도감 ──────────────────────────────────────
  var won = function (n) { return (n == null) ? "—" : n.toLocaleString("ko-KR") + "원"; };
  var mk = function (name) { return $('[data-mk="' + name + '"]'); };

  function renderMarket(d) {
    var items = d.items || [];
    var set = function (k, v) { var el = mk(k); if (el) el.textContent = v; };
    set("lowest", won(d.lowest));
    set("lowest-mall", items.length ? items[0].mall : "결과 없음");
    set("avg", won(d.average));
    set("count", items.length + "건");
    set("source", d.source + (d.cached ? " · 캐시" : ""));
    var note = mk("note");
    if (note) {
      note.textContent = d.source === "네이버쇼핑"
        ? "네이버 쇼핑 검색에서 모은 가격입니다. 실제 결제 금액은 판매처 정책에 따라 다를 수 있어요."
        : "실제 시세가 아닌 예시입니다. NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 를 설정하면 실제 가격을 불러옵니다.";
    }

    var list = mk("list");
    if (!list) return;
    list.innerHTML = items.length ? items.map(function (it, i) {
      var best = i === 0;
      var link = it.link
        ? '<a href="' + it.link + '" target="_blank" rel="noopener" class="shrink-0 text-xs font-bold text-primary">보러가기 →</a>'
        : "";
      return '<div class="flex items-center gap-3 border-b border-border py-3 last:border-b-0">' +
               '<span class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-black ' +
                 (best ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground') + '">' + (i + 1) + '</span>' +
               '<div class="min-w-0 flex-1">' +
                 '<p class="truncate text-sm font-bold text-foreground">' + esc(it.title || it.mall) + '</p>' +
                 '<p class="text-[11px] text-muted-foreground">' + esc(it.mall) + (best ? ' · 최저가' : '') + '</p>' +
               '</div>' +
               '<span class="shrink-0 text-sm font-black tabular-nums ' + (best ? 'text-primary' : 'text-foreground') + '">' +
                 won(it.price) + '</span>' + link +
             '</div>';
    }).join("") : '<p class="py-8 text-center text-xs text-subtle">결과가 없어요.</p>';
  }

  function renderInfo(d) {
    var set = function (k, v) { var el = mk(k); if (el) el.textContent = v; };
    set("info-title", d.title || "—");
    set("info-species", d.species || "");
    set("info-summary", d.summary || "");
    set("info-source", d.source + (d.cached ? " · 캐시" : ""));

    var photo = mk("info-photo");
    if (photo) {
      photo.innerHTML = d.photo
        ? '<img src="' + d.photo + '" alt="" class="h-full w-full object-cover">'
        : '<div class="flex h-full w-full items-center justify-center text-xs text-subtle">사진 없음</div>';
    }
    var tips = mk("info-tips");
    if (tips) {
      tips.innerHTML = (d.tips || []).map(function (t) {
        return '<span class="rounded-full bg-muted px-3 py-1.5 text-xs font-semibold text-secondary-foreground">' +
               esc(t) + '</span>';
      }).join("");
    }
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function searchMarket(q) {
    var input = mk("q");
    if (q && input) input.value = q;
    var query = (input && input.value.trim()) || "몬스테라";
    var list = mk("list");
    if (list) list.innerHTML = '<p class="py-8 text-center text-xs text-subtle">가격을 모으는 중…</p>';

    fetch("/api/market?q=" + encodeURIComponent(query), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(renderMarket)
      .catch(function () {
        renderMarket({ items: [], source: "오프라인",
                       lowest: null, average: null, cached: false });
      });

    fetch("/api/plant-info?name=" + encodeURIComponent(query), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(renderInfo)
      .catch(function () {
        renderInfo({ title: query, summary: "백엔드가 없어 정보를 불러오지 못했어요.",
                     tips: [], source: "오프라인" });
      });
  }

  // ── 토스트 ───────────────────────────────────────────────────────
  var toastTimer;
  function toast(msg) {
    var el = document.getElementById("toast0");
    if (!el) {
      el = document.createElement("div");
      el.id = "toast0";
      el.style.cssText = "position:fixed;left:50%;bottom:96px;transform:translateX(-50%);" +
        "background:var(--ink);color:var(--primary-foreground);font-weight:700;font-size:14px;" +
        "padding:12px 20px;border-radius:14px;z-index:70;opacity:0;transition:opacity .2s;pointer-events:none";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.style.opacity = "1";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.style.opacity = "0"; }, 1800);
  }

  // ── 이벤트 연결 ──────────────────────────────────────────────────
  document.addEventListener("click", function (e) {
    if (e.target.closest('[data-home="water"], [data-hist="water-btn"]')) {
      e.preventDefault();
      logWater();
      return;
    }
    if (e.target.closest('[data-mk="go"]')) { e.preventDefault(); searchMarket(); return; }
    var chip = e.target.closest("[data-q]");
    if (chip) { e.preventDefault(); searchMarket(chip.dataset.q); return; }

    var range = e.target.closest("[data-range]");
    if (range) {
      histDays = parseInt(range.dataset.range, 10) || 7;
      $$("[data-range]").forEach(function (b) {
        var on = b === range;
        b.classList.toggle("bg-primary", on);
        b.classList.toggle("text-primary-foreground", on);
        b.classList.toggle("text-muted-foreground", !on);
      });
      loadHistory();
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && e.target === mk("q")) { e.preventDefault(); searchMarket(); }
  });

  go("home");
  poll();
  setInterval(poll, 700);
  loadHistory();
  setInterval(loadHistory, 20000);
  loadSettings();
  searchMarket("몬스테라");
})();
