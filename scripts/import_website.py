#!/usr/bin/env python3
"""
Pull a website's pages into Emma's document folder, as plain text.

    python scripts/import_website.py https://embassypattaya.com/
    python scripts/import_website.py https://example.com/ --max-pages 30

Why this exists instead of a live "browse the web" tool: live grounding is
a billing-tier feature (measured — see scripts/probe_web_search.py), but a
*known* site doesn't need live search at all. Crawl it once into
PERSONAL_DOCS_DIR and `search_my_documents` picks the files up on the next
question, no restart — that is the mydocs contract. Re-run whenever the
site changes; files are overwritten by slug, so a re-import is a refresh,
not a duplicate.

Same-domain only, HTML only, and the obvious WordPress noise (feeds,
comment streams, "-copy-" draft pages) is skipped rather than indexed —
a draft page in the corpus becomes a confidently wrong answer later.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SKIP_PATTERNS = re.compile(
    r"/feed/?$|/comments/|\.(css|js|jpg|jpeg|png|gif|webp|svg|ico|pdf|xml|zip)$"
    r"|-copy[-\d]*(/|$)|/wp-|/core/cache/"
    # Index shells, not content: a /tag/ page is a list of links to articles
    # this crawl already visits, and 20 of them buried the real corpus the
    # first time the sitemap was compared against what got saved.
    r"|/tag/|/category/|/writer/|/author/|/page/\d",
    re.I,
)


def visible_text(html: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<(br|/p|/div|/h[1-6]|/li|/tr)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    # Un-escape the handful of entities WordPress actually emits.
    import html as html_mod

    text = html_mod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def slug_for(url: str) -> str:
    path = urlparse(url).path.strip("/") or "home"
    return re.sub(r"[^a-zA-Z0-9ก-๙_-]+", "-", path)[:60]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--max-pages", type=int, default=25)
    ap.add_argument("--out", default=None,
                    help="ปลายทาง (default: PERSONAL_DOCS_DIR จาก .env)")
    args = ap.parse_args()

    import httpx

    from app.config import settings

    out_dir = Path(args.out or settings.personal_docs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    start = args.url if args.url.startswith("http") else "https://" + args.url
    domain = urlparse(start).netloc
    queue, seen, saved = [start], set(), 0
    headers = {"User-Agent": "Mozilla/5.0 (condo-voice importer)"}

    # The sitemap is the site's own list of everything it has — link-walking
    # alone missed 70 articles (and the floorplan page) the first time,
    # because the crawl cap hit before the blog archive was reached.
    def seed_from_sitemap(client) -> None:
        for sm in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"):
            try:
                r = client.get(start.rstrip("/") + sm)
            except Exception:
                continue
            if r.status_code != 200:
                continue
            locs = re.findall(r"<loc>([^<]+)</loc>", r.text)
            for loc in locs:
                if "sitemap" in loc and loc.endswith(".xml"):
                    try:
                        locs.extend(re.findall(r"<loc>([^<]+)</loc>",
                                               client.get(loc).text))
                    except Exception:
                        pass
            pages = [u for u in locs if not u.endswith(".xml")
                     and urlparse(u).netloc == domain]
            if pages:
                queue.extend(pages)
                print(f"  sitemap: {len(pages)} หน้า")
                return

    with httpx.Client(follow_redirects=True, timeout=20, headers=headers) as client:
        seed_from_sitemap(client)
        while queue and saved < args.max_pages:
            url = queue.pop(0)
            bare = url.split("#")[0].rstrip("/")
            if bare in seen:
                continue
            seen.add(bare)
            if SKIP_PATTERNS.search(bare):
                continue
            try:
                r = client.get(url)
            except Exception as e:
                print(f"  ข้าม {url} ({type(e).__name__})")
                continue
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                continue

            text = visible_text(r.text)
            if len(text) < 200:          # nav-only shells add noise, not answers
                continue
            name = "web-%s.txt" % slug_for(bare)
            (out_dir / name).write_text(
                "ที่มา: %s\n\n%s" % (bare, text), encoding="utf-8")
            saved += 1
            print(f"  เก็บ {name} ({len(text)} ตัวอักษร)")

            for href in re.findall(r'href="([^"#]+)"', r.text):
                nxt = urljoin(url, href)
                if urlparse(nxt).netloc == domain:
                    queue.append(nxt)

    print(f"\nได้ {saved} หน้า -> {out_dir}")
    print("Emma ค้นได้ทันทีในคำถามถัดไป (search_my_documents จับไฟล์ใหม่เอง)")
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
