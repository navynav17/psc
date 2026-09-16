import asyncio
import json
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from apify import Actor

START_URL = "https://www.psc.guru/mcqs/practice"


def clean(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def extract_urls_from_js(text, origin):
    urls = set()

    # Real absolute URLs
    for m in re.findall(r'https?://[^"\'\s\\<>]+', text):
        urls.add(m)

    # API-like relative paths
    for m in re.findall(r'["\'](/(?:api|trpc|graphql)[^"\']*)["\']', text, re.I):
        urls.add(urljoin(origin, m))

    # Fetch strings containing likely data endpoints
    for m in re.findall(r'["\']([^"\']*(?:mcq|question|quiz|practice|exam)[^"\']*)["\']', text, re.I):
        if m.startswith("/") and len(m) < 300 and not any(x in m.lower() for x in ("static", "chunk", "svg", "css")):
            urls.add(urljoin(origin, m))

    return urls


def extract_questions(obj, source_url, results=None, seen=None):
    if results is None:
        results = []
    if seen is None:
        seen = set()

    if isinstance(obj, dict):
        q = None
        for key in ("question", "questionText", "question_text", "prompt"):
            if isinstance(obj.get(key), str) and len(clean(obj[key])) >= 8:
                q = clean(obj[key])
                break

        options_value = None
        for key in ("options", "choices", "alternatives"):
            if isinstance(obj.get(key), (list, dict)):
                options_value = obj[key]
                break

        options = []
        if isinstance(options_value, list):
            for i, item in enumerate(options_value):
                if isinstance(item, dict):
                    text = clean(item.get("text") or item.get("label") or item.get("value") or item.get("answer"))
                else:
                    text = clean(item)
                if text:
                    options.append({"label": chr(65 + i) if i < 26 else str(i + 1), "text": text})
        elif isinstance(options_value, dict):
            for label, item in options_value.items():
                if isinstance(item, dict):
                    text = clean(item.get("text") or item.get("label") or item.get("value") or item.get("answer"))
                else:
                    text = clean(item)
                if text:
                    options.append({"label": str(label), "text": text})

        if q and len(options) >= 2:
            k = q.casefold()
            if k not in seen:
                seen.add(k)
                record = {
                    "question": q,
                    "options": options[:10],
                    "source": "PSC Guru",
                    "source_url": source_url,
                }
                for key in ("correctAnswer", "correct_answer", "answer", "correctOption", "correct_option", "solution", "explanation", "chapter", "subject", "category", "exam", "examType", "difficulty", "id", "questionId", "question_id"):
                    if key in obj and obj[key] not in (None, ""):
                        record[key] = obj[key]
                results.append(record)

        for value in obj.values():
            extract_questions(value, source_url, results, seen)

    elif isinstance(obj, list):
        for item in obj:
            extract_questions(item, source_url, results, seen)

    return results


def discover_next_chunks(html, origin):
    soup = BeautifulSoup(html, "html.parser")
    chunks = set()
    for script in soup.find_all("script", src=True):
        src = script.get("src", "")
        if "_next/static/" in src and src.endswith(".js"):
            chunks.add(urljoin(origin, src))
    return chunks


def fetch(session, url):
    r = session.get(url, timeout=60)
    r.raise_for_status()
    return r


async def main():
    async with Actor:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/json,text/plain,*/*",
        })

        parsed = urlparse(START_URL)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        Actor.log.info(f"Downloading {START_URL}")
        page = fetch(session, START_URL)
        html = page.text
        Actor.log.info(f"HTTP {page.status_code} | {len(html):,} bytes")

        chunks = discover_next_chunks(html, origin)
        Actor.log.info(f"Discovered {len(chunks)} Next.js chunks")

        endpoint_candidates = set()
        all_questions = []
        seen_questions = set()

        # Scan chunks for API/data endpoint strings.
        for index, chunk_url in enumerate(sorted(chunks), 1):
            try:
                r = fetch(session, chunk_url)
                urls = extract_urls_from_js(r.text, origin)
                if urls:
                    Actor.log.info(f"Chunk {index}/{len(chunks)} -> {len(urls)} endpoint-like URLs")
                endpoint_candidates.update(urls)
            except Exception as exc:
                Actor.log.warning(f"Chunk failed: {chunk_url} :: {exc}")

        # Keep only same-site candidates likely to return data.
        filtered = set()
        for url in endpoint_candidates:
            try:
                p = urlparse(url)
            except Exception:
                continue
            if p.netloc and p.netloc != parsed.netloc:
                continue
            low = url.lower()
            if any(x in low for x in ("/api/", "/trpc/", "/graphql", "mcq", "question", "quiz", "practice")):
                filtered.add(url)

        Actor.log.info(f"Candidate data endpoints after filtering: {len(filtered)}")

        for index, url in enumerate(sorted(filtered), 1):
            try:
                Actor.log.info(f"Trying {index}/{len(filtered)}: {url}")
                r = fetch(session, url)
                ctype = r.headers.get("content-type", "").lower()
                body = r.text.lstrip()
                if "json" not in ctype and not body.startswith(("{", "[")):
                    continue
                payload = r.json()
                found = extract_questions(payload, url)
                added = 0
                for item in found:
                    k = item["question"].casefold()
                    if k in seen_questions:
                        continue
                    seen_questions.add(k)
                    all_questions.append(item)
                    await Actor.push_data(item)
                    added += 1
                if added:
                    Actor.log.info(f"FOUND {added}; total MCQs {len(all_questions)}")
            except Exception as exc:
                Actor.log.info(f"Skipped {url}: {exc}")

        # Diagnostic record when no endpoint produced questions.
        if not all_questions:
            await Actor.push_data({
                "source": "PSC Guru",
                "type": "diagnostic",
                "source_url": START_URL,
                "message": "No MCQs extracted. Inspect endpoint candidates or runtime requests.",
                "candidate_count": len(filtered),
                "sample_candidates": sorted(filtered)[:100],
            })

        Actor.log.info(f"DONE | unique MCQs extracted: {len(all_questions)}")


if __name__ == "__main__":
    asyncio.run(main())
