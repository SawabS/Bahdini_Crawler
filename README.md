# Bahdini Crawler

A web crawler for collecting Badini (Bahdini) Kurdish text corpora for LLM
fine-tuning. It crawls a configured list of Badini-language websites,
maps their full page structure, and downloads every publicly linked document
(PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, ZIP and more).

## Results at a glance

- **4,485 documents (~7.4 GB)** harvested from 4 sources
- **~10,000 pages** crawled and mapped
- **~7.1M words of extractable Badini text (~9 to 14M LLM tokens)**, with a
  further 5 to 14M words locked in scanned PDFs that need OCR

See [CRAWL_REPORT.md](CRAWL_REPORT.md) for full results and the token
estimate, and [BLOCKERS.md](BLOCKERS.md) for problems encountered (one source
is ISP-blocked, one server is infected with cloaking malware).

## Sources

| Source | Type | Yield |
|---|---|---|
| [spirez.org](https://spirez.org/) | Badini literary magazine | 2,291 PDFs (mostly scanned issues) |
| [zcks.uoz.edu.krd](https://zcks.uoz.edu.krd/) | Zakho Center for Kurdish Studies | 713 PDFs (books, theses, journals) |
| [journal.uod.ac](https://journal.uod.ac/index.php/uodjournal) | University of Duhok journal (OJS) | 1,481 article PDFs |
| [govarabadinan.blogspot.com](https://govarabadinan.blogspot.com/) | Badini blog magazine | HTML posts only, structure mapped |
| [xaniagency.com](https://xaniagency.com/) | Kurdish news agency (WordPress) | HTML articles only, crawl stopped early |
| [govarametin.com](https://govarametin.com/) | Badini magazine | blocked at ISP level, see BLOCKERS.md |

The source list lives in [base_urls.md](base_urls.md).

Additionally, [AbdulrahmanBamarni_PartokxanaElectroni/](AbdulrahmanBamarni_PartokxanaElectroni/)
holds a separate Playwright-based scrape of the **Partokxana Electroni**
Facebook group (1,318 PDFs, ~6 GB, mixed Badini/Arabic/Sorani — Badini
classification tracked in its `pdf_table.md`). See its own README.

## Repository layout

```
Bahdini_Crawler/
├── crawler.py              # the crawler (configuration in the SITES list at the top)
├── base_urls.md            # source sites, robots.txt and sitemap notes
├── govarabadinan.xml       # locally supplied sitemap for the Blogspot source
├── CRAWL_REPORT.md         # full crawl results + Badini token estimate
├── BLOCKERS.md             # blockers and errors encountered
├── scripts/
│   └── token_estimate.py   # PDF sampling + language classification
├── logs/                   # raw session logs of each crawl run
├── AbdulrahmanBamarni_PartokxanaElectroni/  # Facebook-group PDF scrape (see its README)
└── crawls/
    ├── crawl_summary.json  # machine-readable per-site summary
    └── <site>/
        ├── documents/      # downloaded files (NOT in git, ~7.4 GB)
        ├── pages.jsonl     # one JSON record per crawled page
        ├── urls.csv        # flat URL inventory
        ├── documents.csv   # doc URL -> source page -> local file
        ├── site_structure.md  # hierarchical page tree
        └── errors.log      # per-URL failures
```

## Usage

Requirements: Python 3.10+ with `requests`, `beautifulsoup4`, and `lxml`.

```bash
python3 crawler.py                  # crawl every configured site
python3 crawler.py zcks uod         # crawl selected sites only
```

Each site is a dict in the `SITES` list in [crawler.py](crawler.py) with
optional per-site overrides (`delay`, `workers`, `max_pages`, `sitemaps`,
`extra_seeds`, `force_http`). Adding a new source means adding one entry.

How a site is crawled:

1. Fetch robots.txt (rules respected, sitemap declarations harvested)
2. Parse sitemaps recursively (sitemap indexes and gzip supported)
3. BFS-crawl internal links up to depth 10
4. Detect documents by extension, by OJS-style download routes, and by
   response Content-Type
5. Download documents through a priority queue so large page backlogs can
   never starve downloads

To estimate the Kurdish text volume of the downloaded PDFs
(requires `pdftotext` from poppler-utils):

```bash
python3 scripts/token_estimate.py
```

## Next steps

- OCR the ~1,730 scanned spirez PDFs (largest untapped Badini pool)
- Add article-text extraction to persist HTML content from govarabadinan
  and xaniagency
- Re-crawl govarametin.com from an unfiltered network
