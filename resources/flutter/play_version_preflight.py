"""Fail before a Google Play upload unless the candidate versionCode is new.

Usage: play_version_preflight.py SERVICE_JSON PACKAGE VERSION_NAME VERSION_CODE

The candidate versionCode must be greater than every bundle already uploaded for the package. A temporary edit is
opened only to list bundles and is always deleted again.
"""

import sys

import play_api


def highest_bundle_version_code(token: str, package_name: str) -> int:
    base = play_api.app_url(package_name)
    edit = play_api.request_json('POST', base + '/edits', token=token, data={}, timeout=60)
    edit_id = play_api.quote(edit['id'])
    try:
        bundles = play_api.request_json('GET', base + f'/edits/{edit_id}/bundles', token=token, timeout=60).get('bundles', [])
    finally:
        play_api.request_json('DELETE', base + f'/edits/{edit_id}', token=token, timeout=60)
    return max((int(bundle['versionCode']) for bundle in bundles if bundle.get('versionCode') is not None), default=0)


def main(argv) -> None:
    service_json, package_name, version_name, version_code_text = argv
    version_code = int(version_code_text)
    token = play_api.access_token(play_api.load_service(service_json))
    highest = highest_bundle_version_code(token, package_name)
    print(f'Google Play highest versionCode: {highest}; candidate: {version_name}+{version_code}')
    if version_code <= highest:
        raise SystemExit(
            f'Google Play versionCode must be greater than {highest}; '
            f'candidate {version_code} has already been used or is stale.'
        )


if __name__ == '__main__':
    main(sys.argv[1:])
