# Abdulrahman Bamarni: Partokxana Electroni (پەرتووکخانەیا ئەلیکترۆنی)

PDF collection scraped from the **Partokxana Electroni** ("Electronic
Library") Facebook group, gathered as Kurdish text data for the Kurdish LLM
fine-tuning project (Phase 2, Data Collection; plan lives at
`KI_finetuning/kurdish_llm_finetuning_plan.md`, outside this repo).

Yield: **1,318 PDFs, ~6 GB**, mixed Badini/Arabic/Sorani. Per-file language
tagging (`is_bahdini` / `is_arabic`) is tracked in [pdf_table.md](pdf_table.md).

## Source

| | |
|---|---|
| Group | <https://www.facebook.com/groups/1589495248028152> |
| Files tab | <https://www.facebook.com/groups/1589495248028152/files/files> |
| File location | Each file lives behind a group-post permalink, e.g. <https://www.facebook.com/groups/1589495248028152/permalink/1608627992781544/> |
| Access | Requires a logged-in Facebook account that is a member of the group |

## Contents of this folder

| Path | What it is |
|---|---|
| `facebook_pdf_downloader.py` | The scraper (Playwright + requests). See below. |
| `pdf_table.md` | Per-PDF list with `is_bahdini` / `is_arabic` tags (in progress) |
| `check.txt` | Working notes for pending tag corrections in pdf_table.md |
| `pdfs/` | Downloaded PDF files. Not in git (~6 GB). |
| `permalinks.json` | Cached list of post IDs harvested from the files tab |
| `manifest.json` | Per-post download log: status, filename, size, errors. This is what makes runs resumable. |
| `legacy/` | Superseded first version of the scraper, kept for reference |
| `.fb_profile/` | Persistent Chromium profile holding the Facebook session, so you only log in once. **Contains auth cookies. Never commit or share** (gitignored). |

## How to run

Requires the `ai` conda environment (already has `playwright 1.61`,
`requests`, `tqdm`, and the Chromium browser installed).

```bash
cd "/home/sawab/AI - Project/KI_finetuning/data/Bahdini_Crawler/AbdulrahmanBamarni_PartokxanaElectroni"
conda run -n ai --live-stream python facebook_pdf_downloader.py
```

What happens, in order:

1. **Login**: a Chromium window opens. Log in to Facebook yourself
   (credentials, 2FA, any checkpoint). The script polls for the session
   cookie and continues automatically; nothing to press. On later runs the
   saved session in `.fb_profile/` is reused and this step is skipped.
2. **Scan**: the files tab is opened and auto-scrolled until no new rows
   load; every unique `/permalink/<id>` is collected into `permalinks.json`.
3. **Download**: each permalink is visited, the attachment link
   (`lookaside.fbsbx.com` / `attachment.php`) is located and streamed to
   `pdfs/`. The console shows an overall `[i/N]` counter plus a per-file
   byte progress bar, and each result is written to `manifest.json`
   immediately.

Non-PDF attachments are skipped (recorded as `skipped_not_pdf`). Duplicate
filenames from different posts are kept by suffixing the post ID.

### Useful flags

```bash
python facebook_pdf_downloader.py --limit 5        # test run on 5 posts
python facebook_pdf_downloader.py --rescan         # re-harvest the files tab (new uploads)
python facebook_pdf_downloader.py --retry-failed   # retry posts that errored
python facebook_pdf_downloader.py --delay 8        # slower, gentler pacing (default 4 s)
```

Interrupting with `Ctrl+C` is safe: progress is in `manifest.json`, and the
next run continues with whatever is still pending.

## Troubleshooting

- **"no attachment link found on post"**: the post layout didn't match the
  known attachment selectors, or the file was deleted. Open the permalink
  from `manifest.json` manually to check; if a real file is there, the
  selector in `FILE_LINK_SELECTOR` needs a new pattern.
- **Scan finds fewer files than expected**: Facebook lazy-loads the files
  list; increase `SCROLL_STABLE_ROUNDS` / `SCROLL_PAUSE` at the top of the
  script and run with `--rescan`.
- **Logged out / checkpoint on a later run**: delete `.fb_profile/` and run
  again to do a fresh manual login.
- **Rate limiting**: if Facebook starts throwing errors mid-run, stop,
  wait a while, and resume with a higher `--delay`.

## Notes

- The account used must be a member of the group, otherwise the files tab
  and permalinks are not visible.
- Keep the delay reasonable; this is a personal-account crawl of a group
  you have access to, not a bulk scraper.
