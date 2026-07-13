# Pertokên Badinî PDF (پەرتوکێن بادینـی PDF)

Badini book collection shared as a public Google Drive folder, downloaded as
Kurdish text data for the Kurdish LLM fine-tuning project.

## Source

| | |
|---|---|
| Drive folder | <https://drive.google.com/drive/folders/14aQrn3W5tKN4FYrFcTCB4m6PGrR_GAXf> |
| Folder title | پەرتوکێن بادینـی PDF |
| Contents | 312 files, organized into genre subfolders (ئایینی/religion, …) |
| Access | Public, no login required |

## Contents of this folder

| Path | What it is |
|---|---|
| `documents/` | The downloaded books, keeping the Drive subfolder structure. Not in git (`crawls/*/documents/` is gitignored). |
| `documents.csv` | Per-file download log: Drive file id, relative path, size, status. |

## How to (re-)download

```bash
# from the repo root
conda run -n ai --live-stream python scripts/gdrive_download.py \
    "https://drive.google.com/drive/folders/14aQrn3W5tKN4FYrFcTCB4m6PGrR_GAXf" \
    crawls/pertokenbadini/documents
```

Already-present files are skipped, so re-running resumes an interrupted
download and retries anything that failed.
