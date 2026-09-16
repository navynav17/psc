import asyncio
import json
import re
from urllib.parse import urlparse

import requests

from apify import Actor

START_URL = "https://www.psc.guru/mcqs/practice"
API_BASE = "https://api.psc.guru/api"


def clean(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_question(text):
    return clean(text).casefold()


def normalize_options(content):
    if not isinstance(content, dict):
        return []

    value = content.get("options")
    if not isinstance(value, (list, dict)):
        return []

    output = []

    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, dict):
                text = clean(
                    item.get("text")
                    or item.get("label")
                    or item.get("value")
                    or item.get("answer")
                )
            else:
                text = clean(item)

            if text:
                output.append({
                    "label": chr(65 + index) if index < 26 else str(index + 1),
                    "text": text,
                })
    else:
        for label, item in value.items():
            if isinstance(item, dict):
                text = clean(
                    item.get("text")
                    or item.get("label")
                    or item.get("value")
                    or item.get("answer")
                )
            else:
                text = clean(item)

            if text:
                output.append({
                    "label": str(label),
                    "text": text,
                })

    return output


def build_record(item, source_url):
    if not isinstance(item, dict):
        return None

    question = clean(
        item.get("questionText")
        or item.get("question")
        or item.get("text")
    )

    if len(question) < 5:
        return None

    options = normalize_options(item.get("content"))
    if len(options) < 2:
        return None

    record = {
        "id": item.get("id"),
        "question": question,
        "options": options,
        "correctAnswer": item.get("correctAnswer"),
        "tags": item.get("tags", []),
        "source": "PSC Guru",
        "source_url": source_url,
    }

    for field in (
        "explanation",
        "explanationText",
        "solution",
        "subject",
        "difficulty",
        "category",
        "exam",
        "examType",
    ):
        if item.get(field) is not None:
            record[field] = item[field]

    return record


def fetch_page(session, mode, subject=None, page=1, count=100):
    params = {
        "mode": mode,
        "page": page,
        "count": count,
    }

    if subject:
        params["subject"] = subject

    response = session.get(
        f"{API_BASE}/questions",
        params=params,
        timeout=60,
    )
    response.raise_for_status()

    payload = response.json()

    if isinstance(payload, dict):
        items = payload.get("items")
        if items is None:
            items = payload.get("questions")
        if items is None and isinstance(payload.get("data"), list):
            items = payload["data"]
    else:
        items = payload

    if not isinstance(items, list):
        items = []

    return items, response.url, payload


async def collect():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": START_URL,
        "Origin": "https://www.psc.guru",
    })

    subjects = [
        None,
        "computer",
        "constitution",
        "economy",
        "english",
        "environment",
        "geography",
        "history",
        "international",
        "iq-non-verbal",
        "iq-numerical",
        "iq-verbal",
        "literature-culture",
        "mathematics",
        "nepali",
        "public-admin",
        "science-tech",
    ]

    seen = set()
    total = 0
    page_size = 100

    Actor.log.info(f"PSC Guru API: {API_BASE}")

    for subject in subjects:
        page = 1
        subject_name = subject or "ALL"
        Actor.log.info(f"Starting subject: {subject_name}")

        while True:
            try:
                items, source_url, payload = fetch_page(
                    session,
                    mode="practice",
                    subject=subject,
                    page=page,
                    count=page_size,
                )
            except Exception as exc:
                Actor.log.warning(
                    f"Request failed subject={subject_name} page={page}: {exc}"
                )
                break

            if not items:
                Actor.log.info(
                    f"No items: subject={subject_name} page={page}"
                )
                break

            added = 0

            for item in items:
                record = build_record(item, source_url)
                if not record:
                    continue

                key = normalize_question(record["question"])
                if not key or key in seen:
                    continue

                seen.add(key)
                await Actor.push_data(record)
                total += 1
                added += 1

            Actor.log.info(
                f"subject={subject_name} page={page} returned={len(items)} "
                f"added={added} total={total}"
            )

            if len(items) < page_size:
                break

            page += 1

    Actor.log.info(f"DONE | total unique MCQs: {total}")


async def main():
    async with Actor:
        await collect()


if __name__ == "__main__":
    asyncio.run(main())
