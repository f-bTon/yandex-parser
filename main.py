import os
import re
import httpx
from fastapi import FastAPI, HTTPException

app = FastAPI(title="Лид-бот 2GIS v1")

TWOGIS_KEY = os.getenv("TWOGIS_API_KEY", "")

async def search_2gis(query: str, location: str, max_results: int = 5):
    """Ищем бизнесы через официальный 2GIS Places API."""
    results = []

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

    for item in items[:max_results]:
        try:
            name = item.get("name", "")
            if not name:
                continue

            # Адрес
            address_name = item.get("address_name", "")
            full_address = item.get("full_name", address_name)

            # Телефоны и сайт из contact_groups
            phones = []
            website = ""
            social = []
            for group in item.get("contact_groups", []):
                for contact in group.get("contacts", []):
                    ctype = contact.get("type", "")
                    value = contact.get("value", "")
                    if ctype == "phone":
                        phones.append(value)
                    elif ctype == "website":
                        website = value
                    elif ctype in ("vkontakte", "instagram", "facebook", "telegram", "whatsapp"):
                        social.append(f"{ctype}: {value}")

            # Рубрики/категории
            rubrics = [r.get("name", "") for r in item.get("rubrics", []) if r.get("name")]

            # Описание
            description = item.get("description", "")

            # Рейтинг и отзывы
            reviews_obj = item.get("reviews", {})
            rating = reviews_obj.get("general_rating", 0)
            reviews_count = reviews_obj.get("general_review_count", 0)

            # Режим работы
            schedule = item.get("schedule", {})
            schedule_text = ""
            days_map = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Вс"}
            schedule_lines = []
            for day_en, day_ru in days_map.items():
                day_data = schedule.get(day_en, {})
                if day_data.get("working"):
                    working_hours = day_data.get("working_hours", [])
                    if working_hours:
                        wh = working_hours[0]
                        schedule_lines.append(f"{day_ru}: {wh.get('from','')}-{wh.get('to','')}")
                else:
                    schedule_lines.append(f"{day_ru}: выходной")
            schedule_text = ", ".join(schedule_lines)

            # Фото
            photos = []
            for ext in item.get("external_content", []):
                if ext.get("type") == "photo":
                    for photo in ext.get("photos", [])[:3]:
                        preview = photo.get("preview_url", "")
                        if preview:
                            photos.append(preview)

            # Ссылка на 2GIS
            obj_id = item.get("id", "")
            maps_url = f"https://2gis.ru/firm/{obj_id}" if obj_id else ""

            results.append({
                "name": name,
                "address": full_address,
                "city": location,
                "phones": phones,
                "website": website,
                "social": social,
                "categories": rubrics,
                "description": description,
                "rating": rating,
                "reviews_count": reviews_count,
                "schedule": schedule_text,
                "photos": photos,
                "maps_url": maps_url,
                "has_website": bool(website),
            })
        except Exception:
            continue

    return results


def format_lead_card(biz: dict) -> str:
    """Форматируем карточку лида для Telegram — чтобы копировать и вставлять мне."""
    lines = []
    lines.append("📋 ДАННЫЕ ДЛЯ СБОРКИ САЙТА")
    lines.append("─" * 30)
    lines.append(f"🏢 Название: {biz['name']}")
    lines.append(f"📂 Категория: {', '.join(biz['categories']) or '—'}")
    lines.append(f"📍 Адрес: {biz['address']}")
    lines.append(f"📞 Телефон: {', '.join(biz['phones']) or '—'}")
    lines.append(f"🌐 Сайт: {biz['website'] or 'нет'}")

    if biz['social']:
        lines.append(f"📱 Соцсети: {', '.join(biz['social'])}")

    if biz['rating']:
        lines.append(f"⭐ Рейтинг: {biz['rating']} ({biz['reviews_count']} отзывов)")

    if biz['schedule']:
        lines.append(f"🕐 Режим работы: {biz['schedule']}")

    if biz['description']:
        lines.append(f"📝 Описание: {biz['description'][:300]}")

    lines.append(f"🔗 2GIS: {biz['maps_url']}")

    if biz['photos']:
        lines.append("─" * 30)
        lines.append("📸 Фото:")
        for i, url in enumerate(biz['photos'][:3], 1):
            lines.append(f"  {i}. {url}")

    lines.append("─" * 30)
    has = "✅ есть" if biz['has_website'] else "❌ нет"
    lines.append(f"💻 Сайт: {has}")

    return "\n".join(lines)


@app.get("/")
async def root():
    return {"status": "ok", "message": "Лид-бот 2GIS работает"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/leads")
async def get_leads(body: dict):
    """
    Ищет бизнесы через 2GIS и возвращает карточки лидов.

    Запрос: {"query": "автосервис", "location": "Серов", "maxResults": 5}
    """
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
            "query": query,
            "location": location,
            "count": 0,
            "cards": [],
            "message": "Ничего не найдено"
        }

    cards = []
    for biz in items:
        cards.append({
            "card_text": format_lead_card(biz),
            "data": biz,
        })

    return {
        "query": query,
        "location": location,
        "count": len(cards),
        "cards": cards,
    }
