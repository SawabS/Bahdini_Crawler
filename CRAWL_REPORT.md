# Bahdini Crawler — Crawl Report

Crawl date: 2026-07-07 · Crawler: [crawler.py](crawler.py) (Python, requests + BeautifulSoup/lxml)
Method per site: robots.txt → sitemap discovery (recursive, index + gz) → BFS link crawl (depth ≤ 10) → document detection by extension/route + Content-Type verification → prioritized download queue.

## Results overview

| Site | Base URL | Pages crawled | Documents | Doc size | Status |
|---|---|---|---|---|---|
| spirez | https://spirez.org/ | 163 | 2,291 | 1.5 GB | ✅ complete |
| zcks | https://zcks.uoz.edu.krd/ | 657 | 713 | 4.3 GB | ✅ complete |
| uod | https://journal.uod.ac/…/uodjournal | 5,371 | 1,481 | 1.6 GB | ✅ content-complete |
| govarabadinan | https://govarabadinan.blogspot.com/ | 191 | 0 | — | ✅ complete (HTML-only blog) |
| xaniagency | https://xaniagency.com/ | ~3,600 (partial) | 0 | — | ⏹ stopped intentionally — no documents exist |
| govarametin | https://govarametin.com/ | 0 | 0 | — | ❌ blocked (see BLOCKERS.md) |
| **Total** | | **~10,000** | **4,485** | **~7.4 GB** | |

## Per-site outputs

Each `crawls/<site>/` folder contains:

- `documents/` — downloaded files (excluded from git; ~7.4 GB total)
- `pages.jsonl` — one JSON record per page (url, status, title, depth, referer, links, doc links)
- `urls.csv` — flat URL inventory
- `documents.csv` — document URL → source page → local file mapping
- `site_structure.md` — full hierarchical page-structure tree
- `errors.log` — per-URL failures

### spirez.org
Badini literary magazine. 161/163 pages OK. The whole archive is exposed as per-issue/per-article PDFs — 2,291 files. **~76% of sampled PDFs are image-only scans** (est. ~1,730 files) with no text layer → OCR required to unlock their text.

### zcks.uoz.edu.krd (Zakho Center for Kurdish Studies)
539/657 pages OK; the 118 errors are all the site's own dead links (404). 713 documents, 4.3 GB — books, theses, and journal issues. Richest source of extractable Badini text.

### journal.uod.ac (Journal of the University of Duhok, OJS)
All ~1,482 articles across 40 issues crawled; 1,481 article PDFs downloaded (a handful of galleys return 403 — restricted). The crawl stopped at its page cap with ~17k URLs still queued, but those are exclusively citation-format permutations (APA/IEEE/BibTeX… per article) with no content value — article/issue coverage is complete. Language mix of sampled articles: ~48% English, ~42% Arabic, ~10% Kurdish.

### govarabadinan.blogspot.com
Badini blog magazine. 187/191 pages OK via Blogger sitemap + provided [govarabadinan.xml](govarabadinan.xml). Publishes pure HTML posts; no downloadable documents exist. Post text is not persisted by this crawler (structure only) — a text-extraction pass would be needed to harvest it as corpus data.

### xaniagency.com
WordPress news agency, ~18,700 URLs in its sitemap. Crawled ~3,600 pages with **zero documents found** — it publishes HTML articles only, so the crawl was stopped early on request. Like govarabadinan, harvesting its text would need an article-text extraction pass, plus throttling (the site rate-limits with HTTP 429).

### govarametin.com
Unreachable from this network — ISP-level filtering. See [BLOCKERS.md](BLOCKERS.md).

## Estimated Badini token volume (extractable text, PDFs only)

Method: random sample per site (45–60 PDFs), `pdftotext` extraction, script/language classification (Kurdish Arabic-script markers ێ ۆ ڤ ڕ ڵ vs plain Arabic vs Latin), extrapolated to full corpus.

| Site | Est. Kurdish words | Notes |
|---|---|---|
| zcks | ~5,140,000 | main contributor |
| uod | ~1,370,000 | journal is mostly EN/AR |
| spirez | ~615,000 | only the ~25% of PDFs with a text layer |
| **Total** | **~7,100,000 words** | **≈ 9–14M LLM tokens (~11M typical)** |

### Upside not yet captured
- **OCR on spirez scans**: ~1,730 scanned magazine PDFs. At a rough 3–8k words per issue this is potentially **5–14M additional Badini words** — likely the largest untapped pool.
- **HTML article text** from govarabadinan (~190 posts) and xaniagency (~18k articles, mixed dialects): not persisted in this structure-focused crawl.
- Caveat: the classifier separates Kurdish from Arabic/English reliably, but not Badini from Sorani; given all sources are Badini-region publications, most Kurdish content should be Badini.

## Reproducing / re-running

```bash
python3 crawler.py                 # all sites
python3 crawler.py zcks uod        # selected sites
```

Site configs (sitemaps, per-site delay/workers/page caps) are in the `SITES` list at the top of [crawler.py](crawler.py).
