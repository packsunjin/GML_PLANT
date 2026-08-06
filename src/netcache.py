"""
netcache.py
===========
외부 요청을 위한 아주 작은 도우미. 표준 라이브러리만 사용합니다.

- 결과를 `data/app/cache/` 에 저장해 같은 요청을 반복하지 않습니다(TTL).
- 네트워크가 없거나 막혀 있어도 예외 대신 None을 돌려주므로,
  호출하는 쪽에서 준비된 대체 데이터로 넘어갈 수 있습니다.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "app", "cache")
UA = "Chorokmal/1.0 (교육용 식물 모니터링 프로젝트)"
TIMEOUT = 8


def _path(key):
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return os.path.join(CACHE_DIR, digest + ".json")


def cache_get(key, ttl):
    """TTL(초) 안에 저장된 값이 있으면 돌려준다."""
    path = _path(key)
    try:
        if time.time() - os.path.getmtime(path) > ttl:
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def cache_put(key, value):
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(_path(key), "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
    except OSError:
        pass          # 캐시는 실패해도 기능에 지장이 없다


def get_json(url, headers=None, timeout=TIMEOUT):
    """JSON을 받아온다. 실패하면 None."""
    req = urllib.request.Request(url, headers=dict({"User-Agent": UA}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, ValueError, OSError):
        return None


def post_json(url, payload, headers=None, timeout=20):
    """JSON을 POST하고 JSON을 받는다. 실패하면 None."""
    body = json.dumps(payload).encode("utf-8")
    head = dict({"User-Agent": UA, "Content-Type": "application/json"}, **(headers or {}))
    req = urllib.request.Request(url, data=body, headers=head, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, ValueError, OSError):
        return None


def quote(text):
    return urllib.parse.quote(str(text))
