#!/bin/bash
# GPP uploads every matching AAB in --artifact-dir. Recheck the verified AAB against its recorded SHA-256, then
# copy only that file into an emptied PUBLICATION_DIR and check the staged copy again.
set -euo pipefail
aab="${RELEASE_AAB:?RELEASE_AAB is required}"
publication_dir="${PUBLICATION_DIR:?PUBLICATION_DIR is required}"
release_dir=$(dirname "$aab")
name=$(basename "$aab")
(cd "$release_dir" && sha256sum --check -- "$name.sha256")
expected=$(cut -d' ' -f1 "$aab.sha256")
rm -rf -- "$publication_dir"
mkdir -p -- "$publication_dir"
install -m 0644 "$aab" "$publication_dir/$name"
cmp -- "$aab" "$publication_dir/$name"
printf '%s  %s\n' "$expected" "$publication_dir/$name" | sha256sum --check -
