"""
store.py
========
대시보드가 남기는 기록(이벤트·식물·설정)을 파일로 보관합니다.

하드웨어가 없어도 동작하도록 데이터베이스 없이 파일만 사용합니다.

    data/app/events.jsonl   상태 변화·물주기 등 이벤트 로그 (한 줄에 JSON 하나)
    data/app/plants.json    등록한 식물 목록
    data/app/settings.json  알림 설정

이벤트는 한 줄씩 덧붙이기만 하므로, 실행 중 파일이 깨질 걱정 없이
여러 번 실행해도 기록이 이어집니다.
"""

import json
import os
import threading
from collections import OrderedDict
from datetime import datetime, timedelta

_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "app")
_EVENTS = os.path.join(_DIR, "events.jsonl")
_PLANTS = os.path.join(_DIR, "plants.json")
_SETTINGS = os.path.join(_DIR, "settings.json")

# log_state()가 락을 잡은 채 read_events()를 호출하므로 재진입 가능한 락이어야 한다.
_lock = threading.RLock()

# 같은 상태가 이어질 때 매초 기록하지 않도록, 이 간격 안에서는 다시 남기지 않는다.
_STATE_REPEAT_SEC = 600

DEFAULT_PLANTS = [
    {"id": "monstera", "name": "거실 몬스테라", "species": "Monstera deliciosa",
     "photo": "/static/img/photo-1614594975525.jpg", "temp": 24.5, "humidity": 32},
    {"id": "ficus", "name": "침실 뱅갈고무나무", "species": "Ficus benghalensis",
     "photo": "/static/img/photo-1602923668104.jpg", "temp": 23.1, "humidity": 61},
    {"id": "olive", "name": "베란다 올리브", "species": "Olea europaea",
     "photo": "/static/img/photo-1593482892290.jpg", "temp": 19.4, "humidity": 44},
]

DEFAULT_SETTINGS = {
    "수분부족 경고": True,
    "타격 이벤트": True,
    "최저가 알림": True,
    "주간 리포트": False,
}


def _ensure_dir():
    os.makedirs(_DIR, exist_ok=True)


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, obj):
    _ensure_dir()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)   # 쓰다 만 파일이 남지 않도록 원자적으로 교체


# ---------------------------------------------------------------- 이벤트

def _append(event):
    _ensure_dir()
    with open(_EVENTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_events(limit=200):
    """최근 이벤트를 새 것부터 반환한다."""
    if not os.path.isfile(_EVENTS):
        return []
    with _lock:
        try:
            with open(_EVENTS, encoding="utf-8") as f:
                lines = f.readlines()[-limit:]
        except OSError:
            return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue          # 쓰다 만 줄은 건너뛴다
    out.reverse()
    return out


def _last_state_event():
    for ev in read_events(80):
        if ev.get("kind") == "state":
            return ev
    return None


def log_state(state, proba=None, plant="monstera"):
    """분류 상태를 기록한다. 상태가 그대로면 일정 시간 동안은 남기지 않는다.

    기록했으면 True, 건너뛰었으면 False.
    """
    now = datetime.now()
    with _lock:
        last = _last_state_event()
    if last and last.get("state") == state:
        try:
            gap = (now - datetime.fromisoformat(last["ts"])).total_seconds()
        except (ValueError, KeyError):
            gap = _STATE_REPEAT_SEC + 1
        if gap < _STATE_REPEAT_SEC:
            return False
    with _lock:
        _append({"ts": now.isoformat(timespec="seconds"), "kind": "state",
                 "state": state, "proba": proba, "plant": plant})
    return True


def log_water(plant="monstera", ml=280):
    ev = {"ts": datetime.now().isoformat(timespec="seconds"), "kind": "water",
          "plant": plant, "ml": ml}
    with _lock:
        _append(ev)
    return ev


def daily_summary(days=7, plant=None):
    """최근 N일을 하루 단위로 묶어 요약한다(오래된 날부터).

    각 날짜마다 상태별 횟수, 물 준 횟수, 대표 상태를 담는다.
    """
    today = datetime.now().date()
    start = today - timedelta(days=days - 1)
    buckets = OrderedDict()
    for i in range(days):
        d = start + timedelta(days=i)
        buckets[d.isoformat()] = {"date": d.isoformat(),
                                  "weekday": "월화수목금토일"[d.weekday()],
                                  "states": {}, "water": 0, "top": None}

    for ev in read_events(4000):
        if plant and ev.get("plant") not in (None, plant):
            continue
        try:
            day = datetime.fromisoformat(ev["ts"]).date().isoformat()
        except (ValueError, KeyError):
            continue
        if day not in buckets:
            continue
        b = buckets[day]
        if ev.get("kind") == "state":
            s = ev.get("state")
            b["states"][s] = b["states"].get(s, 0) + 1
        elif ev.get("kind") == "water":
            b["water"] += 1

    for b in buckets.values():
        if b["states"]:
            b["top"] = max(b["states"].items(), key=lambda kv: kv[1])[0]
    return list(buckets.values())


def stats():
    """홈·랭킹 화면에서 쓰는 누적 수치."""
    evs = read_events(4000)
    water = [e for e in evs if e.get("kind") == "water"]
    states = [e for e in evs if e.get("kind") == "state"]
    healthy = sum(1 for e in states if e.get("state") == "정상")
    days = {e["ts"][:10] for e in evs if "ts" in e}
    return {
        "records": len(evs),
        "water_count": len(water),
        "state_count": len(states),
        "healthy_ratio": round(healthy / len(states) * 100) if states else None,
        "active_days": len(days),
    }


# ---------------------------------------------------------------- 식물·설정

def plants():
    data = _read_json(_PLANTS, None)
    if not isinstance(data, list) or not data:
        _write_json(_PLANTS, DEFAULT_PLANTS)
        return list(DEFAULT_PLANTS)
    return data


def save_plants(items):
    _write_json(_PLANTS, items)
    return items


def settings():
    data = _read_json(_SETTINGS, None)
    if not isinstance(data, dict):
        _write_json(_SETTINGS, DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)
    merged = dict(DEFAULT_SETTINGS)
    merged.update(data)
    return merged


def save_settings(values):
    current = settings()
    current.update({k: bool(v) for k, v in values.items() if k in DEFAULT_SETTINGS})
    _write_json(_SETTINGS, current)
    return current
