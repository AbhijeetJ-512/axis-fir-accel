#!/usr/bin/env python3
"""make_preview.py - animated SVG preview of docs/architecture_explorer.html for the README.

    python3 docs/diagrams/make_explorer_preview.py     # writes docs/diagrams/architecture_explorer_preview.svg

A README cannot run JavaScript, so this draws the explorer's block diagram as a plain SVG that animates with SMIL and CSS
only (both work in an <img> on GitHub). The blocks, wires, registers and stage bands are read from the DATA object embedded in
the explorer page, so the preview cannot drift from the page: change the page, run this again.
"""
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "..", "architecture_explorer.html")
OUT = os.path.join(HERE, "architecture_explorer_preview.svg")
CLOCK = 1.7   # seconds per animated clock cycle, the same as the explorer at 1x
FONT_D = "'Arial Narrow','Roboto Condensed','Helvetica Neue',Arial,sans-serif"
FONT_M = "ui-monospace,'SFMono-Regular',Menlo,Consolas,monospace"
FONT_B = "-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"


def load():
    src = open(PAGE, encoding="utf-8").read()
    m = re.search(r"const DATA=(\{.*?\});\nconst \{META", src, re.S)
    if not m:
        sys.exit("could not find the embedded DATA object in %s" % PAGE)
    return json.loads(m.group(1))


def length(pts):
    return sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def build(d):
    e = html.escape
    VIEW, BANDS, TYPES, NODES, BARS, EDGES = d["VIEW"], d["BANDS"], d["TYPES"], d["NODES"], d["BARS"], d["EDGES"]
    W, H = VIEW["w"], VIEW["h"] + 58
    used = [t for t in TYPES if any(x["type"] == t for x in EDGES)]
    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" aria-label="%s">'
         % (W, H, W, H, e(d["META"].get("diagramLabel", d["META"]["title"])))]
    o.append("""<style>
svg{--bg:#E9EDF1;--surface:#FFFFFF;--surface2:#F3F6F8;--ink:#121B24;--muted:#556574;--line:#C3CDD7;--grid:#D3DBE3;
 --data:#1B5FC4;--fwd:#B86200;--ctl:#6E3FC4;--trap:#C42F38;--mem:#0F7F55;--ver:#6A7B8C;
 --b0:#1B5FC4;--b1:#6E3FC4;--b2:#B86200;--b3:#0F7F55;--b4:#A0306F;--b5:#0E7C8C}
@media (prefers-color-scheme: dark){svg{--bg:#0C1219;--surface:#131B24;--surface2:#18222D;--ink:#E1E8EF;--muted:#8D9BA9;--line:#2B3947;--grid:#1B2631;
 --data:#5FA3FF;--fwd:#F0A63A;--ctl:#A986F5;--trap:#FF6B70;--mem:#3CCB8E;--ver:#8496A8;
 --b0:#5FA3FF;--b1:#A986F5;--b2:#F0A63A;--b3:#3CCB8E;--b4:#F07AB8;--b5:#3CC6D6}}
.bg{fill:var(--bg)} .dot{fill:var(--grid)} .band{fill:var(--surface2);opacity:.6}
.hd{font:700 16px %(fd)s;letter-spacing:.04em}
.box{fill:var(--surface);stroke:var(--line);stroke-width:1.2} .ext .box{fill:var(--surface2);stroke-dasharray:4 3} .ver .box{stroke-dasharray:2 3}
.nt{font:700 15px %(fd)s;fill:var(--ink)}
.ns{font:400 10.5px %(fm)s;fill:var(--muted)}
.nl{font:400 10.5px %(fb)s;fill:var(--muted)}
.bar{fill:var(--surface2);stroke:var(--ink);stroke-opacity:.45;stroke-width:1.2}
.bt{font:600 11px %(fm)s;fill:var(--muted);letter-spacing:.08em}
.bnd{fill:none;stroke:var(--line);stroke-dasharray:6 5;stroke-width:1.2}
.cap{font:600 11px %(fm)s;fill:var(--muted);letter-spacing:.06em}
.lg{font:400 12px %(fb)s;fill:var(--muted)}
.ln{fill:none;stroke-width:1.6}
.t-data{stroke:var(--data)} .t-fwd{stroke:var(--fwd);stroke-width:2} .t-ctl{stroke:var(--ctl);stroke-dasharray:5 4}
.t-trap{stroke:var(--trap);stroke-dasharray:7 3} .t-mem{stroke:var(--mem);stroke-width:2} .t-ver{stroke:var(--ver);stroke-dasharray:2 4}
.p-data{fill:var(--data)} .p-fwd{fill:var(--fwd)} .p-ctl{fill:var(--ctl)} .p-trap{fill:var(--trap)} .p-mem{fill:var(--mem)} .p-ver{fill:var(--ver)}
.pulse{fill:var(--data);animation:clk %(clk).1fs steps(1,end) infinite}
@keyframes clk{0%%{opacity:0}88%%{opacity:.55}100%%{opacity:0}}
@media (prefers-reduced-motion: reduce){.pulse{animation:none;opacity:0}}
</style>""" % dict(fd=FONT_D, fm=FONT_M, fb=FONT_B, clk=CLOCK))
    o.append('<defs><pattern id="g" width="16" height="16" patternUnits="userSpaceOnUse"><circle class="dot" cx="1" cy="1" r=".9"/></pattern>')
    for t in used:
        o.append('<marker id="a-%s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                 '<path d="M0 0L10 5L0 10z" class="p-%s"/></marker>' % (t, t))
    o.append("</defs>")
    o.append('<rect class="bg" width="%d" height="%d"/><rect width="%d" height="%d" fill="url(#g)"/>' % (W, H, W, VIEW["h"]))
    for b in BANDS:
        o.append('<rect class="band" x="%d" y="12" width="%d" height="%d" rx="4"/>' % (b["x"], b["w"], VIEW["bandH"]))
        o.append('<rect x="%d" y="12" width="%d" height="4" rx="2" fill="var(%s)"/>' % (b["x"], b["w"], b["c"]))
        o.append('<text class="hd" x="%d" y="40" fill="var(%s)">%s</text>' % (b["x"] + 10, b["c"], e(b["n"])))
    bd = VIEW.get("boundary")
    if bd:
        o.append('<rect class="bnd" x="%d" y="%d" width="%d" height="%d" rx="6"/>' % (bd["x"], bd["y"], bd["w"], bd["h"]))
        o.append('<text class="cap" x="%d" y="%d" text-anchor="end">%s</text>' % (bd["x"] + bd["w"] - 6, bd["y"] + bd["h"] - 6, e(bd["label"])))
    if VIEW.get("outsideLabel"):
        o.append('<text class="cap" x="%d" y="%d" text-anchor="end">%s</text>' % (W - 12, VIEW["h"] - 4, e(VIEW["outsideLabel"])))
    for ed in EDGES:
        ed["d"] = "M" + " L".join("%d %d" % tuple(p) for p in ed["pts"])
        o.append('<path class="ln t-%s" d="%s" marker-end="url(#a-%s)"><title>%s</title></path>' % (ed["type"], ed["d"], ed["type"], e(ed["label"])))
    for ed in EDGES:
        L = length(ed["pts"])
        dur = max(1.2, L / 70.0)
        n = max(1, round(L / 170))
        for i in range(n):
            o.append('<circle r="3.2" class="p-%s"><animateMotion dur="%.2fs" begin="-%.2fs" repeatCount="indefinite" path="%s"/></circle>'
                     % (ed["type"], dur, dur * i / n, ed["d"]))
    for bid, b in BARS.items():
        y = b.get("y", VIEW.get("barY", 150)); h = b.get("h", VIEW.get("barH", 500)); cy = y + h / 2
        o.append('<rect class="bar" x="%d" y="%d" width="18" height="%d" rx="3"/>' % (b["x"], y, h))
        o.append('<rect class="pulse" x="%d" y="%d" width="18" height="%d" rx="3"/>' % (b["x"], y, h))
        o.append('<text class="bt" x="%d" y="%d" transform="rotate(-90 %d %d)" text-anchor="middle">%s</text>'
                 % (b["x"] + 13, cy, b["x"] + 13, cy, e(b["t"] + ("" if b.get("noSuffix") else " register"))))
    for nid, n in NODES.items():
        cls = "n" + (" ext" if n.get("ext") else "") + (" ver" if n.get("ver") else "")
        o.append('<g class="%s"><rect class="box" x="%d" y="%d" width="%d" height="%d" rx="5"/>' % (cls, n["x"], n["y"], n["w"], n["h"]))
        y = n["y"] + 19
        o.append('<text class="nt" x="%d" y="%d">%s</text>' % (n["x"] + 10, y, e(n["t"])))
        y += 15
        o.append('<text class="ns" x="%d" y="%d">%s</text>' % (n["x"] + 10, y, e(n["s"])))
        for line in n.get("l", []):
            y += 14
            if y > n["y"] + n["h"] - 6:
                break
            o.append('<text class="nl" x="%d" y="%d">%s</text>' % (n["x"] + 10, y, e(line)))
        o.append("</g>")
    x = 16; ly = VIEW["h"] + 20
    for t in used:
        o.append('<path class="ln t-%s" d="M%d %d L%d %d" marker-end="url(#a-%s)"/>' % (t, x, ly, x + 26, ly, t))
        o.append('<text class="lg" x="%d" y="%d">%s</text>' % (x + 34, ly + 4, e(d["TYPES"][t])))
        x += 44 + int(len(d["TYPES"][t]) * 6.6)
    o.append('<text class="lg" x="%d" y="%d" text-anchor="end">Open the interactive explorer to run scenarios through the design</text>' % (W - 12, ly + 24))
    o.append("</svg>")
    return "\n".join(o) + "\n"


def main():
    d = load()
    open(OUT, "w", encoding="utf-8").write(build(d))
    print("wrote %s: %d blocks, %d registers, %d wires" % (os.path.relpath(OUT), len(d["NODES"]), len(d["BARS"]), len(d["EDGES"])))


if __name__ == "__main__":
    main()
