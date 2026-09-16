import asyncio
import re

import requests
from apify import Actor

START_URL = "https://www.psc.guru/mcqs/practice"
API_BASE = "https://api.psc.guru/api"
COUNT = 5000


def clean(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


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
                text = clean(item.get("text") or item.get("label") or item.get("value") or item.get("answer"))
                key = item.get("key") or (chr(65 + index) if index < 26 else str(index + 1))
            else:
                text = clean(item)
                key = chr(65 + index) if index < 26 else str(index + 1)
            if text:
                output.append({"key": key, "text": text})
    else:
        for label, item in value.items():
            if isinstance(item, dict):
                text = clean(item.get("text") or item.get("label") or item.get("value") or item.get("answer"))
            else:
                text = clean(item)
            if text:
                output.append({"key": str(label), "text": text})
    return output


def build_record(item, source_url):
    if not isinstance(item, dict):
        return None
    question = clean(item.get("questionText") or item.get("question") or item.get("text"))
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


def fetch_mode(session, mode, subject=None, count=COUNT):
    params = {"mode": mode, "count": count}
    if subject:
        params["subject"] = subject

    response = session.get(
        f"{API_BASE}/questions",
        params=params,
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items", []) if isinstance(payload, dict) else payload
    return items if isinstance(items, list) else [], response.url


async def run_mode(session, mode, label, subject=None):
    Actor.log.info(f"Fetching {label}: mode={mode} subject={subject or 'ALL'} count={COUNT}")
    items, source_url = fetch_mode(session, mode, subject, COUNT)

    seen = set()
    added = 0
    for item in items:
        record = build_record(item, source_url)
        if not record:
            continue
        qid = record.get("id") or clean(record.get("questionText")).casefold()
        if qid in seen:
            continue
        seen.add(qid)
        await Actor.push_data(record)
        added += 1

    Actor.log.info(f"{label}: returned={len(items)} added={added}")
    return len(items), added


async def main():
    async with Actor:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.psc.guru/mcqs/practice",
            "Origin": "https://www.psc.guru",
        })

        await run_mode(session, "practice", "Practice")

        try:
            items, source_url = fetch_mode(session, "dailyQuiz", count=10)
            Actor.log.info(f"Daily Quiz: returned={len(items)} source={source_url}")
            for item in items:
                record = build_record(item, source_url)
                if record:
                    record["mode"] = "dailyQuiz"
                    await Actor.push_data(record)
        except Exception as exc:
            Actor.log.warning(f"Daily Quiz failed: {exc}")

        Actor.log.info("DONE")


if __name__ == "__main__":
    asyncio.run(main())
