import asyncio
import httpx
from fastapi import FastAPI, HTTPException

app = FastAPI(title="Яндекс.Карты Парсер v2")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://yandex.ru/maps/",
    "Origin": "https://yandex.ru",
}

async def search_yandex(query: str, location: str, max_results: int = 10):
    results = []
    text = f"{query} {location}"
    url = "https://yandex.ru/maps/api/search"
    params = {
        "text": text,
        "type": "biz",
        "lang": "ru_RU",
        "results": min(max_results * 2, 50),
        "origin": "maps-web.serp",
        "ajax": 1,
    }
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
        features = (
            data.get("data", {}).get("features", []) or
            data.get("features", []) or
            data.get("results", []) or
            []
        )

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
            maps_url = f"https://yandex.ru/maps/org/{org_id}/" if org_id else ""
            results.append({
                "title": name,
                "url": maps_url,
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


async def search_via_geocoder(query: str, location: str, max_results: int = 10):
    results = []
    text = f"{query} {location}"
    url = "https://suggest-maps.yandex.ru/suggest-geo"
    params = {
        "text": text,
        "lang": "ru_RU",
        "n": max_results,
        "v": 9,
        "type": "biz",
    }
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


@app.get("/")
async def root():
    return {"status": "ok", "message": "Яндекс.Карты парсер v2 работает"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/search")
async def search(body: dict):
    query = body.get("query", "").strip()
    location = body.get("location", "").strip()
    max_res = int(body.get("maxResults", 10))
    if not query or not location:
        raise HTTPException(status_code=400, detail="query и location обязательны")
    items = await search_yandex(query, location, max_res)
    if not items:
        items = await search_via_geocoder(query, location, max_res)
    return {"query": query, "location": location, "count": len(items), "items": items}

@app.post("/search/no_website")
async def search_no_website(body: dict):
    query = body.get("query", "").strip()
    location = body.get("location", "").strip()
    max_res = int(body.get("maxResults", 10))
    if not query or not location:
        raise HTTPException(status_code=400, detail="query и location обязательны")
    all_items = await search_yandex(query, location, max_res * 2)
    if not all_items:
        all_items = await search_via_geocoder(query, location, max_res * 2)
    filtered = [b for b in all_items if not b.get("hasWebsite")]
    return {"query": query, "location": location, "count": len(filtered), "items": filtered[:max_res]}
