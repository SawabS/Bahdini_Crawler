#!/usr/bin/env python
"""Download all PDF files shared in the "Partokxana Electroni" Facebook group.

Group:      https://www.facebook.com/groups/1589495248028152
Files tab:  https://www.facebook.com/groups/1589495248028152/files/files

The files tab only lists the documents; each file actually lives behind a
group post permalink such as:

    https://www.facebook.com/groups/1589495248028152/permalink/1608627992781544/

so the script works in three phases:

  1. LOGIN    - opens a visible Chromium window. You log in manually
                (credentials, 2FA, checkpoints). The session is stored in
                ".fb_profile/" so later runs skip this step.
  2. SCAN     - opens the group's files tab, scrolls until no new rows
                appear, and harvests every unique permalink. The result is
                cached in "permalinks.json" (re-scan with --rescan).
  3. DOWNLOAD - visits each permalink, locates the attachment link
                (lookaside.fbsbx.com / attachment.php), and streams the file
                into "pdfs/" with a per-file progress bar and an overall
                [i/N] counter. Progress is recorded in "manifest.json" so an
                interrupted run resumes where it stopped.

Run it inside the "ai" conda environment:

    conda run -n ai --live-stream python facebook_pdf_downloader.py

Options:
    --rescan        ignore permalinks.json and harvest the files tab again
    --retry-failed  retry permalinks that previously errored
    --limit N       only process the first N pending permalinks (for testing)
    --delay S       seconds to wait between posts (default 4, be polite)
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright
from tqdm import tqdm

GROUP_ID = "1589495248028152"
GROUP_FILES_URL = f"https://www.facebook.com/groups/{GROUP_ID}/files/files"
PERMALINK_TPL = f"https://www.facebook.com/groups/{GROUP_ID}/permalink/{{post_id}}/"

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / ".fb_profile"
PDF_DIR = BASE_DIR / "pdfs"
PERMALINKS_FILE = BASE_DIR / "permalinks.json"
MANIFEST_FILE = BASE_DIR / "manifest.json"

# Anchors on a permalink page that point at the actual file.
# Group files are served as facebook.com/download/<id>/<filename>?av=..&eav=..;
# the other patterns are kept as fallbacks for older attachment styles.
FILE_LINK_SELECTOR = (
    'a[href*="facebook.com/download/"], '
    'a[href*="lookaside.fbsbx.com/file"], '
    'a[href*="attachment.php"], '
    'a[href*="fbcdn.net"][href*=".pdf"]'
)

SCROLL_PAUSE = 2.5          # seconds between scroll steps while scanning
SCROLL_STABLE_ROUNDS = 8    # stop scanning after this many scrolls w/o new links


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_json(path: Path, default):
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return default


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


def sanitize_filename(name: str) -> str:
    name = unquote(name).strip()
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    return name[:180] or "unnamed"


# --------------------------------------------------------------------------
# Phase 1 - login
# --------------------------------------------------------------------------

def is_logged_in(context) -> bool:
    return any(c["name"] == "c_user" for c in context.cookies("https://www.facebook.com"))


def dismiss_popups(page) -> None:
    """Close "Remember password" and similar prompts that block navigation."""
    for label in ("Not now", "Not Now", "OK"):
        try:
            btn = page.query_selector(
                f'div[role="button"]:has-text("{label}"), button:has-text("{label}")'
            )
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(1000)
                return
        except Exception:
            pass


def ensure_login(context, page) -> None:
    if is_logged_in(context):
        log("Existing Facebook session found in .fb_profile/ - skipping login.")
        return
    log("Not logged in. Opening facebook.com - please log in in the browser window.")
    log("(Handle credentials / 2FA / any checkpoint yourself; I will wait.)")
    page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
    while not is_logged_in(context):
        time.sleep(2)
    log("Login detected. Session saved for future runs.")
    time.sleep(3)
    dismiss_popups(page)


# --------------------------------------------------------------------------
# Phase 2 - harvest permalinks from the files tab
# --------------------------------------------------------------------------

def harvest_permalinks(page) -> list[str]:
    log(f"Opening files tab: {GROUP_FILES_URL}")
    page.goto(GROUP_FILES_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)

    seen: set[str] = set()
    stable_rounds = 0
    while stable_rounds < SCROLL_STABLE_ROUNDS:
        hrefs = page.eval_on_selector_all(
            'a[href*="/permalink/"]', "els => els.map(e => e.href)"
        )
        before = len(seen)
        for href in hrefs:
            m = re.search(rf"/groups/{GROUP_ID}/permalink/(\d+)", href)
            if m:
                seen.add(m.group(1))
        stable_rounds = stable_rounds + 1 if len(seen) == before else 0
        log(f"  scrolling... {len(seen)} unique permalinks so far")
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(int(SCROLL_PAUSE * 1000))

    post_ids = sorted(seen)
    log(f"Scan finished: {len(post_ids)} permalinks found.")
    return post_ids


# --------------------------------------------------------------------------
# Phase 3 - download files behind each permalink
# --------------------------------------------------------------------------

def build_http_session(context) -> requests.Session:
    """requests session that reuses the browser's cookies + user agent."""
    sess = requests.Session()
    for c in context.cookies():
        sess.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
    ua = context.pages[0].evaluate("navigator.userAgent")
    # facebook.com/download/ answers 400 unless the request looks like a real
    # browser navigation, so mirror Chrome's navigation headers
    sess.headers.update({
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    })
    return sess


def find_file_link(page) -> tuple[str, str] | None:
    """Return (href, link_text) of the attachment on a permalink page."""
    try:
        page.wait_for_selector(FILE_LINK_SELECTOR, timeout=15000)
    except PWTimeout:
        return None
    el = page.query_selector(FILE_LINK_SELECTOR)
    href = el.get_attribute("href") or ""
    text = (el.inner_text() or "").strip().split("\n")[0]
    return (href, text) if href else None


def filename_from_response(resp: requests.Response, fallback: str) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    # prefer the RFC 5987 form: filename*=UTF-8''%D8%A7...  (real Arabic name);
    # the plain filename= is often a placeholder like "_.pdf"
    m = re.search(r"filename\*=\s*UTF-8''([^;]+)", cd, re.I)
    if m:
        return sanitize_filename(m.group(1))
    m = re.search(r'filename="?([^";]+)', cd)
    if m and m.group(1).strip() not in ("_.pdf", "_"):
        return sanitize_filename(m.group(1))
    path_name = Path(urlparse(resp.url).path).name
    if path_name and "." in path_name:
        return sanitize_filename(path_name)
    return sanitize_filename(fallback)


def download_file(sess: requests.Session, url: str, fallback_name: str,
                  post_id: str, referer: str) -> tuple[Path, int]:
    with sess.get(url, stream=True, timeout=120,
                  headers={"Referer": referer}) as resp:
        resp.raise_for_status()
        name = filename_from_response(resp, fallback_name)
        ctype = resp.headers.get("Content-Type", "")
        if not name.lower().endswith(".pdf") and "pdf" not in ctype:
            raise ValueError(f"not a PDF (Content-Type: {ctype or '?'}, name: {name})")
        if not name.lower().endswith(".pdf"):
            name += ".pdf"

        dest = PDF_DIR / name
        if dest.exists():  # same name from a different post - keep both
            dest = PDF_DIR / f"{dest.stem}_{post_id}{dest.suffix}"

        # Facebook serves an HTML page (login wall / rate limit) with a .pdf
        # filename when it refuses the download, so trust bytes, not names.
        # Some genuine PDFs have junk before the header; the spec allows it
        # anywhere in the first 1024 bytes.
        chunks = resp.iter_content(chunk_size=65536)
        first = next(chunks, b"")
        if b"%PDF-" not in first[:1024]:
            raise RuntimeError(
                f"response is not a PDF (got {first[:40]!r}) - "
                "likely a rate-limit or login page"
            )

        total = int(resp.headers.get("Content-Length", 0)) or None
        size = 0
        with open(dest, "wb") as fh, tqdm(
            total=total, unit="B", unit_scale=True, unit_divisor=1024,
            desc=f"    {name[:60]}", leave=True,
        ) as bar:
            fh.write(first)
            size += len(first)
            bar.update(len(first))
            for chunk in chunks:
                fh.write(chunk)
                size += len(chunk)
                bar.update(len(chunk))
        return dest, size


def process_permalinks(context, page, post_ids: list[str], manifest: dict,
                       delay: float, limit: int | None, retry_failed: bool) -> None:
    skip_states = {"done", "skipped_not_pdf"} if retry_failed else {"done", "skipped_not_pdf", "error"}
    pending = [p for p in post_ids if manifest.get(p, {}).get("status") not in skip_states]
    if limit:
        pending = pending[:limit]
    already = len(post_ids) - len(pending)
    log(f"{len(pending)} posts to process ({already} already handled, see manifest.json).")

    sess = build_http_session(context)
    consecutive_blocked = 0
    for i, post_id in enumerate(pending, 1):
        url = PERMALINK_TPL.format(post_id=post_id)
        print(f"\n[{i}/{len(pending)}] post {post_id}", flush=True)
        entry = {"permalink": url, "checked_at": datetime.now(timezone.utc).isoformat()}
        try:
            page.goto(url, wait_until="domcontentloaded")
            link = find_file_link(page)
            if link is None:
                # a popup or a bounce to the feed can swallow the post dialog;
                # clear popups and reload once before giving up
                dismiss_popups(page)
                page.goto(url, wait_until="domcontentloaded")
                link = find_file_link(page)
            if link is None:
                entry.update(status="error", error="no attachment link found on post")
                log("  !! no attachment link found - marked as error")
            else:
                href, text = link
                dest, size = download_file(sess, href, text or f"post_{post_id}",
                                           post_id, referer=url)
                entry.update(status="done", filename=dest.name, bytes=size)
                log(f"  saved -> pdfs/{dest.name} ({size / 1024 / 1024:.1f} MB)")
        except ValueError as exc:            # attachment exists but is not a PDF
            entry.update(status="skipped_not_pdf", error=str(exc))
            log(f"  -- skipped: {exc}")
        except Exception as exc:             # network / layout / HTTP errors
            entry.update(status="error", error=f"{type(exc).__name__}: {exc}")
            log(f"  !! error: {exc}")
        if entry.get("status") == "done":
            consecutive_blocked = 0
        elif "not a PDF (got" in entry.get("error", ""):
            consecutive_blocked += 1
            if consecutive_blocked >= 5:
                manifest[post_id] = entry
                save_json(MANIFEST_FILE, manifest)
                log("!! 5 posts in a row served HTML instead of files - "
                    "Facebook is blocking downloads. Stopping; wait a while "
                    "and resume with --retry-failed.")
                break
        manifest[post_id] = entry
        save_json(MANIFEST_FILE, manifest)
        time.sleep(delay)


def summarize(manifest: dict) -> None:
    counts: dict[str, int] = {}
    for entry in manifest.values():
        counts[entry.get("status", "?")] = counts.get(entry.get("status", "?"), 0) + 1
    total_bytes = sum(e.get("bytes", 0) for e in manifest.values())
    print("\n" + "=" * 60)
    print("Summary")
    for status, n in sorted(counts.items()):
        print(f"  {status:18s} {n}")
    print(f"  total downloaded   {total_bytes / 1024 / 1024:.1f} MB -> {PDF_DIR}/")
    print("=" * 60)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rescan", action="store_true",
                    help="ignore permalinks.json and scan the files tab again")
    ap.add_argument("--retry-failed", action="store_true",
                    help="retry permalinks that previously errored")
    ap.add_argument("--limit", type=int, default=None,
                    help="only process the first N pending permalinks")
    ap.add_argument("--delay", type=float, default=4.0,
                    help="seconds between posts (default: 4)")
    args = ap.parse_args()

    PDF_DIR.mkdir(exist_ok=True)
    manifest = load_json(MANIFEST_FILE, {})

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,           # login + Facebook bot-detection need a real window
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        ensure_login(context, page)

        # warm-up: land on the feed once and clear any "Remember password"
        # style popups so they don't swallow the first permalink navigation
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        dismiss_popups(page)

        post_ids = None if args.rescan else load_json(PERMALINKS_FILE, None)
        if post_ids is None:
            post_ids = harvest_permalinks(page)
            save_json(PERMALINKS_FILE, post_ids)
        else:
            log(f"Loaded {len(post_ids)} permalinks from permalinks.json "
                "(use --rescan to refresh).")

        try:
            process_permalinks(context, page, post_ids, manifest,
                               args.delay, args.limit, args.retry_failed)
        except KeyboardInterrupt:
            log("Interrupted - progress is saved, just run the script again to resume.")
        finally:
            summarize(manifest)
            context.close()


if __name__ == "__main__":
    sys.exit(main())
