"""Verify and archive the two Cloud Spend for AWS upload bundles."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


PACKAGE = 'com.maksonlee.cloudcost.aws'
ANDROID = '{http://schemas.android.com/apk/res/android}'
BUNDLETOOL = Path('/release-tools/bundletool-all-1.18.3.jar')
BUNDLETOOL_SHA = 'a099cfa1543f55593bc2ed16a70a7c67fe54b1747bb7301f37fdfd6d91028e29'


class VerificationError(Exception):
    pass


def sha256(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def capture(command, action):
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode:
        raise VerificationError(f'{action} failed (exit {result.returncode})')
    return result.stdout


def require_signature(report, status):
    lower = report.lower()
    if any(message in lower for message in ('unsigned entries', 'treated as unsigned', 'jar is unsigned')):
        raise VerificationError('AAB contains unsigned entries')
    if status == 0 and 'jar verified.' in lower:
        return
    if status == 4 and 'jar verified, with signer errors.' in lower and 'Error:' in report:
        errors = report.split('Error:', 1)[1].split('Warning:', 1)[0].strip().splitlines()
        allowed = (
            'This jar contains entries whose certificate chain is invalid. Reason: PKIX path building failed:',
            'This jar contains entries whose signer certificate is self-signed.',
        )
        if errors and all(line.strip().startswith(allowed) for line in errors if line.strip()):
            return
    raise VerificationError(f'AAB JAR signature check failed (exit {status})')


def signer_fingerprint(report):
    signers = re.findall(r'^Signer #[0-9]+:\s*$', report, re.MULTILINE)
    match = re.search(r'^\s*SHA256:\s*((?:[A-Fa-f0-9]{2}:){31}[A-Fa-f0-9]{2})\s*$', report, re.MULTILINE)
    if len(signers) != 1 or match is None:
        raise VerificationError('AAB does not have exactly one identifiable signer')
    return match.group(1).replace(':', '').lower()


def verify_manifest(xml, version, code, form_factor):
    root = ET.fromstring(xml)
    sdk = root.find('uses-sdk')
    actual = {
        'package': root.get('package'),
        'version_name': root.get(ANDROID + 'versionName'),
        'version_code': root.get(ANDROID + 'versionCode'),
        'min_sdk': sdk.get(ANDROID + 'minSdkVersion') if sdk is not None else None,
        'target_sdk': sdk.get(ANDROID + 'targetSdkVersion') if sdk is not None else None,
    }
    minimum, target = ('26', '36') if form_factor == 'phone' else ('30', '35')
    expected = dict(package=PACKAGE, version_name=version, version_code=code, min_sdk=minimum, target_sdk=target)
    if actual != expected:
        raise VerificationError(f'{form_factor} AAB manifest does not match the expected package, version, or SDK levels')

    watch = root.findall(f"uses-feature[@{ANDROID}name='android.hardware.type.watch']")
    standalone = root.findall(f"application/meta-data[@{ANDROID}name='com.google.android.wearable.standalone']")
    if form_factor == 'wear':
        if len(watch) != 1 or watch[0].get(ANDROID + 'required', 'true') != 'true':
            raise VerificationError('Wear AAB must require watch hardware')
        if len(standalone) != 1 or standalone[0].get(ANDROID + 'value') != 'true':
            raise VerificationError('Wear AAB must declare standalone operation')
    elif watch or standalone:
        raise VerificationError('Phone AAB unexpectedly declares Wear OS targeting')
    return actual | {'form_factor': form_factor}


def verify_bundle(bundle, form_factor, version, code, expected_signer):
    with zipfile.ZipFile(bundle) as archive:
        required = {'base/manifest/AndroidManifest.xml', 'base/dex/classes.dex'}
        if not required.issubset(archive.namelist()) or archive.testzip() is not None:
            raise VerificationError(f'{form_factor} AAB ZIP structure or CRC check failed')

    signature = subprocess.run(
        ['jarsigner', '-J-Duser.language=en', '-J-Duser.country=US', '-verify', '-strict', str(bundle)],
        capture_output=True, text=True, errors='replace', check=False,
    )
    require_signature(signature.stdout + signature.stderr, signature.returncode)
    certificate = capture(
        ['keytool', '-J-Duser.language=en', '-J-Duser.country=US', '-printcert', '-jarfile', str(bundle)],
        'AAB signer certificate read',
    )
    signer = signer_fingerprint(certificate.decode(errors='replace'))
    if signer != expected_signer:
        raise VerificationError(f'{form_factor} AAB signer does not match the Vault-bound upload certificate')

    tool = ['java', '-jar', str(BUNDLETOOL)]
    capture([*tool, 'validate', '--bundle', str(bundle)], f'{form_factor} bundletool validate')
    xml = capture([*tool, 'dump', 'manifest', '--bundle', str(bundle), '--module', 'base'], 'AAB manifest read')
    return verify_manifest(xml, version, code, form_factor) | {
        'sha256': sha256(bundle),
        'upload_certificate_sha256': signer,
    }


def archive_bundles(source, evidence):
    if sha256(BUNDLETOOL) != BUNDLETOOL_SHA:
        raise VerificationError('Worker bundletool SHA-256 differs from the pinned tool')
    certificate = capture(
        ['keytool', '-exportcert', '-keystore', os.environ['JKS_PATH'],
         '-storepass:env', 'STORE_PASSWORD', '-alias', os.environ['KEY_ALIAS']],
        'Vault-bound upload certificate read',
    )
    expected_signer = hashlib.sha256(certificate).hexdigest()
    version = os.environ['VERSION_NAME']
    bundles = []
    for form_factor, code, module in (
        ('phone', os.environ['PHONE_VERSION_CODE'], 'aws'),
        ('wear', os.environ['WEAR_VERSION_CODE'], 'aws-wear'),
    ):
        bundle = source / f'apps/{module}/build/outputs/bundle/release/{module}-release.aab'
        result = verify_bundle(bundle, form_factor, version, code, expected_signer)
        result['file'] = f'{form_factor}-{version}-{code}.aab'
        bundles.append((bundle, result))

    with tempfile.TemporaryDirectory(prefix='verified-', dir=evidence) as staging:
        destination = Path(staging) / 'bundles'
        destination.mkdir()
        checksums = []
        for bundle, result in bundles:
            copy = destination / result['file']
            shutil.copyfile(bundle, copy)
            if sha256(copy) != result['sha256']:
                raise VerificationError('Archived AAB copy does not match verified bytes')
            checksums.append(f"{result['sha256']}  {copy.name}\n")
        (destination / 'SHA256SUMS').write_text(''.join(checksums), encoding='ascii')
        destination.rename(evidence / 'bundles')

    report = {
        'result': 'passed',
        'source_commit': os.environ['PRODUCT_SHA'],
        'bundletool_sha256': BUNDLETOOL_SHA,
        'bundles': [result for _, result in bundles],
    }
    (evidence / 'signed-bundles.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Verified and archived both signed AABs for {PACKAGE} {version} ({os.environ["PRODUCT_SHA"]})')


if __name__ == '__main__':
    try:
        archive_bundles(Path(sys.argv[1]), Path(sys.argv[2]))
    except VerificationError as error:
        raise SystemExit(str(error)) from None
    except (OSError, ET.ParseError, zipfile.BadZipFile, KeyError):
        raise SystemExit('Bundle verification could not read a required artifact, tool, or environment binding') from None
