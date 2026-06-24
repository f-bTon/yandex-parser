"""
Яндекс.Карты парсер — замена Apify
Запускается как веб-сервис, принимает запросы от Make.com

Вход:  POST /search  { "query": "автосервис", "location": "Серов", "maxResults": 10 }
Выход: JSON массив бизнесов в том же формате, что давал Apify
"""

import re
import json
import time
import random
import asyncio
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Яндекс.Карты Парсер")

# ── модель запроса ──────────────────────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str          # ниша, например "автосервис"
    location: str       # город, например "Серов"
    maxResults: int = 10

# ── хелперы ─────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://yandex.ru/maps/",
}

def extract_website(org: dict) -> str:
    """Достаём сайт из данных организации."""
    links = org.get("links", [])
    for link in links:
        if link.get("type") == "website":
            return link.get("uri", "")
    # иногда сайт лежит в другом месте
    properties = org.get("properties", {})
    return properties.get("CompanyMetaData", {}).get("url", "")

def extract_phones(org: dict) -> list:
    """Достаём телефоны."""
    phones = []
    for p in org.get("phones", []):
        formatted = p.get("formatted", "")
        if formatted:
            phones.append(formatted)
    return phones

def build_maps_url(org_id: str, name: str) -> str:
    """Строим прямую ссылку на карточку бизнеса."""
    safe_name = name.replace(" ", "%20")
    return f"https://yandex.ru/maps/org/{safe_name}/{org_id}/"

def parse_org(feature: dict) -> dict:
    """Преобразуем сырые данные Яндекса в формат как у Apify."""
    props = feature.get("properties", {})
    meta = props.get("CompanyMetaData", {})
    geo  = feature.get("geometry", {}).get("coordinates", [0, 0])

    name     = meta.get("name", "")
    org_id   = meta.get("id", "")
    address  = meta.get("address", "")
    website  = meta.get("url", "")
    phones   = [p.get("formatted", "") for p in meta.get("Phones", [])]
    categories = [c.get("name", "") for c in meta.get("Categories", [])]
    hours    = meta.get("Hours", {}).get("text", "")

    rating_obj = meta.get("rating", {})
    rating     = rating_obj.get("ratings", 0)
    reviews    = rating_obj.get("reviews", 0)

    # город из адреса (первая часть до запятой, обычно страна/регион — берём правильно)
    city = ""
    if address:
        parts = [p.strip() for p in address.split(",")]
        # ищем часть похожую на город (обычно 2-3 с конца в российских адресах)
        if len(parts) >= 2:
            city = parts[-2] if len(parts) > 2 else parts[0]

    return {
        "title":       name,
        "url":         build_maps_url(org_id, name),
        "website":     website,
        "phones":      phones,
        "address":     address,
        "city":        city,
        "categories":  categories,
        "rating":      rating,
        "ratingsCount": reviews,
        "hours":       hours,
        "coordinates": {"lat": geo[1], "lng": geo[0]},
        "hasWebsite":  bool(website and "yandex" not in website),
    }

# ── основной парсер ──────────────────────────────────────────────────────────
async def search_yandex_maps(query: str, location: str, max_results: int = 10) -> list:
    """
    Делаем запрос к поисковому API Яндекс.Карт.
    Возвращаем список организаций.
    """
    results = []
    text = f"{query} {location}"

    # Яндекс.Карты отдают данные через свой внутренний API
    url = "https://yandex.ru/maps/api/search/v4/"
    params = {
        "text":    text,
        "type":    "biz",
        "lang":    "ru_RU",
        "results": min(max_results, 50),
        "origin":  "maps-web.serp",
    }

    async with httpx.AsyncClient(headers=HEADERS, timeout=30) as client:
        try:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Яндекс вернул статус {resp.status_code}"
                )
            data = resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Ошибка запроса: {e}")

    features = (
        data
        .get("data", {})
        .get("features", [])
    )

    for feature in features[:max_results]:
        try:
            org = parse_org(feature)
            results.append(org)
            # небольшая задержка между обработкой, чтобы не спамить
            await asyncio.sleep(0.05)
        except Exception:
            continue  # пропускаем битые записи, не ломаем весь ответ

    return results

# ── запасной парсер через suggest API ───────────────────────────────────────
async def search_via_suggest(query: str, location: str, max_results: int = 10) -> list:
    """Запасной вариант если основной API не ответил."""
    results = []
    text = f"{query} {location}"

    url = "https://suggest-maps.yandex.ru/suggest-geo"
    params = {
        "text":    text,
        "lang":    "ru_RU",
        "n":       max_results,
        "type":    "biz",
        "v":       9,
    }

    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as client:
        try:
            resp = await client.get(url, params=params)
            data = resp.json()
        except Exception:
            return []

    for item in data.get("results", [])[:max_results]:
        tags = item.get("tags", [])
        if "business" not in tags and "biz" not in tags:
            continue
        name    = item.get("title", {}).get("text", "")
        address = item.get("subtitle", {}).get("text", "")
        uri     = item.get("uri", "")
        # достаём id из uri
        org_id = ""
        if "org/" in uri:
            parts = uri.split("org/")
            if len(parts) > 1:
                org_id = parts[1].rstrip("/")

        results.append({
            "title":        name,
            "url":          build_maps_url(org_id, name) if org_id else uri,
            "website":      "",
            "phones":       [],
            "address":      address,
            "city":         location,
            "categories":   [],
            "rating":       0,
            "ratingsCount": 0,
            "hours":        "",
            "coordinates":  {"lat": 0, "lng": 0},
            "hasWebsite":   False,
        })

    return results

# ── эндпоинты ────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "ok", "message": "Яндекс.Карты парсер работает"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/search")
async def search(req: SearchRequest):
    """
    Главный эндпоинт — Make.com вызывает его вместо Apify.

    Пример запроса:
    POST /search
    {
        "query": "автосервис",
        "location": "Серов",
        "maxResults": 10
    }
    """
    if not req.query.strip() or not req.location.strip():
        raise HTTPException(status_code=400, detail="query и location обязательны")

    # сначала пробуем основной API
    results = await search_yandex_maps(req.query, req.location, req.maxResults)

    # если ничего не нашли — пробуем запасной
    if not results:
        results = await search_via_suggest(req.query, req.location, req.maxResults)

    return {
        "query":    req.query,
        "location": req.location,
        "count":    len(results),
        "items":    results,
    }

@app.post("/search/no_website")
async def search_no_website(req: SearchRequest):
    """
    Поиск только тех бизнесов, у которых нет сайта.
    Удобно для твоей задачи — находить лиды сразу.
    """
    all_results = await search_yandex_maps(req.query, req.location, req.maxResults * 2)

    # фильтруем — только те, у кого нет нормального сайта
    no_website = [
        biz for biz in all_results
        if not biz.get("hasWebsite") or not biz.get("website")
    ]

    return {
        "query":    req.query,
        "location": req.location,
        "count":    len(no_website),
        "items":    no_website[:req.maxResults],
    }
