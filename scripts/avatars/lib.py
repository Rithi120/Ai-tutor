"""Shared drawing helpers for the Learnova avatar set.

Only the face construction (eyes, brows, nose, mouth, head silhouette) is shared
so the 20 characters read as one family; every costume piece is drawn per
character in chars.py.
"""
import math
import random
import re


def n(v):
    s = f"{v:.1f}"
    if s.endswith(".0"):
        s = s[:-2]
    if s == "-0":
        s = "0"
    return s


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(a[k] + (b[k] - a[k]) * t):02x}" for k in range(3))


def stops(lst):
    out = []
    for s in lst:
        o, c = s[0], s[1]
        op = f' stop-opacity="{s[2]}"' if len(s) > 2 else ""
        out.append(f'<stop offset="{o}" stop-color="{c}"{op}/>')
    return "".join(out)


_TOK = re.compile(r"[MLCQSTAHVZ]|-?\d*\.?\d+")


def mirror(d, axis=128):
    """Mirror an absolute-coordinate path horizontally around x=axis."""
    toks = _TOK.findall(d)
    out, cmd, idx = [], None, 0
    for t in toks:
        if t.isalpha():
            cmd, idx = t, 0
            out.append(t)
            continue
        v = float(t)
        if cmd in "MLCQST":
            if idx % 2 == 0:
                v = 2 * axis - v
        elif cmd == "H":
            v = 2 * axis - v
        elif cmd == "A":
            k = idx % 7
            if k == 4:
                v = 1 - v
            elif k == 5:
                v = 2 * axis - v
        out.append(n(v))
        idx += 1
    s = ""
    for t in out:
        if t.isalpha():
            s += t
        else:
            if s and not s[-1].isalpha():
                s += " " if not t.startswith("-") else ""
            s += t
    return s


def pts(points, close=True):
    d = "M" + " ".join(f"{n(x)} {n(y)}" for x, y in points[:1])
    d += "L" + " ".join(f"{n(x)} {n(y)}" for x, y in points[1:])
    return d + ("Z" if close else "")


def star(cx, cy, R, r, k=5, rot=-90):
    p = []
    for i in range(2 * k):
        a = math.radians(rot + i * 180 / k)
        rr = R if i % 2 == 0 else r
        p.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts(p)


def sparkle(cx, cy, R, w=None):
    w = w if w is not None else R * 0.22
    return (f"M{n(cx)} {n(cy - R)}Q{n(cx + w)} {n(cy - w)} {n(cx + R)} {n(cy)}"
            f"Q{n(cx + w)} {n(cy + w)} {n(cx)} {n(cy + R)}Q{n(cx - w)} {n(cy + w)} {n(cx - R)} {n(cy)}"
            f"Q{n(cx - w)} {n(cy - w)} {n(cx)} {n(cy - R)}Z")


def gear(cx, cy, r, teeth=10, depth=None, hole=0.42, rot=0):
    depth = depth if depth is not None else r * 0.22
    p = []
    step = 360 / teeth
    for i in range(teeth):
        base = rot + i * step
        for off, rr in ((-0.3, r - depth), (-0.18, r), (0.18, r), (0.3, r - depth)):
            a = math.radians(base + off * step)
            p.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d = pts(p)
    if hole:
        h = r * hole
        d += f"M{n(cx - h)} {n(cy)}A{n(h)} {n(h)} 0 1 0 {n(cx + h)} {n(cy)}A{n(h)} {n(h)} 0 1 0 {n(cx - h)} {n(cy)}Z"
    return d


def circ(cx, cy, r):
    return f"M{n(cx - r)} {n(cy)}A{n(r)} {n(r)} 0 1 0 {n(cx + r)} {n(cy)}A{n(r)} {n(r)} 0 1 0 {n(cx - r)} {n(cy)}Z"


def blob(cx, cy, rx, ry, amp=0.12, k=3, phase=0.0, npts=10, seed=None):
    rnd = random.Random(seed)
    P = []
    for i in range(npts):
        a = 2 * math.pi * i / npts
        m = 1 + amp * math.sin(k * a + phase) + (rnd.uniform(-amp, amp) * 0.5 if seed is not None else 0)
        P.append((cx + rx * m * math.cos(a), cy + ry * m * math.sin(a)))
    return smooth_closed(P)


def smooth_closed(P):
    L = len(P)
    d = f"M{n(P[0][0])} {n(P[0][1])}"
    for i in range(L):
        p0, p1, p2, p3 = P[i - 1], P[i], P[(i + 1) % L], P[(i + 2) % L]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f"C{n(c1[0])} {n(c1[1])} {n(c2[0])} {n(c2[1])} {n(p2[0])} {n(p2[1])}"
    return d + "Z"


def smooth_open(P):
    d = f"M{n(P[0][0])} {n(P[0][1])}"
    L = len(P)
    for i in range(L - 1):
        p0 = P[i - 1] if i > 0 else P[i]
        p1, p2 = P[i], P[i + 1]
        p3 = P[i + 2] if i + 2 < L else P[i + 1]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f"C{n(c1[0])} {n(c1[1])} {n(c2[0])} {n(c2[1])} {n(p2[0])} {n(p2[1])}"
    return d


class Av:
    def __init__(self, aid, title):
        self.id = aid
        self.title = title
        self.defs = []
        self.body = []

    def i(self, name):
        return f"{self.id}-{name}"

    def u(self, name):
        return f"url(#{self.id}-{name})"

    def lg(self, name, st, x1=0, y1=0, x2=0, y2=1, user=False):
        gu = ' gradientUnits="userSpaceOnUse"' if user else ""
        self.defs.append(f'<linearGradient id="{self.i(name)}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"{gu}>{stops(st)}</linearGradient>')
        return self.u(name)

    def rg(self, name, st, cx=0.5, cy=0.5, r=0.5, fx=None, fy=None, user=False):
        gu = ' gradientUnits="userSpaceOnUse"' if user else ""
        f = ""
        if fx is not None:
            f = f' fx="{fx}" fy="{fy}"'
        self.defs.append(f'<radialGradient id="{self.i(name)}" cx="{cx}" cy="{cy}" r="{r}"{f}{gu}>{stops(st)}</radialGradient>')
        return self.u(name)

    def add(self, *parts):
        self.body.extend(parts)

    def svg(self):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256" '
                f'role="img" aria-labelledby="{self.id}-title"><title id="{self.id}-title">{self.title}</title>'
                f'<defs>{"".join(self.defs)}</defs>{"".join(self.body)}</svg>')


# ---------------------------------------------------------------- the face kit
HEAD = "M84 118C84 84 104 68 128 68C152 68 172 84 172 118C172 150 154 172 128 172C102 172 84 150 84 118Z"
TORSO = "M34 256C36 220 60 200 98 192L158 192C196 200 220 220 222 256Z"
NECK = "M110 150L110 196Q128 206 146 196L146 150Z"
EX = (111, 145)
EY = 126
MOUTH = "#5a1f1f"

BROWS = {
    "soft": (102, 112, 110, 106.5, 119, 109.5),
    "raised": (102, 107, 110, 101, 119, 104.5),
    "focused": (102, 107.5, 111, 107, 120, 113),
    "determined": (102, 108.5, 111, 106.5, 120, 111),
    "flat": (102, 110.5, 111, 108, 120, 110),
    "worried": (102, 112, 111, 107, 120, 106),
}


def brow_path(style, side):
    x0, y0, cx, cy, x1, y1 = BROWS[style]
    d = f"M{n(x0)} {n(y0)}Q{n(cx)} {n(cy)} {n(x1)} {n(y1)}"
    return d if side == 0 else mirror(d)


def eye(kind, ex, ey, iris, side, lashes):
    o = []
    if kind in ("open", "wide", "big"):
        rx, ry, h = {"open": (6.4, 7.8, 2.4), "wide": (7, 8.8, 2.8), "big": (9.6, 11.6, 3.4)}[kind]
        o.append(f'<ellipse cx="{n(ex)}" cy="{n(ey)}" rx="{rx}" ry="{ry}" fill="{iris}"/>')
        o.append(f'<circle cx="{n(ex + rx * 0.36)}" cy="{n(ey - ry * 0.34)}" r="{h}" fill="#fff"/>')
        o.append(f'<circle cx="{n(ex - rx * 0.32)}" cy="{n(ey + ry * 0.36)}" r="{n(h * 0.45)}" fill="#fff" fill-opacity=".75"/>')
        if lashes:
            sx = -1 if side == 0 else 1
            o.append(f'<path d="M{n(ex + sx * rx * 0.8)} {n(ey - ry * 0.62)}L{n(ex + sx * (rx + 3.6))} {n(ey - ry - 0.8)}" stroke="{iris}" stroke-width="2.3" stroke-linecap="round"/>')
    elif kind == "narrow":
        o.append(f'<ellipse cx="{ex}" cy="{ey + 1}" rx="6.4" ry="5.4" fill="{iris}"/>')
        o.append(f'<circle cx="{n(ex + 2.3)}" cy="{n(ey - 0.8)}" r="2" fill="#fff"/>')
        o.append(f'<path d="M{ex - 8.5} {ey - 2.5}Q{ex} {ey - 7.5} {ex + 8.5} {ey - 2.5}" fill="none" stroke="{iris}" stroke-width="2.6" stroke-linecap="round"/>')
    elif kind == "calm":
        o.append(f'<path d="M{n(ex - 6.6)} {n(ey + 0.5)}Q{ex} {n(ey - 2.6)} {n(ex + 6.6)} {n(ey + 0.5)}Q{n(ex + 6.6)} {n(ey + 8)} {ex} {n(ey + 8)}Q{n(ex - 6.6)} {n(ey + 8)} {n(ex - 6.6)} {n(ey + 0.5)}Z" fill="{iris}"/>')
        o.append(f'<circle cx="{n(ex + 2.4)}" cy="{n(ey + 2.4)}" r="1.9" fill="#fff"/>')
        o.append(f'<path d="M{n(ex - 8.6)} {n(ey + 0.6)}Q{ex} {n(ey - 4.2)} {n(ex + 8.6)} {n(ey + 0.6)}" fill="none" stroke="{iris}" stroke-width="2.6" stroke-linecap="round"/>')
    elif kind in ("happy", "wink"):
        o.append(f'<path d="M{ex - 7} {ey + 2}Q{ex} {ey - 6.5} {ex + 7} {ey + 2}" fill="none" stroke="{iris}" stroke-width="3.4" stroke-linecap="round"/>')
        if lashes:
            sx = -1 if side == 0 else 1
            o.append(f'<path d="M{n(ex + sx * 7)} {ey + 2}l{n(sx * 3.4)} -1.4" stroke="{iris}" stroke-width="2.2" stroke-linecap="round"/>')
    return "".join(o)


def mouth(kind, col=MOUTH):
    if kind == "grin":
        return (f'<path d="M113 149Q128 153 143 149Q141 166 128 166Q115 166 113 149Z" fill="{col}"/>'
                '<path d="M116 150.2Q128 153.4 140 150.2L139.3 154.2Q128 156.4 116.7 154.2Z" fill="#fff"/>'
                '<path d="M120 162.4Q128 157 136 162.4Q132.4 165.6 128 165.6Q123.6 165.6 120 162.4Z" fill="#ef7a7a"/>')
    if kind == "open":
        return (f'<path d="M112 148Q128 151 144 148Q142 170 128 170Q114 170 112 148Z" fill="{col}"/>'
                '<path d="M115 149.2Q128 152.2 141 149.2L140.3 153.6Q128 156 115.7 153.6Z" fill="#fff"/>'
                '<path d="M118 165Q128 157.5 138 165Q133.5 169.4 128 169.4Q122.5 169.4 118 165Z" fill="#ef7a7a"/>')
    if kind == "o":
        return (f'<ellipse cx="128" cy="155" rx="5.4" ry="6.6" fill="{col}"/>'
                '<path d="M124 159Q128 156 132 159Q130 161.4 128 161.4Q126 161.4 124 159Z" fill="#ef7a7a"/>')
    strokes = {
        "smile": "M115 150Q128 161 141 150",
        "soft": "M118 151Q128 158.5 138 151",
        "smirk": "M117 153Q130 158 141 147.5",
        "half": "M119 152.5Q128 156.5 137.5 151",
        "flat": "M120 153Q128 155 136 153",
    }
    return f'<path d="{strokes[kind]}" fill="none" stroke="{col}" stroke-width="3.6" stroke-linecap="round"/>'


def face(a, skin, shade, eyes="open", brows="soft", mth="smile", brow="#3b2418", iris="#2a1a14",
         lashes=False, blush=True, freckles=False, ears=True, nose=True, head=True, blush_op=0.3,
         mouth_col=MOUTH, skin_name="skin"):
    if isinstance(eyes, str):
        eyes = (eyes, eyes)
    if isinstance(brows, str):
        brows = (brows, brows)
    o = []
    g = a.rg(skin_name, [(0, mix(skin, "#ffffff", 0.14)), (0.55, skin), (1, shade)], cx=0.42, cy=0.36, r=0.72)
    if ears:
        for ex in (85, 171):
            o.append(f'<ellipse cx="{ex}" cy="127" rx="8" ry="11" fill="{shade}"/>')
            inner = f"M{ex - 2 if ex < 128 else ex + 2} 121Q{ex - 5 if ex < 128 else ex + 5} 127 {ex - 1 if ex < 128 else ex + 1} 133"
            o.append(f'<path d="{inner}" fill="none" stroke="{mix(shade, "#000000", 0.18)}" stroke-width="2" stroke-linecap="round"/>')
    if head:
        o.append(f'<path d="{HEAD}" fill="{g}"/>')
    if blush:
        o.append(f'<g fill="#ff6f7d" fill-opacity="{blush_op}"><ellipse cx="101" cy="145" rx="8" ry="4.6"/><ellipse cx="155" cy="145" rx="8" ry="4.6"/></g>')
    if freckles:
        fc = mix(shade, "#5a2a14", 0.3)
        o.append(f'<g fill="{fc}" fill-opacity=".7">' + "".join(
            f'<circle cx="{x}" cy="{y}" r="1.3"/>' for x, y in ((99, 139), (104, 142), (108, 138.5), (157, 139), (152, 142), (148, 138.5))) + "</g>")
    for side, ex in enumerate(EX):
        o.append(eye(eyes[side], ex, EY, iris, side, lashes))
        if brows[side]:
            o.append(f'<path d="{brow_path(brows[side], side)}" fill="none" stroke="{brow}" stroke-width="4.2" stroke-linecap="round"/>')
    if nose:
        o.append(f'<path d="M124.5 139.5Q128 143.5 131.5 139.5" fill="none" stroke="{mix(shade, "#000000", 0.08)}" stroke-width="2.8" stroke-linecap="round"/>')
    if mth:
        o.append(mouth(mth, mouth_col))
    return "".join(o)


def neck(skin, shade):
    return f'<path d="{NECK}" fill="{shade}"/><path d="M110 168Q128 182 146 168L146 158Q128 172 110 158Z" fill="#000" fill-opacity=".08"/>'
