#!/usr/bin/env python3
"""Download KDE and GNOME translation catalogs into local caches.

Usage:
  python3 scripts/download_translations.py
  python3 scripts/download_translations.py --modules nautilus gnome-shell
  python3 scripts/download_translations.py --langs zh_CN de
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVN_BASE = "svn://anonsvn.kde.org/home/kde/trunk/l10n-kf6"
RAW = "https://gitlab.gnome.org/{module}/-/raw/{branch}/{path}"
GNOME_PROJECTS_API = "https://gitlab.gnome.org/api/v4/groups/GNOME/projects?include_subgroups=true&per_page=100&page={page}"
BRANCHES = ["main", "master"]
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


def fetch_gnome(cache, modules, langs, jobs):
    modules = list(dict.fromkeys(modules))
    cache.mkdir(parents=True, exist_ok=True)
    log(f"GNOME: checking translations for {len(modules)} projects with {jobs} workers")
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        if langs:
            module_languages = {module: langs for module in modules}
            cache_states = {}
        else:
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
                    downloads[(module, language)] = destination
                else:
                    cached += 1

        total = len(downloads)
        log(f"GNOME: downloading {total} PO files; {cached} already cached")
        futures = {
            executor.submit(get_gnome_file, module, f"po/{language}.po"):
                (module, language, destination)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gnome-cache", type=Path, default=ROOT / ".cache" / "gnome-l10n")
    parser.add_argument("--kde-cache", type=Path, default=ROOT / ".cache" / "kde-l10n")
    parser.add_argument("--modules", nargs="+", help="GNOME projects to fetch (default: discover all projects)")
    parser.add_argument("--langs", nargs="+", help="GNOME languages to fetch for selected projects")
    parser.add_argument("--jobs", type=int, default=4, help="parallel GNOME download workers (default: 4)")
    parser.add_argument("--svn-base", default=SVN_BASE)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")

    fetch_kde(args.kde_cache, args.svn_base)
    modules = args.modules if args.modules else list_gnome_modules()
    fetch_gnome(args.gnome_cache, modules, args.langs, args.jobs)


if __name__ == "__main__":
    main()