import asyncio
import json
import re
from urllib.parse import urlparse

import requests

from apify import Actor

START_URL = "https://www.psc.guru/mcqs/practice"


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def extract_questions_from_json(data, source_url):
    results = []
    seen = set()

    def walk(node):
        if isinstance(node, dict):
            q = None
            for key in ("question", "questionText", "text", "title"):
                value = node.get(key)
                if isinstance(value, str) and len(clean(value)) >= 8:
                    q = clean(value)
                    if key == "title" and "?" not in q and len(q) < 20:
                        q = None
                    break

            options = []
            for key in ("options", "choices", "answers", "alternatives"):
                value = node.get(key)
                if isinstance(value, list):
                    for idx, item in enumerate(value[:4]):
                        if isinstance(item, str):
                            options.append({"label": chr(65 + idx), "text": clean(item)})
                        elif isinstance(item, dict):
                            text = clean(item.get("text") or item.get("label") or item.get("value"))
                            if text:
                                options.append({"label": chr(65 + idx), "text": text})
                    if options:
                        break

            if q and len(options) >= 2:
                key = q.casefold()
                if key not in seen:
                    seen.add(key)
                    record = {
                        "question": q,
                        "options": options[:4],
                        "source": "PSC Guru",
                        "source_url": source_url,
                    }
                    for answer_key in ("correctAnswer", "correct_answer", "answer", "correctOption"):
                        if answer_key in node:
                            record["correctAnswer"] = node[answer_key]
                            break
                    for solution_key in ("solution", "explanation", "explanationText"):
                        if isinstance(node.get(solution_key), str) and clean(node[solution_key]):
                            record["solution"] = clean(node[solution_key])
                            break
                    results.append(record)

            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return results


def fetch_json(url):
    response = requests.get(
        url,
        timeout=60,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; PSC-Guru-Apify-Collector/1.0)",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    response.raise_for_status()
    return response.json()


def discover_json_urls(html, base_url):
    urls = set()
    for match in re.findall(r'''["']([^"']+\.json(?:\?[^"']*)?)["']''', html, re.I):
        if match.startswith("//"):
            match = "https:" + match
        elif match.startswith("/"):
            match = "https://www.psc.guru" + match
        elif not match.startswith("http"):
            match = base_url.rstrip("/") + "/" + match.lstrip("/")
        urls.add(match)
    return urls


def main_sync():
    Actor.log.info(f"Opening public PSC Guru page: {START_URL}")
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; PSC-Guru-Apify-Collector/1.0)"
    })

    response = session.get(START_URL, timeout=60)
    response.raise_for_status()
    Actor.log.info(f"HTTP {response.status_code} | {response.url}")

    json_urls = discover_json_urls(response.text, START_URL)
    Actor.log.info(f"Discovered {len(json_urls)} JSON candidates in page source.")

    # Also try common API paths used by frontend applications.
    parsed = urlparse(START_URL)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    common_paths = [
        "/api/mcqs/practice",
        "/api/mcqs",
        "/api/questions",
        "/api/questions/practice",
        "/api/mcq",
    ]
    json_urls.update(origin + path for path in common_paths)

    total = 0
    seen_urls = set()
    for url in list(json_urls):
        if url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            Actor.log.info(f"Trying JSON endpoint: {url}")
            payload = fetch_json(url)
            questions = extract_questions_from_json(payload, url)
            Actor.log.info(f"Extracted {len(questions)} MCQs from {url}")
            for record in questions:
                asyncio.run(Actor.push_data(record))
                total += 1
        except Exception as exc:
            Actor.log.info(f"Skipped {url}: {exc}")

    # Save raw page HTML metadata even if no JSON endpoint was exposed.
    if total == 0:
        awaitable = Actor.push_data({
            "source": "PSC Guru",
            "source_url": START_URL,
            "type": "diagnostic",
            "message": "No JSON MCQ endpoint was discovered from the public practice page.",
            "http_status": response.status_code,
            "content_type": response.headers.get("content-type"),
        })
        asyncio.run(awaitable)

    Actor.log.info(f"DONE | total extracted unique MCQs: {total}")


async def main():
    async with Actor:
        try:
            # Run blocking requests in a worker thread so the Apify Actor remains responsive.
            await asyncio.to_thread(main_sync)
        except Exception as exc:
            Actor.log.error(f"FATAL: {type(exc).__name__}: {exc}")
            raise


if __name__ == "__main__":
    asyncio.run(main())
