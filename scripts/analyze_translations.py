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
import unicodedata
from collections import Counter, defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parent.parent
MAX_LEN = 200
CREDIT_CTXTS = {"NAME OF TRANSLATORS", "EMAIL OF TRANSLATORS"}
LANGUAGE_RE = re.compile(r"^[A-Za-z0-9]+(?:[_@.-][A-Za-z0-9]+)*$")


def log(message):
    print(message, flush=True)


def msgid_sort_key(msgid):
    normalized = "".join(char.casefold() for char in msgid
                          if unicodedata.category(char)[0] not in {"P", "S"})
    ignored = sum(unicodedata.category(char)[0] in {"P", "S"} for char in msgid)
    return normalized, ignored, msgid.casefold(), msgid


def usable(entry):
    return (not entry.obsolete and entry.msgid and not entry.msgid_plural
            and len(entry.msgid) <= MAX_LEN and "\n" not in entry.msgid
            and entry.msgctxt not in CREDIT_CTXTS)


def collect_catalog(language, po_path):
    try:
        po = polib.pofile(str(po_path)) if po_path.suffix == ".po" else polib.mofile(str(po_path))
    except Exception as error:
        return language, po_path, None, None, str(error)

    seen = set()
    counters = Counter()
    for entry in po:
        if not usable(entry):
            continue
        key = entry.msgid
        seen.add(key)
        if entry.msgstr and "fuzzy" not in entry.flags:
            counters[key, entry.msgstr] += 1
    return language, po_path, seen, counters, None


def collect(cache, source, executor, jobs):
    """Return per-domain occurrence counts and per-language translations."""
    occurrences = Counter()
    translations = {}
    if not cache.is_dir():
        log(f"{source}: cache not found, skipping {cache}")
        return occurrences, translations

    language_dirs = sorted(path for path in cache.iterdir()
                           if path.is_dir() and LANGUAGE_RE.fullmatch(path.name))
    log(f"{source}: scanning {len(language_dirs)} language directories in {cache}")
    language_files = {}
    language_counts = defaultdict(Counter)
    language_translations = defaultdict(lambda: defaultdict(Counter))
    pending = deque()
    total_files = 0
    for language_index, language_dir in enumerate(language_dirs, 1):
        language = language_dir.name
        paths = sorted(language_dir.rglob("*.po")) + sorted(language_dir.rglob("*.mo"))
        language_files[language] = len(paths)
        total_files += len(paths)
        log(f"{source} [{language_index}/{len(language_dirs)}] {language}: queued {len(paths)} catalogs")
        for po_path in paths:
            pending.append((language, po_path))

    total_completed = 0
    active = {}
    limit = max(jobs, jobs * 2)
    progress_steps = {language: max(1, (count + 9) // 10)
                      for language, count in language_files.items()}
    completed_by_language = Counter()

    while pending or active:
        while pending and len(active) < limit:
            language, po_path = pending.popleft()
            future = executor.submit(collect_catalog, language, po_path)
            active[future] = (language, po_path)
        done, _ = wait(active, return_when=FIRST_COMPLETED)
        for future in done:
            active.pop(future)
            language, po_path, seen, counters, error = future.result()
            total_completed += 1
            completed_by_language[language] += 1
            if error:
                print(f"bad {po_path}: {error}", file=sys.stderr, flush=True)
            else:
                language_counts[language].update(seen)
                for (key, msgstr), count in counters.items():
                    language_translations[language][key][msgstr] += count
            language_completed = completed_by_language[language]
            if (language_completed % progress_steps[language] == 0
                    or language_completed == language_files[language]):
                log(f"{source}: scanned {total_completed}/{total_files} catalogs; "
                    f"{language} {language_completed}/{language_files[language]} ({po_path.name})")

    for language, files_per_key in language_counts.items():
        for key, count in files_per_key.items():
            occurrences[key] = max(occurrences[key], count)
    translations.update(language_translations)
    log(f"{source}: scan complete ({len(language_dirs)} languages, {len(occurrences)} unique strings)")
    return occurrences, translations


def collect_all(caches, jobs):
    occurrences = Counter()
    translations = defaultdict(lambda: defaultdict(Counter))
    with ProcessPoolExecutor(max_workers=jobs) as executor:
        for source, cache in caches:
            log(f"Starting {source} cache analysis with {jobs} workers")
            source_occurrences, source_translations = collect(cache, source, executor, jobs)
            occurrences.update(source_occurrences)
            for language, counters in source_translations.items():
                for key, counts in counters.items():
                    translations[language][key].update(counts)
    log(f"Combined cache analysis complete: {len(occurrences)} unique strings, "
        f"{len(translations)} languages")
    return occurrences, translations


def entry_for(key, occurrences, msgstr=""):
    return polib.POEntry(msgid=key, msgstr=msgstr,
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
    existing = {entry.msgid for entry in po if not entry.obsolete}
    added = 0
    for key in keys:
        if key not in existing:
            po.append(entry_for(key, occurrences))
            existing.add(key)
            added += 1
    return added


def build(caches, out, min_count, pot_path, jobs=4):
    log(f"Analysis started: minimum frequency {min_count}, workers {jobs}")
    occurrences, translations = collect_all(caches, jobs)
    keys = sorted((key for key, count in occurrences.items() if count >= min_count),
                  key=msgid_sort_key)
    log(f"Frequency filtering complete: {len(keys)} strings meet the threshold")
    out.mkdir(parents=True, exist_ok=True)

    log(f"Updating POT: {pot_path}")
    pot, _ = load_or_create(pot_path)
    added = append_missing(pot, keys, occurrences)
    pot.sort(key=lambda entry: msgid_sort_key(entry.msgid))
    pot.save(str(pot_path))
    log(f"POT: added {added} strings")

    total_languages = len(translations)
    for language_index, (language, counters) in enumerate(translations.items(), 1):
        po_path = out / f"{language}.po"
        po, is_new = load_or_create(po_path)
        existing = {entry.msgid for entry in po if not entry.obsolete}
        added = 0
        for key in keys:
            counts = counters.get(key)
            if key in existing or not counts:
                continue
            translation = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
            po.append(entry_for(key, occurrences, translation))
            existing.add(key)
            added += 1
        if added or is_new or po_path.exists():
            po.metadata["Language"] = language
            po.save(str(po_path))
        log(f"PO [{language_index}/{total_languages}] {language}: added {added} strings")

    for po_path in sorted(out.glob("*.po")):
        po = polib.pofile(str(po_path))
        po.sort(key=lambda entry: msgid_sort_key(entry.msgid))
        po.save(str(po_path))
    log("Analysis complete")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kde-cache", type=Path, default=ROOT / ".cache" / "kde-l10n")
    parser.add_argument("--gnome-cache", type=Path, default=ROOT / ".cache" / "gnome-l10n")
    parser.add_argument("--min-count", type=int, default=10)
    parser.add_argument("--jobs", type=int, default=12,
                        help="parallel catalog parsers (default: 12)")
    parser.add_argument("--pot", type=Path, default=ROOT / "core-i18n.pot")
    parser.add_argument("--out", type=Path, default=ROOT / "po")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    build([("KDE", args.kde_cache), ("GNOME", args.gnome_cache)],
          args.out, args.min_count, args.pot, args.jobs)


if __name__ == "__main__":
    main()