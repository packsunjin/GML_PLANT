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
import subprocess
import sys
import threading
import time
from collections import deque

import numpy as np

import sensor_control
from inference import RealtimeClassifier, SAMPLE_RATE_HZ

# 최신 결과를 담는 공유 상태 (백그라운드 스레드가 갱신, 웹 요청이 읽음)
_latest = {"ready": False}
_lock = threading.Lock()

# ---- 수집/학습 작업 (SSH 없이 브라우저에서 실행) -------------------------
# 한 번에 하나만 돌린다. 실시간 루프와 같은 프로세스에서 무거운 학습을 돌리면 GIL 때문에
# 대시보드가 멈추므로, 별도 프로세스(subprocess)로 띄우고 출력만 받아온다.
_job = {"running": False, "kind": None, "label": None, "ok": None,
        "started": None, "finished": None, "step": None}
_job_log = deque(maxlen=400)
_job_lock = threading.Lock()
# 수집 중에는 실시간 루프를 재운다. 같은 ADS1115를 두 프로세스가 번갈아 읽으면
# 수집 쪽 샘플레이트가 떨어지는데, 그러면 학습 데이터의 시간축이 어긋난다.
_pause = threading.Event()


def _job_say(line):
    with _job_lock:
        _job_log.append(line.rstrip())


def _job_snapshot():
    with _job_lock:
        d = dict(_job)
        d["log"] = list(_job_log)
        return d


def _run_steps(kind, label, steps, cwd, pause_live=False, on_done=None):
    """steps = [(단계이름, 커맨드 리스트), ...] 를 순서대로 별도 프로세스로 실행하고
    표준출력을 _job_log에 모은다. 이미 실행 중이면 False를 돌려준다."""
    with _job_lock:
        if _job["running"]:
            return False
        _job.update({"running": True, "kind": kind, "label": label, "ok": None,
                     "started": time.time(), "finished": None, "step": steps[0][0]})
        _job_log.clear()

    def work():
        ok = True
        if pause_live:
            _pause.set()
            time.sleep(0.3)   # 실시간 루프가 대기 상태로 들어갈 여유
        try:
            for name, cmd in steps:
                with _job_lock:
                    _job["step"] = name
                _job_say(f"── {name} ──")
                # 자식 프로세스가 한글을 그대로 뱉도록 UTF-8을 강제한다(파이 기본 로캘이
                # C인 경우 UnicodeEncodeError로 죽는 것을 막는다).
                env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
                proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True,
                                        encoding="utf-8", errors="replace", bufsize=1)
                for line in proc.stdout:
                    _job_say(line)
                if proc.wait() != 0:
                    _job_say(f"❌ '{name}' 단계가 실패했습니다 (종료코드 {proc.returncode})")
                    ok = False
                    break
        except Exception as e:
            _job_say(f"❌ 오류: {e}")
            ok = False
        finally:
            if pause_live:
                _pause.clear()
            if ok and on_done is not None:
                try:
                    on_done()
                except Exception as e:
                    _job_say(f"⚠️ 후처리 실패: {e}")
            with _job_lock:
                _job.update({"running": False, "ok": ok, "finished": time.time(), "step": None})
            _job_say("✅ 완료" if ok else "중단됨")

    threading.Thread(target=work, daemon=True).start()
    return True


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
        # 아날로그 습도 센서(ADS1115 A1). 안 꽂혀 있으면 None -> 화면은 "센서 대기 중"
        "humidity": sensor_control.read_moisture(),
        # 실제로 초당 몇 샘플을 읽고 있는지. 명목(250Hz)보다 많이 낮으면 필터/스펙트로그램이
        # 가정하는 주파수축이 어긋나 정확도가 떨어진다는 신호다.
        "sample_rate": SAMPLE_RATE_HZ,
        "actual_rate": clf.actual_rate(),
    }


def _worker(clf, source):
    """백그라운드: 실시간으로 step()을 돌리며 최근 5초 버퍼와 최신 결과를 갱신한다."""
    recent = np.zeros(int(5 * clf.sample_rate))
    while True:
        # 수집 작업 중에는 센서를 양보하고 쉰다. 다시 시작할 때 pace()가 목표 시각을
        # 리셋하므로(1초 이상 밀리면 t0 재설정) 밀린 만큼 폭주하지 않는다.
        while _pause.is_set():
            time.sleep(0.2)
        result = clf.step()
        if result is not None:
            n_new = min(clf.predict_every, len(result["filtered_signal"]), len(recent))
            recent = np.roll(recent, -n_new)
            recent[-n_new:] = result["filtered_signal"][-n_new:]
            payload = _payload(result, recent, clf)
            # 라벨을 매번 다시 읽는다. /api/mode 로 시뮬레이션 상태를 바꾸면
            # 배지 문구도 바로 따라가야 하기 때문이다.
            src_kind, src_label = clf.input_source()
            payload["source_kind"] = src_kind   # hardware / sim / csv
            payload["source"] = src_label       # 사람이 읽는 라벨
            with _lock:
                _latest.clear()
                _latest.update(payload)
        clf.pace()


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

    # git pull 로 JS/CSS만 바뀌면 브라우저가 캐시된 옛 파일을 계속 써서 "버튼이 안 눌리는"
    # 증상이 난다(HTML은 새것, live.js는 옛것). Flask 기본 static 캐시가 12시간이라
    # 헤더만 바꿔서는 이미 캐시된 사본이 안 지워지므로, URL 자체에 파일 수정시각을 붙여
    # 새 주소로 만든다.
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    def _asset_version():
        stamps = []
        for name in ("live.js", "ui.css", "app.css"):
            p = os.path.join(web_dir, "static", name) if web_dir else None
            if p and os.path.isfile(p):
                stamps.append(int(os.path.getmtime(p)))
        return str(max(stamps)) if stamps else "0"

    @app.route("/")
    def index():
        if not web_dir:
            return PAGE
        with open(os.path.join(web_dir, "chorokmal.html"), encoding="utf-8") as f:
            html = f.read()
        v = _asset_version()
        for name in ("live.js", "ui.css", "app.css"):
            html = html.replace(f'"/static/{name}"', f'"/static/{name}?v={v}"')
        resp = app.make_response(html)
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/data")
    def data():
        with _lock:
            return jsonify(dict(_latest))

    # ---- 모드 전환 (SSH 없이 브라우저에서) -----------------------------
    # RealtimeClassifier 는 매 스텝마다 self.sim_state 를 읽으므로, 값만 바꿔도
    # 다음 샘플부터 바로 반영된다. 하드웨어로 도는 중에는 의미가 없어 막는다.
    SIM_STATES = ("정상", "수분부족", "자극", "순환")

    def _mode_info():
        kind, label = clf.input_source()
        return {"source_kind": kind, "source": label,
                "sim_state": clf.sim_state, "options": list(SIM_STATES),
                "editable": kind == "sim"}

    @app.route("/api/mode", methods=["GET", "POST"])
    def api_mode():
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            want = body.get("sim_state")
            if want not in SIM_STATES:
                return jsonify({"ok": False, "error": "알 수 없는 상태입니다"}), 400
            kind, _ = clf.input_source()
            if kind != "sim":
                return jsonify({"ok": False, "error": "하드웨어/CSV 입력에서는 바꿀 수 없습니다",
                                **_mode_info()}), 409
            clf.sim_state = want
            print(f"[web] 시뮬레이션 상태 변경 -> {want}")
            return jsonify({"ok": True, **_mode_info()})
        return jsonify(_mode_info())

    # ---- 데이터 수집 / 재학습 (SSH 없이 브라우저에서) ---------------------
    src_dir = os.path.dirname(os.path.abspath(__file__))
    TASKS = ("3종", "정상-수분부족", "정상-자극", "전체")
    MODES = ("픽셀", "특징", "둘다")

    def _reload():
        info = clf.reload_model()
        _job_say(f"↻ 새 모델 적용: {info['name']} / 클래스 {info['classes']}")
        print(f"[web] 모델 교체 -> {info['name']} {info['classes']}")

    @app.route("/api/job")
    def api_job():
        return jsonify(_job_snapshot())

    @app.route("/api/collect", methods=["POST"])
    def api_collect():
        body = request.get_json(silent=True) or {}
        state = body.get("state")
        if state not in sensor_control.VALID_STATES:
            return jsonify({"ok": False, "error": "알 수 없는 상태입니다"}), 400
        try:
            duration = float(body.get("duration", 30))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "수집 시간이 숫자가 아닙니다"}), 400
        if not (5 <= duration <= 600):
            return jsonify({"ok": False, "error": "수집 시간은 5~600초로 넣어주세요"}), 400

        cmd = [sys.executable, "sensor_control.py", "--state", state,
               "--duration", str(duration), "--rate", str(SAMPLE_RATE_HZ)]
        started = _run_steps("collect", f"{state} {duration:g}초 수집",
                             [(f"'{state}' 수집", cmd)], src_dir, pause_live=True)
        if not started:
            return jsonify({"ok": False, "error": "이미 다른 작업이 실행 중입니다"}), 409
        return jsonify({"ok": True, "job": _job_snapshot()})

    @app.route("/api/train", methods=["POST"])
    def api_train():
        body = request.get_json(silent=True) or {}
        task = body.get("task", "3종")
        mode = body.get("mode", "둘다")
        if task not in TASKS or mode not in MODES:
            return jsonify({"ok": False, "error": "알 수 없는 학습 옵션입니다"}), 400

        steps = [("전처리 (필터 → 스펙트로그램 → 특징)", [sys.executable, "preprocess.py"]),
                 (f"학습 ({task} / {mode})",
                  [sys.executable, "train.py", "--task", task, "--mode", mode])]
        # 학습이 끝나면 돌고 있는 분류기에 새 모델을 바로 반영한다(재시작 불필요).
        started = _run_steps("train", f"{task} / {mode} 재학습", steps, src_dir, on_done=_reload)
        if not started:
            return jsonify({"ok": False, "error": "이미 다른 작업이 실행 중입니다"}), 409
        return jsonify({"ok": True, "job": _job_snapshot()})

    # ---- 학습 자료 보기 / 내려받기 / 지우기 -------------------------------
    # 브라우저에서 다룰 수 있는 폴더는 아래 둘로 제한한다. 요청 경로는 반드시
    # realpath로 풀어서 이 안에 있는지 확인한다(../ 로 저장소 밖을 건드리지 못하게).
    root_dir = os.path.abspath(os.path.join(src_dir, ".."))
    ALLOWED_ROOTS = (os.path.join(root_dir, "data"), os.path.join(root_dir, "models"))

    def _safe_path(rel):
        """저장소 기준 상대경로를 검증해 절대경로로 바꾼다. 허용 폴더 밖이면 None."""
        if not rel or os.path.isabs(rel) or "\x00" in rel:
            return None
        full = os.path.realpath(os.path.join(root_dir, rel))
        for base in ALLOWED_ROOTS:
            if full == os.path.realpath(base) or full.startswith(os.path.realpath(base) + os.sep):
                return full
        return None

    def _entry(full):
        st = os.stat(full)
        return {"path": os.path.relpath(full, root_dir).replace(os.sep, "/"),
                "name": os.path.basename(full),
                "size": st.st_size,
                "mtime": int(st.st_mtime)}

    # 스펙트로그램은 수집을 늘리면 수천 장이 될 수 있어 목록을 잘라 보낸다.
    LIST_CAP = 300

    def _listing(rel_dir, exts, recursive=False):
        base = os.path.join(root_dir, rel_dir)
        out, total = [], 0
        if not os.path.isdir(base):
            return out, 0
        walker = os.walk(base) if recursive else [(base, [], sorted(os.listdir(base)))]
        for dirpath, _, names in walker:
            for n in sorted(names):
                full = os.path.join(dirpath, n)
                if os.path.isfile(full) and n.lower().endswith(exts):
                    total += 1
                    if len(out) < LIST_CAP:
                        out.append(_entry(full))
        return out, total

    def _group(key, title, kind, note, rel_dir, exts, recursive=False):
        files, total = _listing(rel_dir, exts, recursive)
        return {"key": key, "title": title, "kind": kind, "note": note,
                "files": files, "total": total}

    @app.route("/api/files")
    def api_files():
        return jsonify({"current_model": os.path.relpath(os.path.realpath(clf.model_path),
                                                         root_dir).replace(os.sep, "/"),
                        "groups": [
            _group("raw", "원시 데이터 (CSV)", "csv",
                   "수집한 전위 신호. 지우고 다시 수집할 수 있어요.", "data/raw", (".csv",)),
            _group("features", "특징 테이블 (CSV)", "csv",
                   "전처리가 뽑은 윈도우별 특징 14개.", "data", (".csv",)),
            _group("spectrogram", "스펙트로그램 이미지", "image",
                   "학습에 쓰인 224x224 이미지. 상태별로 들어 있어요.",
                   "data/spectrogram", (".png",), recursive=True),
            _group("eval", "평가 이미지 (혼동행렬)", "image",
                   "학습 결과 confusion matrix.", "models", (".png",)),
            _group("model", "학습된 모델", "model",
                   "지우면 해당 모델로는 실행할 수 없게 됩니다.", "models", (".joblib",)),
        ]})

    @app.route("/api/files/download")
    def api_files_download():
        full = _safe_path(request.args.get("path", ""))
        if not full or not os.path.isfile(full):
            return jsonify({"ok": False, "error": "없는 파일이거나 접근할 수 없는 경로입니다"}), 404
        as_attachment = request.args.get("view") != "1"
        return send_from_directory(os.path.dirname(full), os.path.basename(full),
                                   as_attachment=as_attachment)

    @app.route("/api/files/delete", methods=["POST"])
    def api_files_delete():
        body = request.get_json(silent=True) or {}
        paths = body.get("paths") or ([body["path"]] if body.get("path") else [])
        if not paths:
            return jsonify({"ok": False, "error": "지울 파일이 없습니다"}), 400
        with _job_lock:
            if _job["running"]:
                return jsonify({"ok": False, "error": "작업이 도는 중에는 지울 수 없습니다"}), 409
        deleted, failed = [], []
        for rel in paths:
            full = _safe_path(rel)
            if not full or not os.path.isfile(full):
                failed.append({"path": rel, "error": "없는 파일이거나 접근할 수 없는 경로"})
                continue
            # 지금 추론에 쓰고 있는 모델 파일은 지우지 못하게 막는다.
            if os.path.realpath(full) == os.path.realpath(clf.model_path):
                failed.append({"path": rel, "error": "지금 사용 중인 모델이라 지울 수 없습니다"})
                continue
            try:
                os.remove(full)
                deleted.append(rel)
            except OSError as e:
                failed.append({"path": rel, "error": str(e)})
        print(f"[web] 파일 삭제: {len(deleted)}개 성공, {len(failed)}개 실패")
        return jsonify({"ok": not failed, "deleted": deleted, "failed": failed})

    @app.route("/api/train/options")
    def api_train_options():
        raw_dir = os.path.join(src_dir, "..", "data", "raw")
        have = sorted(s for s in sensor_control.VALID_STATES
                      if os.path.isfile(os.path.join(raw_dir, sensor_control.KOR_FILENAMES[s])))
        return jsonify({"tasks": list(TASKS), "modes": list(MODES),
                        "states": list(sensor_control.VALID_STATES),
                        "collected": have,
                        "hardware": sensor_control.HARDWARE_AVAILABLE,
                        "model": clf.model_name, "classes": list(clf.classes)})

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
const COLOR={"정상":"#1f9d5f","수분부족":"#1667c2","자극":"#c07216","스트레스":"#c62828"};
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
    parser.add_argument("--sim_state", default="정상", choices=["정상", "수분부족", "자극", "순환"],
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
