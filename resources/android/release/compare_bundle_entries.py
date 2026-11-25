"""Prove that signing a bundle outside Gradle changed nothing but the signature files.

Usage: compare_bundle_entries.py UNSIGNED.aab SIGNED.aab

Every entry of the unsigned bundle must be in the signed one with the same bytes. The signed bundle may add only
META-INF signature files, and may replace META-INF/MANIFEST.MF.
"""

import hashlib
import re
import sys
import zipfile

SIGNATURE_FILE = re.compile(r'META-INF/[^/]+\.(SF|RSA|DSA|EC)')
MANIFEST = 'META-INF/MANIFEST.MF'


def digests(path):
    with zipfile.ZipFile(path) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise SystemExit(f'{path} has duplicate entries')
        return {name: hashlib.sha256(bundle.read(name)).hexdigest() for name in names}


def compare(unsigned_path, signed_path):
    unsigned, signed = digests(unsigned_path), digests(signed_path)
    if any(SIGNATURE_FILE.fullmatch(name) for name in unsigned):
        raise SystemExit('The unsigned bundle already carries signature files')
    changed = sorted(name for name, digest in unsigned.items() if name != MANIFEST and signed.get(name) != digest)
    if changed:
        raise SystemExit(f'Signing changed or dropped {len(changed)} entries, first: {changed[0]}')
    added = sorted(name for name in signed if name not in unsigned and name != MANIFEST)
    unexpected = [name for name in added if not SIGNATURE_FILE.fullmatch(name)]
    if unexpected:
        raise SystemExit(f'Signing added unexpected entries, first: {unexpected[0]}')
    if not added:
        raise SystemExit('The signed bundle has no signature files')
    return len(unsigned), added


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    preserved, added = compare(sys.argv[1], sys.argv[2])
    print(f'{preserved} original entries preserved; signature files added: {", ".join(added)}')
