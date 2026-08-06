"""
market.py
=========
"전국 최저가 식물 마켓" — 식물 가격을 실제로 모아 최저가를 찾습니다.

가격은 **네이버 쇼핑 검색 오픈 API**로 가져옵니다. 쇼핑몰 HTML을 직접 긁으면
사이트 구조가 바뀔 때마다 깨지고 이용약관에도 걸리기 때문에, 공개된 API를
사용하고 키가 없으면 조회를 시도하지 않습니다.

    # https://developers.naver.com 에서 애플리케이션 등록 후
    export NAVER_CLIENT_ID=...
    export NAVER_CLIENT_SECRET=...

키가 없거나 네트워크가 막혀 있으면 내장된 예시 시세를 돌려주고,
응답에 `source: "예시"` 를 표시해 실제 시세가 아님을 알립니다.
"""

import os
import re

import netcache

TTL = 60 * 30                 # 시세는 30분 캐시
API = "https://openapi.naver.com/v1/search/shop.json?query={q}&display=30&sort=asc"

# 조회가 안 될 때 보여줄 예시 (실제 시세가 아님)
SAMPLE = {
    "몬스테라": [
        {"mall": "양재 꽃시장 그린하우스", "title": "몬스테라 델리시오사 중형", "price": 18900},
        {"mall": "대구 화훼공판장", "title": "몬스테라 15cm 화분", "price": 19500},
        {"mall": "초록마을 플랜트", "title": "몬스테라 델리시오사", "price": 21000},
    ],
    "스투키": [
        {"mall": "청주 육거리 화원", "title": "스투키 산세베리아 3대", "price": 9800},
        {"mall": "인천 계양 정원", "title": "스투키 중형", "price": 10500},
        {"mall": "울산 삼산 플랜트", "title": "스투키 산세베리아", "price": 11900},
    ],
    "올리브": [
        {"mall": "제주 애월 그린팜", "title": "올리브나무 소형 12cm", "price": 12500},
        {"mall": "천안 온누리원예", "title": "올리브나무 묘목", "price": 13900},
        {"mall": "서울 강서 플라워", "title": "올리브나무", "price": 15400},
    ],
    "뱅갈고무나무": [
        {"mall": "김해 대성농원", "title": "뱅갈고무나무 대형 120cm", "price": 42000},
        {"mall": "고양 원당화훼단지", "title": "뱅갈고무나무 대형", "price": 44500},
        {"mall": "광주 말바우 화원", "title": "뱅갈고무나무", "price": 49000},
    ],
}

_TAGS = re.compile(r"<[^>]+>")


def _clean(text):
    """네이버 응답의 <b> 강조 태그와 HTML 엔티티를 정리한다."""
    text = _TAGS.sub("", text or "")
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(a, b)
    return text.strip()


def _naver(query):
    cid = os.environ.get("NAVER_CLIENT_ID")
    secret = os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and secret):
        return None
    data = netcache.get_json(
        API.format(q=netcache.quote(query)),
        headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret},
    )
    if not data or not data.get("items"):
        return None

    items = []
    for it in data["items"]:
        try:
            price = int(it.get("lprice") or 0)
        except ValueError:
            continue
        if price <= 0:
            continue
        items.append({
            "mall": _clean(it.get("mallName")) or "판매처",
            "title": _clean(it.get("title")),
            "price": price,
            "link": it.get("link"),
            "photo": it.get("image"),
        })
    return items or None


def _sample(query):
    for key, items in SAMPLE.items():
        if key in query or query in key:
            return [dict(it) for it in items]
    return [dict(it) for it in SAMPLE["몬스테라"]]


def search(query, limit=8):
    """식물 이름으로 시세를 찾아 최저가순으로 돌려준다.

    돌려주는 값:
        query, items(최저가순), lowest, average, source("네이버쇼핑"/"예시"), cached
    """
    query = (query or "").strip() or "몬스테라"
    cached = netcache.cache_get("market:" + query, TTL)
    if cached:
        cached["cached"] = True
        return cached

    items, source = _naver(query), "네이버쇼핑"
    if not items:
        items, source = _sample(query), "예시"

    items.sort(key=lambda it: it["price"])
    items = items[:limit]
    prices = [it["price"] for it in items]
    result = {
        "query": query,
        "items": items,
        "lowest": prices[0] if prices else None,
        "average": round(sum(prices) / len(prices)) if prices else None,
        "source": source,
        "cached": False,
    }
    if source == "네이버쇼핑":
        netcache.cache_put("market:" + query, result)
    return result
