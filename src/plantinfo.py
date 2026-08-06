"""
plantinfo.py
============
"이 식물에 대해 알아보기" — 식물 정보를 실제로 가져옵니다.

순서대로 시도하고, 되는 것까지만 사용합니다.

1. **위키백과**(ko → en) 요약 API로 개요·학명·사진을 가져옵니다.
2. `ANTHROPIC_API_KEY` 가 있으면 **Claude API**로 키우기 팁을 정리합니다.
3. 둘 다 안 되면(오프라인 등) 내장된 기본 정보를 돌려주고,
   응답에 `source: "내장"` 을 표시해 실제 조회가 아님을 알립니다.

키가 필요한 것은 환경변수로만 받습니다(코드에 넣지 않습니다).

    export ANTHROPIC_API_KEY=...      # 선택: 케어 팁 생성
"""

import os

import netcache

TTL = 60 * 60 * 24 * 7        # 식물 정보는 일주일 캐시

WIKI = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"

# 조회가 안 될 때 쓰는 최소 정보 (프로젝트에서 다루는 식물들)
BUILTIN = {
    "몬스테라": {
        "title": "몬스테라", "species": "Monstera deliciosa",
        "summary": "중앙아메리카 열대우림이 원산지인 관엽식물로, 자라면서 잎에 구멍이 생깁니다. "
                   "밝은 간접광과 높은 습도를 좋아하고 과습에 약합니다.",
        "tips": ["겉흙이 2~3cm 마르면 흠뻑 주세요",
                 "직사광선은 피하고 밝은 간접광에 두세요",
                 "공중 습도 50~60%를 유지해 주세요"],
        "temp": [18, 27], "humidity": [50, 70],
    },
    "뱅갈고무나무": {
        "title": "뱅갈고무나무", "species": "Ficus benghalensis",
        "summary": "인도가 원산지인 큰 나무로, 실내에서는 관엽식물로 기릅니다. 잎이 두껍고 관리가 비교적 쉽습니다.",
        "tips": ["흙이 절반쯤 마르면 주세요", "밝은 곳에 두되 갑작스러운 자리 이동은 피하세요",
                 "잎의 먼지를 닦아주면 광합성에 좋습니다"],
        "temp": [16, 27], "humidity": [40, 60],
    },
    "올리브": {
        "title": "올리브나무", "species": "Olea europaea",
        "summary": "지중해 지역이 원산지인 나무로 햇빛을 많이 필요로 합니다. 건조에는 강하지만 추위에 약합니다.",
        "tips": ["햇빛이 잘 드는 곳에 두세요", "흙이 완전히 마른 뒤에 주세요",
                 "겨울철 야간 온도가 10°C 아래로 내려가지 않게 하세요"],
        "temp": [15, 28], "humidity": [30, 50],
    },
    "스투키": {
        "title": "스투키", "species": "Sansevieria stuckyi",
        "summary": "건조에 매우 강한 다육식물로 공기정화 식물로 알려져 있습니다. 물을 자주 주면 뿌리가 썩습니다.",
        "tips": ["2~3주에 한 번만 주세요", "물이 잎 사이에 고이지 않게 하세요",
                 "반그늘에서도 잘 자랍니다"],
        "temp": [16, 30], "humidity": [20, 50],
    },
}


def _builtin(name):
    for key, info in BUILTIN.items():
        if key in name or name in key:
            return dict(info, source="내장", cached=False)
    return {"title": name, "species": None,
            "summary": "아직 정보를 찾지 못했어요. 인터넷에 연결하면 자동으로 채워집니다.",
            "tips": [], "temp": None, "humidity": None, "source": "내장", "cached": False}


def _wikipedia(name):
    for lang in ("ko", "en"):
        data = netcache.get_json(WIKI.format(lang=lang, title=netcache.quote(name)))
        if not data or data.get("type", "").endswith("not_found"):
            continue
        extract = (data.get("extract") or "").strip()
        if not extract:
            continue
        return {
            "title": data.get("title") or name,
            "summary": extract,
            "photo": (data.get("thumbnail") or {}).get("source"),
            "url": ((data.get("content_urls") or {}).get("desktop") or {}).get("page"),
            "source": "위키백과(%s)" % lang,
        }
    return None


def _claude_tips(name, summary):
    """ANTHROPIC_API_KEY 가 있을 때만 케어 팁을 생성한다."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    model = os.environ.get("CHOROKMAL_MODEL", "claude-sonnet-5")
    prompt = (
        "다음 식물을 실내에서 키울 때 꼭 필요한 관리 팁을 한국어로 3개만 알려줘.\n"
        "각 팁은 한 문장, 25자 이내. 번호나 기호 없이 줄바꿈으로만 구분해서 답해.\n\n"
        "식물: %s\n설명: %s" % (name, (summary or "")[:600])
    )
    res = netcache.post_json(
        base + "/v1/messages",
        {"model": model, "max_tokens": 300,
         "messages": [{"role": "user", "content": prompt}]},
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    if not res:
        return None
    try:
        text = "".join(b.get("text", "") for b in res.get("content", []))
    except AttributeError:
        return None
    tips = [t.strip(" -•*\t") for t in text.splitlines() if t.strip()]
    return tips[:3] or None


def lookup(name):
    """식물 정보를 조회한다. 항상 딕셔너리를 돌려주며 실패해도 예외를 내지 않는다."""
    name = (name or "").strip() or "몬스테라"
    cached = netcache.cache_get("info:" + name, TTL)
    if cached:
        cached["cached"] = True
        return cached

    base = _builtin(name)
    wiki = _wikipedia(name)
    if wiki:
        base.update({k: v for k, v in wiki.items() if v})

    tips = _claude_tips(base.get("title", name), base.get("summary"))
    if tips:
        base["tips"] = tips
        base["source"] = base.get("source", "") + " + Claude"

    base["cached"] = False
    if wiki or tips:          # 실제로 조회한 것만 캐시에 남긴다
        netcache.cache_put("info:" + name, base)
    return base
