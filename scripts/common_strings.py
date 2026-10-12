#!/usr/bin/env python3
"""Fetch GNOME and KDE translations and append their common strings.

Each source is downloaded into its own cache, then both caches are counted
together. Existing POT and PO entries are never removed or overwritten.

Usage:
  pip install polib
  python3 scripts/common_strings.py
  python3 scripts/common_strings.py --skip-fetch
  python3 scripts/common_strings.py --modules nautilus gnome-shell
"""
import argparse
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parent.parent
SVN_BASE = "svn://anonsvn.kde.org/home/kde/trunk/l10n-kf6"
RAW = "https://gitlab.gnome.org/GNOME/{module}/-/raw/{branch}/{path}"
BRANCHES = ["main", "master"]
MAX_LEN = 200
CREDIT_CTXTS = {"NAME OF TRANSLATORS", "EMAIL OF TRANSLATORS"}
GNOME_MODULES = [
    "nautilus", "gnome-control-center", "gnome-shell", "gnome-software", "gnome-calendar",
    "gnome-contacts", "gnome-settings-daemon", "gnome-text-editor", "gnome-terminal",
    "gnome-system-monitor", "gnome-disk-utility", "gnome-calculator", "gnome-clocks",
    "gnome-maps", "gnome-weather", "gnome-music", "gnome-tweaks", "gnome-characters",
    "gnome-font-viewer", "gnome-logs", "evince", "eog", "file-roller", "baobab", "totem",
    "epiphany", "gedit", "gtk", "glib", "libadwaita", "mutter", "gnome-builder",
    "gnome-online-accounts", "gnome-user-docs", "simple-scan", "cheese", "seahorse",
]


def get_gnome_file(module, path):
    for branch in BRANCHES:
        url = RAW.format(module=module, branch=branch, path=path)
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return response.read()
        except urllib.error.URLError:
            continue
    return None


def fetch_gnome(cache, modules, langs):
    for module in modules:
        module_langs = langs
        if not module_langs:
            linguas = get_gnome_file(module, "po/LINGUAS")
            if linguas is None:
                print(f"skip GNOME {module}: no po/LINGUAS", file=sys.stderr)
                continue
            module_langs = [language for language in linguas.decode("utf-8", "replace").split()
                            if not language.startswith("#")]
        for language in module_langs:
            destination = cache / language / f"{module}.po"
            if destination.exists():
                continue
            data = get_gnome_file(module, f"po/{language}.po")
            if data is None:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)


def fetch_kde(cache, base):
    cache.mkdir(parents=True, exist_ok=True)
    listing = subprocess.run(["svn", "ls", base], check=True, capture_output=True, text=True).stdout
    languages = [name.rstrip("/") for name in listing.split()
                 if name.endswith("/") and not name.startswith(".")]
    for language in languages:
        destination = cache / language
        if destination.exists():
            continue
        result = subprocess.run(
            ["svn", "export", "--quiet", f"{base}/{language}/messages", str(destination)],
            capture_output=True, text=True)
        if result.returncode:
            print(f"skip KDE {language}: {result.stderr.strip()}", file=sys.stderr)


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

    for language_dir in sorted(path for path in cache.iterdir() if path.is_dir()):
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


def entry_for(key, occurrences, source, msgstr=""):
    return polib.POEntry(msgctxt=key[0] or None, msgid=key[1], msgstr=msgstr,
                         comment=f"Occurs in {occurrences[key]} {source} PO files")


def load_or_create(path):
    if path.exists():
        return polib.pofile(str(path)), False
    po = polib.POFile()
    po.metadata = {"Project-Id-Version": "core-i18n", "MIME-Version": "1.0",
                   "Content-Type": "text/plain; charset=UTF-8",
                   "Content-Transfer-Encoding": "8bit"}
    return po, True


def append_missing(po, keys, occurrences, source):
    existing = {(entry.msgctxt or "", entry.msgid) for entry in po if not entry.obsolete}
    added = 0
    for key in keys:
        if key not in existing:
            po.append(entry_for(key, occurrences, source))
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
    added = append_missing(pot, keys, occurrences, "KDE+GNOME")
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
            po.append(entry_for(key, occurrences, "KDE+GNOME", translation))
            existing.add(key)
            added += 1
        if added or is_new:
            po.metadata["Language"] = language
            po.save(str(po_path))
        print(f"{language}: added {added} strings")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gnome-cache", type=Path, default=ROOT / ".cache" / "gnome-l10n")
    parser.add_argument("--kde-cache", type=Path, default=ROOT / ".cache" / "kde-l10n")
    parser.add_argument("--modules", nargs="+", default=GNOME_MODULES)
    parser.add_argument("--langs", nargs="+", default=None)
    parser.add_argument("--svn-base", default=SVN_BASE)
    parser.add_argument("--skip-fetch", action="store_true", help="reuse both source caches")
    parser.add_argument("--min-count", type=int, default=10)
    parser.add_argument("--pot", type=Path, default=ROOT / "core-i18n.pot")
    parser.add_argument("--out", type=Path, default=ROOT / "po")
    args = parser.parse_args()

    if not args.skip_fetch:
        fetch_kde(args.kde_cache, args.svn_base)
        fetch_gnome(args.gnome_cache, args.modules, args.langs)
    build([args.kde_cache, args.gnome_cache], args.out, args.min_count, args.pot)


if __name__ == "__main__":
    main()