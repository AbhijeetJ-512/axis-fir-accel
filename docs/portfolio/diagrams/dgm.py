#!/usr/bin/env python3
"""dgm.py - one diagram specification, two outputs.

A diagram is described once (boxes, arrows, labels in draw.io pixel units) and written as

  * a native draw.io file  (`Diagram.save_drawio`)  - editable in diagrams.net / draw.io desktop
  * a PNG / SVG image      (`Diagram.save_png`, `save_svg`) - rendered here with matplotlib

Because both come from the same objects, the image cannot drift from the editable source. The renderer is NOT
draw.io's own exporter: line routing is exactly the waypoints given (no automatic re-routing), and fonts are
Liberation Sans (metric-compatible with the Helvetica/Arial draw.io uses). `check_text()` reports any label that
overflows its shape so the layout can be fixed before export.

Shape kinds
  block   rounded rectangle (default)        mem     cylinder-ish rectangle (storage)
  ctrl    control logic (orange)             io      external interface (grey)
  fsm     state / sequencer (purple)         accent  highlighted unit (green)
  proc    flowchart process                  term    flowchart start/end (pill)
  dec     flowchart decision (diamond)       data    flowchart input/output (parallelogram)
  sub     flowchart sub-process              note    yellow note
Edge kinds
  data    solid dark blue                    ctrl    dashed orange
  clk     dotted grey (clock / reset)        flow    solid black (flowchart)
"""
from __future__ import annotations

import html
import os
import textwrap
from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple, Union

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Polygon, Rectangle  # noqa: E402

FONT = "Liberation Sans"
matplotlib.rcParams["font.family"] = [FONT, "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"

# kind -> (fill, stroke, font colour)
BOX_STYLE = {
    "block": ("#dae8fc", "#6c8ebf", "#111111"),
    "mem": ("#fff2cc", "#d6b656", "#111111"),
    "ctrl": ("#ffe6cc", "#d79b00", "#111111"),
    "io": ("#eeeeee", "#666666", "#111111"),
    "fsm": ("#e1d5e7", "#9673a6", "#111111"),
    "accent": ("#d5e8d4", "#82b366", "#111111"),
    "proc": ("#dae8fc", "#6c8ebf", "#111111"),
    "term": ("#d5e8d4", "#82b366", "#111111"),
    "dec": ("#fff2cc", "#d6b656", "#111111"),
    "data": ("#e1d5e7", "#9673a6", "#111111"),
    "sub": ("#f8cecc", "#b85450", "#111111"),
    "note": ("#ffffcc", "#999933", "#111111"),
    "bad": ("#f8cecc", "#b85450", "#111111"),
}
EDGE_STYLE = {
    # colour, width, dash (draw.io dashPattern) or None
    "data": ("#1f4e9c", 2.2, None),
    "ctrl": ("#c55a11", 1.8, "6 4"),
    "clk": ("#777777", 1.5, "2 3"),
    "flow": ("#222222", 1.8, None),
}

Point = Tuple[float, float]


@dataclass
class Node:
    id: str
    x: float
    y: float
    w: float
    h: float
    label: str
    kind: str = "block"
    sub: str = ""
    size: int = 12
    bold: bool = True
    align: str = "center"
    container: bool = False
    dashed: bool = False
    valign: str = "top"

    def anchor(self, side: str, f: float = 0.5) -> Point:
        if side == "l":
            return (self.x, self.y + self.h * f)
        if side == "r":
            return (self.x + self.w, self.y + self.h * f)
        if side == "t":
            return (self.x + self.w * f, self.y)
        if side == "b":
            return (self.x + self.w * f, self.y + self.h)
        raise ValueError(side)


@dataclass
class Edge:
    id: str
    src: Union[str, Point]
    dst: Union[str, Point]
    kind: str = "data"
    label: str = ""
    sside: str = "r"
    sf: float = 0.5
    dside: str = "l"
    df: float = 0.5
    via: Sequence[Point] = ()
    lpos: Optional[Point] = None
    both: bool = False
    nohead: bool = False


@dataclass
class Text:
    id: str
    x: float
    y: float
    text: str
    size: int = 11
    bold: bool = False
    color: str = "#222222"
    align: str = "left"
    italic: bool = False


@dataclass
class Diagram:
    title: str
    width: int = 1200
    height: int = 700
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    legend: Optional[Sequence[str]] = None  # edge kinds to show, e.g. ("data","ctrl")
    legend_pos: Optional[Point] = None
    _ids: dict = field(default_factory=dict)

    # ---- building -------------------------------------------------------
    def box(self, id, x, y, w, h, label, kind="block", sub="", size=12, bold=True, **kw) -> Node:
        n = Node(id, x, y, w, h, label, kind, sub, size, bold, **kw)
        assert id not in self._ids, f"duplicate id {id}"
        self._ids[id] = n
        self.nodes.append(n)
        return n

    def group(self, id, x, y, w, h, label, size=12, dashed=True, label_pos="top") -> Node:
        n = Node(id, x, y, w, h, label, "group", "", size, True, "left", True, dashed, label_pos)
        self._ids[id] = n
        self.nodes.insert(0, n)
        return n

    def arrow(self, id, src, dst, kind="data", label="", sside="r", sf=0.5, dside="l", df=0.5,
              via=(), lpos=None, both=False, nohead=False) -> Edge:
        e = Edge(id, src, dst, kind, label, sside, sf, dside, df, tuple(via), lpos, both, nohead)
        self.edges.append(e)
        return e

    def text(self, id, x, y, text, size=11, bold=False, color="#222222", align="left", italic=False) -> Text:
        t = Text(id, x, y, text, size, bold, color, align, italic)
        self.texts.append(t)
        return t

    def node(self, id) -> Node:
        return self._ids[id]

    # ---- geometry -------------------------------------------------------
    def _end(self, ref, side, f) -> Point:
        if isinstance(ref, tuple):
            return ref
        return self._ids[ref].anchor(side, f)

    def route(self, e: Edge) -> list:
        return [self._end(e.src, e.sside, e.sf), *e.via, self._end(e.dst, e.dside, e.df)]

    # ---- draw.io --------------------------------------------------------
    def to_drawio(self) -> str:
        esc = lambda s: html.escape(s, quote=True)  # noqa: E731
        out = ['<mxfile host="app.diagrams.net" agent="dgm.py" version="24.0.0">',
               f'<diagram id="{esc(self.title.lower().replace(" ", "-"))}" name="{esc(self.title)}">',
               f'<mxGraphModel dx="{self.width}" dy="{self.height}" grid="1" gridSize="10" guides="1" tooltips="1" '
               f'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{self.width}" '
               f'pageHeight="{self.height}" math="0" shadow="0"><root>',
               '<mxCell id="0"/><mxCell id="1" parent="0"/>']

        def label_html(n: Node) -> str:
            lab = "<b>%s</b>" % esc(n.label) if n.bold else esc(n.label)
            lab = lab.replace("\n", "<br>")
            if n.sub:
                lab += '<br><font style="font-size: %dpx">%s</font>' % (max(n.size - 2, 8), esc(n.sub).replace("\n", "<br>"))
            return lab

        for n in self.nodes:
            if n.kind == "group":
                st = ("rounded=1;arcSize=2;whiteSpace=wrap;html=1;fillColor=none;strokeColor=#888888;"
                      f"dashed={1 if n.dashed else 0};dashPattern=8 4;verticalAlign={n.valign};align=left;spacingLeft=8;spacingTop=4;spacingBottom=4;"
                      f"fontStyle=1;fontSize={n.size};fontColor=#555555;")
                val = esc(n.label)
            else:
                fill, stroke, fc = BOX_STYLE[n.kind]
                shape = {
                    "block": "rounded=1;arcSize=8;",
                    "accent": "rounded=1;arcSize=8;",
                    "ctrl": "rounded=1;arcSize=8;",
                    "fsm": "rounded=1;arcSize=30;",
                    "mem": "shape=cylinder3;boundedLbl=1;backgroundOutline=1;size=8;",
                    "io": "rounded=0;",
                    "proc": "rounded=0;",
                    "term": "rounded=1;arcSize=50;",
                    "dec": "rhombus;",
                    "data": "shape=parallelogram;perimeter=parallelogramPerimeter;fixedSize=1;size=18;",
                    "sub": "shape=process;backgroundOutline=1;",
                    "note": "shape=note;size=12;",
                    "bad": "rounded=1;arcSize=50;",
                }[n.kind]
                st = (f"{shape}whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};fontColor={fc};"
                      f"fontSize={n.size};fontFamily=Helvetica;align={n.align};")
                val = label_html(n)
            out.append(f'<mxCell id="{esc(n.id)}" value="{esc(val)}" style="{st}" vertex="1" parent="1">'
                       f'<mxGeometry x="{n.x:g}" y="{n.y:g}" width="{n.w:g}" height="{n.h:g}" as="geometry"/></mxCell>')
        for t in self.texts:
            st = (f"text;html=1;strokeColor=none;fillColor=none;align={t.align};verticalAlign=top;whiteSpace=wrap;"
                  f"fontSize={t.size};fontColor={t.color};fontStyle={(1 if t.bold else 0) | (2 if t.italic else 0)};"
                  "fontFamily=Helvetica;")
            lines = t.text.split("\n")
            wpx = max(len(s) for s in lines) * t.size * 0.56 + 8
            hpx = len(lines) * t.size * 1.25 + 6
            x = t.x - (wpx / 2 if t.align == "center" else (wpx if t.align == "right" else 0))
            out.append(f'<mxCell id="{esc(t.id)}" value="{esc("<br>".join(esc(s) for s in lines))}" style="{st}" '
                       f'vertex="1" parent="1"><mxGeometry x="{x:g}" y="{t.y:g}" width="{wpx:g}" height="{hpx:g}" '
                       'as="geometry"/></mxCell>')
        for e in self.edges:
            col, wd, dash = EDGE_STYLE[e.kind]
            pts = self.route(e)
            st = (f"endArrow={'none' if e.nohead else 'classic'};startArrow={'classic' if e.both else 'none'};html=1;"
                  f"strokeColor={col};strokeWidth={wd:g};rounded=0;")
            if dash:
                st += f"dashed=1;dashPattern={dash};"
            ends = ""
            if not isinstance(e.src, tuple):
                fx, fy = _frac(self._ids[e.src], pts[0])
                st += f"exitX={fx:g};exitY={fy:g};exitDx=0;exitDy=0;"
                ends += f' source="{esc(e.src)}"'
            if not isinstance(e.dst, tuple):
                fx, fy = _frac(self._ids[e.dst], pts[-1])
                st += f"entryX={fx:g};entryY={fy:g};entryDx=0;entryDy=0;"
                ends += f' target="{esc(e.dst)}"'
            attrs = f'id="{esc(e.id)}" style="{st}" edge="1" parent="1"{ends}'
            geo = '<mxGeometry relative="1" as="geometry">'
            if isinstance(e.src, tuple):
                geo += f'<mxPoint x="{pts[0][0]:g}" y="{pts[0][1]:g}" as="sourcePoint"/>'
            if isinstance(e.dst, tuple):
                geo += f'<mxPoint x="{pts[-1][0]:g}" y="{pts[-1][1]:g}" as="targetPoint"/>'
            if e.via:
                geo += '<Array as="points">' + "".join(f'<mxPoint x="{x:g}" y="{y:g}"/>' for x, y in e.via) + "</Array>"
            geo += "</mxGeometry>"
            out.append(f'<mxCell {attrs} value="">{geo}</mxCell>')
            if e.label:
                lx, ly = e.lpos if e.lpos else _mid(pts, e.label)
                lines = e.label.split("\n")
                wpx = max(len(s) for s in lines) * 6.4 + 8
                hpx = len(lines) * 15 + 4
                out.append(f'<mxCell id="{esc(e.id)}_lbl" value="{esc("<br>".join(esc(s) for s in lines))}" '
                           'style="text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;'
                           f'fontSize=10;fontColor={col};fontFamily=Helvetica;" vertex="1" parent="1">'
                           f'<mxGeometry x="{lx - wpx / 2:g}" y="{ly - hpx / 2:g}" width="{wpx:g}" height="{hpx:g}" '
                           'as="geometry"/></mxCell>')
        out.append("</root></mxGraphModel></diagram></mxfile>")
        return "\n".join(out)

    def save_drawio(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_drawio())

    # ---- rendering ------------------------------------------------------
    def _figure(self, scale: float):
        fig = plt.figure(figsize=(self.width / 100, self.height / 100), dpi=100 * scale)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, self.width)
        ax.set_ylim(self.height, 0)
        ax.axis("off")
        fig.patch.set_facecolor("white")
        return fig, ax

    def _draw(self, ax):
        self._overflow = []
        # title
        ax.text(self.width / 2, 22, self.title, ha="center", va="center", fontsize=15, fontweight="bold", color="#111")
        for n in self.nodes:
            self._draw_node(ax, n)
        for e in self.edges:
            self._draw_edge(ax, e)
        for t in self.texts:
            ax.text(t.x, t.y, t.text, ha=t.align, va="top", fontsize=t.size * 0.75, color=t.color,
                    fontweight="bold" if t.bold else "normal", fontstyle="italic" if t.italic else "normal",
                    linespacing=1.25)
        if self.legend:
            self._draw_legend(ax)

    def _fit(self, ax, txt, n: Node, shape_w_factor=1.0, shape_h_factor=1.0):
        # record overflow of label vs. its shape (data units == px)
        fig = ax.figure
        fig.canvas.draw()
        bb = txt.get_window_extent(renderer=fig.canvas.get_renderer())
        inv = ax.transData.inverted()
        (x0, y0), (x1, y1) = inv.transform((bb.x0, bb.y0)), inv.transform((bb.x1, bb.y1))
        tw, th = abs(x1 - x0), abs(y1 - y0)
        if tw > n.w * shape_w_factor - 6 or th > n.h * shape_h_factor - 4:
            self._overflow.append((n.id, round(tw), round(th), n.w * shape_w_factor, n.h * shape_h_factor))

    def _draw_node(self, ax, n: Node):
        if n.kind == "group":
            ax.add_patch(FancyBboxPatch((n.x, n.y), n.w, n.h, boxstyle="round,pad=0,rounding_size=6", fc="none",
                                        ec="#888888", lw=1.3, ls=(0, (6, 4)) if n.dashed else "-", zorder=0))
            if n.valign == "bottom":
                ax.text(n.x + 8, n.y + n.h - 6, n.label, ha="left", va="bottom", fontsize=n.size * 0.75, color="#555555",
                        fontweight="bold", zorder=1)
            else:
                ax.text(n.x + 8, n.y + 6, n.label, ha="left", va="top", fontsize=n.size * 0.75, color="#555555",
                        fontweight="bold", zorder=1)
            return
        fill, stroke, fc = BOX_STYLE[n.kind]
        k = n.kind
        x, y, w, h = n.x, n.y, n.w, n.h
        if k in ("block", "accent", "ctrl"):
            p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=%g" % (min(w, h) * 0.08), fc=fill, ec=stroke, lw=1.6)
        elif k == "fsm" or k == "bad":
            p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=%g" % (min(w, h) * 0.3), fc=fill, ec=stroke, lw=1.6)
        elif k == "term":
            p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=%g" % (h / 2), fc=fill, ec=stroke, lw=1.6)
        elif k == "dec":
            p = Polygon([(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)], closed=True, fc=fill, ec=stroke, lw=1.6)
        elif k == "data":
            s = 18
            p = Polygon([(x + s, y), (x + w, y), (x + w - s, y + h), (x, y + h)], closed=True, fc=fill, ec=stroke, lw=1.6)
        elif k == "sub":
            p = Rectangle((x, y), w, h, fc=fill, ec=stroke, lw=1.6)
            ax.add_patch(p)
            for dx in (10, w - 10):
                ax.plot([x + dx, x + dx], [y, y + h], color=stroke, lw=1.2)
        elif k == "mem":
            p = FancyBboxPatch((x, y + 6), w, h - 6, boxstyle="round,pad=0,rounding_size=4", fc=fill, ec=stroke, lw=1.6)
        else:  # io, proc, note
            p = Rectangle((x, y), w, h, fc=fill, ec=stroke, lw=1.6)
        if k != "sub":
            ax.add_patch(p)
        if k == "mem":
            ax.plot([x, x + w], [y + 14, y + 14], color=stroke, lw=1.0)
        # label
        lines = n.label.split("\n")
        fs = n.size * 0.75
        main = "\n".join(lines)
        full = main + ("\n" + n.sub if n.sub else "")
        cx = x + w / 2
        cy = y + h / 2 + (3 if k == "mem" else 0)
        wf = 0.62 if k == "dec" else (0.8 if k == "data" else 1.0)
        hf = 0.62 if k == "dec" else 1.0
        if n.sub:
            nl_main = len(lines)
            nl_sub = len(n.sub.split("\n"))
            lh = fs * 1.22 * 100 / 72  # px per line (dpi-independent: points->px at 100 dpi / data unit == px)
            total = (nl_main * fs + nl_sub * (fs - 1.5)) * 1.25 * 100 / 72
            top = cy - total / 2
            t1 = ax.text(cx, top, main, ha="center", va="top", fontsize=fs, color=fc, fontweight="bold" if n.bold else "normal", linespacing=1.2)
            t2 = ax.text(cx, top + nl_main * fs * 1.25 * 100 / 72 + 1, n.sub, ha="center", va="top", fontsize=fs - 1.5, color="#333333", linespacing=1.2)
            for t in (t1, t2):
                self._fit(ax, t, n, wf, hf)
            _ = lh
        else:
            t = ax.text(cx, cy, main, ha="center" if n.align == "center" else "left", va="center", fontsize=fs, color=fc,
                        fontweight="bold" if n.bold else "normal", linespacing=1.2)
            if n.align != "center":
                t.set_x(x + 8)
            self._fit(ax, t, n, wf, hf)
        _ = full

    def _draw_edge(self, ax, e: Edge):
        col, wd, dash = EDGE_STYLE[e.kind]
        pts = self.route(e)
        xs, ys = zip(*pts)
        ls = (0, tuple(int(v) * 1.0 for v in dash.split())) if dash else "-"
        ax.plot(xs[:-1] + (xs[-1],), ys[:-1] + (ys[-1],), color=col, lw=wd, ls=ls, solid_capstyle="butt", zorder=2)
        if not e.nohead:
            _arrow_head(ax, pts[-2], pts[-1], col)
        if e.both:
            _arrow_head(ax, pts[1], pts[0], col)
        if e.label:
            lx, ly = e.lpos if e.lpos else _mid(pts, e.label)
            ax.text(lx, ly, e.label, ha="center", va="center", fontsize=7.5, color=col, linespacing=1.15,
                    bbox=dict(fc="white", ec="none", pad=0.6, alpha=0.9), zorder=4)

    def _draw_legend(self, ax):
        names = {"data": "data path", "ctrl": "control / status", "clk": "clock / reset", "flow": "flow"}
        x0, y0 = self.legend_pos or (self.width - 190, self.height - 20 - 20 * len(self.legend))
        ax.add_patch(Rectangle((x0 - 8, y0 - 8), 180, 20 * len(self.legend) + 10, fc="white", ec="#bbbbbb", lw=0.8, zorder=1))
        for i, k in enumerate(self.legend):
            col, wd, dash = EDGE_STYLE[k]
            ls = (0, tuple(int(v) * 1.0 for v in dash.split())) if dash else "-"
            yy = y0 + 20 * i + 4
            ax.plot([x0, x0 + 36], [yy, yy], color=col, lw=wd, ls=ls)
            ax.text(x0 + 44, yy, names[k], va="center", fontsize=8.5, color="#222")

    def check_text(self):
        """Render once and return a list of overflowing labels: (node id, text w, text h, shape w, shape h)."""
        fig, ax = self._figure(1)
        self._draw(ax)
        plt.close(fig)
        return self._overflow

    def save_png(self, path, scale=2.0, palette=False):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fig, ax = self._figure(scale)
        self._draw(ax)
        fig.savefig(path, dpi=100 * scale, facecolor="white")
        plt.close(fig)
        if palette:
            from PIL import Image
            im = Image.open(path).convert("RGB").quantize(colors=48, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
            im.save(path, optimize=True)
        return path

    def save_svg(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fig, ax = self._figure(1)
        self._draw(ax)
        fig.savefig(path, format="svg", facecolor="white")
        plt.close(fig)
        return path

    def export_all(self, folder, stem, warn=True):
        """drawio + hi-res png + compact png (for embedding in documents) + svg. Returns overflow list."""
        ov = self.check_text()
        if warn and ov:
            print(f"[dgm] {stem}: label overflow ->", ov)
        self.save_drawio(os.path.join(folder, stem + ".drawio"))
        self.save_png(os.path.join(folder, stem + ".png"), scale=2.0)
        self.save_png(os.path.join(folder, stem + "_doc.png"), scale=1.0, palette=True)
        self.save_svg(os.path.join(folder, stem + ".svg"))
        return ov


def _frac(n: Node, p: Point):
    return (min(max((p[0] - n.x) / n.w, 0), 1), min(max((p[1] - n.y) / n.h, 0), 1))


def _mid(pts, label=""):
    """Label anchor: midpoint of the longest segment, nudged off the line (above a horizontal run, right of a
    vertical one) so the label does not sit on top of the arrow."""
    best, bl, horiz = None, -1, True
    for a, b in zip(pts, pts[1:]):
        l = abs(a[0] - b[0]) + abs(a[1] - b[1])
        if l > bl:
            best, bl, horiz = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), l, abs(a[1] - b[1]) < abs(a[0] - b[0])
    w = max((len(s) for s in label.split("\n")), default=0) * 3.6 + 8
    n = label.count("\n") + 1
    return (best[0], best[1] - 8 - 5 * (n - 1)) if horiz else (best[0] + w, best[1])


def _arrow_head(ax, a: Point, b: Point, col: str):
    import math
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    L, W = 11, 5
    tip = b
    base = (tip[0] - L * math.cos(ang), tip[1] - L * math.sin(ang))
    p1 = (base[0] + W * math.sin(ang), base[1] - W * math.cos(ang))
    p2 = (base[0] - W * math.sin(ang), base[1] + W * math.cos(ang))
    ax.add_patch(Polygon([tip, p1, p2], closed=True, fc=col, ec=col, lw=0.5, zorder=3))


def wrap(s: str, width: int) -> str:
    return "\n".join(textwrap.wrap(s, width))
