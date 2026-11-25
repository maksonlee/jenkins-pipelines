"""Upload the approved release AAB to one Google Play track and commit the edit.

Usage: play_upload.py SERVICE_JSON AAB PACKAGE TRACK STATUS ROLLOUT_FRACTION RELEASE_NOTES_JSON

Environment:
  EXPECTED_AAB_SHA256      SHA-256 shown in the approval input; the bundle must still match it.
  RELEASE_ARTIFACT_REPORT  Optional report from scripts/check_android_release_artifact.py; it must have passed for
                           this commit, bundle, package and version.
  PLAY_VERSION_NAME/CODE   The pubspec version the report is compared with.
  PLAY_METADATA_ROOT       <module>/src/main/play; its listings/ and app details (contact-*.txt,
                           default-language.txt) are synced into the same edit when present.

One edit uploads the bundle, syncs listing text, graphics and app details, sets the track release and is
committed. Flutter, native Android and veil-android releases all upload through this tool.
"""

import hashlib
import json
import os
import pathlib
import subprocess
import sys

import play_api

IMAGE_TYPES = {
    'icon': 'icon',
    'feature-graphic': 'featureGraphic',
    'phone-screenshots': 'phoneScreenshots',
    'seven-inch-screenshots': 'sevenInchScreenshots',
    'ten-inch-screenshots': 'tenInchScreenshots',
    # The directory names Gradle Play Publisher uses for the same image types.
    'tablet-screenshots': 'sevenInchScreenshots',
    'large-tablet-screenshots': 'tenInchScreenshots',
    'tv-banner': 'tvBanner',
    'tv-screenshots': 'tvScreenshots',
    'wear-screenshots': 'wearScreenshots',
}
DETAIL_FILES = (('contact-email.txt', 'contactEmail'), ('contact-phone.txt', 'contactPhone'),
                ('contact-website.txt', 'contactWebsite'), ('default-language.txt', 'defaultLanguage'))
LISTING_LIMITS = (('title', 50), ('short description', 80), ('full description', 4000))


def verify_bundle(bundle: bytes, package_name: str, environ=os.environ, source_commit=None) -> None:
    sha256 = hashlib.sha256(bundle).hexdigest()
    expected = environ.get('EXPECTED_AAB_SHA256', '').strip()
    if expected and expected != sha256:
        raise SystemExit('Release AAB no longer matches the SHA-256 approved for upload')
    report_path = environ.get('RELEASE_ARTIFACT_REPORT')
    if not report_path:
        return
    report = json.loads(pathlib.Path(report_path).read_text(encoding='utf-8'))
    if source_commit is None:
        source_commit = subprocess.check_output(['git', '-C', 'source', 'rev-parse', 'HEAD'], text=True).strip()
    if (
        report.get('status') != 'passed'
        or report.get('source_commit') != source_commit
        or report.get('sha256') != sha256
        or report.get('package') != package_name
        or report.get('version_name') != environ.get('PLAY_VERSION_NAME')
        or str(report.get('version_code')) != environ.get('PLAY_VERSION_CODE')
    ):
        raise SystemExit('Release artifact no longer matches the successful pre-publication checks')
    print(f'Verified upload artifact: source={source_commit}, sha256={sha256}')


def load_release_notes(path: str) -> list:
    with open(path, 'r', encoding='utf-8') as file:
        by_language = json.load(file)
    if not isinstance(by_language, dict) or not by_language:
        raise SystemExit('Google Play release notes are required')
    notes = []
    for language, text in sorted(by_language.items()):
        if not isinstance(language, str) or not isinstance(text, str) or not text.strip():
            raise SystemExit(f'Invalid Google Play release note for language: {language!r}')
        normalized = text.strip()
        if len(normalized) > 500:
            raise SystemExit(f'Google Play release notes exceed 500 characters for {language}: {len(normalized)}')
        notes.append({'language': language, 'text': normalized})
    return notes


def track_release(version_code: str, status: str, rollout_fraction: str, notes: list) -> dict:
    release = {'versionCodes': [version_code], 'status': status, 'releaseNotes': notes}
    if status == 'inProgress':
        if not rollout_fraction.strip():
            raise SystemExit('PLAY_ROLLOUT_FRACTION is required when PLAY_RELEASE_STATUS=inProgress')
        try:
            fraction = float(rollout_fraction)
        except ValueError:
            raise SystemExit(f'Invalid PLAY_ROLLOUT_FRACTION: {rollout_fraction!r}')
        if not 0 < fraction < 1:
            raise SystemExit('PLAY_ROLLOUT_FRACTION must be greater than 0 and less than 1')
        release['userFraction'] = fraction
    elif rollout_fraction.strip():
        print(f'Ignoring PLAY_ROLLOUT_FRACTION because release status is {status}.')
    return release


def read_required_text(path: pathlib.Path) -> str:
    if not path.is_file():
        raise SystemExit(f'Missing Play listing metadata file: {path}')
    text = path.read_text(encoding='utf-8').strip()
    if not text:
        raise SystemExit(f'Play listing metadata file is empty: {path}')
    return text


def content_type_for_image(path: pathlib.Path) -> str:
    suffix = path.suffix.lower()
    if suffix == '.png':
        return 'image/png'
    if suffix in ('.jpg', '.jpeg'):
        return 'image/jpeg'
    raise SystemExit(f'Unsupported Play listing image type: {path}')


def sync_listing_metadata(token: str, package_name: str, edit_id: str, metadata_root: str) -> None:
    listings_root = pathlib.Path(metadata_root) / 'listings'
    if not metadata_root or not listings_root.is_dir():
        print(f'No Play listing metadata found at {listings_root}; skipping listing sync.')
        return
    base = play_api.app_url(package_name)
    upload_base = play_api.upload_url(package_name)
    language_count = 0
    image_count = 0
    for language_dir in sorted(path for path in listings_root.iterdir() if path.is_dir()):
        language = play_api.quote(language_dir.name)
        text_files = [language_dir / name for name in ('title.txt', 'short-description.txt', 'full-description.txt')]
        if any(path.exists() for path in text_files):
            values = [read_required_text(path) for path in text_files]
            for (name, limit), value in zip(LISTING_LIMITS, values):
                if len(value) > limit:
                    raise SystemExit(f'Play {name} for {language_dir.name} exceeds {limit} characters: {len(value)}')
            play_api.request_json('PUT', base + f'/edits/{edit_id}/listings/{language}', token=token, data={
                'title': values[0], 'shortDescription': values[1], 'fullDescription': values[2],
            })
            language_count += 1
            print(f'Updated Play listing text for {language_dir.name}.')

        graphics_root = language_dir / 'graphics'
        if not graphics_root.is_dir():
            continue
        for local_name, image_type in IMAGE_TYPES.items():
            image_dir = graphics_root / local_name
            if not image_dir.is_dir():
                continue
            images = sorted(p for p in image_dir.iterdir() if p.is_file() and p.suffix.lower() in ('.png', '.jpg', '.jpeg'))
            if not images:
                continue
            path = f'/edits/{edit_id}/listings/{language}/{play_api.quote(image_type)}'
            play_api.request_json('DELETE', base + path, token=token)
            for image in images:
                play_api.request_json('POST', upload_base + path + '?uploadType=media', token=token,
                                      data=image.read_bytes(), content_type=content_type_for_image(image))
                image_count += 1
            print(f'Uploaded {len(images)} Play {image_type} image(s) for {language_dir.name}.')
    print(f'Play listing metadata sync complete: {language_count} language(s), {image_count} image(s).')


def sync_app_details(token: str, package_name: str, edit_id: str, metadata_root: str) -> None:
    """Send the contact details and default language kept beside the listings, when the project keeps any."""
    if not metadata_root:
        return
    details = {}
    for name, field in DETAIL_FILES:
        path = pathlib.Path(metadata_root) / name
        if path.is_file():
            details[field] = read_required_text(path)
    if not details:
        return
    play_api.request_json('PATCH', play_api.app_url(package_name) + f'/edits/{edit_id}/details', token=token, data=details)
    print(f'Updated Play app details: {", ".join(sorted(details))}.')


def main(argv, environ=os.environ) -> None:
    service_json, aab_path, package_name, track, status, rollout_fraction, notes_path = argv
    bundle = pathlib.Path(aab_path).read_bytes()
    verify_bundle(bundle, package_name, environ)
    notes = load_release_notes(notes_path)
    token = play_api.access_token(play_api.load_service(service_json))
    base = play_api.app_url(package_name)
    edit_id = play_api.quote(play_api.request_json('POST', base + '/edits', token=token)['id'])
    print(f'Created Google Play edit: {edit_id}')
    uploaded = play_api.request_json('POST', play_api.upload_url(package_name) + f'/edits/{edit_id}/bundles?uploadType=media',
                                     token=token, data=bundle, content_type='application/octet-stream')
    version_code = str(uploaded['versionCode'])
    print(f'Uploaded bundle versionCode: {version_code}')
    sync_listing_metadata(token, package_name, edit_id, environ.get('PLAY_METADATA_ROOT', ''))
    sync_app_details(token, package_name, edit_id, environ.get('PLAY_METADATA_ROOT', ''))
    release = track_release(version_code, status, rollout_fraction, notes)
    play_api.request_json('PUT', base + f'/edits/{edit_id}/tracks/{play_api.quote(track)}', token=token,
                          data={'track': track, 'releases': [release]})
    play_api.request_json('POST', base + f'/edits/{edit_id}:commit', token=token)
    print(f'Committed Google Play {track} release as {status}.')


if __name__ == '__main__':
    main(sys.argv[1:])
