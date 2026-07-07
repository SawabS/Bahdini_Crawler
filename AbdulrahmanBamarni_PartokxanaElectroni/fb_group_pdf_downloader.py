#!/usr/bin/env python
"""
Facebook Group Files -> PDF downloader.

Flow:
  1. Opens a visible Chromium window (persistent profile in ./fb_profile).
     You log in manually (credentials + any 2FA/checkpoint). The session is
     saved, so next runs skip login.
  2. Opens the group's Files tab and scrolls until no new entries appear,
     harvesting every permalink (files live behind permalink posts).
  3. Visits each permalink, extracts the attachment download URL and
     downloads it with a live progress bar.

State is kept in ./manifest.json so re-runs resume where they stopped.

Usage (inside the `ai` conda env):
    python fb_group_pdf_downloader.py
    python fb_group_pdf_downloader.py --all-types      # not just PDFs
    python fb_group_pdf_downloader.py --out ./pdfs     # custom output dir
"""

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from tqdm import tqdm
from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

GROUP_ID = "1589495248028152"
FILES_URL = f"https://www.facebook.com/groups/{GROUP_ID}/files/files"
PERMALINK_RE = re.compile(rf"/groups/{GROUP_ID}/(?:permalink|posts)/(\d+)")

SCRIPT_DIR = Path(__file__).resolve().parent
PROFILE_DIR = SCRIPT_DIR / "fb_profile"
MANIFEST_PATH = SCRIPT_DIR / "manifest.json"

# rounds of scrolling with no new links before we assume the list is complete
IDLE_SCROLL_ROUNDS = 6
SCROLL_PAUSE_S = 2.5
LOGIN_TIMEOUT_S = 600


def log(msg: str) -> None:
    tqdm.write(msg)


# ---------------------------------------------------------------- manifest

def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {"permalinks": {}}


def save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- login

def wait_for_login(page) -> None:
    page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
    if _logged_in(page):
        log("[login] Existing session found - skipping login.")
        return
    log("=" * 62)
    log("[login] Please log in to Facebook in the browser window.")
    log("[login] Complete any 2FA / verification steps as needed.")
    log(f"[login] Waiting up to {LOGIN_TIMEOUT_S // 60} minutes...")
    log("=" * 62)
    deadline = time.time() + LOGIN_TIMEOUT_S
    while time.time() < deadline:
        if _logged_in(page):
            log("[login] Login detected. Continuing.")
            return
        time.sleep(2)
    sys.exit("[login] Timed out waiting for login.")


def _logged_in(page) -> bool:
    cookies = {c["name"]: c["value"] for c in page.context.cookies("https://www.facebook.com")}
    if "c_user" not in cookies:
        return False
    return "checkpoint" not in page.url and "login" not in page.url


# ---------------------------------------------------------------- harvest

def harvest_permalinks(page, manifest: dict) -> None:
    log(f"[scan] Opening files tab: {FILES_URL}")
    page.goto(FILES_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)

    known = manifest["permalinks"]
    idle_rounds = 0
    bar = tqdm(desc="[scan] permalinks found", unit="link", initial=len(known))
    while idle_rounds < IDLE_SCROLL_ROUNDS:
        new = 0
        for href in page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)"):
            m = PERMALINK_RE.search(href)
            if not m:
                continue
            url = f"https://www.facebook.com/groups/{GROUP_ID}/permalink/{m.group(1)}/"
            if url not in known:
                known[url] = {"status": "pending"}
                new += 1
                bar.update(1)
        if new:
            idle_rounds = 0
            save_manifest(manifest)
        else:
            idle_rounds += 1
        page.mouse.wheel(0, 12000)
        page.wait_for_timeout(int(SCROLL_PAUSE_S * 1000))
    bar.close()
    save_manifest(manifest)
    log(f"[scan] Done. {len(known)} permalinks in manifest.")


# ---------------------------------------------------------------- extract

FILE_URL_HINTS = ("fbsbx.com", "facebook.com/download", "/attachment")


def extract_file_url(page, permalink: str) -> str | None:
    """Open a permalink post and return the attachment's download URL."""
    page.goto(permalink, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass
    page.wait_for_timeout(1500)

    anchors = page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => ({href: e.href, dl: e.getAttribute('download')}))",
    )
    for a in anchors:
        if a["dl"] is not None and a["href"].startswith("http"):
            return a["href"]
    for a in anchors:
        if any(h in a["href"] for h in FILE_URL_HINTS):
            return a["href"]

    # Fallback: click a visible "Download" control and let the browser
    # start the download so we can grab its URL.
    btn = page.locator(
        "a:has-text('Download'), div[role=button]:has-text('Download')"
    ).first
    try:
        if btn.is_visible(timeout=2000):
            with page.expect_download(timeout=15000) as dl_info:
                btn.click()
            dl = dl_info.value
            dl.cancel()
            return dl.url
    except PWTimeout:
        pass
    return None


# ---------------------------------------------------------------- download

def filename_from_response(url: str, resp: requests.Response) -> str:
    cd = resp.headers.get("content-disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
    name = unquote(m.group(1)) if m else Path(urlparse(url).path).name or "file"
    name = unicodedata.normalize("NFKC", unquote(name))
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip() or "file"


def unique_path(out_dir: Path, name: str) -> Path:
    path = out_dir / name
    stem, suffix, i = path.stem, path.suffix, 1
    while path.exists():
        path = out_dir / f"{stem}_{i}{suffix}"
        i += 1
    return path


def download_file(session: requests.Session, url: str, out_dir: Path,
                  pdf_only: bool, label: str) -> tuple[str, str]:
    """Returns (status, detail). status: 'done' | 'skipped' | 'failed'."""
    with session.get(url, stream=True, timeout=60) as resp:
        if resp.status_code != 200:
            return "failed", f"HTTP {resp.status_code}"
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        name = filename_from_response(url, resp)
        is_pdf = name.lower().endswith(".pdf") or ctype == "application/pdf"
        if ctype.startswith("text/html"):
            return "failed", "got HTML instead of a file (link expired?)"
        if pdf_only and not is_pdf:
            return "skipped", f"not a PDF ({name}, {ctype})"

        total = int(resp.headers.get("content-length", 0)) or None
        path = unique_path(out_dir, name)
        bar = tqdm(total=total, unit="B", unit_scale=True, unit_divisor=1024,
                   desc=f"  {label} {name[:48]}", leave=True)
        with open(path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                fh.write(chunk)
                bar.update(len(chunk))
        bar.close()

        if is_pdf:
            with open(path, "rb") as fh:
                if fh.read(5) != b"%PDF-":
                    path.unlink(missing_ok=True)
                    return "failed", "downloaded data is not a valid PDF"
        return "done", path.name


def build_session(context) -> requests.Session:
    session = requests.Session()
    for c in context.cookies():
        session.cookies.set(c["name"], c["value"],
                            domain=c.get("domain", ""), path=c.get("path", "/"))
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    return session


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description="Download PDFs from a Facebook group's Files tab.")
    ap.add_argument("--out", default=str(SCRIPT_DIR / "downloads"), help="output directory")
    ap.add_argument("--all-types", action="store_true", help="download every file type, not only PDFs")
    ap.add_argument("--rescan", action="store_true", help="re-scan the files tab even if manifest has links")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-notifications"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        wait_for_login(page)

        if args.rescan or not manifest["permalinks"]:
            harvest_permalinks(page, manifest)
        else:
            log(f"[scan] Reusing {len(manifest['permalinks'])} permalinks from "
                f"manifest.json (use --rescan to refresh).")

        pending = [(u, e) for u, e in manifest["permalinks"].items()
                   if e.get("status") not in ("done", "skipped")]
        total = len(manifest["permalinks"])
        log(f"\n[download] {len(pending)} of {total} permalinks still pending. "
            f"Output: {out_dir}\n")

        session = build_session(context)
        counts = {"done": 0, "skipped": 0, "failed": 0}
        for i, (permalink, entry) in enumerate(pending, 1):
            label = f"[{i}/{len(pending)}]"
            log(f"{label} {permalink}")
            try:
                file_url = extract_file_url(page, permalink)
                if not file_url:
                    entry.update(status="failed", error="no file link found on post")
                    counts["failed"] += 1
                    log("    !! no downloadable file link found")
                else:
                    status, detail = download_file(session, file_url, out_dir,
                                                   not args.all_types, label)
                    entry.update(status=status)
                    counts[status] += 1
                    if status == "done":
                        entry["file"] = detail
                        log(f"    -> saved: {detail}")
                    else:
                        entry["error"] = detail
                        log(f"    !! {status}: {detail}")
            except Exception as exc:  # noqa: BLE001 - keep the batch going
                entry.update(status="failed", error=str(exc))
                counts["failed"] += 1
                log(f"    !! error: {exc}")
            save_manifest(manifest)

        context.close()

    log("\n" + "=" * 62)
    log(f"Finished. downloaded={counts['done']}  skipped(non-PDF)={counts['skipped']}  "
        f"failed={counts['failed']}")
    if counts["failed"]:
        log("Failed items are marked in manifest.json - re-run the script to retry them.")
    log("=" * 62)


if __name__ == "__main__":
    main()
