#!/usr/bin/env python3
"""Build core-i18n.pot and po/<lang>.po from KDE project translations.

Steps:
  1. fetch   - svn export every language's messages/ directory from KDE's
               l10n SVN (needs `svn`) into .cache/kde-l10n/<lang>/.
  2. stats   - count in how many PO files (domains) each msgctxt+msgid occurs.
  3. build   - strings occurring >= --min-count (default 10) go into
               core-i18n.pot; each po/<lang>.po gets the most frequent
               translation among the KDE PO files of that language.

Usage:
  pip install polib
  python3 scripts/kde_common_strings.py                  # fetch + build
  python3 scripts/kde_common_strings.py --skip-fetch     # reuse the cache
  python3 scripts/kde_common_strings.py --cache DIR      # use own PO tree
                                                         # (DIR/<lang>/**/*.po or *.mo)
"""
import argparse
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parent.parent
SVN_BASE = "https://svn.kde.org/home/kde/trunk/l10n-kf6"
MAX_LEN = 200
CREDIT_CTXTS = {"NAME OF TRANSLATORS", "EMAIL OF TRANSLATORS"}


def fetch(cache, base):
    cache.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(["svn", "ls", base], check=True, capture_output=True, text=True).stdout
    langs = [l.rstrip("/") for l in out.split() if l.endswith("/") and not l.startswith(".")]
    for lang in langs:
        dest = cache / lang
        if dest.exists():
            continue
        r = subprocess.run(["svn", "export", "--quiet", f"{base}/{lang}/messages", str(dest)],
                           capture_output=True, text=True)
        if r.returncode:
            print(f"skip {lang}: {r.stderr.strip()}", file=sys.stderr)


def usable(e):
    return (not e.obsolete and e.msgid and not e.msgid_plural and len(e.msgid) <= MAX_LEN
            and "\n" not in e.msgid and e.msgctxt not in CREDIT_CTXTS)


def collect(cache):
    """-> occurrences {(ctxt,msgid): max #files across languages},
          translations {lang: {(ctxt,msgid): Counter(msgstr)}}"""
    occ = Counter()
    trans = {}
    for ldir in sorted(p for p in cache.iterdir() if p.is_dir()):
        lang = ldir.name
        files_per_key = Counter()
        counters = defaultdict(Counter)
        for po_path in sorted(ldir.rglob("*.po")) + sorted(ldir.rglob("*.mo")):
            try:
                po = polib.pofile(str(po_path)) if po_path.suffix == ".po" else polib.mofile(str(po_path))
            except Exception as ex:
                print(f"bad {po_path}: {ex}", file=sys.stderr)
                continue
            seen = set()
            for e in po:
                if not usable(e):
                    continue
                key = (e.msgctxt or "", e.msgid)
                if key not in seen:
                    seen.add(key)
                    files_per_key[key] += 1
                if e.msgstr and "fuzzy" not in e.flags:
                    counters[key][e.msgstr] += 1
        for k, n in files_per_key.items():
            occ[k] = max(occ[k], n)
        trans[lang] = counters
    return occ, trans


def collect_all(caches):
    """Combine statistics of several PO trees: occurrences are summed,
    translation counters are merged per language."""
    occ = Counter()
    trans = defaultdict(lambda: defaultdict(Counter))
    for cache in caches:
        o, t = collect(cache)
        occ.update(o)
        for lang, counters in t.items():
            for k, c in counters.items():
                trans[lang][k].update(c)
    return occ, trans


def build(cache, out, min_count, pot_path=None, source="KDE"):
    """cache may be one directory or a list of directories (combined)."""
    caches = [cache] if isinstance(cache, Path) else list(cache)
    occ, trans = collect_all(caches)
    keys = sorted((k for k, n in occ.items() if n >= min_count), key=lambda k: (-occ[k], k))
    print(f"{len(keys)} strings occur >= {min_count} times")
    out.mkdir(exist_ok=True)

    def entry(k, msgstr=""):
        return polib.POEntry(msgctxt=k[0] or None, msgid=k[1], msgstr=msgstr,
                             comment=f"Occurs in {occ[k]} {source} PO files")

    pot = polib.POFile()
    pot.metadata = {"Project-Id-Version": "core-i18n", "MIME-Version": "1.0",
                    "Content-Type": "text/plain; charset=UTF-8",
                    "Content-Transfer-Encoding": "8bit"}
    for k in keys:
        pot.append(entry(k))
    pot.save(str(pot_path or ROOT / "core-i18n.pot"))

    for lang, counters in trans.items():
        po = polib.POFile()
        po.metadata = dict(pot.metadata, Language=lang)
        for k in keys:
            c = counters.get(k)
            if c:
                best = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                po.append(entry(k, best))
        if len(po):
            po.save(str(out / f"{lang}.po"))
            print(f"{lang}: {len(po)}/{len(keys)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", type=Path, default=ROOT / ".cache" / "kde-l10n")
    ap.add_argument("--svn-base", default=SVN_BASE)
    ap.add_argument("--skip-fetch", action="store_true")
    ap.add_argument("--min-count", type=int, default=10)
    ap.add_argument("--out", type=Path, default=ROOT / "po")
    a = ap.parse_args()
    if not a.skip_fetch:
        fetch(a.cache, a.svn_base)
    build(a.cache, a.out, a.min_count)


if __name__ == "__main__":
    main()
