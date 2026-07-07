# Blockers & Errors

## 1. govarametin.com — BLOCKED at ISP level (unresolved)

The site is unreachable from this network in both protocols:

- **HTTPS**: the TLS handshake is intercepted; the certificate presented is
  `CN=*.newrozholdings.com, O=Allai Newroz Telecom, L=Erbil` — the ISP's
  FortiGate firewall, not the real site. Verification fails on any client.
- **HTTP**: returns a captive-portal JavaScript redirect to
  `https://hq-74-gateway.newrozholdings.com:1003/fgtauth?...` (FortiGate
  authentication gateway) instead of site content.
- Applies to both IPv4 (Cloudflare edge 188.114.x.x) and IPv6 — interception
  is SNI-based, so changing DNS does not help.

**Workarounds**: crawl from a different network / VPN, or ask Newroz Telecom
why the domain is categorized/filtered. The crawler config for the site is
ready and will work once the domain is reachable.

## 2. journal.uod.ac — SEO-spam cloaking on the server (worked around)

With a User-Agent containing a crawler token, the server returns **cloaked
spam doorway pages** (gambling "BUJANGTOTO" pages, teepublic merch) instead of
the real OJS journal. With a plain browser UA it serves the legitimate site.
This strongly suggests the OJS install (v3.1.2.4) is **compromised with
cloaking malware** — worth reporting to the University of Duhok IT staff.

*Workaround applied*: plain browser User-Agent; real journal crawled fully.

## 3. xaniagency.com — aggressive rate limiting (worked around, then stopped)

The server answers bursts with HTTP 429 (the first run died on it). Reduced to
2 workers with 1s delay worked reliably. The crawl was later **stopped
intentionally** after ~3,600 pages because the site exposes no documents
(HTML-only articles); full crawl of its ~18.7k sitemap URLs would take hours
for no document yield.

## 4. spirez.org — most PDFs are image-only scans (data-quality blocker)

~76% of sampled spirez PDFs (est. ~1,730 of 2,291) have no text layer.
The files are downloaded and valid, but **OCR is required** before their text
(potentially 5–14M Badini words) can be used for training.

## 5. Minor, expected errors (no action needed)

- **zcks**: 118 × HTTP 404 — dead links on the site itself.
- **govarabadinan**: Blogger's robots.txt disallows `/search` pagination
  (respected); a few post links with unencoded spaces 404 — broken links on
  the blog itself. All posts were still reached via sitemap.
- **uod**: 8 galley pages return 403 (restricted access galleys);
  1 external PDF (publicationethics.org) returns 403 to non-browser clients.

Full per-URL detail: `crawls/<site>/errors.log`.
