#!/bin/sh
set -eu
exec sudo -n -- /usr/local/sbin/piano-practice-origin "${SSH_ORIGINAL_COMMAND:-}"
