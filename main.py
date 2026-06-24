import asyncio
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="Яндекс.Карты Парсер")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://yandex.ru/maps/",
}

def build_maps_url(org_id: str, name: str) -> str:
    safe = name.replace(" ", "%20")
    return f"https://yandex.ru/maps/org/{safe}/{org_id}/"

def parse_org(feature: dict) -> dict:
    props = feature.get("properties", {})
    meta  = props.get("CompanyMetaData", {})
    name     = meta.get("name", "")
    org_id   = meta.get("id", "")
    address  = meta.get("address", "")
    website  = meta.get("url", "")
    phones   = [p.get("formatted", "") for p in meta.get("Phones", [])]
    cats     = [c.get("name", "") for c in meta.get("Categories", [])]
    rating_o = meta.get("rating", {})
    rating   = rating_o.get("ratings", 0)
    reviews  = rating_o.get("reviews", 0)
    city = ""
    if address:
        parts = [p.strip() for p in address.split(",")]
        city = parts[-2] if len(parts) > 2 else parts[0]
    return {
        "title":        name,
        "url":          build_maps_url(org_id, name),
        "website":      website,
        "phones":       phones,
        "address":      address,
        "city":         city,
        "categories":   cats,
        "rating":       rating,
        "ratingsCount": reviews,
        "hasWebsite":   bool(website and "yandex" not in website),
    }

async def search_yandex(query: str, location: str, max_results: int = 10):
    text = f"{query} {location}"
    url  = "https://yandex.ru/maps/api/search/v4/"
    params = {
        "text":    text,
        "type":    "biz",
        "lang":    "ru_RU",
        "results": min(max_results, 50),
        "origin":  "maps-web.serp",
    }
    async with httpx.AsyncClient(headers=HEADERS, timeout=30) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return []
        data = resp.json()
    features = data.get("data", {}).get("features", [])
    results = []
    for f in features[:max_results]:
        try:
            results.append(parse_org(f))
        except Exception:
            continue
    return results

@app.get("/")
async def root():
    return {"status": "ok", "message": "Яндекс.Карты парсер работает"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/search")
async def search(body: dict):
    query      = body.get("query", "").strip()
    location   = body.get("location", "").strip()
    max_res    = int(body.get("maxResults", 10))
    if not query or not location:
        raise HTTPException(status_code=400, detail="query и location обязательны")
    items = await search_yandex(query, location, max_res)
    return {"query": query, "location": location, "count": len(items), "items": items}

@app.post("/search/no_website")
async def search_no_website(body: dict):
    query      = body.get("query", "").strip()
    location   = body.get("location", "").strip()
    max_res    = int(body.get("maxResults", 10))
    if not query or not location:
        raise HTTPException(status_code=400, detail="query и location обязательны")
    all_items = await search_yandex(query, location, max_res * 2)
    filtered  = [b for b in all_items if not b.get("hasWebsite")]
    return {"query": query, "location": location, "count": len(filtered), "items": filtered[:max_res]}
