import os
import re
import asyncio
import traceback
from urllib.parse import urlparse

from apify import Actor
from playwright.async_api import async_playwright


START_URL = "https://www.psc.guru/mcqs/practice"


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


async def first_visible(page, selectors):
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            if await loc.is_visible():
                return loc
        except Exception:
            pass
    return None


async def login(page, email, password):
    Actor.log.info(f"Opening PSC Guru: {START_URL}")
    await page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(1500)
    Actor.log.info(f"Initial page URL: {page.url}")

    if "login" not in page.url.lower():
        password_input = page.locator('input[type="password"]').first
        try:
            if not await password_input.is_visible():
                Actor.log.info("Already authenticated; no login form detected.")
                return
        except Exception:
            Actor.log.info("Already authenticated; no login form detected.")
            return

    Actor.log.info("Login form detected; filling credentials.")

    email_input = await first_visible(page, [
        'input[type="email"]',
        'input[name*="email" i]',
        'input[placeholder*="email" i]',
    ])
    password_input = await first_visible(page, [
        'input[type="password"]',
        'input[name*="password" i]',
    ])

    if not email_input or not password_input:
        raise RuntimeError("PSC Guru login form was not detected.")

    await email_input.fill(email)
    await password_input.fill(password)

    submit = await first_visible(page, [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Login")',
        'button:has-text("Sign in")',
        'button:has-text("Log in")',
    ])
    if not submit:
        raise RuntimeError("PSC Guru login submit button was not detected.")

    await submit.click()
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=60000)
    except Exception:
        Actor.log.warning("Post-login navigation did not reach domcontentloaded; continuing.")
    await page.wait_for_timeout(2000)

    Actor.log.info(f"Post-login page URL: {page.url}")

    if "login" in page.url.lower():
        raise RuntimeError("PSC Guru login did not complete. Check Actor input email/password.")


async def extract_question(page, source_url):
    containers = page.locator(
        '[data-question], [class*="question" i], article, .card, li'
    )
    count = min(await containers.count(), 200)

    results = []
    seen = set()

    for i in range(count):
        c = containers.nth(i)
        try:
            raw = clean(await c.inner_text())
        except Exception:
            continue
        if len(raw) < 20:
            continue

        lines = [clean(x) for x in raw.split("\n") if clean(x)]
        opts = []
        first_opt = None

        for idx, line in enumerate(lines):
            m = re.match(r"^([A-D])[.)\s]+(.+)$", line, re.I)
            if m:
                if first_opt is None:
                    first_opt = idx
                opts.append({"label": m.group(1).upper(), "text": clean(m.group(2))})

        if len(opts) < 2 or first_opt is None:
            continue

        qtext = clean(" ".join(lines[:first_opt]))
        if len(qtext) < 8:
            continue

        key = qtext.casefold()
        if key in seen:
            continue
        seen.add(key)

        results.append({
            "question": qtext,
            "options": opts[:4],
            "source_url": source_url,
        })

    return results


async def run():
    actor_input = await Actor.get_input() or {}
    Actor.log.info(f"Actor input keys: {sorted(actor_input.keys())}")

    email = actor_input.get("email") or os.environ.get("PSC_GURU_EMAIL")
    password = actor_input.get("password") or os.environ.get("PSC_GURU_PASSWORD")
    start_url = actor_input.get("startUrl", START_URL)
    max_pages = int(actor_input.get("maxPages", 100))
    max_questions = int(actor_input.get("maxQuestions", 10000))

    if not email or not password:
        raise RuntimeError(
            "Provide PSC Guru credentials in Actor input or PSC_GURU_EMAIL/PSC_GURU_PASSWORD secrets."
        )

    Actor.log.info("Starting PSC Guru collector...")

    async with async_playwright() as pw:
        Actor.log.info("Launching Chromium...")
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (compatible; PSC-Guru-Apify-Collector/1.0)"
        )
        page = await context.new_page()

        try:
            await login(page, email, password)
            await page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1000)
            Actor.log.info(f"Starting crawl at: {page.url}")

            visited = set()
            emitted = set()
            queue = [page.url]

            while queue and len(visited) < max_pages and len(emitted) < max_questions:
                url = queue.pop(0)
                if url in visited:
                    continue
                visited.add(url)

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(1000)
                except Exception as exc:
                    Actor.log.warning(f"Page failed: {url} :: {exc}")
                    continue

                questions = await extract_question(page, url)
                for q in questions:
                    key = clean(q["question"]).casefold()
                    if key in emitted:
                        continue
                    emitted.add(key)
                    await Actor.push_data({
                        **q,
                        "source": "PSC Guru",
                        "source_url": url,
                    })
                    if len(emitted) >= max_questions:
                        break

                links = await page.locator("a[href]").evaluate_all(
                    """els => els.map(a => ({href:a.href, text:(a.innerText||'').trim()}))"""
                )
                for item in links:
                    href = item.get("href", "")
                    label = clean(item.get("text", ""))
                    if not href or href in visited or href in queue:
                        continue
                    parsed = urlparse(href)
                    if parsed.netloc and parsed.netloc != "www.psc.guru":
                        continue
                    low = (href + " " + label).lower()
                    if any(k in low for k in ("mcq", "practice", "question", "quiz")):
                        queue.append(href)

                Actor.log.info(
                    f"Visited {len(visited)} pages | emitted {len(emitted)} unique MCQs | queue {len(queue)}"
                )

        finally:
            await browser.close()
            Actor.log.info("Browser closed.")


async def main():
    async with Actor:
        try:
            await run()
        except Exception as exc:
            Actor.log.error(f"FATAL: {type(exc).__name__}: {exc}")
            Actor.log.error(traceback.format_exc())
            raise


if __name__ == "__main__":
    asyncio.run(main())
