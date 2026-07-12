#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Conda captures output by default, which makes this long-running command look
# frozen. Live streaming plus unbuffered Python ensures status appears at once.
exec conda run --no-capture-output -n ai \
    python -u "$SCRIPT_DIR/download_telegram_documents.py" "$@"
