"""
web_dashboard.py
================
브라우저로 접속해서 보는 실시간 웹 대시보드 (matplotlib/VNC 대신).

- 핵심 분석은 inference.RealtimeClassifier를 그대로 재사용한다.
- 백그라운드 스레드가 실시간으로 신호를 분류하며 최신 결과를 저장하고,
  Flask 웹서버가 그 결과를 웹페이지(/) 와 JSON(/data)로 내보낸다.
- 배치는 기존 VNC 화면과 동일: 상단 = 최근 5초 시계열,
  하단 좌 = 실시간 스펙트로그램, 하단 우 = 상태(색상 원 + 상태명 + 확신도).

실행:
    python web_dashboard.py                         # 하드웨어/자동 시뮬레이션
    python web_dashboard.py --sim_csv ../data/raw/자극.csv
    python web_dashboard.py --model ../models/best_model_정상-자극.joblib
그 뒤 브라우저에서  http://<파이 IP>:5000  으로 접속.
"""

import argparse
import os
import threading
import time

import numpy as np

import store
from inference import RealtimeClassifier, SAMPLE_RATE_HZ

# 최신 결과를 담는 공유 상태 (백그라운드 스레드가 갱신, 웹 요청이 읽음)
_latest = {"ready": False}
_lock = threading.Lock()


def _payload(result, recent, clf):
    """step() 결과 + 최근 5초 버퍼 -> 브라우저로 보낼 JSON 딕셔너리."""
    sig = np.asarray(recent)[::3]  # 5초 버퍼를 1/3로 다운샘플(네트워크 절약)
    spec = np.asarray(result["spectrogram_db"], dtype=float)
    filt = result["filtered_signal"]
    return {
        "ready": True,
        "state": result["state"],
        "proba": result["proba"],
        "probs": result["probs"],
        "classes": list(clf.classes),
        "signal": [round(float(v), 5) for v in sig],
        "spec": [[round(float(x), 1) for x in row] for row in spec],  # (주파수 x 시간)
        "mean": round(float(np.mean(filt)), 4),
        "std": round(float(np.std(filt)), 4),
        "p2p": round(float(np.max(filt) - np.min(filt)), 4),
    }


def _worker(clf, source):
    """백그라운드: 실시간으로 step()을 돌리며 최근 5초 버퍼와 최신 결과를 갱신한다."""
    src_kind, src_label = source
    recent = np.zeros(int(5 * clf.sample_rate))
    while True:
        result = clf.step()
        if result is not None:
            n_new = min(clf.predict_every, len(result["filtered_signal"]), len(recent))
            recent = np.roll(recent, -n_new)
            recent[-n_new:] = result["filtered_signal"][-n_new:]
            payload = _payload(result, recent, clf)
            payload["source_kind"] = src_kind   # hardware / sim / csv
            payload["source"] = src_label       # 사람이 읽는 라벨
            with _lock:
                _latest.clear()
                _latest.update(payload)
            # 상태가 바뀔 때만 기록에 남긴다(같은 상태 반복은 store가 걸러냄)
            store.log_state(result["state"], result["proba"])
        time.sleep(1.0 / clf.sample_rate)


def _web_dir():
    """저장소의 web/ 디렉터리(초록말 UI). 없으면 None."""
    d = os.path.join(os.path.dirname(__file__), "..", "web")
    return d if os.path.isfile(os.path.join(d, "chorokmal.html")) else None


def run_web(model_path, sim_csv=None, sim_state="정상", host="0.0.0.0", port=5000,
            refresh_hz=5.0, ui="초록말"):
    try:
        from flask import Flask, jsonify, request, send_from_directory
    except ImportError:
        print("[web] Flask가 필요합니다:  pip install flask")
        raise

    clf = RealtimeClassifier(model_path=model_path, sim_source_csv=sim_csv,
                             predict_hz=refresh_hz, sim_state=sim_state)
    source = clf.input_source()
    print(f"[web] 모델 로드: {clf.model_name}, 클래스={clf.classes}")
    print(f"[web] 입력 소스: {source[1]}")

    threading.Thread(target=_worker, args=(clf, source), daemon=True).start()

    web_dir = _web_dir() if ui == "초록말" else None
    if ui == "초록말" and web_dir is None:
        print("[web] web/chorokmal.html 을 찾지 못해 기본 화면으로 실행합니다.")

    # 초록말 UI는 web/static 을 그대로 /static 으로 내보낸다. Flask 기본 static
    # 폴더(src/static)는 존재하지 않으므로 여기서 직접 지정해야 404가 나지 않는다.
    app = (Flask(__name__, static_folder=os.path.join(web_dir, "static"), static_url_path="/static")
           if web_dir else Flask(__name__))

    @app.route("/")
    def index():
        if web_dir:
            return send_from_directory(web_dir, "chorokmal.html")
        return PAGE

    @app.route("/data")
    def data():
        with _lock:
            return jsonify(dict(_latest))

    # ---- 기록 API (하드웨어 없이도 동작) --------------------------------
    @app.route("/api/history")
    def api_history():
        days = request.args.get("days", default=7, type=int)
        return jsonify({
            "daily": store.daily_summary(max(1, min(days, 31))),
            "events": store.read_events(30),
            "stats": store.stats(),
            "streak": store.care_streak(),
        })

    @app.route("/api/plant-info")
    def api_plant_info():
        import plantinfo
        return jsonify(plantinfo.lookup(request.args.get("name", "몬스테라")))

    @app.route("/api/market")
    def api_market():
        import market
        return jsonify(market.search(request.args.get("q", "몬스테라")))

    @app.route("/api/water", methods=["POST"])
    def api_water():
        body = request.get_json(silent=True) or {}
        ev = store.log_water(plant=body.get("plant", "monstera"),
                             ml=int(body.get("ml", 280)))
        return jsonify({"ok": True, "event": ev, "stats": store.stats()})

    @app.route("/api/plants", methods=["GET", "POST"])
    def api_plants():
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            items = body.get("plants")
            if not isinstance(items, list):
                return jsonify({"ok": False, "error": "plants 배열이 필요합니다"}), 400
            return jsonify({"ok": True, "plants": store.save_plants(items)})
        return jsonify({"plants": store.plants()})

    @app.route("/api/settings", methods=["GET", "POST"])
    def api_settings():
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            return jsonify({"ok": True, "settings": store.save_settings(body)})
        return jsonify({"settings": store.settings()})

    ip_hint = os.environ.get("GML_IP", "<파이 IP>")
    print(f"[web] 브라우저에서 접속:  http://{ip_hint}:{port}   (같은 기기면 http://localhost:{port})")
    print("[web] Ctrl+C 로 종료")
    app.run(host=host, port=port, threaded=True)


PAGE = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GML 식물 전위 모니터</title>
<style>
  :root{--bg:#e6e8e1;--panel:#f6f8f2;--plot:#f9fbf5;--grid:#bcc5b6;--gmin:#d8ddd0;--hair:#c4cbbc;
    --ink:#151a16;--ink2:#586055;--ink3:#8b9488;--trace:#0e7a45;--scan:rgba(14,122,69,.5);
    --mono:ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,monospace;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif;}
  @media (prefers-color-scheme:dark){:root{--bg:#090c0a;--panel:#0f1310;--plot:#0c100d;--grid:#20302a;--gmin:#141c17;--hair:#212a22;
    --ink:#e6ede8;--ink2:#93a099;--ink3:#5d6a62;--trace:#3ed082;--scan:rgba(62,208,130,.5);}}
  *{box-sizing:border-box}html,body{margin:0}
  body{background:var(--bg);color:var(--ink);font-family:var(--sans);-webkit-font-smoothing:antialiased;padding:clamp(12px,2vw,22px)}
  .mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
  .wrap{max-width:1000px;margin:0 auto;display:flex;flex-direction:column;gap:14px}
  .top{display:flex;align-items:center;gap:14px;font-family:var(--mono);font-size:12px;color:var(--ink3);padding:0 2px}
  .top b{color:var(--ink);font-weight:700}.top b i{color:var(--trace);font-style:normal}.top .sp{flex:1}
  .rec{color:var(--trace);font-weight:700;letter-spacing:.08em;display:inline-flex;gap:7px;align-items:center}
  .rec .d{width:7px;height:7px;border-radius:50%;background:var(--trace);animation:b 1.4s steps(1) infinite}
  @keyframes b{50%{opacity:.25}}
  .badge{display:inline-flex;align-items:center;gap:6px;font-family:var(--sans);font-size:12px;font-weight:700;
    padding:4px 11px;border-radius:999px;border:1px solid transparent}
  .badge .bd{width:7px;height:7px;border-radius:50%;background:currentColor}
  .badge.hardware{color:#1f9d5f;background:rgba(31,157,95,.14);border-color:rgba(31,157,95,.35)}
  .badge.sim{color:#c07216;background:rgba(192,114,22,.15);border-color:rgba(192,114,22,.38)}
  .badge.csv{color:#1667c2;background:rgba(22,103,194,.14);border-color:rgba(22,103,194,.35)}
  .signal{border:1px solid var(--hair);background:var(--panel)}
  .h{display:flex;justify-content:space-between;align-items:baseline;padding:11px 15px 3px}
  .h h2{margin:0;font-size:12px;font-weight:700;color:var(--ink2)}.h .u{font-family:var(--mono);font-size:11px;color:var(--ink3)}
  svg#wave{width:100%;height:240px;display:block;background:var(--plot)}
  .row{display:grid;grid-template-columns:1.3fr .7fr;gap:14px}
  @media (max-width:680px){.row{grid-template-columns:1fr}svg#wave{height:190px}}
  .box{border:1px solid var(--hair);background:var(--panel);padding:14px}
  .box h2{margin:0 0 10px;font-size:12px;font-weight:700;color:var(--ink2)}
  canvas#spec{width:100%;height:180px;display:block;background:var(--plot);border:1px solid var(--hair)}
  .axx{display:flex;justify-content:space-between;font-family:var(--mono);font-size:10px;color:var(--ink3);margin-top:6px}
  .status{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;height:100%;min-height:190px}
  .dot{width:104px;height:104px;border-radius:50%;background:#888;border:3px solid rgba(0,0,0,.15);
    display:grid;place-items:center;color:#fff;font-weight:800;font-size:19px;letter-spacing:-.02em;transition:background .3s}
  .stname{font-size:26px;font-weight:800;letter-spacing:-.02em}
  .stconf{font-family:var(--mono);color:var(--ink2);font-size:14px}
  .stats{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--hair);border:1px solid var(--hair)}
  .stats div{background:var(--panel);padding:9px 10px;text-align:center}
  .stats .k{font-family:var(--mono);font-size:10px;letter-spacing:.04em;text-transform:uppercase;color:var(--ink3)}
  .stats .v{font-family:var(--mono);font-size:14px;font-weight:700;margin-top:2px}
  .wait{color:var(--ink3);font-family:var(--mono);font-size:13px;padding:20px;text-align:center}
</style></head><body>
<div class="wrap">
  <div class="top"><b>GML<i>//</i>식물 전위 모니터</b>
    <span class="badge sim" id="mode"><span class="bd"></span><span id="modetxt">입력 확인 중…</span></span>
    <span class="sp"></span>
    <span class="rec"><span class="d"></span>LIVE</span><span class="mono" id="clock">--:--:--</span></div>

  <div class="signal">
    <div class="h"><h2>전위 신호 · 필터 후</h2><span class="u">최근 5.0 s</span></div>
    <svg id="wave" viewBox="0 0 1000 240" preserveAspectRatio="none"></svg>
  </div>

  <div class="row">
    <div class="box"><h2>실시간 스펙트로그램</h2>
      <canvas id="spec" width="360" height="150"></canvas>
      <div class="axx"><span>0 Hz</span><span>주파수 →</span></div>
    </div>
    <div class="box"><h2>상태</h2>
      <div class="status" id="status"><div class="wait">데이터 수집 중… (버퍼 채우는 중)</div></div>
    </div>
  </div>

  <div class="stats">
    <div><div class="k">평균</div><div class="v" id="s_mean">–</div></div>
    <div><div class="k">표준편차</div><div class="v" id="s_std">–</div></div>
    <div><div class="k">피크투피크</div><div class="v" id="s_p2p">–</div></div>
  </div>
</div>
<script>
const CSS=getComputedStyle(document.documentElement),C=n=>CSS.getPropertyValue(n).trim();
const COLOR={"정상":"#1f9d5f","수분부족":"#1667c2","자극":"#c07216","꺾임":"#c1121f","스트레스":"#c62828"};
const VIR=[[68,1,84],[59,82,139],[33,145,140],[94,201,98],[253,231,37]];
const lut=i=>{const t=i/255*4,k=Math.floor(t),f=t-k,a=VIR[k],b=VIR[Math.min(4,k+1)];
  return `rgb(${a[0]+(b[0]-a[0])*f|0},${a[1]+(b[1]-a[1])*f|0},${a[2]+(b[2]-a[2])*f|0})`;};
setInterval(()=>{document.getElementById('clock').textContent=new Date().toLocaleTimeString('en-GB',{hour12:false});},1000);

function drawWave(sig){
  const W=1000,H=240,n=sig.length; if(!n)return;
  let mn=Math.min(...sig),mx=Math.max(...sig); const pad=(mx-mn||1)*0.15; mn-=pad; mx+=pad;
  const y=v=>H-(v-mn)/(mx-mn)*H, sx=W/(n-1), tr=C('--trace');
  let g=''; for(let i=0;i<=10;i++){const x=i*W/10;g+=`<line x1="${x}" y1="0" x2="${x}" y2="${H}" stroke="${i%2?C('--gmin'):C('--grid')}"/>`;}
  for(let j=0;j<=6;j++){const yy=j*H/6;g+=`<line x1="0" y1="${yy}" x2="${W}" y2="${yy}" stroke="${j%2?C('--gmin'):C('--grid')}"/>`;}
  let d='M0 '+y(sig[0]).toFixed(1); for(let i=1;i<n;i++)d+=' L'+(i*sx).toFixed(1)+' '+y(sig[i]).toFixed(1);
  document.getElementById('wave').innerHTML=
    `<g stroke-width="1">${g}</g>`+
    `<path d="${d}" fill="none" stroke="${tr}" stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round"/>`+
    `<circle cx="${W}" cy="${y(sig[n-1]).toFixed(1)}" r="4.5" fill="${tr}"/>`;
}
function drawSpec(spec){
  if(!spec||!spec.length)return; const nf=spec.length,nt=spec[0].length;
  let mn=1e9,mx=-1e9; for(const r of spec)for(const v of r){if(v<mn)mn=v;if(v>mx)mx=v;}
  const rng=(mx-mn)||1, c=document.getElementById('spec'),x=c.getContext('2d');
  const cw=c.width,ch=c.height,cwn=cw/nt,chn=ch/nf;
  for(let fi=0;fi<nf;fi++)for(let ti=0;ti<nt;ti++){
    x.fillStyle=lut(Math.max(0,Math.min(255,(spec[fi][ti]-mn)/rng*255|0)));
    x.fillRect(ti*cwn, ch-(fi+1)*chn, Math.ceil(cwn), Math.ceil(chn)); }
}
function status(d){
  const col=COLOR[d.state]||"#888", el=document.getElementById('status');
  const conf=d.proba!=null?(d.proba*100).toFixed(1)+'%':'N/A';
  el.innerHTML=`<div class="dot" style="background:${col}">${d.state}</div>`+
    `<div class="stname" style="color:${col}">${d.state}</div>`+
    `<div class="stconf">확신도 ${conf}</div>`;
}
function mode(d){
  const b=document.getElementById('mode');
  b.className='badge '+(d.source_kind||'sim');
  const icon={hardware:'🔌 하드웨어',sim:'🧪 시뮬레이션',csv:'▶ CSV 재생'}[d.source_kind]||'입력';
  document.getElementById('modetxt').textContent=(d.source||icon);
}
async function tick(){
  try{const d=await (await fetch('/data')).json(); if(!d.ready)return;
    mode(d); drawWave(d.signal); drawSpec(d.spec); status(d);
    document.getElementById('s_mean').textContent=d.mean+' V';
    document.getElementById('s_std').textContent=d.std+' V';
    document.getElementById('s_p2p').textContent=d.p2p+' V';
  }catch(e){}
}
setInterval(tick,200); tick();
</script></body></html>"""


def main():
    here = os.path.dirname(__file__)
    parser = argparse.ArgumentParser(description="실시간 식물 상태 웹 대시보드")
    parser.add_argument("--model", default=os.path.join(here, "..", "models", "best_model.joblib"))
    parser.add_argument("--sim_csv", default=None, help="하드웨어 없을 때 재생할 샘플 CSV")
    parser.add_argument("--sim_state", default="정상", choices=["정상", "수분부족", "자극", "꺾임", "순환"],
                        help="CSV·하드웨어 없을 때 라이브로 생성할 상태")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--ui", default="초록말", choices=["초록말", "기본"],
                        help="초록말=web/ 디자인 화면, 기본=예전 내장 화면")
    args = parser.parse_args()

    if not os.path.exists(args.model):
        print(f"[web] 모델 파일이 없습니다: {args.model}  (먼저 train.py 실행)")
        return
    run_web(args.model, sim_csv=args.sim_csv, sim_state=args.sim_state, host=args.host,
            port=args.port, ui=args.ui)


if __name__ == "__main__":
    main()
