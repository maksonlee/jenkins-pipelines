#!/bin/bash
set -euo pipefail
umask 077
signing_dir="source/${PROJECT_WORKDIR}/android"
mkdir -p "${signing_dir}"
: "${UPLOAD_JKS_B64:?ERROR: UPLOAD_JKS_B64 is empty or unset}"
: "${STORE_PASSWORD:?ERROR: STORE_PASSWORD is empty or unset}"
: "${KEY_PASSWORD:?ERROR: KEY_PASSWORD is empty or unset}"
: "${KEY_ALIAS:?ERROR: KEY_ALIAS is empty or unset}"
printf %s "${UPLOAD_JKS_B64}" | sed 's/^data:[^,]*,//' | tr -d '\r\n ' | base64 -d > "${signing_dir}/upload-keystore.jks"
chmod 600 "${signing_dir}/upload-keystore.jks"
cat > "${signing_dir}/key.properties" <<EOF
storePassword=${STORE_PASSWORD}
keyPassword=${KEY_PASSWORD}
keyAlias=${KEY_ALIAS}
storeFile=upload-keystore.jks
EOF
chmod 600 "${signing_dir}/key.properties"
echo "Release signing files prepared. JKS bytes: $(wc -c < "${signing_dir}/upload-keystore.jks")"
