#!/usr/bin/env python3
"""Analyze cached KDE and GNOME catalogs and append common strings.

Run scripts/download_translations.py first to refresh the source caches.
Existing POT and PO entries are preserved and only new entries are appended.

Usage:
  python3 scripts/analyze_translations.py
  python3 scripts/analyze_translations.py --min-count 15
  python3 scripts/analyze_translations.py --kde-cache DIR --gnome-cache DIR
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parent.parent
MAX_LEN = 200
CREDIT_CTXTS = {"NAME OF TRANSLATORS", "EMAIL OF TRANSLATORS"}
LANGUAGE_RE = re.compile(r"^[A-Za-z0-9]+(?:[_@.-][A-Za-z0-9]+)*$")


def usable(entry):
    return (not entry.obsolete and entry.msgid and not entry.msgid_plural
            and len(entry.msgid) <= MAX_LEN and "\n" not in entry.msgid
            and entry.msgctxt not in CREDIT_CTXTS)


def collect(cache):
    """Return per-domain occurrence counts and per-language translations."""
    occurrences = Counter()
    translations = {}
    if not cache.is_dir():
        return occurrences, translations

    language_dirs = sorted(path for path in cache.iterdir()
                           if path.is_dir() and LANGUAGE_RE.fullmatch(path.name))
    for language_dir in language_dirs:
        language = language_dir.name
        files_per_key = Counter()
        counters = defaultdict(Counter)
        paths = sorted(language_dir.rglob("*.po")) + sorted(language_dir.rglob("*.mo"))
        for po_path in paths:
            try:
                po = polib.pofile(str(po_path)) if po_path.suffix == ".po" else polib.mofile(str(po_path))
            except Exception as error:
                print(f"bad {po_path}: {error}", file=sys.stderr)
                continue
            seen = set()
            for entry in po:
                if not usable(entry):
                    continue
                key = (entry.msgctxt or "", entry.msgid)
                if key not in seen:
                    seen.add(key)
                    files_per_key[key] += 1
                if entry.msgstr and "fuzzy" not in entry.flags:
                    counters[key][entry.msgstr] += 1
        for key, count in files_per_key.items():
            occurrences[key] = max(occurrences[key], count)
        translations[language] = counters
    return occurrences, translations


def collect_all(caches):
    occurrences = Counter()
    translations = defaultdict(lambda: defaultdict(Counter))
    for cache in caches:
        source_occurrences, source_translations = collect(cache)
        occurrences.update(source_occurrences)
        for language, counters in source_translations.items():
            for key, counts in counters.items():
                translations[language][key].update(counts)
    return occurrences, translations


def entry_for(key, occurrences, msgstr=""):
    return polib.POEntry(msgctxt=key[0] or None, msgid=key[1], msgstr=msgstr,
                         comment=f"Occurs in {occurrences[key]} KDE+GNOME PO files")


def load_or_create(path):
    if path.exists():
        return polib.pofile(str(path)), False
    po = polib.POFile()
    po.metadata = {"Project-Id-Version": "core-i18n", "MIME-Version": "1.0",
                   "Content-Type": "text/plain; charset=UTF-8",
                   "Content-Transfer-Encoding": "8bit"}
    return po, True


def append_missing(po, keys, occurrences):
    existing = {(entry.msgctxt or "", entry.msgid) for entry in po if not entry.obsolete}
    added = 0
    for key in keys:
        if key not in existing:
            po.append(entry_for(key, occurrences))
            existing.add(key)
            added += 1
    return added


def build(caches, out, min_count, pot_path):
    occurrences, translations = collect_all(caches)
    keys = sorted((key for key, count in occurrences.items() if count >= min_count),
                  key=lambda key: (-occurrences[key], key))
    print(f"{len(keys)} strings occur >= {min_count} times")
    out.mkdir(parents=True, exist_ok=True)

    pot, _ = load_or_create(pot_path)
    added = append_missing(pot, keys, occurrences)
    if added or not pot_path.exists():
        pot.save(str(pot_path))
    print(f"POT: added {added} strings")

    for language, counters in translations.items():
        po_path = out / f"{language}.po"
        po, is_new = load_or_create(po_path)
        existing = {(entry.msgctxt or "", entry.msgid) for entry in po if not entry.obsolete}
        added = 0
        for key in keys:
            counts = counters.get(key)
            if key in existing or not counts:
                continue
            translation = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
            po.append(entry_for(key, occurrences, translation))
            existing.add(key)
            added += 1
        if added or is_new:
            po.metadata["Language"] = language
            po.save(str(po_path))
        print(f"{language}: added {added} strings")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kde-cache", type=Path, default=ROOT / ".cache" / "kde-l10n")
    parser.add_argument("--gnome-cache", type=Path, default=ROOT / ".cache" / "gnome-l10n")
    parser.add_argument("--min-count", type=int, default=10)
    parser.add_argument("--pot", type=Path, default=ROOT / "core-i18n.pot")
    parser.add_argument("--out", type=Path, default=ROOT / "po")
    args = parser.parse_args()
    build([args.kde_cache, args.gnome_cache], args.out, args.min_count, args.pot)


if __name__ == "__main__":
    main()