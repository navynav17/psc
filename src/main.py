import asyncio
import json
import re
from html import unescape
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from apify import Actor

START_URL = "https://www.psc.guru/mcqs/practice"
USER_AGENT = "Mozilla/5.0 (compatible; PSC-Guru-MCQ-Collector/1.0)"


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def question_record(node, source_url):
    if not isinstance(node, dict):
        return None

    q = None
    for key in ("question", "questionText", "question_text"):
        value = node.get(key)
        if isinstance(value, str) and len(clean(value)) >= 8:
            q = clean(value)
            break

    options_value = None
    for key in ("options", "choices", "answers", "alternatives"):
        if key in node:
            options_value = node[key]
            break

    if not q or not isinstance(options_value, (list, dict)):
        return None

    options = []
    items = enumerate(options_value) if isinstance(options_value, list) else options_value.items()
    for idx, item in items:
        if isinstance(item, dict):
            text = item.get("text") or item.get("label") or item.get("value") or item.get("answer")
        else:
            text = item
        text = clean(text)
        if text:
            label = chr(65 + idx) if isinstance(idx, int) and idx < 26 else str(idx)
            options.append({"label": label, "text": text})

    if len(options) < 2:
        return None

    record = {
        "question": q,
        "options": options[:10],
        "source": "PSC Guru",
        "source_url": source_url,
    }

    for key in ("correctAnswer", "correct_answer", "answer", "correctOption", "correct_option"):
        if key in node:
            record["correctAnswer"] = node[key]
            break

    for key in ("solution", "explanation", "explanationText"):
        if isinstance(node.get(key), str) and clean(node[key]):
            record["solution"] = clean(node[key])
            break

    for key in ("chapter", "subject", "category", "exam", "examType", "difficulty", "id", "questionId"):
        if key in node:
            record[key] = node[key]

    return record


def walk_json(node, source_url, output, seen):
    if isinstance(node, dict):
        record = question_record(node, source_url)
        if record:
            k = record["question"].casefold()
            if k not in seen:
                seen.add(k)
                output.append(record)
        for value in node.values():
            walk_json(value, source_url, output, seen)
    elif isinstance(node, list):
        for item in node:
            walk_json(item, source_url, output, seen)


def extract_next_data(html):
    soup = BeautifulSoup(html, "html.parser")
    data = []
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            data.append(json.loads(tag.string))
        except json.JSONDecodeError:
            pass
    return data


def extract_embedded_json(html):
    data = []
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script"):
        text = tag.string or tag.get_text()
        if not text:
            continue
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                data.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
    return data


def discover_real_api_urls(html):
    urls = set()
    # Only capture explicit API-looking strings, not arbitrary HTML text.
    patterns = [
        r'["\'](\/api\/[A-Za-z0-9_?=&\-./]+)["\']',
        r'["\'](\/trpc\/[A-Za-z0-9_?=&\-./]+)["\']',
        r'["\'](\/graphql[^"\']*)["\']',
        r'["\'](https?://[^"\']+\/api\/[^"\']+)["\']',
    ]
    for pattern in patterns:
        for match in re.findall(pattern, html, re.I):
            urls.add(match)
    return urls


def absolute(url):
    return urljoin(START_URL, url)


def main_sync():
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json,text/plain,*/*",
    })

    print(f"[1] GET {START_URL}", flush=True)
    response = session.get(START_URL, timeout=60)
    response.raise_for_status()
    print(f"[OK] HTTP {response.status_code} | {len(response.content):,} bytes", flush=True)

    html = response.text
    candidates = set()
    candidates.update(absolute(x) for x in discover_real_api_urls(html))

    parsed = urlparse(START_URL)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates.update({
        origin + "/api/mcqs/practice",
        origin + "/api/mcqs",
        origin + "/api/questions",
        origin + "/api/questions/practice",
    })

    payloads = []
    payloads.extend(extract_next_data(html))
    payloads.extend(extract_embedded_json(html))

    records = []
    seen = set()

    for payload in payloads:
        walk_json(payload, START_URL, records, seen)

    print(f"[2] MCQs found in embedded JSON: {len(records)}", flush=True)
    print(f"[3] API candidates: {len(candidates)}", flush=True)

    for url in sorted(candidates):
        if urlparse(url).netloc != parsed.netloc:
            continue
        try:
            print(f"[API] {url}", flush=True)
            r = session.get(url, timeout=60, headers={"Accept": "application/json,text/plain,*/*"})
            print(f"      HTTP {r.status_code} | {r.headers.get('content-type','')}", flush=True)
            if r.status_code != 200:
                continue
            try:
                payload = r.json()
            except ValueError:
                continue
            walk_json(payload, url, records, seen)
        except requests.RequestException as exc:
            print(f"      skipped: {exc}", flush=True)

    print(f"[DONE] UNIQUE MCQs: {len(records)}", flush=True)

    return records


async def main():
    async with Actor:
        records = await asyncio.to_thread(main_sync)
        for record in records:
            await Actor.push_data(record)
        Actor.log.info(f"Saved {len(records)} unique MCQs to Apify Dataset")


if __name__ == "__main__":
    asyncio.run(main())
