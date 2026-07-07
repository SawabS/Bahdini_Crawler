#!/usr/bin/env python3
"""
Bahdini_Crawler — sitemap + recursive crawler with document harvesting.

For each configured site:
  1. Fetch robots.txt (best effort) and respect Disallow rules.
  2. Parse sitemap(s) recursively (sitemapindex + urlset, .gz supported).
  3. BFS-crawl internal links starting from the base URL + sitemap URLs.
  4. Detect and download documents (pdf/doc/docx/xls/xlsx/ppt/pptx/zip/rtf/odt,
     plus OJS /article/download/ style routes), verified by Content-Type.
  5. Write per-site outputs under crawls/<site>/:
       pages.jsonl        one JSON record per crawled page
       urls.csv           url, status, depth, content_type, title
       documents.csv      doc url, source page, local file, size, content_type
       documents/         downloaded files
       site_structure.md  hierarchical path tree of every discovered URL
       errors.log         per-URL failures
  6. Write crawls/crawl_summary.json at the end (used for the root report).
"""

import csv
import gzip
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib import robotparser
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode, unquote

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(ROOT, "crawls")

# NOTE: a UA containing a crawler token made journal.uod.ac serve cloaked
# SEO-spam pages instead of the real OJS site, and looked bot-like to
# xaniagency's rate limiter -> use a plain browser UA.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

DOC_EXT_RE = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|rtf|odt|ods|odp|epub|djvu)($|[?#])", re.I)
DOC_ROUTE_RE = re.compile(
    r"/(article/download|issue/download|article/viewFile|downloadSuppFile)/", re.I)
DOC_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/rtf": ".rtf",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/epub+zip": ".epub",
}
SKIP_EXT_RE = re.compile(
    r"\.(jpe?g|png|gif|webp|svg|ico|bmp|tiff?|mp3|mp4|avi|mov|wmv|webm|ogg|wav|"
    r"css|js|json|woff2?|ttf|eot|otf|xml|rss|atom)($|[?#])", re.I)
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term",
                   "utm_content", "fbclid", "gclid", "replytocom", "share", "m"}

MAX_PAGES = 2500          # per-site crawl cap (HTML pages fetched)
MAX_DEPTH = 10
SITE_TIME_BUDGET = 4500   # seconds per site
REQUEST_TIMEOUT = 40
WORKERS_PER_SITE = 4
DELAY = 0.25              # per-worker politeness delay
MAX_DOC_SIZE = 300 * 1024 * 1024

SITES = [
    {
        "name": "spirez",
        "base": "https://spirez.org/",
        "domains": {"spirez.org", "www.spirez.org"},
        "sitemaps": ["https://spirez.org/sitemap.xml",
                     "https://spirez.org/wp-sitemap.xml",
                     "https://spirez.org/sitemap_index.xml"],
    },
    {
        # aggressive 429 rate limiting -> crawl slowly with fewer workers
        "name": "xaniagency",
        "base": "https://xaniagency.com/",
        "domains": {"xaniagency.com", "www.xaniagency.com"},
        "sitemaps": ["https://xaniagency.com/wp-sitemap.xml"],
        "delay": 1.0,
        "workers": 2,
        "max_pages": 5500,
    },
    {
        # HTTPS is broken for this domain on this network (ISP presents a
        # *.newrozholdings.com cert) -> crawl over plain HTTP.
        "name": "govarametin",
        "base": "http://govarametin.com/",
        "domains": {"govarametin.com", "www.govarametin.com"},
        "sitemaps": ["http://govarametin.com/sitemap_index.xml"],
        "force_http": True,
    },
    {
        "name": "govarabadinan",
        "base": "https://govarabadinan.blogspot.com/",
        "domains": {"govarabadinan.blogspot.com"},
        "sitemaps": ["https://govarabadinan.blogspot.com/sitemap.xml"],
        "local_sitemaps": [os.path.join(ROOT, "govarabadinan.xml")],
    },
    {
        "name": "zcks",
        "base": "https://zcks.uoz.edu.krd/",
        "domains": {"zcks.uoz.edu.krd"},
        "sitemaps": ["https://zcks.uoz.edu.krd/sitemap.xml"],
    },
    {
        "name": "uod",
        "base": "https://journal.uod.ac/index.php/uodjournal",
        "domains": {"journal.uod.ac", "www.journal.uod.ac"},
        "sitemaps": ["https://journal.uod.ac/index.php/uodjournal/sitemap"],
        "extra_seeds": ["https://journal.uod.ac/index.php/uodjournal/issue/archive"],
        # ~3k sitemap URLs + ~1.3k galley viewer pages that hold download links
        "max_pages": 5000,
    },
]


def log(site, msg):
    print(f"[{time.strftime('%H:%M:%S')}] [{site}] {msg}", flush=True)


def normalize_url(url, force_http=False):
    try:
        url, _ = re.subn(r"#.*$", "", url.strip())
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return None
        scheme = "http" if force_http else p.scheme
        netloc = p.netloc.lower()
        netloc = re.sub(r":80$|:443$", "", netloc)
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS]
        query = urlencode(q)
        path = p.path or "/"
        return urlunparse((scheme, netloc, path, p.params, query, ""))
    except Exception:
        return None


def is_doc_url(url):
    return bool(DOC_EXT_RE.search(url) or DOC_ROUTE_RE.search(url))


def safe_filename(url, content_type=None):
    p = urlparse(url)
    base = unquote(os.path.basename(p.path.rstrip("/"))) or "file"
    # OJS-style: /article/download/123/456 -> article_123_456
    m = re.search(r"/(article|issue)/(?:download|viewFile)/([\w.-]+)/?([\w.-]*)", p.path)
    if m:
        base = f"{m.group(1)}_{m.group(2)}" + (f"_{m.group(3)}" if m.group(3) else "")
    base = re.sub(r"[^\w.\-]+", "_", base)[:150]
    if not re.search(r"\.\w{1,5}$", base):
        ext = DOC_CONTENT_TYPES.get((content_type or "").split(";")[0].strip(), "")
        base += ext or ".bin"
    h = hashlib.sha1(url.encode()).hexdigest()[:8]
    root, ext = os.path.splitext(base)
    return f"{root}_{h}{ext}"


class SiteCrawler:
    def __init__(self, cfg):
        self.cfg = cfg
        self.name = cfg["name"]
        self.force_http = cfg.get("force_http", False)
        self.domains = cfg["domains"]
        self.delay = cfg.get("delay", DELAY)
        self.workers = cfg.get("workers", WORKERS_PER_SITE)
        self.out_dir = os.path.join(OUT_ROOT, self.name)
        self.doc_dir = os.path.join(self.out_dir, "documents")
        os.makedirs(self.doc_dir, exist_ok=True)

        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        adapter = requests.adapters.HTTPAdapter(
            max_retries=requests.adapters.Retry(
                total=2, backoff_factor=1.0,
                status_forcelist=[429, 500, 502, 503, 504]),
            pool_maxsize=self.workers * 2)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        self.lock = threading.Lock()
        self.seen = set()          # normalized URLs queued or done
        self.doc_seen = set()
        self.pages = []            # page records
        self.docs = []             # document records
        self.errors = []
        # docs are drained with priority so a large page queue can't starve
        # downloads; the page cap never applies to the doc queue
        self.page_queue = deque()  # (url, depth, referer)
        self.doc_queue = deque()   # (url, referer)
        self.max_pages = cfg.get("max_pages", MAX_PAGES)
        self.robots = None
        self.start_time = None
        self.pages_fetched = 0
        self.stopped_reason = None

    # ---------- robots ----------
    def load_robots(self):
        url = urljoin(self.cfg["base"], "/robots.txt")
        try:
            r = self.session.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200 and r.text.strip():
                rp = robotparser.RobotFileParser()
                rp.parse(r.text.splitlines())
                self.robots = rp
                log(self.name, f"robots.txt loaded ({len(r.text.splitlines())} lines)")
                # harvest sitemap declarations
                for line in r.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        sm = line.split(":", 1)[1].strip()
                        if sm not in self.cfg["sitemaps"]:
                            self.cfg["sitemaps"].append(sm)
            else:
                log(self.name, f"no robots.txt (HTTP {r.status_code})")
        except Exception as e:
            self.record_error(url, f"robots.txt fetch failed: {e}")

    def allowed(self, url):
        if self.robots is None:
            return True
        try:
            return self.robots.can_fetch(USER_AGENT, url) or \
                   self.robots.can_fetch("*", url)
        except Exception:
            return True

    # ---------- sitemaps ----------
    def parse_sitemap_content(self, content, source, depth=0):
        urls = []
        if depth > 5:
            return urls
        if content[:2] == b"\x1f\x8b":
            try:
                content = gzip.decompress(content)
            except Exception:
                return urls
        try:
            soup = BeautifulSoup(content, "xml")
        except Exception:
            soup = BeautifulSoup(content, "lxml")
        # nested sitemap index
        for sm in soup.find_all("sitemap"):
            loc = sm.find("loc")
            if loc and loc.text.strip():
                child = loc.text.strip()
                try:
                    r = self.session.get(child, timeout=REQUEST_TIMEOUT)
                    if r.status_code == 200:
                        urls += self.parse_sitemap_content(r.content, child, depth + 1)
                    else:
                        self.record_error(child, f"sitemap HTTP {r.status_code}")
                except Exception as e:
                    self.record_error(child, f"sitemap fetch failed: {e}")
        for u in soup.find_all("url"):
            loc = u.find("loc")
            if loc and loc.text.strip():
                urls.append(loc.text.strip())
        if urls:
            log(self.name, f"sitemap {source}: {len(urls)} URLs (cumulative at this node)")
        return urls

    def load_sitemaps(self):
        found = []
        for sm in self.cfg.get("sitemaps", []):
            try:
                r = self.session.get(sm, timeout=REQUEST_TIMEOUT)
                if r.status_code == 200:
                    found += self.parse_sitemap_content(r.content, sm)
                else:
                    log(self.name, f"sitemap {sm} -> HTTP {r.status_code}")
            except Exception as e:
                self.record_error(sm, f"sitemap fetch failed: {e}")
        for path in self.cfg.get("local_sitemaps", []):
            try:
                with open(path, "rb") as f:
                    found += self.parse_sitemap_content(f.read(), path)
            except Exception as e:
                self.record_error(path, f"local sitemap read failed: {e}")
        return found

    # ---------- helpers ----------
    def in_scope(self, url):
        return urlparse(url).netloc.lower() in self.domains

    def record_error(self, url, msg):
        with self.lock:
            self.errors.append({"url": url, "error": msg,
                                "time": time.strftime("%Y-%m-%d %H:%M:%S")})

    def enqueue(self, url, depth, referer=""):
        n = normalize_url(url, self.force_http and self.in_scope(url))
        if not n:
            return
        if SKIP_EXT_RE.search(n):
            return
        if is_doc_url(n):
            with self.lock:
                if n not in self.doc_seen:
                    self.doc_seen.add(n)
                    self.doc_queue.append((n, referer))
            return
        if not self.in_scope(n):
            return
        with self.lock:
            if n in self.seen:
                return
            self.seen.add(n)
            self.page_queue.append((n, depth, referer))

    # ---------- fetching ----------
    def handle_page(self, url, depth, referer):
        if not self.allowed(url):
            self.record_error(url, "blocked by robots.txt")
            return
        time.sleep(self.delay)
        try:
            r = self.session.get(url, timeout=REQUEST_TIMEOUT, stream=True)
        except Exception as e:
            self.record_error(url, f"fetch failed: {type(e).__name__}: {e}")
            return
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        rec = {"url": url, "final_url": r.url, "status": r.status_code,
               "content_type": ctype, "depth": depth, "referer": referer,
               "title": "", "n_links": 0, "doc_links": []}
        try:
            if r.status_code != 200:
                self.record_error(url, f"HTTP {r.status_code}")
                with self.lock:
                    self.pages.append(rec)
                return
            # a "page" that is actually a document
            if ctype in DOC_CONTENT_TYPES:
                self.save_document_response(r, url, referer)
                return
            if ctype and "html" not in ctype and "xml" not in ctype:
                with self.lock:
                    self.pages.append(rec)
                return
            body = r.content[: 5 * 1024 * 1024]
        finally:
            r.close()

        try:
            soup = BeautifulSoup(body, "lxml")
        except Exception as e:
            self.record_error(url, f"parse failed: {e}")
            return
        t = soup.find("title")
        rec["title"] = (t.get_text(strip=True) if t else "")[:300]

        links = set()
        for tag, attr in (("a", "href"), ("iframe", "src"), ("embed", "src"),
                          ("object", "data")):
            for el in soup.find_all(tag, **{attr: True}):
                links.add(urljoin(r.url, el[attr]))
        rec["n_links"] = len(links)
        for link in links:
            if is_doc_url(link):
                rec["doc_links"].append(link)
            if depth + 1 <= MAX_DEPTH:
                self.enqueue(link, depth + 1, url)
        with self.lock:
            self.pages.append(rec)
            self.pages_fetched += 1
            if self.pages_fetched % 100 == 0:
                log(self.name, f"{self.pages_fetched} pages, "
                               f"{len(self.docs)} docs, "
                               f"queue={len(self.page_queue)}+{len(self.doc_queue)}")

    def save_document_response(self, r, url, referer):
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        fname = safe_filename(url, ctype)
        path = os.path.join(self.doc_dir, fname)
        size = 0
        try:
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    size += len(chunk)
                    if size > MAX_DOC_SIZE:
                        raise ValueError("exceeds max size")
                    f.write(chunk)
        except Exception as e:
            self.record_error(url, f"download failed: {e}")
            if os.path.exists(path):
                os.remove(path)
            return
        with self.lock:
            self.docs.append({"url": url, "source_page": referer, "file": fname,
                              "bytes": size, "content_type": ctype,
                              "status": r.status_code})
        log(self.name, f"doc saved: {fname} ({size/1024:.0f} KB)")

    def handle_doc(self, url, referer):
        if not self.allowed(url):
            self.record_error(url, "doc blocked by robots.txt")
            return
        time.sleep(self.delay)
        try:
            r = self.session.get(url, timeout=REQUEST_TIMEOUT, stream=True)
        except Exception as e:
            self.record_error(url, f"doc fetch failed: {type(e).__name__}: {e}")
            return
        try:
            if r.status_code != 200:
                self.record_error(url, f"doc HTTP {r.status_code}")
                return
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            # some doc-looking URLs return HTML (viewer pages) -> crawl instead
            if "html" in ctype and not DOC_EXT_RE.search(url):
                if self.in_scope(r.url):
                    body = r.content[: 5 * 1024 * 1024]
                    soup = BeautifulSoup(body, "lxml")
                    for el in soup.find_all("a", href=True):
                        self.enqueue(urljoin(r.url, el["href"]), MAX_DEPTH, url)
                return
            self.save_document_response(r, url, referer)
        finally:
            r.close()

    # ---------- main loop ----------
    def run(self):
        self.start_time = time.time()
        try:
            self.load_robots()
            sm_urls = self.load_sitemaps()
            log(self.name, f"sitemaps yielded {len(sm_urls)} URLs total")
            self.enqueue(self.cfg["base"], 0, "seed")
            for s in self.cfg.get("extra_seeds", []):
                self.enqueue(s, 0, "seed")
            for u in sm_urls:
                self.enqueue(u, 1, "sitemap")
            self.crawl_loop()
        except Exception as e:
            self.record_error(self.cfg["base"],
                              f"FATAL: {e}\n{traceback.format_exc()}")
            self.stopped_reason = f"fatal error: {e}"
        finally:
            self.write_outputs()
        return self.summary()

    def crawl_loop(self):
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            pending = set()
            while True:
                if time.time() - self.start_time > SITE_TIME_BUDGET:
                    self.stopped_reason = "time budget exhausted"
                    break
                page_capped = self.pages_fetched >= self.max_pages
                if page_capped and self.stopped_reason is None:
                    self.stopped_reason = f"page cap ({self.max_pages}) reached"
                with self.lock:
                    while len(pending) < self.workers * 2:
                        if self.doc_queue:
                            url, ref = self.doc_queue.popleft()
                            pending.add(ex.submit(self.handle_doc, url, ref))
                        elif self.page_queue and not page_capped:
                            url, depth, ref = self.page_queue.popleft()
                            pending.add(
                                ex.submit(self.handle_page, url, depth, ref))
                        else:
                            break
                if not pending:
                    with self.lock:
                        empty = not self.doc_queue and \
                            (page_capped or not self.page_queue)
                    if empty:
                        break
                    continue
                done = {f for f in pending if f.done()}
                if not done:
                    time.sleep(0.05)
                    continue
                for f in done:
                    exc = f.exception()
                    if exc:
                        self.record_error("worker", f"{type(exc).__name__}: {exc}")
                pending -= done
            for f in pending:
                try:
                    f.result(timeout=REQUEST_TIMEOUT + 10)
                except Exception:
                    pass

    # ---------- outputs ----------
    def write_outputs(self):
        with open(os.path.join(self.out_dir, "pages.jsonl"), "w", encoding="utf-8") as f:
            for p in self.pages:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        with open(os.path.join(self.out_dir, "urls.csv"), "w", newline="",
                  encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["url", "status", "depth", "content_type", "title"])
            for p in sorted(self.pages, key=lambda x: x["url"]):
                w.writerow([p["url"], p["status"], p["depth"],
                            p["content_type"], p["title"]])
        with open(os.path.join(self.out_dir, "documents.csv"), "w", newline="",
                  encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["url", "source_page", "file", "bytes", "content_type"])
            for d in self.docs:
                w.writerow([d["url"], d["source_page"], d["file"],
                            d["bytes"], d["content_type"]])
        with open(os.path.join(self.out_dir, "errors.log"), "w",
                  encoding="utf-8") as f:
            for e in self.errors:
                f.write(f"{e['time']}  {e['url']}  {e['error']}\n")
        self.write_structure()

    def write_structure(self):
        tree = {}
        for p in self.pages:
            parsed = urlparse(p["url"])
            parts = [parsed.netloc] + [s for s in parsed.path.split("/") if s]
            if parsed.query:
                parts.append("?" + parsed.query)
            node = tree
            for part in parts:
                node = node.setdefault(part, {})
        lines = [f"# Site structure: {self.name}", "",
                 f"Base: {self.cfg['base']}",
                 f"Pages crawled: {len(self.pages)}  |  "
                 f"Documents: {len(self.docs)}  |  Errors: {len(self.errors)}",
                 "", "```"]

        def emit(node, prefix=""):
            items = sorted(node.items())
            for i, (name, child) in enumerate(items):
                last = i == len(items) - 1
                lines.append(prefix + ("└── " if last else "├── ") + name)
                emit(child, prefix + ("    " if last else "│   "))

        emit(tree)
        lines.append("```")
        with open(os.path.join(self.out_dir, "site_structure.md"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def summary(self):
        ok = sum(1 for p in self.pages if p["status"] == 200)
        return {
            "site": self.name,
            "base": self.cfg["base"],
            "pages_total": len(self.pages),
            "pages_ok": ok,
            "documents": len(self.docs),
            "doc_bytes": sum(d["bytes"] for d in self.docs),
            "errors": len(self.errors),
            "stopped_reason": self.stopped_reason or "queue exhausted (complete)",
            "duration_s": round(time.time() - self.start_time, 1),
        }


def main():
    os.makedirs(OUT_ROOT, exist_ok=True)
    only = set(sys.argv[1:])
    sites = [s for s in SITES if not only or s["name"] in only]
    results = []
    with ThreadPoolExecutor(max_workers=len(sites)) as ex:
        futs = {ex.submit(SiteCrawler(cfg).run): cfg["name"] for cfg in sites}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = {"site": name, "fatal": f"{e}"}
            results.append(res)
            log(name, f"DONE: {json.dumps(res)}")
    with open(os.path.join(OUT_ROOT, "crawl_summary.json"), "w") as f:
        json.dump(results, f, indent=2)
    print("ALL DONE")


if __name__ == "__main__":
    main()
