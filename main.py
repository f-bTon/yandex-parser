import os
import httpx
from fastapi import FastAPI, HTTPException

app = FastAPI(title="Лид-бот 2GIS v2")

TWOGIS_KEY = os.getenv("TWOGIS_API_KEY", "")

async def search_2gis(query: str, location: str, max_results: int = 5):
    url = "https://catalog.api.2gis.com/3.0/items"
    params = {
        "q": f"{query} {location}",
        "key": TWOGIS_KEY,
        "type": "branch",
        "locale": "ru_RU",
        "page_size": min(max_results, 20),
        "fields": "items.contact_groups,items.schedule,items.description,items.external_content,items.rubrics,items.reviews",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()
    except Exception:
        return []

    items = data.get("result", {}).get("items", [])
    results = []

    for item in items[:max_results]:
        try:
            name = item.get("name", "")
            if not name:
                continue

            address = item.get("address_name", "") or item.get("full_name", "")
            phones, website, social = [], "", []

            for group in item.get("contact_groups", []):
                for contact in group.get("contacts", []):
                    ct = contact.get("type", "")
                    val = contact.get("value", "")
                    if ct == "phone":
                        phones.append(val)
                    elif ct == "website":
                        website = val
                    elif ct in ("vkontakte", "instagram", "facebook", "telegram", "whatsapp"):
                        social.append(f"{ct}: {val}")

            rubrics = [r.get("name", "") for r in item.get("rubrics", []) if r.get("name")]
            description = item.get("description", "")
            reviews_obj = item.get("reviews", {})
            rating = reviews_obj.get("general_rating", 0)
            reviews_count = reviews_obj.get("general_review_count", 0)

            days_map = {"Mon":"Пн","Tue":"Вт","Wed":"Ср","Thu":"Чт","Fri":"Пт","Sat":"Сб","Sun":"Вс"}
            schedule = item.get("schedule", {})
            sched_parts = []
            for day_en, day_ru in days_map.items():
                day_data = schedule.get(day_en, {})
                if day_data.get("working"):
                    wh = day_data.get("working_hours", [{}])[0]
                    sched_parts.append(f"{day_ru}: {wh.get('from','')}-{wh.get('to','')}")
            schedule_text = ", ".join(sched_parts)

            photos = []
            for ext in item.get("external_content", []):
                if ext.get("type") == "photo":
                    for p in ext.get("photos", [])[:3]:
                        if p.get("preview_url"):
                            photos.append(p["preview_url"])

            obj_id = item.get("id", "")
            maps_url = f"https://2gis.ru/firm/{obj_id}" if obj_id else ""

            results.append({
                "name": name,
                "address": address,
                "city": location,
                "phones": ", ".join(phones),
                "website": website,
                "social": ", ".join(social),
                "categories": ", ".join(rubrics),
                "description": description[:300] if description else "",
                "rating": str(rating),
                "reviews_count": str(reviews_count),
                "schedule": schedule_text,
                "photos": "\n".join(photos),
                "maps_url": maps_url,
                "has_website": "✅ есть" if website else "❌ нет",
            })
        except Exception:
            continue

    return results


def format_card(biz: dict) -> str:
    lines = [
        "📋 ДАННЫЕ ДЛЯ СБОРКИ САЙТА",
        "─────────────────────────",
        f"🏢 {biz['name']}",
        f"📂 {biz['categories'] or '—'}",
        f"📍 {biz['address']}",
        f"📞 {biz['phones'] or '—'}",
        f"🌐 Сайт: {biz['website'] or 'нет'}",
    ]
    if biz['social']:
        lines.append(f"📱 {biz['social']}")
    if biz['rating'] and biz['rating'] != '0':
        lines.append(f"⭐ {biz['rating']} ({biz['reviews_count']} отзывов)")
    if biz['schedule']:
        lines.append(f"🕐 {biz['schedule']}")
    if biz['description']:
        lines.append(f"📝 {biz['description']}")
    lines.append(f"🔗 {biz['maps_url']}")
    if biz['photos']:
        lines.append("─────────────────────────")
        lines.append("📸 Фото:")
        for url in biz['photos'].split("\n")[:3]:
            lines.append(f"  {url}")
    lines.append("─────────────────────────")
    lines.append(f"💻 Сайт: {biz['has_website']}")
    return "\n".join(lines)


@app.get("/")
async def root():
    return {"status": "ok", "message": "Лид-бот 2GIS v2 работает"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/leads")
async def get_leads(body: dict):
    query = body.get("query", "").strip()
    location = body.get("location", "").strip()
    max_res = int(body.get("maxResults", 5))

    if not query or not location:
        raise HTTPException(status_code=400, detail="query и location обязательны")
    if not TWOGIS_KEY:
        raise HTTPException(status_code=500, detail="TWOGIS_API_KEY не задан")

    items = await search_2gis(query, location, max_res)

    if not items:
        return {
            "found": False,
            "count": 0,
            "card_text": "❌ Ничего не найдено по запросу",
            "name": "", "address": "", "city": location,
            "phones": "", "website": "", "social": "",
            "categories": "", "description": "", "rating": "",
            "reviews_count": "", "schedule": "", "photos": "",
            "maps_url": "", "has_website": "❌ нет",
            "all_names": ""
        }

    # Первый лид — основной
    main = items[0]
    # Все найденные названия — для информации
    all_names = "\n".join([f"{i+1}. {b['name']}" for i, b in enumerate(items)])

    return {
        "found": True,
        "count": len(items),
        "card_text": format_card(main),
        "all_names": all_names,
        **main
    }
