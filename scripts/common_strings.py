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
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parent.parent
SVN_BASE = "svn://anonsvn.kde.org/home/kde/trunk/l10n-kf6"
RAW = "https://gitlab.gnome.org/GNOME/{module}/-/raw/{branch}/{path}"
GNOME_PROJECTS_API = "https://gitlab.gnome.org/api/v4/groups/GNOME/projects?include_subgroups=true&per_page=100&page={page}"
BRANCHES = ["main", "master"]
MAX_LEN = 200
CREDIT_CTXTS = {"NAME OF TRANSLATORS", "EMAIL OF TRANSLATORS"}
LANGUAGE_RE = re.compile(r"^[A-Za-z0-9]+(?:[_@.-][A-Za-z0-9]+)*$")


def log(message):
    print(message, flush=True)


def list_gnome_modules():
    modules = []
    page = 1
    while True:
        log(f"GNOME: listing projects page {page}")
        request = urllib.request.Request(
            GNOME_PROJECTS_API.format(page=page), headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            projects = json.loads(response.read())
            next_page = response.headers.get("X-Next-Page")
        modules.extend(project["path_with_namespace"].removeprefix("GNOME/")
                   for project in projects)
        if not next_page:
            log(f"GNOME: discovered {len(modules)} projects")
            return modules
        page = int(next_page)


def get_gnome_file(module, path):
    for branch in BRANCHES:
        url = RAW.format(module=module, branch=branch, path=path)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    break
                if error.code != 429 and not 500 <= error.code < 600:
                    raise RuntimeError(f"GNOME download failed ({error.code}): {url}") from error
                retry_after = error.headers.get("Retry-After")
                try:
                    delay = min(60, float(retry_after)) if retry_after else min(30, 2 ** attempt)
                except ValueError:
                    delay = min(30, 2 ** attempt)
                if attempt == 3:
                    raise RuntimeError(f"GNOME download still rate-limited/unavailable after retries: {url}") from error
                log(f"GNOME: HTTP {error.code} for {module}/{path}; retry {attempt + 1}/3 in {delay:g}s")
                time.sleep(delay)
            except urllib.error.URLError as error:
                if attempt == 3:
                    raise RuntimeError(f"GNOME network request failed after retries: {url}: {error}") from error
                delay = min(30, 2 ** attempt)
                log(f"GNOME: network error for {module}/{path}; retry {attempt + 1}/3 in {delay}s")
                time.sleep(delay)
    return None


def parse_linguas(data):
    text = data.decode("utf-8", "replace")
    if "<html" in text.lower() or "<!doctype" in text.lower():
        raise ValueError("response is HTML, not a LINGUAS file")
    languages = []
    for line in text.splitlines():
        line = line.split("#", 1)[0]
        for language in line.split():
            if not LANGUAGE_RE.fullmatch(language):
                raise ValueError(f"invalid language name {language!r}")
            languages.append(language)
    return list(dict.fromkeys(languages))


def project_manifest_path(cache, module):
    digest = hashlib.sha256(module.encode("utf-8")).hexdigest()
    return cache / ".projects" / f"{digest}.json"


def load_project_manifest(cache, module):
    path = project_manifest_path(cache, module)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if manifest.get("module") != module:
        return None
    languages = manifest.get("languages")
    missing = set(manifest.get("missing", []))
    if not isinstance(languages, list):
        return None
    if any(language not in missing and not (cache / language / f"{module}.po").is_file()
           for language in languages):
        return None
    return manifest


def save_project_manifest(cache, module, states):
    path = project_manifest_path(cache, module)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "module": module,
        "languages": sorted(states),
        "missing": sorted(language for language, state in states.items() if state == "missing"),
    }
    path.write_text(json.dumps(manifest, ensure_ascii=True) + "\n", encoding="utf-8")


def fetch_gnome(cache, modules, langs, jobs=12):
    modules = list(dict.fromkeys(modules))
    cache.mkdir(parents=True, exist_ok=True)
    log(f"GNOME: checking translations for {len(modules)} projects with {jobs} workers")
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        if langs:
            module_languages = {module: langs for module in modules}
            cache_states = {}
        else:
            module_languages = {}
            cache_states = {}
            uncached_modules = []
            for module in modules:
                manifest = load_project_manifest(cache, module)
                if manifest is None:
                    uncached_modules.append(module)
                    continue
                cache_states[module] = {
                    language: "missing" if language in manifest.get("missing", []) else "cached"
                    for language in manifest["languages"]
                }
                log(f"GNOME cache: {module}: complete, skipping project ({len(cache_states[module])} languages)")

            log(f"GNOME: fetching LINGUAS for {len(uncached_modules)} uncached projects")
            futures = {executor.submit(get_gnome_file, module, "po/LINGUAS"): module
                       for module in uncached_modules}
            module_languages = {}
            total = len(futures)
            for completed, future in enumerate(as_completed(futures), 1):
                module = futures[future]
                linguas = future.result()
                if linguas is None:
                    log(f"GNOME LINGUAS [{completed}/{total}]: {module}: not found (404 on both branches)")
                    continue
                try:
                    module_languages[module] = parse_linguas(linguas)
                except ValueError as error:
                    log(f"GNOME LINGUAS [{completed}/{total}]: {module}: invalid response: {error}")
                    continue
                cache_states[module] = {
                    language: "cached"
                    for language in module_languages[module]
                    if (cache / language / f"{module}.po").is_file()
                }
                log(f"GNOME LINGUAS [{completed}/{total}]: {module}: {len(module_languages[module])} languages")

        downloads = {}
        cached = 0
        for module, module_langs in module_languages.items():
            for language in module_langs:
                destination = cache / language / f"{module}.po"
                if not destination.exists():
                    key = (module, language)
                    downloads[key] = destination
                else:
                    cached += 1

        total = len(downloads)
        log(f"GNOME: downloading {total} PO files; {cached} already cached")
        futures = {
            executor.submit(get_gnome_file, module, f"po/{language}.po"): (module, language, destination)
            for (module, language), destination in downloads.items()
        }
        for completed, future in enumerate(as_completed(futures), 1):
            module, language, destination = futures[future]
            data = future.result()
            if data is None:
                log(f"GNOME PO [{completed}/{total}]: {module}/{language}: not found")
                if not langs:
                    cache_states[module][language] = "missing"
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            if not langs:
                cache_states[module][language] = "saved"
            log(f"GNOME PO [{completed}/{total}]: {module}/{language}: saved ({len(data)} bytes)")
        if not langs:
            for module, module_langs in module_languages.items():
                states = cache_states[module]
                if all(language in states for language in module_langs):
                    save_project_manifest(cache, module, states)
    log("GNOME: download phase complete")


def fetch_kde(cache, base):
    cache.mkdir(parents=True, exist_ok=True)
    log(f"KDE: listing languages from {base}")
    listing = subprocess.run(["svn", "ls", base], check=True, capture_output=True, text=True).stdout
    languages = [name.rstrip("/") for name in listing.split()
                 if name.endswith("/") and not name.startswith(".")]
    log(f"KDE: found {len(languages)} languages")
    cached = sum((cache / language).exists() for language in languages)
    log(f"KDE: exporting {len(languages) - cached} languages; {cached} already cached")
    total = len(languages)
    for completed, language in enumerate(languages, 1):
        destination = cache / language
        if destination.exists():
            log(f"KDE [{completed}/{total}]: {language}: cached")
            continue
        result = subprocess.run(
            ["svn", "export", "--quiet", f"{base}/{language}/messages", f"{destination}@"],
            capture_output=True, text=True)
        if result.returncode:
            log(f"KDE [{completed}/{total}]: {language}: failed: {result.stderr.strip()}")
        else:
            log(f"KDE [{completed}/{total}]: {language}: exported")
    log("KDE: download phase complete")


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

    for language_dir in sorted(path for path in cache.iterdir()
                                if path.is_dir() and LANGUAGE_RE.fullmatch(path.name)):
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
    parser.add_argument("--modules", nargs="+", help="GNOME projects to fetch (default: discover all projects)")
    parser.add_argument("--langs", nargs="+", default=None)
    parser.add_argument("--jobs", type=int, default=4, help="parallel GNOME download workers (default: 4)")
    parser.add_argument("--svn-base", default=SVN_BASE)
    parser.add_argument("--skip-fetch", action="store_true", help="reuse both source caches")
    parser.add_argument("--min-count", type=int, default=10)
    parser.add_argument("--pot", type=Path, default=ROOT / "core-i18n.pot")
    parser.add_argument("--out", type=Path, default=ROOT / "po")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")

    if not args.skip_fetch:
        fetch_kde(args.kde_cache, args.svn_base)
        modules = args.modules if args.modules else list_gnome_modules()
        fetch_gnome(args.gnome_cache, modules, args.langs, args.jobs)
    build([args.kde_cache, args.gnome_cache], args.out, args.min_count, args.pot)


if __name__ == "__main__":
    main()