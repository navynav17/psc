import asyncio
import re
from urllib.parse import urlparse

import requests

from apify import Actor

START_URL = "https://www.psc.guru/mcqs/practice"
API_BASE = "https://api.psc.guru/api"
PAGE_SIZE = 100
MAX_PAGE = 200


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
                    "key": item.get("key") if isinstance(item, dict) else (chr(65 + index) if index < 26 else str(index + 1)),
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


def get_items(session, params):
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

    return items, response.url


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

    seen_ids = set()
    seen_questions = set()
    total = 0

    for subject in subjects:
        subject_name = subject or "ALL"
        Actor.log.info(f"START {subject_name}")

        # The API's `page` parameter produces changing samples, but it is not
        # guaranteed to be a strict non-overlapping offset. Keep every unique
        # question and stop after a run of pages that adds nothing new.
        no_new_pages = 0

        for page in range(1, MAX_PAGE + 1):
            params = {
                "mode": "practice",
                "page": page,
                "count": PAGE_SIZE,
            }
            if subject:
                params["subject"] = subject

            try:
                items, source_url = get_items(session, params)
            except Exception as exc:
                Actor.log.warning(
                    f"REQUEST FAILED subject={subject_name} page={page}: {exc}"
                )
                break

            if not items:
                Actor.log.info(f"END {subject_name}: empty page {page}")
                break

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
                total += 1
                added += 1

            if added == 0:
                no_new_pages += 1
            else:
                no_new_pages = 0

            Actor.log.info(
                f"{subject_name} page={page} returned={len(items)} "
                f"added={added} total={total}"
            )

            if no_new_pages >= 5:
                Actor.log.info(
                    f"STOP {subject_name}: 5 consecutive pages with no new MCQs"
                )
                break

            if len(items) < PAGE_SIZE:
                Actor.log.info(
                    f"END {subject_name}: partial page {page}"
                )
                break

    Actor.log.info(
        f"DONE | unique MCQs={total} | unique IDs={len(seen_ids)}"
    )


async def main():
    async with Actor:
        await collect()


if __name__ == "__main__":
    asyncio.run(main())
