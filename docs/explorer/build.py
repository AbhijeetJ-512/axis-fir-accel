#!/usr/bin/env python3
"""build.py spec.py sim.js out.html - assemble one explorer page from the shared engine, a project spec and a simulation model.

spec.py defines META, VIEW, BANDS, TYPES, NODES, BARS, EDGES, GROUPS, PATHS (plain Python data, see axi2apb/spec.py).
sim.js defines `const SIM = {...}` (the cycle model). The page embeds the spec as JSON in `const DATA=...;` so that
make_preview.py can redraw the same blocks and wires as an animated SVG for the README.
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    spec = importlib.util.spec_from_file_location("spec", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main(spec_path, sim_path, out_path):
    m = load(spec_path)
    data = dict(META=m.META, VIEW=m.VIEW, BANDS=m.BANDS, TYPES=m.TYPES, NODES=m.NODES, BARS=m.BARS,
                EDGES=m.EDGES, GROUPS=m.GROUPS, PATHS=m.PATHS)
    ids = set(m.NODES) | set(m.BARS)
    for e in m.EDGES:
        assert e["from"] in ids and e["to"] in ids, "edge %s names an unknown block" % e["id"]
        assert e["type"] in m.TYPES, "edge %s has unknown type %s" % (e["id"], e["type"])
    eids = {e["id"] for e in m.EDGES}
    assert len(eids) == len(m.EDGES), "duplicate edge id"
    for p in m.PATHS:
        for s in p["steps"]:
            k = s[0]
            assert (k[2:] in ids) if k.startswith("n:") else (k in eids), "path %s: bad step key %s" % (p["id"], k)
    for g, members in m.GROUPS:
        for x in members:
            assert x in ids, "group %s: unknown block %s" % (g, x)
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    sim = open(sim_path, encoding="utf-8").read()
    config = "const DATA=%s;\nconst {META,VIEW,BANDS,TYPES,NODES,BARS,EDGES,GROUPS,PATHS}=DATA;\n%s\n" % (blob, sim)
    page = open(os.path.join(HERE, "engine.html"), encoding="utf-8").read()
    page = page.replace("%%TITLE%%", m.META["pageTitle"]).replace("%%CONFIG%%", config)
    open(out_path, "w", encoding="utf-8").write(page)
    print("wrote %s: %d blocks, %d registers, %d wires, %d paths" % (out_path, len(m.NODES), len(m.BARS), len(m.EDGES), len(m.PATHS)))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
