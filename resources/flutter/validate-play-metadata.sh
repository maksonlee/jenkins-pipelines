#!/bin/bash
set -euo pipefail
docker pull "${PYTHON_TEST_IMAGE}"
docker run --rm \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD/source:/workspace:ro" \
  -w /workspace \
  "${PYTHON_TEST_IMAGE}" \
  python scripts/validate_play_metadata.py
