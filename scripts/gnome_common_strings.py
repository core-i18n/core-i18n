#!/usr/bin/env python3
"""Build a .pot and <lang>.po files from GNOME project translations.

Steps:
  1. fetch   - download po/<lang>.po of each GNOME module from gitlab.gnome.org
               into .cache/gnome-l10n/<lang>/<module>.po (languages come from
               each module's po/LINGUAS, or --langs).
  2. build   - same statistics/selection as kde_common_strings.py: strings
               occurring >= --min-count go into the POT, and each PO gets the
               most frequent translation per language.

Output goes to core-i18n-gnome.pot and po-gnome/ by default so the KDE data
is not overwritten.

Usage:
  pip install polib
  python3 scripts/gnome_common_strings.py
  python3 scripts/gnome_common_strings.py --skip-fetch
  python3 scripts/gnome_common_strings.py --skip-fetch --combine-kde   # KDE + GNOME stats
  python3 scripts/gnome_common_strings.py --modules gnome-control-center nautilus
"""
import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

import kde_common_strings as kde

ROOT = kde.ROOT
RAW = "https://gitlab.gnome.org/GNOME/{module}/-/raw/{branch}/{path}"
MODULES = [
    "nautilus", "gnome-control-center", "gnome-shell", "gnome-software", "gnome-calendar",
    "gnome-contacts", "gnome-settings-daemon", "gnome-text-editor", "gnome-terminal",
    "gnome-system-monitor", "gnome-disk-utility", "gnome-calculator", "gnome-clocks",
    "gnome-maps", "gnome-weather", "gnome-music", "gnome-tweaks", "gnome-characters",
    "gnome-font-viewer", "gnome-logs", "evince", "eog", "file-roller", "baobab", "totem",
    "epiphany", "gedit", "gtk", "glib", "libadwaita", "mutter", "gnome-builder",
    "gnome-online-accounts", "gnome-user-docs", "simple-scan", "cheese", "seahorse",
]
BRANCHES = ["main", "master"]


def get(module, path):
    for branch in BRANCHES:
        url = RAW.format(module=module, branch=branch, path=path)
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except urllib.error.URLError:
            continue
    return None


def fetch(cache, modules, langs):
    for module in modules:
        mlangs = langs
        if not mlangs:
            linguas = get(module, "po/LINGUAS")
            if linguas is None:
                print(f"skip {module}: no po/LINGUAS", file=sys.stderr)
                continue
            mlangs = [l for l in linguas.decode("utf-8", "replace").split()
                      if not l.startswith("#")]
        for lang in mlangs:
            dest = cache / lang / f"{module}.po"
            if dest.exists():
                continue
            data = get(module, f"po/{lang}.po")
            if data is None:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", type=Path, default=ROOT / ".cache" / "gnome-l10n")
    ap.add_argument("--modules", nargs="+", default=MODULES)
    ap.add_argument("--langs", nargs="+", default=None)
    ap.add_argument("--combine-kde", nargs="?", const=str(ROOT / ".cache" / "kde-l10n"), type=Path,
                    metavar="KDE_CACHE", help="also include a KDE PO tree (default .cache/kde-l10n) "
                    "and combine the statistics of KDE and GNOME")
    ap.add_argument("--skip-fetch", action="store_true")
    ap.add_argument("--min-count", type=int, default=10)
    ap.add_argument("--pot", type=Path, default=ROOT / "core-i18n-gnome.pot")
    ap.add_argument("--out", type=Path, default=ROOT / "po-gnome")
    a = ap.parse_args()
    if not a.skip_fetch:
        fetch(a.cache, a.modules, a.langs)
    if a.combine_kde:
        kde.build([a.cache, a.combine_kde], a.out, a.min_count, a.pot, "KDE+GNOME")
    else:
        kde.build(a.cache, a.out, a.min_count, a.pot, "GNOME")


if __name__ == "__main__":
    main()
