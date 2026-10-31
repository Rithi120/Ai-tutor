import math
import random

from lib import (Av, HEAD, TORSO, face, neck, mirror, mix, n, star, sparkle, gear, circ, blob, smooth_open,
                 smooth_closed, pts)

KNIGHT_PIECE = ("M-10 13H11V9.5H8C8 4 10 0 9 -4C8 -9 4 -12.5 -1 -12.5L-2.5 -16L-5 -11.5C-8 -9.5 -10.5 -5.5 -10.5 -1.5"
                "L-10.5 2L-6 3.5L-2.5 0.5C-1.5 3 -4 6 -6.5 8V9.5H-10Z")


# 11 --------------------------------------------------------------- strategist
def strategist():
    a = Av("strategist", "The Strategist")
    skin, shade = "#f3cdab", "#d6a07a"
    a.add(f'<rect width="256" height="256" fill="{a.lg("bg", [(0, "#4d6380"), (1, "#212e3c")])}"/>')
    q = []
    for j in range(6):
        for u in range(-6, 6):
            if (u + j) % 2:
                continue
            z0, z1 = 1 + j * 0.55, 1 + (j + 1) * 0.55
            y0, y1 = 96 + 160 / z0, 96 + 160 / z1
            q.append(pts([(128 + u * 58 / z0, y0), (128 + (u + 1) * 58 / z0, y0), (128 + (u + 1) * 58 / z1, y1), (128 + u * 58 / z1, y1)]))
    a.add(f'<path d="{"".join(q)}" fill="#22c07f" fill-opacity=".24"/>')
    a.add(f'<rect y="120" width="256" height="40" fill="{a.lg("fade", [(0, "#33445a", 1), (1, "#33445a", 0)])}"/>')
    a.add(f'<path d="{KNIGHT_PIECE}" transform="translate(206 70) scale(-2.4 2.4)" fill="#22c07f" fill-opacity=".16"/>')
    a.add(neck(skin, shade))
    blazer = a.lg("blazer", [(0, "#3a4b5e"), (1, "#1e2934")])
    a.add(f'<path d="{TORSO}" fill="{blazer}"/><path d="M104 192L128 236L152 192Z" fill="#f4f6f8"/>'
          '<path d="M121 195H135L133 204H123Z" fill="#0f8a58"/><path d="M123 204H133L137 234L128 243L119 234Z" fill="#14a06a"/>'
          '<path d="M125 210L131 216M124 220L132 228" stroke="#0f8a58" stroke-width="2"/>')
    lap = "M98 191L112 193L126 240L111 228L95 213L103 207Z"
    a.add(f'<path d="{lap}" fill="#2b3a4a" stroke="#17202a" stroke-width="1.4" stroke-linejoin="round"/><path d="{mirror(lap)}" fill="#2b3a4a" stroke="#17202a" stroke-width="1.4" stroke-linejoin="round"/>'
          '<path d="M74 218L90 214L88 222Z" fill="#22c07f"/>')
    a.add('<circle cx="160" cy="214" r="4" fill="#ffcf4d" stroke="#b3861d" stroke-width="1.2"/>')

    a.add(face(a, skin, shade, eyes="narrow", brows=("flat", "raised"), mth="smirk", brow="#16181f"))
    hair = "#16181f"
    a.add(f'<path d="M84 116C80 80 100 62 130 62C160 62 178 80 172 114C168 98 160 90 150 86C130 82 110 84 96 92C90 98 86 106 84 116Z" fill="{hair}"/>'
          f'<path d="M94 94C104 72 140 66 164 78C146 76 124 80 106 92Z" fill="#2c3242"/>'
          '<path d="M104 78C118 70 138 68 152 72" fill="none" stroke="#5d6a86" stroke-width="2.6" stroke-linecap="round"/>'
          f'<path d="M86 100L84 124L90 124Z" fill="{hair}"/><path d="M170 100L172 124L166 124Z" fill="{hair}"/>')
    em = a.lg("emerald", [(0, "#7dffc8"), (0.5, "#22c07f"), (1, "#0b7a4c")], 0, 0, 1, 1)
    a.add(f'<path d="M36 256C40 234 52 212 66 202L92 212C84 226 80 242 78 256Z" fill="{blazer}" stroke="#17202a" stroke-width="1.4"/>'
          '<path d="M66 210.5L87 213" stroke="#f4f6f8" stroke-width="5" stroke-linecap="round"/>'
          f'<circle cx="74" cy="168" r="30" fill="{a.rg("pglow", [(0, "#5dffb4", 0.45), (1, "#5dffb4", 0)])}"/>'
          f'<g transform="translate(74 172) scale(1.55)"><path d="{KNIGHT_PIECE}" fill="{em}" stroke="#0a5e3b" stroke-width="1" stroke-linejoin="round"/>'
          '<circle cx="1.5" cy="-6" r="1.2" fill="#0a5e3b"/><path d="M5 -7C7 -3 6 2 5 6" fill="none" stroke="#d9fff0" stroke-width="1.2" stroke-linecap="round"/></g>'
          f'<path d="M62 196C62 188 68 186 76 186C84 186 90 189 90 196C90 204 84 209 76 209C68 209 62 204 62 196Z" fill="{skin}"/>'
          f'<path d="M66 192C72 190.5 80 190.5 87 192M65 198C72 196.5 80 196.5 88 198M67 204C72 202.5 80 202.5 85 204" fill="none" stroke="{shade}" stroke-width="1.5" stroke-linecap="round"/>')
    return a


# 12 --------------------------------------------------------------- detective
def detective():
    a = Av("detective", "The Detective")
    skin, shade = "#8e5a3a", "#6c4128"
    a.add(f'<rect width="256" height="256" fill="{a.lg("bg", [(0, "#dba150"), (1, "#6b411e")])}"/>')
    fp = "".join(f'<path d="M{n(52 - r)} {n(70 + r * 0.15)}C{n(52 - r)} {n(70 - r * 1.3)} {n(52 + r)} {n(70 - r * 1.3)} {n(52 + r)} {n(70 + r * 0.15)}"/>' for r in range(5, 34, 5))
    a.add(f'<g fill="none" stroke="#fff" stroke-opacity=".2" stroke-width="2.2" stroke-linecap="round" stroke-dasharray="20 4">{fp}</g>')
    prints = []
    for k, (x, y) in enumerate(((150, 252), (176, 232), (196, 206), (222, 190), (236, 160))):
        dx = -7 if k % 2 else 7
        prints.append(f'<g transform="rotate(-40 {x} {y})"><ellipse cx="{x + dx}" cy="{y - 6}" rx="6" ry="9"/><ellipse cx="{x + dx}" cy="{y + 9}" rx="5" ry="4.4"/></g>')
    a.add('<g fill="#3a220e" fill-opacity=".2">' + "".join(prints) + "</g>")
    hair = "#21140d"
    a.add(f'<path d="M80 106C68 140 64 182 72 216C84 222 98 214 102 200L154 200C158 214 172 222 184 216C192 182 188 140 176 106Z" fill="{hair}"/>')
    a.add(neck(skin, shade))
    coat = a.lg("coat", [(0, "#cfa66a"), (1, "#97703c")])
    a.add(f'<path d="{TORSO}" fill="{coat}"/><path d="M108 194L128 238L148 194Z" fill="#43291a"/>'
          '<path d="M118 200L128 222L138 200" fill="none" stroke="#c9a066" stroke-width="2"/>')
    lap = "M98 194L110 194L126 240L112 232L94 208Z"
    a.add(f'<path d="{lap}" fill="#b48a52" stroke="#86622f" stroke-width="1.4"/><path d="{mirror(lap)}" fill="#b48a52" stroke="#86622f" stroke-width="1.4"/>')
    cu = "M101 206L80 160C94 164 110 180 120 196Z"
    a.add(f'<path d="{cu}" fill="#c29856" stroke="#86622f" stroke-width="1.6" stroke-linejoin="round"/><path d="{mirror(cu)}" fill="#c29856" stroke="#86622f" stroke-width="1.6" stroke-linejoin="round"/>'
          '<circle cx="100" cy="236" r="3" fill="#5a3a1e"/><circle cx="156" cy="236" r="3" fill="#5a3a1e"/>')
    a.add(face(a, skin, shade, eyes=("open", "big"), brows=("soft", "raised"), mth="half", brow="#160d08", lashes=True, blush_op=0.22))
    a.add(f'<path d="M84 116C82 96 92 86 100 84L156 84C164 86 174 96 172 116C166 104 156 98 146 96C130 100 112 98 100 96C94 100 88 106 84 116Z" fill="{hair}"/>')
    # deerstalker
    a.defs.append('<pattern id="detective-check" width="10" height="10" patternUnits="userSpaceOnUse"><path d="M0 5H10M5 0V10" stroke="#4a2c14" stroke-opacity=".45" stroke-width="1.8"/><path d="M0 1H10M1 0V10" stroke="#e6c08a" stroke-opacity=".3" stroke-width="1"/></pattern>')
    crown = "M84 100C80 70 100 50 128 50C156 50 176 70 172 100Z"
    flap = "M86 94C77 86 77 70 85 62C93 68 98 80 96 94Z"
    a.add(f'<path d="{flap}" fill="#7a4e2a"/><path d="{mirror(flap)}" fill="#7a4e2a"/>'
          f'<path d="{crown}" fill="#8f5f34"/><path d="{crown}" fill="url(#detective-check)"/>'
          '<path d="M100 60C110 54 120 52 130 52" fill="none" stroke="#e6c08a" stroke-opacity=".5" stroke-width="2.6" stroke-linecap="round"/>'
          '<path d="M92 98C108 88 148 88 164 98C160 110 96 110 92 98Z" fill="#6e4526"/><path d="M96 98C112 92 144 92 160 98" fill="none" stroke="#a47040" stroke-width="1.6"/>'
          '<path d="M128 52C120 42 110 44 113 51C116 57 124 56 128 52Z" fill="#5e3a1e"/><path d="M128 52C136 42 146 44 143 51C140 57 132 56 128 52Z" fill="#5e3a1e"/><circle cx="128" cy="52" r="3.2" fill="#4a2c14"/>')
    # magnifying glass over the viewer's right eye
    a.add('<path d="M163 144L206 192" stroke="#3e2a1a" stroke-width="10" stroke-linecap="round"/><path d="M164 145L204 190" stroke="#6b4a2e" stroke-width="3" stroke-linecap="round"/>'
          '<path d="M160 140L167 148" stroke="#d9a441" stroke-width="11"/>'
          '<circle cx="145" cy="126" r="21" fill="#eaf8ff" fill-opacity=".18" stroke="#3e2a1a" stroke-width="6"/>'
          '<circle cx="145" cy="126" r="18" fill="none" stroke="#d9a441" stroke-width="1.8"/>'
          '<path d="M131 118Q134 110 142 108" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-opacity=".8"/>')
    return a


# 13 ----------------------------------------------------------------- engineer
def engineer():
    a = Av("engineer", "The Engineer")
    skin, shade = "#5c3521", "#432414"
    a.add(f'<rect width="256" height="256" fill="{a.lg("bg", [(0, "#3480df"), (1, "#15479a")])}"/>')
    minor = "".join(f"M{v} 0V256M0 {v}H256" for v in range(16, 256, 16) if v % 64)
    major = "".join(f"M{v} 0V256M0 {v}H256" for v in range(64, 256, 64))
    a.add(f'<path d="{minor}" stroke="#fff" stroke-opacity=".08" stroke-width="1"/><path d="{major}" stroke="#fff" stroke-opacity=".16" stroke-width="1.6"/>')
    a.add('<g fill="none" stroke="#fff" stroke-opacity=".3" stroke-width="1.8"><circle cx="46" cy="60" r="20"/><circle cx="46" cy="60" r="8"/><path d="M18 60H74M46 32V88"/>'
          '<path d="M196 214H244M196 208V220M244 208V220"/><path d="M200 214l6 -4v8zM240 214l-6 -4v8z" fill="#fff" fill-opacity=".3"/>'
          '<path d="M206 40H236V70H206Z" stroke-dasharray="4 3"/></g>')
    a.add(neck(skin, shade))
    shirt = a.lg("shirt", [(0, "#5a7fa8"), (1, "#30506f")])
    a.add(f'<path d="{TORSO}" fill="{shirt}"/>')
    col = "M108 189L126 206L103 211Z"
    a.add(f'<path d="{col}" fill="#6d93bb"/><path d="{mirror(col)}" fill="#6d93bb"/>')
    vest = a.lg("vest", [(0, "#f0fa4a"), (1, "#c9d61a")])
    vp = "M34 256C36 220 60 200 98 192L110 194L118 256Z"
    a.add(f'<path d="{vp}" fill="{vest}"/><path d="{mirror(vp)}" fill="{vest}"/>')
    a.add('<g stroke="#e3e8ee" stroke-width="7"><path d="M40 236H116M140 236H216"/><path d="M84 198L92 236M172 198L164 236"/></g>'
          '<g stroke="#fff" stroke-width="1.6"><path d="M40 236H116M140 236H216"/><path d="M84 198L92 236M172 198L164 236"/></g>')
    a.add('<g transform="rotate(-26 186 200)"><rect x="150" y="190" width="66" height="19" rx="9.5" fill="#c4e0ff" stroke="#5f8fcf" stroke-width="1.6"/>'
          '<path d="M166 194V205M178 194V205M198 194V205" stroke="#5f8fcf" stroke-opacity=".5" stroke-width="1.4"/><rect x="186" y="190" width="6" height="19" fill="#ff6f61"/>'
          '<ellipse cx="152" cy="199.5" rx="5" ry="9.5" fill="#a6cdf8" stroke="#5f8fcf" stroke-width="1.6"/><path d="M152 196.5A3 3 0 1 1 151 202" fill="none" stroke="#5f8fcf" stroke-width="1.4"/></g>')
    a.add(face(a, skin, shade, eyes="open", brows="determined", mth="grin", brow="#120a06", iris="#140b07", blush_op=0.16))
    beard = "M88 136C90 160 106 178 128 178C150 178 166 160 168 136C162 158 148 170 128 170C108 170 94 158 88 136Z"
    a.add(f'<path d="{beard}" fill="#140c08"/><path d="M86 100L96 100L92 130Q86 116 86 100Z" fill="#140c08"/><path d="M170 100L160 100L164 130Q170 116 170 100Z" fill="#140c08"/>')
    # headset
    a.add('<path d="M88 140C92 156 102 163 112 163" fill="none" stroke="#28313d" stroke-width="3.6" stroke-linecap="round"/>'
          '<ellipse cx="116" cy="163" rx="5.4" ry="4" fill="#28313d"/><circle cx="117" cy="163" r="1.8" fill="#18b9bd"/>'
          '<ellipse cx="85" cy="128" rx="10" ry="13" fill="#28313d"/><ellipse cx="85" cy="128" rx="5.5" ry="8" fill="none" stroke="#18b9bd" stroke-width="2.4"/>')
    hat = a.lg("hat", [(0, "#ffe066"), (1, "#e9a80c")], 0, 0, 0.6, 1)
    a.add(f'<path d="M80 102C80 62 102 44 128 44C154 44 176 62 176 102Z" fill="{hat}"/>'
          '<path d="M120 45C119 70 119 88 120 102H136C137 88 137 70 136 45C131 44 125 44 120 45Z" fill="#fff07a" fill-opacity=".55"/>'
          '<path d="M66 104C72 93 184 93 190 104C188 113 68 113 66 104Z" fill="#f2b511"/><path d="M68 106C80 112 176 112 188 106" fill="none" stroke="#c98d06" stroke-width="2"/>'
          '<path d="M92 74C96 62 104 54 114 50" fill="none" stroke="#fff" stroke-opacity=".6" stroke-width="3.4" stroke-linecap="round"/>')
    a.add(f'<circle cx="128" cy="69" r="30" fill="{a.rg("lamp", [(0, "#fffbe0", 0.95), (0.4, "#fff3a0", 0.45), (1, "#fff3a0", 0)])}"/>'
          '<rect x="112" y="58" width="32" height="22" rx="7" fill="#4e5866" stroke="#2f3742" stroke-width="1.6"/>'
          '<circle cx="128" cy="69" r="8" fill="#fffbd6" stroke="#ffd84a" stroke-width="2"/><circle cx="125.5" cy="66.5" r="2.4" fill="#fff"/>')
    return a


def laurel(p0, p1, p2, side=1):
    o = []
    stem = f"M{p0[0]} {p0[1]}Q{p1[0]} {p1[1]} {p2[0]} {p2[1]}"
    o.append(f'<path d="{stem}" fill="none" stroke="#c98a12" stroke-width="2.2"/>')
    for k in range(7):
        t = 0.08 + k * 0.14
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]
        dx = 2 * (1 - t) * (p1[0] - p0[0]) + 2 * t * (p2[0] - p1[0])
        dy = 2 * (1 - t) * (p1[1] - p0[1]) + 2 * t * (p2[1] - p1[1])
        ang = math.degrees(math.atan2(dy, dx))
        for s in (-1, 1):
            la = ang + s * 38
            cx = x + 6 * math.cos(math.radians(la))
            cy = y + 6 * math.sin(math.radians(la))
            o.append(f'<ellipse cx="{n(cx)}" cy="{n(cy)}" rx="6.6" ry="3" transform="rotate({n(la)} {n(cx)} {n(cy)})"/>')
    return o


# 14 ----------------------------------------------------------------- champion
def champion():
    a = Av("champion", "The Champion")
    skin, shade = "#a96c43", "#88512f"
    a.add(f'<rect width="256" height="256" fill="{a.rg("bg", [(0, "#ffe08a"), (0.6, "#f6a93b"), (1, "#d9661b")], cx=0.5, cy=0.62, r=0.75)}"/>')
    w = []
    for k in range(10):
        a0 = math.radians(180 + k * 18)
        a1 = math.radians(180 + k * 18 + 9)
        w.append(pts([(128, 210), (128 + 300 * math.cos(a0), 210 + 300 * math.sin(a0)), (128 + 300 * math.cos(a1), 210 + 300 * math.sin(a1))]))
    a.add(f'<path d="{"".join(w)}" fill="#fff" fill-opacity=".18"/>')
    rnd = random.Random(14)
    conf = []
    for x, y in ((30, 40), (60, 22), (214, 30), (238, 70), (24, 110), (232, 128), (44, 76), (196, 58), (20, 168), (238, 186), (70, 48), (186, 22)):
        c = rnd.choice(("#7c5cff", "#18b9bd", "#e5384f", "#fff", "#5b3fd6"))
        conf.append(f'<rect x="{x}" y="{y}" width="{rnd.choice((5, 6, 8))}" height="{rnd.choice((3, 4))}" rx="1" fill="{c}" transform="rotate({rnd.randint(-60, 60)} {x} {y})"/>')
    a.add("".join(conf) + '<g fill="#fff"><circle cx="50" cy="132" r="2.4"/><circle cx="206" cy="96" r="2.4"/><circle cx="90" cy="30" r="2"/></g>')
    hair = "#24150e"
    a.add(f'<path d="M140 66C170 46 210 60 208 102C206 126 194 142 200 158C182 146 176 120 184 102C188 86 172 72 150 76Z" fill="{hair}"/>'
          '<path d="M184 72C196 82 198 100 192 118" fill="none" stroke="#4a2e20" stroke-width="2.6" stroke-linecap="round"/>')
    a.add(neck(skin, shade))
    jk = a.lg("jacket", [(0, "#ea4157"), (1, "#a51c30")])
    a.add(f'<path d="{TORSO}" fill="{jk}"/>')
    s1, s2 = "M48 228C62 210 80 200 100 196", "M56 240C70 220 86 210 104 205"
    a.add(f'<g fill="none" stroke="#fff" stroke-width="4.4" stroke-linecap="round"><path d="{s1}"/><path d="{s2}"/><path d="{mirror(s1)}"/><path d="{mirror(s2)}"/></g>'
          '<path d="M128 206V256" stroke="#7a1222" stroke-width="2.6"/>'
          '<path d="M100 186L104 200Q128 212 152 200L156 186Q128 198 100 186Z" fill="#fff"/>')
    a.add('<path d="M106 196L124 216L132 216L114 196Z" fill="#7c5cff"/><path d="M150 196L132 216L124 216L142 196Z" fill="#18b9bd"/>'
          f'<circle cx="128" cy="222" r="13.5" fill="{a.lg("gold", [(0, "#fff0a0"), (1, "#e4a414")], 0, 0, 1, 1)}" stroke="#b8800e" stroke-width="2"/>'
          '<circle cx="128" cy="222" r="9.5" fill="none" stroke="#fff6c4" stroke-width="1.4"/>'
          f'<path d="{star(128, 222.6, 7, 2.9)}" fill="#d9960f"/>')
    a.add(face(a, skin, shade, eyes="open", brows="raised", mth="grin", brow="#1a0f09", lashes=True, blush_op=0.26))
    a.add(f'<path d="M84 116C80 82 102 62 128 62C154 62 176 82 172 116C170 100 164 92 156 88C140 84 116 84 100 88C92 94 86 104 84 116Z" fill="{hair}"/>'
          '<path d="M100 74C112 66 132 64 148 68" fill="none" stroke="#4a2e20" stroke-width="2.6" stroke-linecap="round"/>'
          '<circle cx="156" cy="70" r="5.5" fill="#ffcf3f" stroke="#c98a12" stroke-width="1.4"/>')
    lv = laurel((90, 110), (88, 72), (124, 64)) + laurel((166, 110), (168, 72), (132, 64))
    a.add('<g fill="#ffd23f" stroke="#c98a12" stroke-width="1">' + "".join(lv) + "</g>")
    a.add(f'<g fill="#fff"><path d="{sparkle(206, 182, 7)}"/><path d="{sparkle(50, 186, 5)}"/></g>')
    return a


# 15 -------------------------------------------------------------------- robot
def robot():
    a = Av("robot", "The Robot")
    a.add(f'<rect width="256" height="256" fill="{a.lg("bg", [(0, "#165a7a"), (1, "#0a2233")])}"/>')
    hexes = []
    for row in range(7):
        for col in range(8):
            cx = col * 36 + (18 if row % 2 else 0)
            cy = row * 31 + 10
            hexes.append(pts([(cx + 18 * math.cos(math.radians(30 + 60 * k)), cy + 18 * math.sin(math.radians(30 + 60 * k))) for k in range(6)]))
    a.add(f'<path d="{"".join(hexes)}" fill="none" stroke="#5ff3ff" stroke-opacity=".09" stroke-width="1.4"/>')
    a.add(f'<circle cx="128" cy="124" r="120" fill="{a.rg("glow", [(0, "#5ff3ff", 0.5), (0.55, "#18b9bd", 0.18), (1, "#18b9bd", 0)])}"/>')
    metal = a.lg("metal", [(0, "#f6f9fc"), (0.6, "#ccd5e1"), (1, "#8f9bb0")], 0, 0, 0.3, 1)
    # torso
    a.add(f'<path d="{TORSO}" fill="{metal}"/><path d="M92 200L96 256M164 200L160 256" stroke="#8994a8" stroke-width="2"/>'
          '<path d="M44 226C58 208 76 200 96 196M212 226C198 208 180 200 160 196" fill="none" stroke="#18b9bd" stroke-width="6" stroke-linecap="round"/>'
          '<g fill="#c7cfdb" stroke="#7f8aa0" stroke-width="1.2"><circle cx="104" cy="208" r="2.6"/><circle cx="152" cy="208" r="2.6"/><circle cx="104" cy="244" r="2.6"/><circle cx="152" cy="244" r="2.6"/></g>')
    a.add(f'<circle cx="128" cy="224" r="26" fill="{a.rg("core", [(0, "#ffffff", 1), (0.25, "#9ff9ff", 0.95), (0.5, "#18d4e0", 0.5), (1, "#18b9bd", 0)])}"/>'
          '<circle cx="128" cy="224" r="13" fill="none" stroke="#5a6a80" stroke-width="3.4"/><circle cx="128" cy="224" r="7" fill="#e8feff"/>')
    a.add('<rect x="112" y="162" width="32" height="34" fill="#7f8aa0"/><path d="M112 172H144M112 180H144M112 188H144" stroke="#5f6a80" stroke-width="2"/>')
    # antenna
    a.add(f'<path d="M128 62V36" stroke="#8e9bb0" stroke-width="4.4" stroke-linecap="round"/>'
          f'<circle cx="128" cy="30" r="18" fill="{a.rg("bulbglow", [(0, "#ffd27a", 0.8), (1, "#ffb547", 0)])}"/>'
          f'<circle cx="128" cy="30" r="7.5" fill="{a.rg("bulb", [(0, "#fff6d6"), (1, "#ffb547")], cx=0.4, cy=0.35)}"/>')
    for x in (58, 184):
        a.add(f'<rect x="{x}" y="96" width="14" height="38" rx="6" fill="#9aa6b8" stroke="#7f8aa0" stroke-width="1.6"/><circle cx="{x + 7}" cy="115" r="4" fill="#5ff3ff"/>')
    a.add(f'<rect x="70" y="60" width="116" height="108" rx="38" fill="{metal}" stroke="#7f8aa0" stroke-width="2"/>'
          '<path d="M96 70Q128 63 160 70" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round"/>'
          '<g fill="#c7cfdb" stroke="#7f8aa0" stroke-width="1.2"><circle cx="82" cy="84" r="2.6"/><circle cx="174" cy="84" r="2.6"/><circle cx="82" cy="148" r="2.6"/><circle cx="174" cy="148" r="2.6"/></g>')
    a.defs.append('<pattern id="robot-scan" width="4" height="4" patternUnits="userSpaceOnUse"><path d="M0 0.5H4" stroke="#fff" stroke-opacity=".07"/></pattern>')
    a.add(f'<rect x="84" y="82" width="88" height="72" rx="24" fill="{a.rg("screen", [(0, "#174a66"), (1, "#081a28")])}" stroke="#56657c" stroke-width="2.4"/>'
          '<rect x="84" y="82" width="88" height="72" rx="24" fill="url(#robot-scan)"/>')
    for x in (101, 141):
        a.add(f'<rect x="{x}" y="104" width="14" height="22" rx="7" fill="#62f3ff"/><circle cx="{x + 9.5}" cy="109.5" r="2.8" fill="#fff"/>')
    a.add('<path d="M110 136Q128 150 146 136" fill="none" stroke="#62f3ff" stroke-width="4.4" stroke-linecap="round"/>'
          '<g fill="#ff7ab8" fill-opacity=".75"><rect x="92" y="132" width="10" height="4" rx="2"/><rect x="154" y="132" width="10" height="4" rx="2"/></g>'
          '<path d="M94 96Q98 88 108 87" fill="none" stroke="#fff" stroke-opacity=".3" stroke-width="3" stroke-linecap="round"/>')
    a.add(f'<g fill="#bffcff"><path d="{sparkle(40, 66, 6)}"/><path d="{sparkle(220, 188, 5)}"/><path d="{sparkle(214, 50, 4)}"/></g>')
    return a


CHARS3 = [strategist, detective, engineer, champion, robot]
