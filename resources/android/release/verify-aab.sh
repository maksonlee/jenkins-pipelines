#!/bin/bash
# Verify the signed release AAB on the agent before it is archived or offered for Play upload:
# - it is the only AAB in its directory, and the pinned bundletool validates it;
# - the manifest package is RELEASE_PACKAGE, and versionCode/versionName are recorded in release-manifest.txt;
# - jarsigner verifies every entry, and the signer certificate SHA-256 is recorded as signerSha256;
# - with RELEASE_VERIFY_SIGNER=true, that SHA-256 must equal EXPECTED_UPLOAD_CERT_SHA256 (Vault certificate_sha256);
# - no entry matches RELEASE_FORBIDDEN_AAB_ENTRY (an extended regex), when set.
# Writes <aab>.sha256 with the bare file name next to the bundle.
set -euo pipefail
export LC_ALL=C
shopt -s nullglob
aab="${RELEASE_AAB:?RELEASE_AAB is required}"
release_dir=$(dirname "$aab")
name=$(basename "$aab")
test -s "$aab"
bundles=("$release_dir"/*.aab)
if (( ${#bundles[@]} != 1 )); then
    echo "Expected exactly one AAB in $release_dir." >&2
    exit 1
fi

bundletool="${BUNDLETOOL_JAR:?BUNDLETOOL_JAR is required}"
if ! printf '%s  %s\n' "${BUNDLETOOL_SHA256:?BUNDLETOOL_SHA256 is required}" "$bundletool" | sha256sum --check --status; then
    echo "bundletool at $bundletool does not match its pinned SHA-256." >&2
    exit 1
fi
java -jar "$bundletool" validate --bundle="$aab" > "$release_dir/bundletool-validate.txt"
manifest_value() {
    java -jar "$bundletool" dump manifest --bundle="$aab" --xpath="$1" | tr -d '\r\n'
}
package=$(manifest_value /manifest/@package)
version_code=$(manifest_value /manifest/@android:versionCode)
version_name=$(manifest_value /manifest/@android:versionName)
if [[ "$package" != "${RELEASE_PACKAGE:?RELEASE_PACKAGE is required}" ]]; then
    echo "AAB package $package does not match $RELEASE_PACKAGE." >&2
    exit 1
fi
if [[ ! "$version_code" =~ ^[1-9][0-9]{0,9}$ ]] || (( version_code > 2100000000 )) || [[ -z "$version_name" ]]; then
    echo 'AAB versionCode or versionName is missing or invalid.' >&2
    exit 1
fi
printf 'package=%s\nversionCode=%s\nversionName=%s\n' "$package" "$version_code" "$version_name" \
    > "$release_dir/release-manifest.txt"

signature_report=$release_dir/signature-verification.txt
if ! jarsigner -verify "$aab" > "$signature_report" 2>&1; then
    cat "$signature_report" >&2
    exit 1
fi
grep -q 'jar verified.' "$signature_report"
if grep -qi 'unsigned entries' "$signature_report"; then
    cat "$signature_report" >&2
    echo 'AAB contains unsigned entries.' >&2
    exit 1
fi
actual_signer=$(keytool -printcert -jarfile "$aab" \
    | awk -F': ' '$1 ~ /^[[:space:]]*SHA256$/ { print $2; exit }' \
    | tr -d ':[:space:]' | tr '[:lower:]' '[:upper:]')
if [[ ! "$actual_signer" =~ ^[0-9A-F]{64}$ ]]; then
    echo 'Could not read exactly one signer certificate SHA-256 fingerprint.' >&2
    exit 1
fi
if [[ "${RELEASE_VERIFY_SIGNER:-false}" == true ]]; then
    expected_signer=$(printf %s "${EXPECTED_UPLOAD_CERT_SHA256:?ERROR: expected upload certificate SHA-256 is unset}" \
        | tr -d ':[:space:]' | tr '[:lower:]' '[:upper:]')
    if [[ ! "$expected_signer" =~ ^[0-9A-F]{64}$ ]]; then
        echo 'The expected upload certificate is not exactly one SHA-256 fingerprint.' >&2
        exit 1
    fi
    if [[ "$actual_signer" != "$expected_signer" ]]; then
        echo 'AAB signer does not match the approved upload certificate.' >&2
        exit 1
    fi
    echo 'AAB signer matches the approved upload certificate.'
else
    echo 'No approved upload certificate is configured; Google Play checks the upload key when the AAB is uploaded.'
fi
printf 'signerSha256=%s\n' "$actual_signer" >> "$release_dir/release-manifest.txt"

if [[ -n "${RELEASE_FORBIDDEN_AAB_ENTRY:-}" ]] && unzip -Z1 "$aab" | grep -Eq -- "$RELEASE_FORBIDDEN_AAB_ENTRY"; then
    echo "AAB contains an entry matching $RELEASE_FORBIDDEN_AAB_ENTRY." >&2
    exit 1
fi
(cd "$release_dir" && sha256sum -- "$name" > "$name.sha256")
cat "$release_dir/release-manifest.txt" "$aab.sha256"
