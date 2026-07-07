# Bahdini Crawler

Data collection for **Badini (Bahdini) Kurdish** LLM fine-tuning. This repo
contains two collectors and the metadata of everything they harvested:

1. **Web crawler** ([crawler.py](crawler.py)): crawls six Badini websites,
   maps their full page structure, and downloads every publicly linked
   document (PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, ZIP and more).
2. **Facebook group scraper**
   ([AbdulrahmanBamarni_PartokxanaElectroni/](AbdulrahmanBamarni_PartokxanaElectroni/)):
   a Playwright-based scraper for the "Partokxana Electroni" (Electronic
   Library) Facebook group.

The downloaded files themselves (~13 GB) are kept out of git; this repo holds
the code, configuration, structure maps, manifests, and reports.

## Where to find what

| I want to... | Go to |
|---|---|
| See what was collected and the token estimate | [docs/CRAWL_REPORT.md](docs/CRAWL_REPORT.md) |
| See what went wrong (blocked site, malware finding, rate limits) | [docs/BLOCKERS.md](docs/BLOCKERS.md) |
| See the source sites, their robots.txt and sitemaps | [config/base_urls.md](config/base_urls.md) |
| Browse a site's page structure | `crawls/<site>/site_structure.md` |
| Look up a downloaded file's origin URL | `crawls/<site>/documents.csv` |
| Check the Facebook scrape and its Badini/Arabic tagging | [AbdulrahmanBamarni_PartokxanaElectroni/](AbdulrahmanBamarni_PartokxanaElectroni/) (`pdf_table.md`, `manifest.json`) |
| Re-run or extend a crawl | [Quick start](#quick-start) below |

## Results at a glance

| Collector | Yield |
|---|---|
| Web crawler (4 of 6 sites yielded documents) | 4,485 documents, ~7.4 GB, ~10,000 pages mapped |
| Facebook group scrape | 1,318 PDFs, ~6 GB (mixed Badini/Arabic/Sorani, tagged in `pdf_table.md`) |
| Extractable Badini text (web PDFs, measured by sampling) | ~7.1M words, roughly 9 to 14M LLM tokens |
| Locked in scanned PDFs, needs OCR | potentially 5 to 14M more words |

### Web sources

| Source | Type | Yield |
|---|---|---|
| [spirez.org](https://spirez.org/) | Badini literary magazine | 2,291 PDFs (mostly scanned issues) |
| [zcks.uoz.edu.krd](https://zcks.uoz.edu.krd/) | Zakho Center for Kurdish Studies | 713 PDFs (books, theses, journals) |
| [journal.uod.ac](https://journal.uod.ac/index.php/uodjournal) | University of Duhok journal (OJS) | 1,481 article PDFs |
| [govarabadinan.blogspot.com](https://govarabadinan.blogspot.com/) | Badini blog magazine | HTML posts only, structure mapped |
| [xaniagency.com](https://xaniagency.com/) | Kurdish news agency (WordPress) | HTML articles only, crawl stopped early |
| [govarametin.com](https://govarametin.com/) | Badini magazine | blocked at ISP level, see [docs/BLOCKERS.md](docs/BLOCKERS.md) |

## Repository layout

```
Bahdini_Crawler/
├── README.md                  <- you are here
├── crawler.py                 # web crawler entry point (site configs in SITES at the top)
│
├── config/
│   ├── base_urls.md           # source sites, robots.txt and sitemap notes
│   └── govarabadinan.xml      # locally supplied sitemap for the Blogspot source
│
├── docs/
│   ├── CRAWL_REPORT.md        # full crawl results + Badini token estimate
│   └── BLOCKERS.md            # blockers and errors encountered
│
├── scripts/
│   └── token_estimate.py      # PDF sampling + Kurdish text classification
│
├── logs/                      # raw session logs of each crawl run
│
├── crawls/                    # web crawler output, one folder per site
│   ├── crawl_summary.json     # machine-readable per-site summary
│   └── <site>/
│       ├── documents/         # downloaded files (NOT in git, ~7.4 GB)
│       ├── pages.jsonl        # one JSON record per crawled page
│       ├── urls.csv           # flat URL inventory
│       ├── documents.csv      # doc URL -> source page -> local file
│       ├── site_structure.md  # hierarchical page tree
│       └── errors.log         # per-URL failures
│
└── AbdulrahmanBamarni_PartokxanaElectroni/   # Facebook group scrape (own README)
    ├── facebook_pdf_downloader.py  # Playwright scraper (login, scan, download)
    ├── pdf_table.md           # per-PDF list with is_bahdini / is_arabic tags
    ├── manifest.json          # per-post download log (makes runs resumable)
    ├── permalinks.json        # harvested group post IDs
    ├── legacy/                # superseded first version of the scraper
    └── pdfs/                  # downloaded PDFs (NOT in git, ~6 GB)
```

## Quick start

### Web crawler

Requires Python 3.10+ with `requests`, `beautifulsoup4`, `lxml`.

```bash
python3 crawler.py                  # crawl every configured site
python3 crawler.py zcks uod         # crawl selected sites only
```

Each site is one dict in the `SITES` list in [crawler.py](crawler.py) with
optional overrides (`delay`, `workers`, `max_pages`, `sitemaps`,
`extra_seeds`, `force_http`). Adding a new source means adding one entry.

Pipeline per site: robots.txt (respected, sitemap declarations harvested),
recursive sitemap parsing, BFS link crawl to depth 10, document detection by
extension / OJS download routes / Content-Type, and a priority download queue
so page backlogs can never starve document downloads.

### Token estimate

Requires `pdftotext` (poppler-utils).

```bash
python3 scripts/token_estimate.py
```

### Facebook scraper

See the dedicated
[README](AbdulrahmanBamarni_PartokxanaElectroni/README.md)
(needs Playwright and a Facebook account that is a member of the group).

## Next steps

- OCR the ~1,730 scanned spirez PDFs (largest untapped Badini pool)
- Add article-text extraction to persist HTML content from govarabadinan
  and xaniagency
- Re-crawl govarametin.com from an unfiltered network
- Finish Badini/Arabic tagging in the Facebook scrape's `pdf_table.md`
