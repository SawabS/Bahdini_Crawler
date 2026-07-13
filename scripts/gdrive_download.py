#!/usr/bin/env python
"""Download every file in a public Google Drive folder.

Usage:
    python scripts/gdrive_download.py FOLDER_URL DEST_DIR

Enumerates the folder (recursively) with gdown, then downloads each file
into DEST_DIR, preserving the folder's subdirectory structure. Files that
already exist locally with a non-zero size are skipped, so interrupted
runs can simply be re-run. Every file's outcome is appended to
DEST_DIR/../documents.csv (id, relative path, bytes, status).
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import gdown

# Google Drive rate-limits bursts of downloads ("may have had many
# accesses"). The block is global (all files fail, not just one) and can
# last a long time, so it is handled as a global pause: probe every
# RATE_LIMIT_WAIT without consuming the file's retries, and only abort
# the run if Drive keeps refusing for RATE_LIMIT_GIVE_UP in a row.
FILE_DELAY = 3  # seconds between files
RETRY_WAITS = (30, 90, 180)  # escalating waits for ordinary failures
RATE_LIMIT_MARKERS = ("many accesses", "Cannot retrieve the public link")
RATE_LIMIT_WAIT = 600  # seconds between probes while rate-limited
RATE_LIMIT_GIVE_UP = 6 * 3600  # abort after this long continuously blocked

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder_url", help="Public Google Drive folder URL")
    parser.add_argument("dest", type=Path, help="Local directory for the files")
    return parser.parse_args()


def format_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def main() -> int:
    args = parse_args()
    dest: Path = args.dest
    dest.mkdir(parents=True, exist_ok=True)

    print(f"Listing folder {DIM}{args.folder_url}{RESET} ...")
    files = gdown.download_folder(args.folder_url, skip_download=True, quiet=True)
    if not files:
        print(f"{RED}No files found — is the folder public?{RESET}")
        return 1
    print(f"Found {len(files)} files.\n")

    manifest_path = dest.parent / "documents.csv"
    new_manifest = not manifest_path.exists()

    downloaded = skipped = failed = 0
    downloaded_bytes = 0
    failures: list[str] = []
    started = time.monotonic()

    with manifest_path.open("a", newline="") as manifest_file:
        manifest = csv.writer(manifest_file)
        if new_manifest:
            manifest.writerow(["drive_id", "path", "bytes", "status"])

        for index, entry in enumerate(files, start=1):
            relative = Path(entry.path)
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            label = f"[{index}/{len(files)}] {relative}"

            if target.exists() and target.stat().st_size > 0:
                skipped += 1
                print(f"{label} {YELLOW}already present{RESET}")
                continue

            print(label)
            attempts = len(RETRY_WAITS) + 1
            attempt = 1
            blocked_since: float | None = None
            while True:
                try:
                    result = gdown.download(id=entry.id, output=str(target), quiet=False)
                except Exception as error:  # noqa: BLE001 - report and move on
                    result = None
                    reason = " ".join(str(error).split()) or type(error).__name__

                if result and target.exists() and target.stat().st_size > 0:
                    size = target.stat().st_size
                    downloaded += 1
                    downloaded_bytes += size
                    manifest.writerow([entry.id, str(relative), size, "downloaded"])
                    manifest_file.flush()
                    time.sleep(FILE_DELAY)
                    break

                if result is None and any(m in reason for m in RATE_LIMIT_MARKERS):
                    # Global rate limit: wait it out, don't blame the file.
                    now = time.monotonic()
                    blocked_since = blocked_since or now
                    blocked_for = now - blocked_since
                    if blocked_for > RATE_LIMIT_GIVE_UP:
                        print(
                            f"{RED}Drive has been rate-limiting for "
                            f"{blocked_for / 3600:.1f} h; aborting the run. "
                            f"Re-run later to resume.{RESET}"
                        )
                        failed += 1
                        failures.append(str(relative))
                        manifest.writerow([entry.id, str(relative), 0, "rate_limited"])
                        manifest_file.flush()
                        raise SystemExit(1)
                    print(
                        f"  {YELLOW}rate-limited by Drive{RESET} "
                        f"(blocked {int(blocked_for / 60)} min so far); "
                        f"next probe in {RATE_LIMIT_WAIT // 60} min"
                    )
                    time.sleep(RATE_LIMIT_WAIT)
                    continue

                blocked_since = None
                print(f"  {RED}attempt {attempt}/{attempts} failed:{RESET} {reason[:200] if result is None else 'empty file'}")
                if attempt > len(RETRY_WAITS):
                    failed += 1
                    failures.append(str(relative))
                    target.unlink(missing_ok=True)
                    manifest.writerow([entry.id, str(relative), 0, "failed"])
                    manifest_file.flush()
                    print(f"  {RED}giving up on this file{RESET}")
                    break
                wait = RETRY_WAITS[attempt - 1]
                print(f"  {DIM}waiting {wait}s before retrying...{RESET}")
                time.sleep(wait)
                attempt += 1

    elapsed = time.monotonic() - started
    print(f"\n{GREEN}Run summary{RESET}")
    print(f"  Downloaded:      {downloaded} files ({format_bytes(downloaded_bytes)})")
    print(f"  Already present: {skipped}")
    print(f"  Failed:          {failed}")
    print(f"  Elapsed:         {int(elapsed // 60):02d}:{int(elapsed % 60):02d}")
    if failures:
        print(f"\n{RED}Failed files{RESET} (re-run the same command to retry):")
        for name in failures:
            print(f"  - {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
