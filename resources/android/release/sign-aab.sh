#!/bin/bash
# Sign an unsigned release bundle outside Gradle with the upload key from Vault, for a project whose release build
# is unsigned on purpose. The keystore is decoded into a private directory on tmpfs and removed on every exit; the
# passwords reach jarsigner only through the environment.
#   UNSIGNED_AAB, SIGNED_AAB            input and output paths
#   UPLOAD_JKS_B64, STORE_PASSWORD, KEY_PASSWORD, KEY_ALIAS   from Vault
set -euo pipefail
umask 077
: "${UNSIGNED_AAB:?}" "${SIGNED_AAB:?}" "${UPLOAD_JKS_B64:?}" "${STORE_PASSWORD:?}" "${KEY_PASSWORD:?}" "${KEY_ALIAS:?}"
test -s "$UNSIGNED_AAB"
test ! -e "$SIGNED_AAB"
key_dir=$(mktemp -d /dev/shm/upload-key.XXXXXX)
trap 'rm -rf -- "$key_dir"' EXIT
printf %s "$UPLOAD_JKS_B64" | tr -d '\r\n ' | base64 -d > "$key_dir/upload.jks"
mkdir -p "$(dirname "$SIGNED_AAB")"
jarsigner -keystore "$key_dir/upload.jks" -storepass:env STORE_PASSWORD -keypass:env KEY_PASSWORD \
  -sigalg SHA256withRSA -digestalg SHA-256 -signedjar "$SIGNED_AAB" "$UNSIGNED_AAB" "$KEY_ALIAS" > /dev/null
chmod 644 "$SIGNED_AAB"
echo "Signed $(basename "$SIGNED_AAB")"
