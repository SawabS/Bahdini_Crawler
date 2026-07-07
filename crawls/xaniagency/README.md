# xaniagency — partial crawl (stopped intentionally)

The crawl was stopped by request after ~3,600 of ~18,700 sitemap URLs:
the site publishes HTML-only articles and exposes **no downloadable
documents** (0 found in 3,600 pages).

Notes for a future text-harvesting run:
- Sitemap: https://xaniagency.com/wp-sitemap.xml (WordPress, 10+ post parts × 2,000 URLs)
- The server rate-limits bursts with HTTP 429 — use ≤2 workers with ≥1s delay
  (already configured for this site in crawler.py).
- Because the process was stopped mid-run, pages.jsonl/urls.csv were not
  written for this site.
