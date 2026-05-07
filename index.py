#!/usr/bin/env python3
"""
fetch_trending.py

Advanced trending data fetcher using Playwright for browser automation.
Sources:
   - GitHub Trending        → tech skills signals (full browser render)
   - Hacker News            → industry news (API + page hydration)
   - Dev.to Trending        → tech articles (API + browser fallback)
   - Product Hunt           → product launches (browser scrape)

Database: PostgreSQL via psycopg2

Install deps:
   pip install playwright psycopg2-binary python-dotenv
   playwright install chromium

Env vars (.env):
   DATABASE_URL=postgresql://user:password@localhost:5432/mydb

Run:
   python index.py
"""

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

import psycopg2
from psycopg2 import pool, sql
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# Load environment variables
load_dotenv(".env")

# ─── DB Setup ─────────────────────────────────────────────────────────────────

class DatabasePool:
    """PostgreSQL connection pool manager"""
    
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool = None
    
    def connect(self):
        """Initialize connection pool"""
        self.pool = psycopg2.pool.SimpleConnectionPool(
            1, 5, self.database_url,
            connect_timeout=10,
            sslmode="require"
        )
    
    def get_connection(self):
        """Get a connection from the pool"""
        if not self.pool:
            self.connect()
        return self.pool.getconn()
    
    def return_connection(self, conn):
        """Return a connection to the pool"""
        if self.pool:
            self.pool.putconn(conn)
    
    def close_all(self):
        """Close all connections in pool"""
        if self.pool:
            self.pool.closeall()


# Global DB pool
db_pool = DatabasePool(os.getenv("DATABASE_URL", ""))


async def ensure_table():
    """Create table and indexes if they don't exist"""
    conn = db_pool.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trending_data (
                    id          SERIAL PRIMARY KEY,
                    source      TEXT        NOT NULL,
                    category    TEXT        NOT NULL,
                    title       TEXT        NOT NULL,
                    url         TEXT,
                    summary     TEXT,
                    tags        TEXT[]      DEFAULT '{}',
                    score       INTEGER     DEFAULT 0,
                    fetched_at  TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_trending_source      ON trending_data(source);
                CREATE INDEX IF NOT EXISTS idx_trending_category    ON trending_data(category);
                CREATE INDEX IF NOT EXISTS idx_trending_fetched_at  ON trending_data(fetched_at);
            """)
            conn.commit()
        print("✅ Table ready")
    except Exception as e:
        print(f"❌ Table creation failed: {e}")
        conn.rollback()
    finally:
        db_pool.return_connection(conn)


# ─── Browser Factory ──────────────────────────────────────────────────────────

async def launch_browser(playwright):
    """Launch chromium browser with stealth options"""
    return await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
        ]
    )


async def stealth_context(browser: Browser) -> BrowserContext:
    """
    Creates a stealth-ish browser context that avoids bot detection:
    - Realistic viewport + user agent
    - Disables webdriver flag via init script
    - Blocks images/fonts for faster scraping
    """
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1440, "height": 900},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
        }
    )

    # Remove navigator.webdriver fingerprint
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
    """)

    # Block heavy assets — we only need HTML/JS/JSON
    async def handle_route(route):
        resource_type = route.request.resource_type
        if resource_type in ["image", "font", "media"]:
            await route.abort()
        else:
            await route.continue_()

    await context.route("**/*", handle_route)
    return context


# ─── Helpers ──────────────────────────────────────────────────────────────────

def truncate(text: Optional[str], max_len: int = 300) -> Optional[str]:
    """Truncate string to max length"""
    if not text:
        return None
    s = text.strip()
    if len(s) > max_len:
        return s[:max_len].rstrip() + "…"
    return s


async def human_delay(min_ms: int = 800, max_ms: int = 2200):
    """Human-like random delay between min–max ms"""
    import random
    delay = min_ms + random.random() * (max_ms - min_ms)
    await asyncio.sleep(delay / 1000)


# ─── 1. GitHub Trending (Playwright) ─────────────────────────────────────────

async def scrape_github_trending(context: BrowserContext) -> List[Dict[str, Any]]:
    """Scrape GitHub trending repositories"""
    print("\n📦 Scraping GitHub Trending...")
    page = await context.new_page()

    try:
        await page.goto(
            "https://github.com/trending?since=weekly&spoken_language_code=en",
            wait_until="domcontentloaded",
            timeout=30000
        )

        # Wait for repo cards to appear
        await page.wait_for_selector("article.Box-row", timeout=15000)
        await human_delay(500, 1000)

        records = await page.evaluate("""() => {
            const articles = Array.from(
                document.querySelectorAll("article.Box-row")
            ).slice(0, 20);

            return articles.map((el) => {
                const nameEl = el.querySelector("h2 a");
                const name = nameEl?.textContent.replace(/\s+/g, " ").trim() ?? "";
                const href = nameEl?.getAttribute("href") ?? "";
                const description = el.querySelector("p")?.textContent.trim() ?? null;
                const language =
                    el.querySelector('[itemprop="programmingLanguage"]')
                        ?.textContent.trim() ?? null;

                // Stars today / total stars
                const starsEl = el.querySelector("a[href$='/stargazers']");
                const starsText = starsEl?.textContent.replace(/,/g, "").trim() ?? "0";
                const stars = parseInt(starsText) || 0;

                // Stars gained today
                const gainedEl = el.querySelector(".float-sm-right");
                const gainedText = gainedEl?.textContent.replace(/,/g, "").trim() ?? "";
                const gained = parseInt(gainedText) || 0;

                // Contributors avatars count as a signal
                const contributors = el.querySelectorAll(
                    "a[data-hovercard-type='user']"
                ).length;

                const tags = [];
                if (language) tags.push(language.toLowerCase());

                return {
                    source: "github",
                    category: "tech-skills",
                    title: name,
                    url: `https://github.com${href}`,
                    summary: description,
                    tags,
                    score: stars,
                    metadata: { gained, contributors },
                };
            });
        }""")

        print(f"   ✅ {len(records)} repos scraped")
        return records
    except Exception as err:
        print(f"   ❌ GitHub Trending failed: {err}")
        return []
    finally:
        await page.close()


# ─── 2. Hacker News (API + Playwright for comments preview) ──────────────────

async def scrape_hacker_news(context: BrowserContext) -> List[Dict[str, Any]]:
    """Scrape Hacker News top stories"""
    print("\n📰 Scraping Hacker News...")
    page = await context.new_page()

    try:
        # Use the official Firebase API for story IDs
        await page.goto(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            wait_until="domcontentloaded",
            timeout=15000
        )

        ids_json = await page.evaluate("() => document.body.innerText")
        ids = json.loads(ids_json)[:25]
        await page.close()

        # Fetch story details in parallel batches of 5
        stories = []
        batch_size = 5

        for i in range(0, len(ids), batch_size):
            batch = ids[i : i + batch_size]
            tasks = []

            for story_id in batch:
                async def fetch_story(sid):
                    p = await context.new_page()
                    try:
                        await p.goto(
                            f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                            wait_until="domcontentloaded",
                            timeout=10000
                        )
                        story_json = await p.evaluate("() => document.body.innerText")
                        return json.loads(story_json)
                    except:
                        return None
                    finally:
                        await p.close()

                tasks.append(fetch_story(story_id))

            batch_stories = await asyncio.gather(*tasks)
            stories.extend([s for s in batch_stories if s])
            await human_delay(200, 500)

        records = [
            {
                "source": "hackernews",
                "category": "industry-news",
                "title": s["title"],
                "url": s["url"],
                "summary": None,
                "tags": [],
                "score": s.get("score", 0),
            }
            for s in stories
            if s and s.get("type") == "story" and s.get("title") and s.get("url")
        ]

        print(f"   ✅ {len(records)} stories fetched")
        return records
    except Exception as err:
        print(f"   ❌ Hacker News failed: {err}")
        return []
    finally:
        try:
            await page.close()
        except:
            pass


# ─── 3. Dev.to (API + Playwright for richer metadata) ────────────────────────

async def scrape_devto(context: BrowserContext) -> List[Dict[str, Any]]:
    """Scrape Dev.to trending articles"""
    print("\n📝 Scraping Dev.to Trending Articles...")
    page = await context.new_page()

    try:
        # Use the Dev.to API
        await page.goto(
            "https://dev.to/api/articles?top=7&per_page=20",
            wait_until="domcontentloaded",
            timeout=15000
        )

        json_text = await page.evaluate("() => document.body.innerText")
        articles = json.loads(json_text)

        records = [
            {
                "source": "devto",
                "category": "industry-news",
                "title": a["title"],
                "url": a["url"],
                "summary": truncate(a.get("description")),
                "tags": a.get("tag_list", []),
                "score": a.get("positive_reactions_count", 0) + a.get("comments_count", 0),
            }
            for a in articles
        ]

        print(f"   ✅ {len(records)} articles fetched")
        return records
    except Exception as err:
        print(f"   ❌ Dev.to failed: {err}")
        return []
    finally:
        await page.close()


# ─── 4. Product Hunt (Playwright — JS-heavy SPA) ─────────────────────────────

async def scrape_product_hunt(context: BrowserContext) -> List[Dict[str, Any]]:
    """Scrape Product Hunt products"""
    print("\n🚀 Scraping Product Hunt...")
    page = await context.new_page()

    try:
        await page.goto(
            "https://www.producthunt.com",
            wait_until="networkidle",
            timeout=45000
        )

        # Wait for product cards to render (SPA needs JS)
        await page.wait_for_selector('[data-test="homepage-section-0"]', timeout=20000)
        await human_delay(1000, 2000)

        records = await page.evaluate("""() => {
            const items = [];

            // Product Hunt renders products in section containers
            document.querySelectorAll("li[class*='item']").forEach((el) => {
                const titleEl = el.querySelector("strong, [class*='name'], h3");
                const descEl = el.querySelector("p, [class*='tagline']");
                const linkEl = el.querySelector("a[href*='/posts/']");
                const votesEl = el.querySelector(
                    "[class*='voteCount'], [data-test*='vote']"
                );

                const title = titleEl?.textContent.trim();
                const url = linkEl
                    ? `https://www.producthunt.com${linkEl.getAttribute("href")}`
                    : null;

                if (!title || !url) return;

                const votesText = votesEl?.textContent.replace(/,/g, "").trim() ?? "0";
                const votes = parseInt(votesText) || 0;

                items.push({
                    source: "producthunt",
                    category: "product-launches",
                    title,
                    url,
                    summary: descEl?.textContent.trim() ?? null,
                    tags: [],
                    score: votes,
                });
            });

            return items.slice(0, 20);
        }""")

        # If the selector approach got nothing, try a fallback
        if not records:
            print("   ⚠️  Primary selector failed, trying fallback...")
            fallback = await page.evaluate("""() => {
                const links = Array.from(
                    document.querySelectorAll("a[href*='/posts/']")
                );
                const seen = new Set();
                return links
                    .filter((a) => {
                        const href = a.getAttribute("href");
                        if (seen.has(href)) return false;
                        seen.add(href);
                        return a.textContent.trim().length > 10;
                    })
                    .slice(0, 15)
                    .map((a) => ({
                        source: "producthunt",
                        category: "product-launches",
                        title: a.textContent.trim(),
                        url: `https://www.producthunt.com${a.getAttribute("href")}`,
                        summary: null,
                        tags: [],
                        score: 0,
                    }));
            }""")
            print(f"   ✅ {len(fallback)} products (fallback)")
            return fallback

        print(f"   ✅ {len(records)} products scraped")
        return records
    except Exception as err:
        print(f"   ❌ Product Hunt failed: {err}")
        return []
    finally:
        await page.close()


# ─── Store to PostgreSQL ──────────────────────────────────────────────────────

async def store_to_db(records: List[Dict[str, Any]]):
    """Store records to PostgreSQL database"""
    if not records:
        print("\n⚠️  No records to store.")
        return

    print(f"\n💾 Storing {len(records)} records to PostgreSQL...")

    conn = db_pool.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("BEGIN")

            inserted = 0
            for r in records:
                cur.execute(
                    """INSERT INTO trending_data (source, category, title, url, summary, tags, score)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        r["source"],
                        r["category"],
                        r["title"],
                        r.get("url"),
                        truncate(r.get("summary")),
                        r.get("tags", []),
                        r.get("score", 0),
                    )
                )
                inserted += 1

            cur.execute("COMMIT")
        print(f"   ✅ Inserted {inserted} records")
    except Exception as err:
        conn.rollback()
        print(f"   ❌ DB insert failed: {err}")
        raise
    finally:
        db_pool.return_connection(conn)


# ─── Clean old records ────────────────────────────────────────────────────────

async def clean_old_records(keep_days: int = 7):
    """Delete records older than keep_days"""
    conn = db_pool.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""DELETE FROM trending_data
                   WHERE fetched_at < NOW() - INTERVAL '{keep_days} days'"""
            )
            conn.commit()
            print(f"🗑️  Cleaned {cur.rowcount} records older than {keep_days} days")
    except Exception as err:
        print(f"❌ Cleanup failed: {err}")
    finally:
        db_pool.return_connection(conn)


# ─── Summary Report ───────────────────────────────────────────────────────────

async def print_summary():
    """Print database summary"""
    conn = db_pool.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT source, COUNT(*) as count, MAX(fetched_at) as last_fetch
                FROM trending_data
                GROUP BY source
                ORDER BY source
            """)
            rows = cur.fetchall()

        print("\n📊 DB Summary:")
        print("┌─────────────────┬────────────┬────────────────────────┐")
        print("│ source          │ total_rows │ last_fetch             │")
        print("├─────────────────┼────────────┼────────────────────────┤")
        
        for row in rows:
            source, count, last_fetch = row
            last_fetch_str = last_fetch.strftime("%m/%d/%Y, %I:%M:%S %p") if last_fetch else "N/A"
            print(f"│ {source:15} │ {str(count):10} │ {last_fetch_str:22} │")
        
        print("└─────────────────┴────────────┴────────────────────────┘")
    except Exception as err:
        print(f"❌ Summary failed: {err}")
    finally:
        db_pool.return_connection(conn)


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    """Main entry point"""
    start_time = time.time()
    print("🚀 Starting trending data fetch...")
    print(f"   Time: {datetime.utcnow().isoformat()}Z")
    
    db_url_str = os.getenv("DATABASE_URL", "")
    if db_url_str:
        db_url = re.sub(r":([^@]+)@", ":***@", db_url_str)
    else:
        db_url = "undefined"
    print(f"   DB:   {db_url}\n")

    db_pool.connect()
    browser = None

    try:
        # Ensure DB table exists
        await ensure_table()

        # Launch Playwright
        async with async_playwright() as playwright:
            # Launch single browser, reuse contexts
            browser = await launch_browser(playwright)
            context = await stealth_context(browser)

            # Run scrapers — GitHub and Dev.to in parallel, PH + HN sequentially
            github_data, devto_data = await asyncio.gather(
                scrape_github_trending(context),
                scrape_devto(context)
            )

            await human_delay(1000, 2000)
            hn_data = await scrape_hacker_news(context)

            await human_delay(1000, 2000)
            ph_data = await scrape_product_hunt(context)

            await context.close()

            # Combine and store
            all_records = github_data + hn_data + devto_data + ph_data
            await store_to_db(all_records)

        # Housekeeping
        await clean_old_records(7)

        # Print summary
        await print_summary()

        elapsed = (time.time() - start_time)
        print(f"\n✅ Done in {elapsed:.1f}s")

    except Exception as err:
        print(f"\n❌ Fatal error: {err}")
        sys.exit(1)
    finally:
        if browser:
            await browser.close()
        db_pool.close_all()


if __name__ == "__main__":
    asyncio.run(main())
