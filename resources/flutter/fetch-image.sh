#!/bin/bash
set -euo pipefail
docker pull "${FLUTTER_IMAGE}"
docker image inspect "${FLUTTER_IMAGE}" --format 'IMAGE_ID={{.Id}}'
