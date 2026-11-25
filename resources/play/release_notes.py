"""Collect the Google Play release notes of a native app for one track.

Usage: release_notes.py RELEASE_NOTES_DIR TRACK > notes.json

RELEASE_NOTES_DIR is <module>/src/main/play/release-notes with one directory per language. Each language uses its
<TRACK>.txt, or default.txt when that is missing or empty. The output maps language to text, as play_upload.py
reads it.
"""

import json
import pathlib
import re
import sys

LIMIT = 500


def collect(notes_dir, track):
    if re.fullmatch(r'[a-z][a-z0-9:_-]{0,40}', track) is None:
        raise SystemExit('Unexpected track name')
    languages = sorted(path for path in pathlib.Path(notes_dir).iterdir() if path.is_dir()) \
        if pathlib.Path(notes_dir).is_dir() else []
    if not languages:
        raise SystemExit(f'Missing Google Play release notes under {notes_dir}')
    notes = {}
    for language in languages:
        for name in (f'{track}.txt', 'default.txt'):
            path = language / name
            text = path.read_text(encoding='utf-8').strip() if path.is_file() else ''
            if text:
                break
        else:
            raise SystemExit(f'Missing non-empty {track}.txt or default.txt in {language}')
        if len(text) > LIMIT:
            raise SystemExit(f'{path} exceeds {LIMIT} characters: {len(text)}')
        notes[language.name] = text
    return notes


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    json.dump(collect(sys.argv[1], sys.argv[2]), sys.stdout, ensure_ascii=False)
