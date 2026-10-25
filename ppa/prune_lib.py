#!/usr/bin/env python3
"""prune_lib.py IN.lib OUT.lib --drop PREFIX [--drop PREFIX ...]   |   prune_lib.py --selftest

Writes a copy of a Liberty file without the cells whose name starts with any PREFIX. Yosys 0.33's abc
has no -dont_use and the Sky130 HD library marks none of its cells dont_use, so the synthesis-only copy
simply does not contain the low-power-flow (lpflow_) or probe cells. OpenSTA and OpenROAD read the full
library; the netlist just never uses the dropped cells.
"""
import argparse
import re
import sys

CELL = re.compile(r'^[ \t]*cell[ \t]*\([ \t]*"?([^")\s]+)"?[ \t]*\)[ \t]*\{', re.M)


def cell_end(text, open_brace):
    """Index just past the brace that closes the one at open_brace; braces inside "strings" are ignored."""
    depth, i, n, in_str = 0, open_brace, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced braces in cell")


def prune_text(text, prefixes):
    """Return (new_text, [dropped cell names])."""
    spans, dropped = [], []
    for m in CELL.finditer(text):
        name = m.group(1)
        if any(name.startswith(p) for p in prefixes):
            end = cell_end(text, m.end() - 1)
            spans.append((m.start(), end))
            dropped.append(name)
    out, pos = [], 0
    for start, end in spans:
        out.append(text[pos:start])
        pos = end
        # swallow the newline that ended the dropped cell
        if text[pos:pos + 1] == "\n":
            pos += 1
    out.append(text[pos:])
    return "".join(out), dropped


def selftest():
    lib = (
        'library ("t") {\n'
        '  cell ("keep_a") {\n    area : 1;\n  }\n'
        '  cell ("drop_x") {\n    pin ("A") { function : "{not a brace}"; }\n    note : "a } and a { inside a string";\n  }\n'
        '  cell ("keep_b") {\n    area : 2;\n  }\n'
        '  cell ("drop_y") {\n    area : 3;\n  }\n'
        '}\n'
    )
    new, dropped = prune_text(lib, ["drop_"])
    ok = (dropped == ["drop_x", "drop_y"]
          and 'cell ("keep_a")' in new and 'cell ("keep_b")' in new
          and "drop_" not in new and new.count("{") == new.count("}") == 3
          and new.endswith("}\n"))
    nothing, none = prune_text(lib, ["zzz_"])
    ok = ok and nothing == lib and none == []
    print("prune_lib selftest: " + ("ok" if ok else "FAIL"))
    if not ok:
        print(new)
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--drop", action="append", default=[])
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    if not (args.src and args.dst and args.drop):
        ap.error("need SRC DST and at least one --drop PREFIX")
    with open(args.src) as fh:
        text = fh.read()
    new, dropped = prune_text(text, args.drop)
    with open(args.dst, "w") as fh:
        fh.write(new)
    print(f"prune_lib: dropped {len(dropped)} cells from {args.src} -> {args.dst}")


if __name__ == "__main__":
    main()
