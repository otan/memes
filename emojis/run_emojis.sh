#!/usr/bin/env bash
# Runs run_gimp.py inside GIMP (gimpfu is not available to system python).
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /path/to/image.png" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"

if [[ ! -f "$INPUT" ]]; then
  echo "Not a file: $INPUT" >&2
  exit 1
fi

export RUN_GIMP_INPUT="$INPUT"
cd "$SCRIPT_DIR"

exec gimp -idf --quit --batch-interpreter python-fu-eval \
  -b "import os, sys; sys.path = ['.'] + sys.path; import run_gimp; run_gimp.run(os.environ['RUN_GIMP_INPUT'])"
