"""Load only the reviewed signed phone and Wear OS bundles archived by Jenkins."""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

from verify_bundles import (
    BUNDLETOOL_SHA, PACKAGE, PHONE_BUNDLE_FILENAME, PHONE_VERSION_CODE,
    UPLOAD_CERTIFICATE_SHA256, VERSION_NAME, WEAR_BUNDLE_FILENAME, WEAR_VERSION_CODE, WEAR_VERSION_NAME,
)


# Form factor, operator digest parameter, version name, version code, SDK levels and archived name.
RELEASE_BUNDLES = (
    ('phone', 'SIGNED_SHA256', VERSION_NAME, PHONE_VERSION_CODE, '26', '36', PHONE_BUNDLE_FILENAME),
    ('wear', 'SIGNED_WEAR_SHA256', WEAR_VERSION_NAME, WEAR_VERSION_CODE, '30', '35', WEAR_BUNDLE_FILENAME),
)


class ArtifactVerificationError(Exception):
    pass


def read_archive(root, relative, limit=16_384):
    path = root / relative
    try:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ArtifactVerificationError(f'Invalid signed archive path for {relative}')
        with path.open('rb') as file:
            data = file.read(limit + 1)
    except OSError:
        raise ArtifactVerificationError(f'Could not read signed archive file {relative}') from None
    if len(data) > limit:
        raise ArtifactVerificationError(f'Signed archive file exceeds its size limit: {relative}')
    return data


def load_release_bundles(input_directory, source_commit, pipeline_commit, digests):
    """Return {'phone': bytes, 'wear': bytes} after checking the archive against the operator digests."""
    if not isinstance(digests, dict) or set(digests) != {'phone', 'wear'}:
        raise ArtifactVerificationError('Provide the phone and Wear OS digests for signed archive verification')
    identifiers = [('PRODUCT_SHA', source_commit, 40), ('PIPELINE_SHA', pipeline_commit, 40)]
    identifiers += [(parameter, digests[form_factor], 64) for form_factor, parameter, *_ in RELEASE_BUNDLES]
    for name, value, length in identifiers:
        if not isinstance(value, str) or re.fullmatch(rf'[a-f0-9]{{{length}}}', value) is None:
            raise ArtifactVerificationError(f'Invalid {name} for signed archive verification')

    expected_files = {'SHA256SUMS', *(entry[-1] for entry in RELEASE_BUNDLES)}
    try:
        if input_directory.is_symlink():
            raise ArtifactVerificationError('Invalid signed archive directory')
        root = input_directory.resolve(strict=True)
        directory = root / 'bundles'
        if directory.is_symlink() or {path.name for path in directory.iterdir()} != expected_files:
            raise ArtifactVerificationError('Signed archive must contain exactly one phone AAB, one Wear OS AAB and SHA256SUMS')
    except OSError:
        raise ArtifactVerificationError('Could not read signed archive bundle directory') from None

    metadata = {
        'source-commit.txt': source_commit,
        'pipeline-commit.txt': pipeline_commit,
        'upload-certificate-sha256.txt': UPLOAD_CERTIFICATE_SHA256,
    }
    for file, expected in metadata.items():
        if read_archive(root, file).splitlines() != [expected.encode('ascii')]:
            raise ArtifactVerificationError(f'Signed archive identity verification failed: {file}')

    expected_report = {
        'result': 'passed',
        'source_commit': source_commit,
        'bundletool_sha256': BUNDLETOOL_SHA,
        'bundles': [{
            'package': PACKAGE,
            'version_name': version,
            'version_code': code,
            'min_sdk': minimum,
            'target_sdk': target,
            'form_factor': form_factor,
            'sha256': digests[form_factor],
            'upload_certificate_sha256': UPLOAD_CERTIFICATE_SHA256,
            'file': filename,
        } for form_factor, _, version, code, minimum, target, filename in RELEASE_BUNDLES],
    }
    try:
        report = json.loads(read_archive(root, 'signed-bundles.json'))
    except (ValueError, RecursionError):
        raise ArtifactVerificationError('Signed archive signature report is invalid') from None
    if report != expected_report:
        raise ArtifactVerificationError('Signed archive manifest or signature evidence does not match the reviewed release')

    expected_sums = [
        f'{digests[form_factor]}  {filename}'.encode('ascii')
        for form_factor, *_, filename in RELEASE_BUNDLES
    ]
    actual_sums = read_archive(root, 'bundles/SHA256SUMS').splitlines()
    if actual_sums != expected_sums:
        raise ArtifactVerificationError('Signed archive SHA256SUMS does not match the reviewed release')

    bundles = {}
    for form_factor, *_, filename in RELEASE_BUNDLES:
        data = read_archive(root, f'bundles/{filename}', limit=32 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != digests[form_factor]:
            raise ArtifactVerificationError(f'Signed archive {form_factor} AAB bytes do not match the reviewed release')
        bundles[form_factor] = data
    return bundles


def main(argv):
    if len(argv) != 3:
        raise SystemExit('Usage: verify_submission_artifacts.py INPUT_DIRECTORY REPORT_PATH')
    source_commit = os.environ.get('PRODUCT_SHA', '')
    pipeline_commit = os.environ.get('PIPELINE_SHA', '')
    digests = {form_factor: os.environ.get(parameter, '') for form_factor, parameter, *_ in RELEASE_BUNDLES}
    build = os.environ.get('SIGNED_BUILD', '')
    try:
        if re.fullmatch(r'[1-9][0-9]{0,9}', build) is None:
            raise ArtifactVerificationError('Invalid SIGNED_BUILD for signed archive verification')
        load_release_bundles(Path(argv[1]), source_commit, pipeline_commit, digests)
    except ArtifactVerificationError as error:
        raise SystemExit(str(error)) from None
    report = {
        'result': 'passed',
        'source_commit': source_commit,
        'source_build': int(build),
        'source_pipeline_commit': pipeline_commit,
        'package_name': PACKAGE,
        'version_names': {'phone': VERSION_NAME, 'wear': WEAR_VERSION_NAME},
        'bundle_sha256': {
            code: digests[form_factor] for form_factor, _, _, code, *_ in RELEASE_BUNDLES
        },
    }
    Path(argv[2]).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv)
