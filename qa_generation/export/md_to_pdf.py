#!/usr/bin/env python3
"""Render the Markdown report to an A4 PDF, without a LaTeX toolchain.

Markdown -> HTML (python-markdown) -> A4 PDF (Playwright/Chromium). The
Chromium that Playwright already ships is a full print engine, so this needs
no TeX Live, no pandoc and no wkhtmltopdf, and it produces the same page
geometry as the XeLaTeX edition: A4, 19mm side margins, 21mm bottom.

Mermaid diagrams are rendered for real, not left as code blocks: mermaid.js
is injected from a local copy and the script waits for every diagram to
produce SVG before printing. Passing --mermaid-js keeps it fully offline;
without it the script fetches mermaid once into the report directory.

The visual system matches the XeLaTeX edition -- a muted low-chroma palette,
serif body, sans UI face for labels, no rules under headings -- so both PDFs
read as the same document.

Run inside the conda "ai" env, from the repo root:
    python3 qa_generation/export/md_to_pdf.py
    python3 qa_generation/export/md_to_pdf.py --md path/to/other.md
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

import markdown

REPORT_DIR = Path(__file__).resolve().parent.parent / "report"
MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"

CSS = """
@page { size: A4; margin: 19mm 19mm 21mm 19mm; }

/* Muted, low-chroma palette matching the XeLaTeX edition. Pale members are
   washes only; every value that carries text or a mark was contrast-checked
   against the white page (accent 5.70:1, clay 4.07:1, ink 14.54:1,
   inksoft 6.19:1). */
:root {
  --accent:#35706B; --clay:#A9705B; --wash:#CFE0DC; --creamline:#DCE4E0;
  --cream:#EAF0EE; --coralwash:#F5F1EC; --sagewash:#EAF0EE;
  --ink:#262A28; --inksoft:#5B635E;
  --serif: Charter, "Iowan Old Style", Palatino, Georgia, serif;
  --sans: "Helvetica Neue", Helvetica, Arial, sans-serif;
  --mono: Menlo, "DejaVu Sans Mono", monospace;
  --arabic: "Geeza Pro", "SF Arabic", "Noto Naskh Arabic", serif;
}

* { box-sizing: border-box; }
body {
  margin: 0; background: #fff; color: var(--ink);
  font: 10pt/1.5 var(--serif);
  -webkit-font-smoothing: antialiased;
}

h1, h2, h3, h4 { color: var(--ink); margin: 0; font-family: var(--serif); }
/* No rules under headings, per the report's visual system. */
h1 { font-size: 21pt; font-weight: 700; line-height: 1.15; margin-bottom: 4mm; }
h2 { font-size: 13.5pt; font-weight: 700; color: var(--accent);
     margin: 7mm 0 2.5mm; break-after: avoid; }
h3 { font-size: 11pt; font-weight: 700; margin: 5mm 0 1.5mm; break-after: avoid; }
h4 { font-size: 8pt; font-weight: 600; color: var(--inksoft);
     text-transform: uppercase; letter-spacing: .09em;
     font-family: var(--sans); margin: 4mm 0 1.5mm; break-after: avoid; }

p { margin: 0 0 2.6mm; }
strong { font-weight: 700; }
em { font-style: italic; }
a { color: var(--accent); text-decoration: none; }
hr { border: 0; border-top: 0.4pt solid var(--creamline); margin: 6mm 0; }

code {
  font-family: var(--mono); font-size: 8.4pt; color: var(--inksoft);
  background: var(--cream); border-radius: 2pt; padding: 0 1.2mm;
}
pre {
  background: #fff; border: 0.4pt solid var(--creamline); border-radius: 2mm;
  padding: 3mm 4mm; overflow-x: auto; break-inside: avoid;
}
pre code {
  background: none; padding: 0; font-size: 7.6pt; line-height: 1.45;
  color: var(--ink); white-space: pre-wrap; word-break: break-word;
}

/* Blockquotes carry the report's callout cards. */
blockquote {
  margin: 3mm 0; padding: 3mm 4mm; background: var(--coralwash);
  border-left: 1.2pt solid var(--accent); border-radius: 0 2mm 2mm 0;
  break-inside: avoid;
}
blockquote p { margin: 0 0 1.8mm; }
blockquote p:last-child { margin-bottom: 0; }
blockquote strong:first-child { color: var(--accent); }

table {
  width: 100%; border-collapse: collapse; margin: 3mm 0 4mm;
  font-size: 8.6pt; font-variant-numeric: tabular-nums;
  break-inside: avoid;
}
/* Booktabs rhythm: a rule above and below the header, one at the foot,
   nothing vertical and no filled header band. */
thead th {
  font-family: var(--sans); font-size: 7pt; font-weight: 600;
  text-transform: uppercase; letter-spacing: .07em; color: var(--accent);
  text-align: left; padding: 1.6mm 3mm 1.6mm 0;
  border-top: 0.8pt solid var(--inksoft);
  border-bottom: 0.5pt solid var(--inksoft);
}
tbody td { padding: 1.4mm 3mm 1.4mm 0; border-bottom: 0.3pt solid var(--creamline); }
tbody tr:last-child td { border-bottom: 0.8pt solid var(--inksoft); }
th:last-child, td:last-child { padding-right: 0; }

ul, ol { margin: 0 0 3mm; padding-left: 5mm; }
li { margin-bottom: 1.4mm; }
li::marker { color: var(--inksoft); }

img { max-width: 100%; height: auto; display: block; margin: 3mm auto 4mm; }
figure, .mermaid { break-inside: avoid; }
.mermaid { text-align: center; margin: 4mm 0 5mm; }
.mermaid svg { max-width: 100%; height: auto; }

/* Arabic-script runs: larger and looser than the surrounding Latin, since
   the script has a smaller x-height and stacks marks off the baseline. */
[dir="rtl"] {
  font-family: var(--arabic); font-size: 12pt; line-height: 1.95;
  text-align: right; margin: 2mm 0;
}

/* The contents list is a navigation aid, not body copy. */
h2#contents + ul { font-size: 9pt; columns: 2; column-gap: 8mm; }
h2#contents + ul li { margin-bottom: 0.9mm; break-inside: avoid; }
h2#contents + ul ul { padding-left: 4mm; color: var(--inksoft); }
"""

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><style>{css}</style></head>
<body>{body}
<script>{mermaid_js}</script>
<script>
  mermaid.initialize({{
    startOnLoad: false,
    theme: "base",
    themeVariables: {{
      background: "#ffffff",
      primaryColor: "#EAF0EE",
      primaryTextColor: "#262A28",
      primaryBorderColor: "#DCE4E0",
      lineColor: "#35706B",
      fontFamily: "Helvetica Neue, Helvetica, Arial, sans-serif",
      fontSize: "13px"
    }}
  }});
  window.__mermaidDone = false;
  mermaid.run({{ querySelector: ".mermaid" }})
    .then(() => {{ window.__mermaidDone = true; }})
    .catch((e) => {{ window.__mermaidError = String(e); window.__mermaidDone = true; }});
</script>
</body></html>"""


def to_html(md_text: str, mermaid_js: str) -> str:
    html = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "toc", "attr_list", "md_in_html"],
        extension_configs={"toc": {"permalink": False}},
    )
    # python-markdown emits mermaid fences as <pre><code class="language-mermaid">.
    # mermaid.run() needs a bare <pre class="mermaid"> holding the raw source,
    # so rewrite those blocks and unescape the entities markdown introduced.
    def demote(match):
        body = (match.group(1)
                .replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"'))
        return f'<pre class="mermaid">{body}</pre>'

    html = re.sub(
        r'<pre><code class="language-mermaid">(.*?)</code></pre>',
        demote, html, flags=re.S)
    return html


async def render(html_path: Path, pdf_path: Path) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(html_path.as_uri(), wait_until="networkidle")
        # Diagrams are drawn by script after load; printing before they finish
        # silently produces a PDF with blank gaps where the figures should be.
        try:
            await page.wait_for_function("window.__mermaidDone === true", timeout=60_000)
        except Exception:
            print("  warning: mermaid did not signal completion; printing anyway",
                  file=sys.stderr)
        err = await page.evaluate("window.__mermaidError || null")
        if err:
            print(f"  warning: mermaid reported: {err}", file=sys.stderr)
        n = await page.evaluate("document.querySelectorAll('.mermaid svg').length")
        total = await page.evaluate("document.querySelectorAll('.mermaid').length")
        print(f"  diagrams rendered: {n}/{total}")
        await page.pdf(path=str(pdf_path), format="A4", print_background=True,
                       margin={"top": "19mm", "bottom": "21mm",
                               "left": "19mm", "right": "19mm"})
        await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--md", type=Path,
                        default=REPORT_DIR / "qa_generation_report.md")
    parser.add_argument("--pdf", type=Path, default=None,
                        help="output path (default: alongside the markdown)")
    parser.add_argument("--mermaid-js", type=Path, default=None,
                        help="local mermaid.min.js; downloaded to the report "
                             "directory if absent")
    parser.add_argument("--keep-html", action="store_true",
                        help="leave the intermediate HTML on disk for inspection")
    args = parser.parse_args()

    if not args.md.is_file():
        sys.exit(f"{args.md} does not exist")
    pdf_path = args.pdf or args.md.with_suffix(".pdf")

    mermaid_path = args.mermaid_js or (REPORT_DIR / "mermaid.min.js")
    if not mermaid_path.is_file():
        print(f"Fetching mermaid -> {mermaid_path}")
        import urllib.request
        mermaid_path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MERMAID_URL, mermaid_path)
    mermaid_js = mermaid_path.read_text(encoding="utf-8")

    print(f"Converting {args.md.name} ...")
    body = to_html(args.md.read_text(encoding="utf-8"), mermaid_js)
    html = PAGE.format(css=CSS, body=body, mermaid_js=mermaid_js)

    # Written beside the markdown so relative figure paths resolve unchanged.
    html_path = args.md.with_suffix(".render.html")
    html_path.write_text(html, encoding="utf-8")

    asyncio.run(render(html_path, pdf_path))
    if not args.keep_html:
        html_path.unlink()

    print(f"\nWrote {pdf_path} ({pdf_path.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
