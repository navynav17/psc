import asyncio
import re

import requests

from apify import Actor

START_URL = "https://www.psc.guru/mcqs/practice"
API_BASE = "https://api.psc.guru/api"
REQUEST_COUNT = 5000


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
                key = item.get("key") or (chr(65 + index) if index < 26 else str(index + 1))
            else:
                text = clean(item)
                key = chr(65 + index) if index < 26 else str(index + 1)
            if text:
                output.append({"key": key, "text": text})
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
                output.append({"key": str(label), "text": text})
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

    content = item.get("content")
    if not isinstance(content, dict):
        content = {}

    options = normalize_options(content)
    if len(options) < 2:
        return None

    return {
        "id": item.get("id"),
        "questionText": question,
        "contextText": item.get("contextText"),
        "patternType": item.get("patternType"),
        "content": {
            "options": options,
            "statements": content.get("statements"),
            "groupA": content.get("groupA"),
            "groupB": content.get("groupB"),
            "visualDescription": content.get("visualDescription"),
        },
        "difficulty": item.get("difficulty"),
        "tags": item.get("tags") or [],
        "svgCode": item.get("svgCode"),
        "correctAnswer": item.get("correctAnswer"),
        "explanation": item.get("explanation"),
        "source": "PSC Guru",
        "sourceUrl": source_url,
    }


def get_items(session):
    params = {
        "mode": "practice",
        "count": REQUEST_COUNT,
    }
    response = session.get(
        f"{API_BASE}/questions",
        params=params,
        timeout=120,
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

    return items if isinstance(items, list) else [], response.url


async def collect():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": START_URL,
        "Origin": "https://www.psc.guru",
    })

    Actor.log.info(f"Requesting PSC Guru practice pool with count={REQUEST_COUNT}")
    items, source_url = get_items(session)
    Actor.log.info(f"API returned {len(items)} items")

    seen_ids = set()
    seen_questions = set()
    added = 0

    for item in items:
        record = build_record(item, source_url)
        if not record:
            continue

        qid = record.get("id")
        qkey = normalize_question(record.get("questionText"))

        if qid and qid in seen_ids:
            continue
        if qkey and qkey in seen_questions:
            continue

        if qid:
            seen_ids.add(qid)
        if qkey:
            seen_questions.add(qkey)

        await Actor.push_data(record)
        added += 1

    Actor.log.info(
        f"DONE | API items={len(items)} | unique MCQs emitted={added}"
    )


async def main():
    async with Actor:
        await collect()


if __name__ == "__main__":
    asyncio.run(main())
