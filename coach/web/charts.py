"""SVG chart + gauge builders for the dashboard. Pure functions: data in, HTML/SVG out.

Design rules from the user's feedback: every metric is a gauge with a scale + label
(never a bare number); intensity is folded into the weekly volume as stacked zones;
marks carry data-tip for hover. No external libs — inline SVG only.
"""

from __future__ import annotations

from datetime import date
from typing import Any


def _sx(v, lo, hi, x0, x1):
    return x0 if hi == lo else x0 + (v - lo) / (hi - lo) * (x1 - x0)


def _sy(v, lo, hi, y0, y1):  # y0 bottom, y1 top
    return y0 if hi == lo else y0 + (v - lo) / (hi - lo) * (y1 - y0)


def _svg(w, h):
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet" '
            f'class="chart" role="img">')


# ---------------------------------------------------------------- gauge -------
def gauge(label: str, value: str, tag: str, tag_kind: str, lo: float, hi: float,
          marker: float, zones: list[tuple[float, str]], ends: tuple[str, str],
          note: str = "", peak: float | None = None) -> str:
    """A metric as a scale: colored zone track, a marker at `marker`, value + label."""
    span = hi - lo or 1
    segs = []
    prev = lo
    for end, color in zones:
        left = (prev - lo) / span * 100
        width = (end - prev) / span * 100
        segs.append(f'<div class="fill" style="left:{left:.1f}%;width:{width:.1f}%;'
                    f'background:{color}"></div>')
        prev = end
    mk = max(0, min(100, (marker - lo) / span * 100))
    peak_html = ""
    if peak is not None:
        pk = max(0, min(100, (peak - lo) / span * 100))
        peak_html = f'<div class="peak" style="left:{pk:.1f}%" data-tip="pico {peak}"></div>'
    return (
        f'<div class="gauge">'
        f'<div class="glabel">{label}</div>'
        f'<div class="grow"><span class="gval">{value}</span>'
        f'<span class="gtag t-{tag_kind}">{tag}</span></div>'
        f'<div class="track">{"".join(segs)}{peak_html}'
        f'<div class="mk" data-tip="{label}: {value}" style="left:{mk:.1f}%"></div></div>'
        f'<div class="ends"><span>{ends[0]}</span><span>{ends[1]}</span></div>'
        + (f'<div class="gnote">{note}</div>' if note else "")
        + '</div>'
    )


# ------------------------------------------------- load: CTL vs ATL lines -----
def _month_ticks(lo_ord, hi_ord, x0, x1, y, months):
    out = []
    for mo, lbl in months:
        xo = date(2026, mo, 1).toordinal()
        if lo_ord <= xo <= hi_ord:
            x = _sx(xo, lo_ord, hi_ord, x0, x1)
            out.append(f'<text x="{x:.1f}" y="{y}" class="tk tk-m">{lbl}</text>')
    return "".join(out)


MONTHS = [(3, "mar"), (4, "abr"), (5, "mai"), (6, "jun"), (7, "jul"), (8, "ago")]


def load_lines(load: list[dict]) -> str:
    W, H = 720, 300
    ml, mr, mt, mb = 34, 14, 16, 30
    xs = [date.fromisoformat(x["date"]).toordinal() for x in load]
    lo, hi = min(xs), max(xs)
    vmax = max(max(x["ctl"] for x in load), max(x["atl"] for x in load)) * 1.12
    x0, x1, y0, y1 = ml, W - mr, H - mb, mt
    p = [_svg(W, H)]
    for gv in range(0, int(vmax) + 1, 2):
        y = _sy(gv, 0, vmax, y0, y1)
        p.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" class="grid-l"/>')
        p.append(f'<text x="{ml-6}" y="{y+3:.1f}" class="tk tk-r">{gv}</text>')
    p.append(_month_ticks(lo, hi, x0, x1, H - 10, MONTHS))
    def poly(key, cls):
        pts = " ".join(f"{_sx(date.fromisoformat(x['date']).toordinal(),lo,hi,x0,x1):.1f},"
                       f"{_sy(x[key],0,vmax,y0,y1):.1f}" for x in load)
        return f'<polyline points="{pts}" fill="none" class="{cls}"/>'
    p.append(poly("atl", "l-atl"))
    p.append(poly("ctl", "l-ctl"))
    # sparse hover dots (every ~5th day) to keep it light
    for i, x in enumerate(load):
        if i % 4 and i != len(load) - 1:
            continue
        cx = _sx(date.fromisoformat(x["date"]).toordinal(), lo, hi, x0, x1)
        tip = f'{x["date"]}<br><b>Condição</b> {x["ctl"]} · <b>Fadiga</b> {x["atl"]} · Forma {x["tsb"]:+.1f}'
        p.append(f'<circle cx="{cx:.1f}" cy="{_sy(x["ctl"],0,vmax,y0,y1):.1f}" r="5" '
                 f'fill="transparent" class="hoverable" data-tip="{tip}"/>')
    peak = max(load, key=lambda x: x["ctl"])
    px = _sx(date.fromisoformat(peak["date"]).toordinal(), lo, hi, x0, x1)
    py = _sy(peak["ctl"], 0, vmax, y0, y1)
    p.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3" class="d-ctl"/>')
    p.append(f'<text x="{px:.1f}" y="{py-8:.1f}" class="note tk-m">pico {peak["ctl"]:.1f}</text>')
    lx = _sx(hi, lo, hi, x0, x1)
    for key, cls in (("ctl", "d-ctl"), ("atl", "d-atl")):
        p.append(f'<circle cx="{lx:.1f}" cy="{_sy(load[-1][key],0,vmax,y0,y1):.1f}" r="3.4" class="{cls}"/>')
    p.append("</svg>")
    return "".join(p)


def form_area(load: list[dict]) -> str:
    W, H = 720, 150
    ml, mr, mt, mb = 30, 14, 10, 24
    xs = [date.fromisoformat(x["date"]).toordinal() for x in load]
    lo, hi = min(xs), max(xs)
    amax = max(3.0, max(abs(x["tsb"]) for x in load) * 1.1)
    x0, x1, y0, y1 = ml, W - mr, H - mb, mt
    yz = _sy(0, -amax, amax, y0, y1)
    pts = [(_sx(date.fromisoformat(x['date']).toordinal(), lo, hi, x0, x1),
            _sy(x["tsb"], -amax, amax, y0, y1)) for x in load]
    pos = f'M {pts[0][0]:.1f} {yz:.1f} ' + " ".join(f"L {x:.1f} {min(y,yz):.1f}" for x, y in pts) + f' L {pts[-1][0]:.1f} {yz:.1f} Z'
    neg = f'M {pts[0][0]:.1f} {yz:.1f} ' + " ".join(f"L {x:.1f} {max(y,yz):.1f}" for x, y in pts) + f' L {pts[-1][0]:.1f} {yz:.1f} Z'
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    p = [_svg(W, H)]
    p.append(f'<text x="{ml-5}" y="{y1+8:.1f}" class="tk tk-r">+{amax:.0f}</text>')
    p.append(f'<text x="{ml-5}" y="{y0:.1f}" class="tk tk-r">-{amax:.0f}</text>')
    p.append(f'<path d="{neg}" class="a-neg"/>')
    p.append(f'<path d="{pos}" class="a-pos"/>')
    p.append(f'<line x1="{ml}" y1="{yz:.1f}" x2="{x1}" y2="{yz:.1f}" class="axis-l"/>')
    p.append(f'<polyline points="{line}" fill="none" class="l-form"/>')
    p.append(_month_ticks(lo, hi, x0, x1, H - 8, MONTHS))
    p.append("</svg>")
    return "".join(p)


# ---------------------------------------- weekly/monthly volume w/ intensity --
def volume_bars(items: list[dict], span_label: str) -> str:
    """Stacked bars (easy/mod/hard km) per period, with hover breakdown."""
    W, H = 720, 300
    ml, mr, mt, mb = 30, 12, 16, 40
    n = len(items)
    vmax = max((it["km"] for it in items), default=1) * 1.15 or 1
    x0, x1, y0, y1 = ml, W - mr, H - mb, mt
    bw = (x1 - x0) / n
    p = [_svg(W, H)]
    for gv in range(0, int(vmax) + 1, 5):
        y = _sy(gv, 0, vmax, y0, y1)
        p.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" class="grid-l"/>')
        p.append(f'<text x="{ml-6}" y="{y+3:.1f}" class="tk tk-r">{gv}</text>')
    for i, it in enumerate(items):
        cx = x0 + bw * i + bw / 2
        bwid = min(34, bw * 0.62)
        if it["km"] <= 0:
            p.append(f'<text x="{cx:.1f}" y="{y0-4:.1f}" class="tk tk-m" opacity=".5">0</text>')
        else:
            tip = (f'<b>{it["label"]}</b><br>{it["km"]:.1f} km · {it["runs"]} treino(s)'
                   f'<br>fácil {it["easy"]:.0f} · mod {it["mod"]:.0f} · forte {it["hard"]:.0f} km')
            base = y0
            for zk, cls in (("easy", "b-easy"), ("mod", "b-mod"), ("hard", "b-hard")):
                seg = it[zk]
                if seg <= 0:
                    continue
                h = (base - _sy(seg, 0, vmax, y0, y1))
                if base - h < base:
                    top = base - h
                    p.append(f'<rect x="{cx-bwid/2:.1f}" y="{top+1:.1f}" width="{bwid:.1f}" '
                             f'height="{max(0.5,h-1):.1f}" rx="2.5" class="{cls} hoverable" data-tip="{tip}"/>')
                    base = top
            p.append(f'<text x="{cx:.1f}" y="{_sy(it["km"],0,vmax,y0,y1)-5:.1f}" class="note tk-m">{it["km"]:.0f}</text>')
        p.append(f'<text x="{cx:.1f}" y="{H-22}" class="tk tk-m">{it["short"]}</text>')
        p.append(f'<text x="{cx:.1f}" y="{H-9}" class="tk tk-m" opacity=".7">{it["runs"]}t</text>')
    p.append("</svg>")
    return "".join(p)


# --------------------------------------------------- resting HR recovery ------
def rhr_line(rows: list[dict]) -> str:
    W, H = 720, 230
    ml, mr, mt, mb = 30, 14, 18, 26
    xs = [date.fromisoformat(w["date"]).toordinal() for w in rows]
    lo, hi = min(xs), max(xs)
    vals = [w["resting_hr"] for w in rows]
    ymin, ymax = min(vals) - 2, max(vals) + 2
    x0, x1, y0, y1 = ml, W - mr, H - mb, mt
    base = sum(vals) / len(vals)
    yb = _sy(base, ymin, ymax, y0, y1)
    p = [_svg(W, H)]
    p.append(f'<line x1="{ml}" y1="{yb:.1f}" x2="{x1}" y2="{yb:.1f}" class="dash"/>')
    p.append(f'<text x="{x1}" y="{yb-4:.1f}" class="note tk-r">média {base:.0f}</text>')
    for hv in range(int(ymin) + 1, int(ymax), 3):
        y = _sy(hv, ymin, ymax, y0, y1)
        p.append(f'<text x="{ml-6}" y="{y+3:.1f}" class="tk tk-r">{hv}</text>')
    pts = [(_sx(date.fromisoformat(w["date"]).toordinal(), lo, hi, x0, x1),
            _sy(w["resting_hr"], ymin, ymax, y0, y1), w) for w in rows]
    p.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x,y,_ in pts)}" fill="none" class="l-rhr"/>')
    for x, y, w in pts:
        hot = w["resting_hr"] >= base + 4
        tip = f'{w["date"]}<br><b>{w["resting_hr"]} bpm</b>'
        p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{4 if hot else 3}" '
                 f'class="{"d-hot" if hot else "d-rhr"} hoverable" data-tip="{tip}"/>')
    p.append(_month_ticks(lo, hi, x0, x1, H - 8, MONTHS))
    p.append("</svg>")
    return "".join(p)


def bb_range(rows: list[dict]) -> str:
    W, H = 720, 240
    ml, mr, mt, mb = 26, 14, 16, 30
    n = len(rows)
    x0, x1, y0, y1 = ml, W - mr, H - mb, mt
    bw = (x1 - x0) / n
    p = [_svg(W, H)]
    for gv in (0, 25, 50, 75, 100):
        y = _sy(gv, 0, 100, y0, y1)
        p.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" class="grid-l"/>')
        p.append(f'<text x="{ml-6}" y="{y+3:.1f}" class="tk tk-r">{gv}</text>')
    for i, w in enumerate(rows):
        cx = x0 + bw * i + bw / 2
        yhi = _sy(w["body_battery_high"], 0, 100, y0, y1)
        ylo = _sy(w["body_battery_low"], 0, 100, y0, y1)
        depleted = w["body_battery_high"] < 40
        tip = (f'<b>{w["date"]}</b><br>Body Battery {w["body_battery_low"]}–{w["body_battery_high"]}'
               f'<br>estresse {w.get("avg_stress") if w.get("avg_stress") is not None else "—"}')
        p.append(f'<rect x="{cx-4:.1f}" y="{yhi:.1f}" width="8" height="{max(1,ylo-yhi):.1f}" '
                 f'rx="4" class="{"bb-bad" if depleted else "bb-ok"} hoverable" data-tip="{tip}"/>')
        if w.get("avg_stress") is not None:
            p.append(f'<circle cx="{cx:.1f}" cy="{_sy(w["avg_stress"],0,100,y0,y1):.1f}" r="2.4" class="d-stress"/>')
        if i % 3 == 0:
            p.append(f'<text x="{cx:.1f}" y="{H-9}" class="tk tk-m">{w["date"][5:]}</text>')
    p.append("</svg>")
    return "".join(p)


# chart-specific stroke/fill styles (kept here so charts.py is self-contained)
CHART_CSS = """
.l-ctl{stroke:var(--accent);stroke-width:2.6;stroke-linejoin:round;stroke-linecap:round}
.l-atl{stroke:var(--mod);stroke-width:1.8;stroke-linejoin:round;opacity:.85}
.d-ctl{fill:var(--accent)} .d-atl{fill:var(--mod)}
.a-pos{fill:var(--good);opacity:.20} .a-neg{fill:var(--alert);opacity:.16}
.l-form{stroke:var(--ink-2);stroke-width:1.5;stroke-linejoin:round}
.b-easy{fill:var(--easy)} .b-mod{fill:var(--mod)} .b-hard{fill:var(--hard)}
.l-rhr{stroke:var(--violet);stroke-width:2;stroke-linejoin:round}
.d-rhr{fill:var(--violet)} .d-hot{fill:var(--alert)}
.bb-ok{fill:var(--good);opacity:.75} .bb-bad{fill:var(--alert);opacity:.85}
.d-stress{fill:var(--ink-2)}
"""
