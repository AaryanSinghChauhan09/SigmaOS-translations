"""Translation catalog engine for SigmaOS-translations / guide-translations
(inspired by cinnamon-translations, mint-translations).

Principles: a pure, dependency-free PO parser (msgid/msgstr/plurals/fuzzy),
progress is computed exactly (fuzzy counts as untranslated, header excluded),
update-plan diffs a POT against a PO without touching any catalog, export is
the only side effect and only writes where the user points it.
"""
import argparse, ast, csv, json, os, sys

ENGINE = {
    "name": "sigma-translations",
    "summary": "Catalog, measure, plan updates and export PO translations",
    "language": "python",
    "verbs": ["catalog", "progress", "update-plan", "export"],
    "principles": ["Abstraction", "Separation of Concerns",
                   "Defensive Programming", "Fail-Fast", "KISS", "DRY",
                   "Deterministic Testing"],
}

cli_doc = """\
$ sigma-translations catalog ./po
de.po   120 entries  75.0% translated
fr.po   120 entries  92.5% translated
$ sigma-translations progress de.po
total=120 translated=90 fuzzy=10 untranslated=20 percent=75.0
$ sigma-translations update-plan messages.pot de.po
new      3   obsolete 5   untranslated 20
$ sigma-translations export ./po --out report.csv --format csv
"""


class TranslationError(ValueError):
    pass


def _unquote(token, lineno):
    token = token.strip()
    if not (token.startswith('"') and token.endswith('"')):
        raise TranslationError(f"line {lineno}: expected quoted string, "
                               f"got {token!r}")
    try:
        value = ast.literal_eval(token)
    except (SyntaxError, ValueError) as e:
        raise TranslationError(f"line {lineno}: bad string literal") from e
    if not isinstance(value, str):
        raise TranslationError(f"line {lineno}: not a string")
    return value


def parse_po(text):
    """Parse PO text into entries (header entry excluded)."""
    entries = []
    current, slot = None, None
    pending_fuzzy = False   # '#, fuzzy' may precede the msgid it flags
    seen_str = False        # every entry must carry at least one msgstr

    def flush():
        nonlocal current, slot, seen_str
        if current is not None and current["msgid"] != "":
            if not seen_str:
                raise TranslationError(
                    f"entry {current['msgid']!r} has no msgstr line")
            entries.append(current)
        current, slot, seen_str = None, None, False

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith("#"):
            if line.startswith("#,") and "fuzzy" in line:
                if current is None:
                    pending_fuzzy = True
                else:
                    current["fuzzy"] = True
            continue
        if line.startswith("msgid_plural"):
            if current is None:
                raise TranslationError(f"line {lineno}: msgid_plural "
                                       f"without msgid")
            current["msgid_plural"] = _unquote(line[len("msgid_plural"):],
                                               lineno)
            slot = None
        elif line.startswith("msgid "):
            flush()
            current = {"msgid": _unquote(line[len("msgid "):], lineno),
                       "msgstr": "", "msgid_plural": None,
                       "msgstrs": {}, "fuzzy": pending_fuzzy}
            pending_fuzzy = False
            slot = "msgid"
        elif line.startswith("msgstr["):
            if current is None:
                raise TranslationError(f"line {lineno}: msgstr without msgid")
            rest = line[len("msgstr["):]
            idx, sep, value = rest.partition("]")
            if not sep or not idx.isdigit():
                raise TranslationError(f"line {lineno}: bad plural msgstr")
            idx = int(idx)
            current["msgstrs"][idx] = _unquote(value, lineno) \
                if value.strip() else ""
            seen_str = True
            slot = ("msgstr", idx)
        elif line.startswith("msgstr"):
            if current is None:
                raise TranslationError(f"line {lineno}: msgstr without msgid")
            current["msgstr"] = _unquote(line[len("msgstr"):], lineno)
            seen_str = True
            slot = "msgstr"
        elif line.startswith('"'):
            if current is None or slot is None:
                raise TranslationError(f"line {lineno}: orphan continuation")
            piece = _unquote(line, lineno)
            if slot == "msgid":
                current["msgid"] += piece
            elif slot == "msgstr":
                current["msgstr"] += piece
            else:
                _, idx = slot
                current["msgstrs"][idx] += piece
        else:
            raise TranslationError(f"line {lineno}: cannot parse {line!r}")
    flush()
    return entries


def is_translated(entry):
    """Fuzzy entries never count as translated ( gettext semantics)."""
    if entry["fuzzy"]:
        return False
    if entry["msgid_plural"] is not None:
        return bool(entry["msgstrs"]) and all(
            v.strip() for v in entry["msgstrs"].values())
    return bool(entry["msgstr"].strip())


def progress(entries):
    total = len(entries)
    translated = sum(1 for e in entries if is_translated(e))
    fuzzy = sum(1 for e in entries if e["fuzzy"])
    percent = round(100.0 * translated / total, 1) if total else 0.0
    return {"total": total, "translated": translated, "fuzzy": fuzzy,
            "untranslated": total - translated, "percent": percent}


def catalog(po_dir):
    """Progress for every *.po file in a directory, sorted by name."""
    if not os.path.isdir(po_dir):
        raise TranslationError(f"no such directory: {po_dir!r}")
    out = []
    for name in sorted(os.listdir(po_dir)):
        if not name.endswith(".po"):
            continue
        path = os.path.join(po_dir, name)
        with open(path, "r", encoding="utf-8") as fh:
            entries = parse_po(fh.read())
        stats = progress(entries)
        stats.update({"file": name, "path": path})
        out.append(stats)
    if not out:
        raise TranslationError(f"no .po files in {po_dir!r}")
    return out


def update_plan(pot_text, po_entries):
    """Diff a POT template against PO entries (pure, sorted output)."""
    pot_ids = {e["msgid"] for e in parse_po(pot_text)}
    po_by_id = {e["msgid"]: e for e in po_entries}
    new = sorted(pot_ids - set(po_by_id))
    obsolete = sorted(set(po_by_id) - pot_ids)
    untranslated = sorted(m for m in pot_ids & set(po_by_id)
                          if not is_translated(po_by_id[m]))
    return {"new": new, "obsolete": obsolete, "untranslated": untranslated}


def export(rows, out_path, fmt="json"):
    """Write a progress report as json or csv. Returns the path."""
    if fmt not in ("json", "csv"):
        raise TranslationError(f"bad format: {fmt!r}")
    if fmt == "json":
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, sort_keys=True)
    else:
        fields = ["file", "total", "translated", "fuzzy", "untranslated",
                  "percent"]
        with open(out_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields,
                                    extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
    return out_path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=ENGINE["name"],
                                description=ENGINE["summary"])
    sub = p.add_subparsers(dest="cmd", required=True)
    ca = sub.add_parser("catalog", help="summarise a po directory")
    ca.add_argument("po_dir")
    pr = sub.add_parser("progress", help="progress for one .po file")
    pr.add_argument("po_file")
    up = sub.add_parser("update-plan", help="diff a POT against a .po")
    up.add_argument("pot_file")
    up.add_argument("po_file")
    ex = sub.add_parser("export", help="export a progress report")
    ex.add_argument("po_dir")
    ex.add_argument("--out", required=True)
    ex.add_argument("--format", choices=["json", "csv"], default="json")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "catalog":
            for stats in catalog(args.po_dir):
                print(f"{stats['file']:<12} {stats['total']:>5} entries  "
                      f"{stats['percent']}% translated")
        elif args.cmd == "progress":
            with open(args.po_file, "r", encoding="utf-8") as fh:
                stats = progress(parse_po(fh.read()))
            print(f"total={stats['total']} "
                  f"translated={stats['translated']} "
                  f"fuzzy={stats['fuzzy']} "
                  f"untranslated={stats['untranslated']} "
                  f"percent={stats['percent']}")
        elif args.cmd == "update-plan":
            with open(args.pot_file, "r", encoding="utf-8") as fh:
                pot_text = fh.read()
            with open(args.po_file, "r", encoding="utf-8") as fh:
                po_entries = parse_po(fh.read())
            plan = update_plan(pot_text, po_entries)
            print(f"new={len(plan['new'])} obsolete={len(plan['obsolete'])} "
                  f"untranslated={len(plan['untranslated'])}")
            for msgid in plan["new"]:
                print(f"  NEW          {msgid!r}")
            for msgid in plan["obsolete"]:
                print(f"  OBSOLETE     {msgid!r}")
        elif args.cmd == "export":
            rows = catalog(args.po_dir)
            out = export(rows, args.out, fmt=args.format)
            print(f"exported {len(rows)} row(s) -> {out}")
        return 0
    except (TranslationError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


_HEADER = '''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
"Language: de\\n"
'''

_SAMPLE_PO = '''\
# German translations for SigmaOS.
#, fuzzy
msgid ""
msgstr ""
"Language: de\\n"

msgid "Open file"
msgstr "Datei öffnen"

msgid "Close window"
msgstr "Fenster schließen"

#, fuzzy
msgid "Save changes"
msgstr "Änderungen speichern"

msgid "Quit application"
msgstr ""

msgid "one file"
msgid_plural "%d files"
msgstr[0] "%d Datei"
msgstr[1] ""

msgid "Search"
msgstr ""
"Suchen"
'''


def self_test() -> None:
    import tempfile
    entries = parse_po(_SAMPLE_PO)
    # header + 6 real entries
    assert len(entries) == 6
    by_id = {e["msgid"]: e for e in entries}
    assert by_id["Open file"]["msgstr"] == "Datei öffnen"
    assert by_id["Save changes"]["fuzzy"] is True
    assert by_id["one file"]["msgid_plural"] == "%d files"
    assert by_id["one file"]["msgstrs"] == {0: "%d Datei", 1: ""}
    # multi-line continuation joined
    assert by_id["Search"]["msgstr"] == "Suchen"

    stats = progress(entries)
    assert stats["total"] == 6
    assert stats["fuzzy"] == 1
    # translated: Open file, Close window, Search (msgstr + continuation);
    # "one file" is untranslated because plural form [1] is empty
    assert stats["translated"] == 3, stats
    assert stats["untranslated"] == 3
    assert stats["percent"] == 50.0

    assert progress([]) == {"total": 0, "translated": 0, "fuzzy": 0,
                            "untranslated": 0, "percent": 0.0}

    # update-plan: new / obsolete / untranslated
    pot = '''\
msgid "Open file"
msgstr ""

msgid "Close window"
msgstr ""

msgid "Quit application"
msgstr ""

msgid "Brand new string"
msgstr ""

msgid "one file"
msgid_plural "%d files"
msgstr[0] ""
msgstr[1] ""
'''
    plan = update_plan(pot, entries)
    assert plan["new"] == ["Brand new string"]
    assert plan["obsolete"] == ["Save changes", "Search"]
    assert plan["untranslated"] == ["Quit application", "one file"]

    # PO syntax errors fail fast
    for bad in ('msgid not-quoted\n',
                'garbage line here\n',
                'msgid "x"\nmsgstr[nope] "y"\n',
                'msgid "x"\nmsgid "y"\n',              # orphan continuation
                ):
        try:
            parse_po(bad)
        except TranslationError:
            pass
        else:
            raise AssertionError(f"bad po accepted: {bad!r}")
    # stray continuation after a complete entry is caught
    try:
        parse_po('msgid "x"\nmsgstr "y"\n\n"leftover"\n')
    except TranslationError:
        pass
    else:
        raise AssertionError("orphan continuation accepted")

    with tempfile.TemporaryDirectory() as d:
        # catalog over a po directory
        with open(os.path.join(d, "de.po"), "w", encoding="utf-8") as fh:
            fh.write(_SAMPLE_PO)
        with open(os.path.join(d, "en.po"), "w", encoding="utf-8") as fh:
            fh.write(_HEADER + '\nmsgid "Open file"\nmsgstr "Open file"\n\n'
                                'msgid "Close window"\nmsgstr "Close window"\n'
                     )
        with open(os.path.join(d, "notes.txt"), "w") as fh:
            fh.write("ignored")
        rows = catalog(d)
        assert [r["file"] for r in rows] == ["de.po", "en.po"]
        assert rows[0]["percent"] == 50.0
        assert rows[1]["percent"] == 100.0

        out = export(rows, os.path.join(d, "report.json"), fmt="json")
        data = json.load(open(out, encoding="utf-8"))
        assert data[1]["file"] == "en.po"
        out = export(rows, os.path.join(d, "report.csv"), fmt="csv")
        header = open(out, encoding="utf-8").readline().strip()
        assert header == "file,total,translated,fuzzy,untranslated,percent"
        try:
            export(rows, os.path.join(d, "x"), fmt="xml")
        except TranslationError:
            pass
        else:
            raise AssertionError("bad export format accepted")
        try:
            catalog(os.path.join(d, "ghost"))
        except TranslationError:
            pass
        else:
            raise AssertionError("missing dir accepted")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
