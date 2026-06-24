import re
import os
import httpx
from fastapi import FastAPI, HTTPException

app = FastAPI(title="Лид-бот v3")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://yandex.ru/maps/",
}

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


async def search_yandex(query: str, location: str, max_results: int = 10):
    results = []
    text = f"{query} {location}"
    url = "https://yandex.ru/maps/api/search"
    params = {"text": text, "type": "biz", "lang": "ru_RU", "results": min(max_results * 2, 50), "origin": "maps-web.serp", "ajax": 1}
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()
    except Exception:
        return []

    features = []
    if isinstance(data, dict):
        features = data.get("data", {}).get("features", []) or data.get("features", []) or []

    for feature in features[:max_results]:
        try:
            props = feature.get("properties", {})
            meta = props.get("CompanyMetaData", {})
            name = meta.get("name", "") or props.get("name", "")
            if not name:
                continue
            org_id = meta.get("id", "")
            address = meta.get("address", "") or props.get("description", "")
            website = meta.get("url", "")
            phones = [p.get("formatted", "") for p in meta.get("Phones", []) if p.get("formatted")]
            categories = [c.get("name", "") for c in meta.get("Categories", []) if c.get("name")]
            rating_obj = meta.get("rating", {})
            rating = rating_obj.get("ratings", 0) or 0
            reviews = rating_obj.get("reviews", 0) or 0
            city = location
            if address:
                parts = [p.strip() for p in address.split(",")]
                if len(parts) >= 2:
                    city = parts[-2]
            results.append({
                "title": name,
                "url": f"https://yandex.ru/maps/org/{org_id}/" if org_id else "",
                "website": website,
                "phones": phones,
                "address": address,
                "city": city,
                "categories": categories,
                "rating": rating,
                "ratingsCount": reviews,
                "hasWebsite": bool(website and "yandex" not in website),
            })
        except Exception:
            continue
    return results


async def search_via_suggest(query: str, location: str, max_results: int = 10):
    results = []
    text = f"{query} {location}"
    url = "https://suggest-maps.yandex.ru/suggest-geo"
    params = {"text": text, "lang": "ru_RU", "n": max_results, "v": 9, "type": "biz"}
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20) as client:
            resp = await client.get(url, params=params)
            data = resp.json()
    except Exception:
        return []
    for item in data.get("results", [])[:max_results]:
        try:
            name = item.get("title", {}).get("text", "")
            if not name:
                continue
            subtitle = item.get("subtitle", {}).get("text", "")
            uri = item.get("uri", "")
            org_id = ""
            if "org/" in uri:
                parts = uri.split("org/")
                if len(parts) > 1:
                    org_id = parts[1].rstrip("/").split("/")[0]
            results.append({
                "title": name,
                "url": f"https://yandex.ru/maps/org/{org_id}/" if org_id else uri,
                "website": "",
                "phones": [],
                "address": subtitle,
                "city": location,
                "categories": [],
                "rating": 0,
                "ratingsCount": 0,
                "hasWebsite": False,
            })
        except Exception:
            continue
    return results


async def score_with_claude(biz: dict, api_key: str) -> dict:
    """Вызываем Claude и возвращаем готовый скоринг."""
    prompt = f"""Ты — AI-аналитик лидов для продажи сайтов и AI-автоматизаций малому бизнесу.

Данные бизнеса из Яндекс Карт:
Название: {biz.get('title', '')}
Категория: {', '.join(biz.get('categories', []))}
Город: {biz.get('city', '')}
Адрес: {biz.get('address', '')}
Телефон: {', '.join(biz.get('phones', []))}
Сайт: {biz.get('website', '')}
Рейтинг: {biz.get('rating', 0)}
Количество отзывов: {biz.get('ratingsCount', 0)}
Ссылка на карточку: {biz.get('url', '')}

Проанализируй бизнес:
1. Есть ли полноценный сайт (ссылка на Яндекс Карты НЕ считается сайтом)
2. Оцени перспективность для предложения сайта или AI-автоматизации
3. Поставь оценку лида от 1 до 10
4. Определи статус: горячий / средний / слабый
5. Напиши короткий вердикт
6. Предложи что именно продать
7. Напиши первое сообщение клиенту — коротко, вежливо, персонально

Верни ТОЛЬКО JSON объект без какого-либо текста вокруг него:
{{"est_sait": "", "ocenka_lida": "", "status_lida": "", "verdikt": "", "chto_predlozhit": "", "soobshchenie_klientu": ""}}"""

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 700,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
            if resp.status_code != 200:
                return {}
            data = resp.json()
            text = data.get("content", [{}])[0].get("text", "")
            # Очищаем от markdown если есть
            text = re.sub(r'```json\s*', '', text)
            text = re.sub(r'```\s*', '', text)
            text = text.strip()
            # Ищем JSON объект
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                import json
                return json.loads(match.group())
    except Exception:
        pass
    return {}


@app.get("/")
async def root():
    return {"status": "ok", "message": "Лид-бот v3 работает"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/find_leads")
async def find_leads(body: dict):
    """
    Главный эндпоинт — ищет бизнесы И скорит их через Claude.
    Make.com вызывает только этот один модуль.

    Запрос: {"query": "кафе", "location": "Серов", "maxResults": 5, "claude_key": "sk-ant-..."}
    Ответ: список лидов с готовым скорингом
    """
    query = body.get("query", "").strip()
    location = body.get("location", "").strip()
    max_res = int(body.get("maxResults", 5))
    api_key = body.get("claude_key", "") or ANTHROPIC_API_KEY

    if not query or not location:
        raise HTTPException(status_code=400, detail="query и location обязательны")
    if not api_key:
        raise HTTPException(status_code=400, detail="claude_key обязателен")

    # Ищем бизнесы
    items = await search_yandex(query, location, max_res)
    if not items:
        items = await search_via_suggest(query, location, max_res)

    if not items:
        return {"query": query, "location": location, "count": 0, "leads": []}

    # Берём первый бизнес и скорим через Claude
    biz = items[0]
    scoring = await score_with_claude(biz, api_key)

    # Собираем итоговый результат
    lead = {
        "title": biz.get("title", ""),
        "url": biz.get("url", ""),
        "website": biz.get("website", ""),
        "phones": ", ".join(biz.get("phones", [])),
        "address": biz.get("address", ""),
        "city": biz.get("city", ""),
        "categories": ", ".join(biz.get("categories", [])),
        "rating": biz.get("rating", 0),
        "ratingsCount": biz.get("ratingsCount", 0),
        # Скоринг от Claude
        "est_sait": scoring.get("est_sait", ""),
        "ocenka_lida": scoring.get("ocenka_lida", ""),
        "status_lida": scoring.get("status_lida", ""),
        "verdikt": scoring.get("verdikt", ""),
        "chto_predlozhit": scoring.get("chto_predlozhit", ""),
        "soobshchenie_klientu": scoring.get("soobshchenie_klientu", ""),
    }

    return {
        "query": query,
        "location": location,
        "count": len(items),
        "lead": lead,
        "all_items": items,
    }


@app.post("/search")
async def search(body: dict):
    """Простой поиск без скоринга — для совместимости."""
    query = body.get("query", "").strip()
    location = body.get("location", "").strip()
    max_res = int(body.get("maxResults", 10))
    if not query or not location:
        raise HTTPException(status_code=400, detail="query и location обязательны")
    items = await search_yandex(query, location, max_res)
    if not items:
        items = await search_via_suggest(query, location, max_res)
    return {"query": query, "location": location, "count": len(items), "items": items}
