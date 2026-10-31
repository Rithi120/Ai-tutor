import math
import random

from lib import (Av, HEAD, TORSO, face, neck, mirror, mix, n, star, sparkle, gear, circ, blob, smooth_open,
                 smooth_closed, pts)


# 1 ------------------------------------------------------------------ explorer
def explorer():
    a = Av("explorer", "The Explorer")
    skin, shade = "#f0c29f", "#d39871"
    a.add(f'<rect width="256" height="256" fill="{a.lg("bg", [(0, "#56dccf"), (1, "#0e7d85")])}"/>')
    rings = []
    for cx, cy, s, ph in ((54, 62, 46, 0.4), (206, 206, 54, 1.7), (214, 44, 26, 2.6)):
        for k in (1, 0.72, 0.46, 0.22):
            rings.append(f'<path d="{blob(cx, cy, s * k, s * k * 0.78, 0.11, 3, ph + k)}"/>')
    a.add('<g fill="none" stroke="#fff" stroke-opacity=".17" stroke-width="2">' + "".join(rings) + "</g>")
    a.add('<path d="M18 168C34 132 30 104 52 84" fill="none" stroke="#ffe3a8" stroke-opacity=".5" stroke-width="3" stroke-dasharray="6 7" stroke-linecap="round"/>'
          '<path d="M50 72l10 10M60 72l-10 10" stroke="#ffb547" stroke-width="3.4" stroke-linecap="round"/>')
    # body
    a.add(neck(skin, shade))
    shirt = a.lg("shirt", [(0, "#e2c387"), (1, "#bf9a58")])
    a.add(f'<path d="{TORSO}" fill="{shirt}"/>')
    col = "M104 188L126 206L100 214Z"
    a.add(f'<path d="{col}" fill="#ecd39f"/><path d="{mirror(col)}" fill="#ecd39f"/>')
    strap = "M80 200C90 220 94 240 94 256"
    a.add(f'<g fill="none" stroke-linecap="round"><path d="{strap}" stroke="#7b4a2b" stroke-width="14"/><path d="{mirror(strap)}" stroke="#7b4a2b" stroke-width="14"/>'
          f'<path d="{strap}" stroke="#a7714a" stroke-width="2" stroke-dasharray="4 4"/><path d="{mirror(strap)}" stroke="#a7714a" stroke-width="2" stroke-dasharray="4 4"/></g>'
          '<rect x="84" y="226" width="16" height="9" rx="2" fill="#d8b04a"/><rect x="156" y="226" width="16" height="9" rx="2" fill="#d8b04a"/>')
    a.add('<path d="M102 190Q128 202 154 190L128 222Z" fill="#ff8a3d"/><path d="M128 222L118 204L138 204Z" fill="#e8692a"/>'
          '<circle cx="128" cy="196" r="7" fill="#e8692a"/>')
    a.add('<path d="M118 200Q128 212 138 200" fill="none" stroke="#6b4a2a" stroke-width="1.6"/>'
          '<circle cx="128" cy="222" r="11.5" fill="#ffb547" stroke="#c98314" stroke-width="2.4"/><circle cx="128" cy="222" r="7.6" fill="#fff7e4"/>'
          '<path d="M128 215.5L130.6 222L125.4 222Z" fill="#e5484d"/><path d="M128 228.5L130.6 222L125.4 222Z" fill="#7b8a99"/>')
    # head
    a.add(face(a, skin, shade, eyes="open", brows="soft", mth="grin", brow="#5b3820"))
    hair = "#6b4226"
    side = "M85 98L97 98Q95 112 91 126Q85 114 85 98Z"
    a.add(f'<path d="{side}" fill="{hair}"/><path d="{mirror(side)}" fill="{hair}"/>'
          f'<path d="M94 99Q100 109 106 101Q111 108 117 100Q122 106 128 99L128 96L94 96Z" fill="{hair}"/>')
    # hat
    a.add('<path d="M88 100C110 110 146 110 168 100L168 106C146 116 110 116 88 106Z" fill="#000" fill-opacity=".12"/>')
    crown = a.lg("crown", [(0, "#f4dba6"), (1, "#cfa867")])
    a.add(f'<path d="M48 94C58 74 198 74 208 94C192 108 64 108 48 94Z" fill="#e6c487"/>'
          f'<path d="M48 94C64 108 192 108 208 94C200 106 56 106 48 94Z" fill="#c09a5c"/>'
          f'<path d="M86 88C86 56 104 40 128 40C152 40 170 56 170 88Z" fill="{crown}"/>'
          '<path d="M110 47Q128 56 146 47" fill="none" stroke="#b58e52" stroke-width="2.4" stroke-linecap="round"/>'
          '<path d="M86 76C108 82 148 82 170 76L170 88C148 94 108 94 86 88Z" fill="#1f6f6b"/>'
          '<path d="M86 80C108 86 148 86 170 80" fill="none" stroke="#2fa59f" stroke-width="1.6"/>'
          '<path d="M60 90C80 80 120 78 150 79" fill="none" stroke="#fff" stroke-opacity=".35" stroke-width="2.4" stroke-linecap="round"/>')
    return a


# 2 ----------------------------------------------------------------- scientist
def scientist():
    a = Av("scientist", "The Scientist")
    skin, shade = "#7b4a2f", "#5c3420"
    a.add(f'<rect width="256" height="256" fill="{a.lg("bg", [(0, "#a08bff"), (1, "#5236c9")])}"/>')
    mol = []
    for (x1, y1), (x2, y2) in (((30, 52), (58, 38)), ((58, 38), (78, 60)), ((30, 52), (26, 82)), ((206, 196), (228, 176)), ((206, 196), (214, 226)), ((206, 196), (184, 214)), ((214, 92), (236, 110))):
        mol.append(f'<path d="M{x1} {y1}L{x2} {y2}" stroke="#fff" stroke-opacity=".2" stroke-width="3"/>')
    for x, y, r in ((30, 52, 8), (58, 38, 6), (78, 60, 5), (26, 82, 5), (206, 196, 9), (228, 176, 6), (214, 226, 5), (184, 214, 5), (214, 92, 6), (236, 110, 4)):
        mol.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="#fff" fill-opacity=".18"/>')
    a.add("".join(mol))
    hair, hair2 = "#1d1310", "#3a2721"
    bumps = []
    for i in range(14):
        t = math.radians(150 + i * 240 / 13)
        bumps.append(f'<circle cx="{n(128 + 62 * math.cos(t))}" cy="{n(100 + 52 * math.sin(t))}" r="17"/>')
    a.add(f'<g fill="{hair}"><ellipse cx="128" cy="102" rx="64" ry="54"/>' + "".join(bumps) + "</g>")
    a.add(f'<g fill="{hair2}">' + "".join(
        f'<circle cx="{x}" cy="{y}" r="{r}"/>' for x, y, r in ((82, 74, 8), (102, 54, 9), (128, 46, 8), (154, 54, 9), (174, 72, 8), (70, 102, 7), (186, 100, 7), (114, 64, 6), (142, 64, 6), (74, 128, 6), (182, 128, 6))) + "</g>")
    # body
    a.add(neck(skin, shade))
    coat = a.lg("coat", [(0, "#ffffff"), (1, "#d6daf0")])
    a.add(f'<path d="{TORSO}" fill="{coat}"/><path d="M104 192L128 232L152 192Z" fill="#7c5cff"/>'
          '<path d="M110 192Q128 204 146 192L128 214Z" fill="#9b82ff"/>')
    lap = "M98 191L109 191L128 234L118 248L90 206Z"
    a.add(f'<path d="{lap}" fill="#eceefa" stroke="#c4c9e2" stroke-width="1.6" stroke-linejoin="round"/><path d="{mirror(lap)}" fill="#eceefa" stroke="#c4c9e2" stroke-width="1.6" stroke-linejoin="round"/>')
    a.add('<rect x="78" y="226" width="26" height="22" rx="3" fill="#eef0fb" stroke="#c4c9e2" stroke-width="1.6"/>'
          '<rect x="83" y="214" width="4" height="16" rx="2" fill="#18b9bd"/><rect x="90" y="217" width="4" height="13" rx="2" fill="#ffb547"/>')
    a.add('<path d="M153 204H159V210L166 223Q167 227 163 227H149Q145 227 146 223L153 210Z" fill="#fff" stroke="#5b3fd6" stroke-width="1.8" stroke-linejoin="round"/>'
          '<path d="M149.6 217H162.4L165 223Q165.6 225.2 163 225.2H149Q146.4 225.2 147 223Z" fill="#18b9bd"/>')
    a.add(face(a, skin, shade, eyes="wide", brows="raised", mth="open", brow="#140c0a", iris="#1a0f0b", lashes=True, blush_op=0.22))
    a.add('<circle cx="85" cy="140" r="2.6" fill="#ffb547"/><circle cx="171" cy="140" r="2.6" fill="#ffb547"/>')
    # front hairline curls
    front = "M84 114C82 84 100 68 128 68C156 68 174 84 172 114C166 100 156 92 146 94C136 88 120 88 110 94C100 92 90 100 84 114Z"
    a.add(f'<path d="{front}" fill="{hair}"/>')
    a.add(f'<g fill="{hair}">' + "".join(f'<circle cx="{x}" cy="{y}" r="6"/>' for x, y in ((90, 104), (100, 96), (112, 92), (126, 91), (140, 92), (152, 95), (163, 102))) + "</g>")
    # goggles
    a.add('<path d="M74 88C96 78 160 78 182 88L182 96C160 86 96 86 74 96Z" fill="#18b9bd"/>')
    glass = a.lg("glass", [(0, "#c9fbfb"), (1, "#22a9b8")], 0, 0, 1, 1)
    for gx in (108, 148):
        a.add(f'<circle cx="{gx}" cy="84" r="15" fill="{glass}" stroke="#3a2a8c" stroke-width="4.5"/>'
              f'<path d="M{gx - 8} 80Q{gx - 6} {74} {gx} 73" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-opacity=".85"/>')
    a.add('<path d="M122 82Q128 78 134 82" fill="none" stroke="#3a2a8c" stroke-width="4"/>')
    # glowing bubble
    glow = a.rg("glow", [(0, "#e6ffff", 0.95), (0.45, "#5ee8ea", 0.55), (1, "#18b9bd", 0)])
    a.add(f'<circle cx="196" cy="62" r="26" fill="{glow}"/><circle cx="196" cy="62" r="11" fill="#bff8f8" fill-opacity=".45" stroke="#fff" stroke-width="2"/>'
          '<circle cx="192" cy="58" r="3" fill="#fff"/><circle cx="210" cy="40" r="5" fill="none" stroke="#fff" stroke-opacity=".8" stroke-width="1.8"/>'
          '<circle cx="180" cy="40" r="3" fill="#fff" fill-opacity=".7"/>')
    return a


# 3 ------------------------------------------------------------------ inventor
def inventor():
    a = Av("inventor", "The Inventor")
    skin, shade = "#dca575", "#b97d50"
    a.add(f'<rect width="256" height="256" fill="{a.lg("bg", [(0, "#ffae66"), (1, "#c9502a")])}"/>')
    a.add('<g fill="#fff" fill-opacity=".13" fill-rule="evenodd">' + "".join(
        f'<path d="{gear(x, y, r, t, rot=ro)}"/>' for x, y, r, t, ro in ((36, 58, 28, 10, 4), (220, 216, 38, 12, 0), (214, 50, 18, 8, 10), (40, 210, 20, 9, 0), (76, 26, 12, 7, 0))) + "</g>")
    hair, hair2 = "#8a3518", "#b5522a"
    # back hair (messy, behind ears)
    a.add(f'<path d="M80 132C74 112 76 96 84 86L172 86C180 96 182 112 176 132Z" fill="{hair}"/>')
    a.add(neck(skin, shade))
    shirt = a.lg("shirt", [(0, "#f6e8cf"), (1, "#dcc49e")])
    a.add(f'<path d="{TORSO}" fill="{shirt}"/>')
    a.add('<path d="M104 190L128 210L152 190" fill="none" stroke="#cdb28a" stroke-width="3" stroke-linejoin="round"/>')
    bib = a.lg("bib", [(0, "#c4632f"), (1, "#8f3d1b")])
    a.add(f'<path d="M92 256L97 214H159L164 256Z" fill="{bib}"/><rect x="114" y="222" width="28" height="16" rx="3" fill="#a64a22" stroke="#e8a45c" stroke-width="1.6" stroke-dasharray="3 3"/>'
          '<path d="M100 216L82 194" stroke="#8a3a19" stroke-width="10" stroke-linecap="round"/><path d="M156 216L174 194" stroke="#8a3a19" stroke-width="10" stroke-linecap="round"/>'
          '<circle cx="100" cy="216" r="4" fill="#ffcf6a"/><circle cx="156" cy="216" r="4" fill="#ffcf6a"/>')
    # wrench clipped on strap (viewer's left)
    a.add('<g transform="rotate(-38 84 206)"><rect x="80" y="196" width="8" height="40" rx="4" fill="#d2d9e2" stroke="#8b97a6" stroke-width="1.6"/>'
          '<path d="M73 190Q73 179 84 177L84 186H88L88 177Q99 179 99 190Q99 200 84 201Q73 200 73 190Z" fill="#d2d9e2" stroke="#8b97a6" stroke-width="1.6" stroke-linejoin="round"/>'
          '<rect x="80" y="205" width="8" height="2" fill="#8b97a6"/></g>'
          '<rect x="86" y="204" width="10" height="7" rx="2" fill="#4a4f58"/>')
    a.add(face(a, skin, shade, eyes=("big", "open"), brows=("raised", "soft"), mth="smirk", brow="#6a2a12", lashes=True))
    a.add('<path d="M150 146Q156 141 163 145Q162 151 156 151Q150 151 150 146Z" fill="#3a2a22" fill-opacity=".38"/>'
          '<path d="M160 141l5 -2" stroke="#3a2a22" stroke-opacity=".38" stroke-width="2.4" stroke-linecap="round"/>')
    spiky = ("M82 118C76 96 82 78 96 68L92 52L108 60L112 42L125 56L136 38L143 56L158 44L158 62L174 58L168 74"
             "C180 86 182 102 174 118C170 104 164 98 158 96L98 96C92 100 86 108 82 118Z")
    a.add(f'<path d="{spiky}" fill="{hair}"/><path d="M112 58L124 70L136 50M146 62L156 70" fill="none" stroke="{hair2}" stroke-width="3" stroke-linecap="round"/>')
    # headband with gears
    a.add('<path d="M82 98C100 84 156 84 174 98L174 108C156 94 100 94 82 108Z" fill="#5e351c"/>'
          '<path d="M84 102C102 90 154 90 172 102" fill="none" stroke="#a8754a" stroke-width="1.4" stroke-dasharray="3 3"/>')
    a.add(f'<g fill-rule="evenodd"><path d="{gear(146, 92, 10, 8, rot=8)}" fill="#e7b04f"/><path d="{gear(162, 98, 6.5, 7)}" fill="#c98a2c"/></g>')
    # flip-down magnifier on the viewer's left eye
    a.add('<circle cx="96" cy="99" r="4.5" fill="#e7b04f" stroke="#a46a1d" stroke-width="1.5"/>'
          '<path d="M96 99L104 113" stroke="#c98a2c" stroke-width="4" stroke-linecap="round"/>'
          '<circle cx="111" cy="126" r="15" fill="#c9f6ff" fill-opacity=".32" stroke="#c98a2c" stroke-width="5"/>'
          '<circle cx="111" cy="126" r="15" fill="none" stroke="#ffd98a" stroke-width="1.4"/>'
          '<path d="M101 119Q104 113 110 112" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-opacity=".8"/>')
    return a


# 4 ----------------------------------------------------------------- astronaut
def astronaut():
    a = Av("astronaut", "The Astronaut")
    skin, shade = "#b97a4f", "#95593a"
    a.add(f'<rect width="256" height="256" fill="{a.rg("bg", [(0, "#3245a0"), (1, "#121a4c")], cx=0.5, cy=0.4, r=0.75)}"/>')
    rnd = random.Random(7)
    st = []
    for _ in range(40):
        x, y = rnd.uniform(4, 252), rnd.uniform(4, 252)
        if 60 < x < 196 and 40 < y < 256:
            continue
        st.append(f'<circle cx="{n(x)}" cy="{n(y)}" r="{n(rnd.choice((0.9, 1.2, 1.6, 2.1)))}" fill-opacity="{rnd.choice(("0.5", "0.7", "0.9"))}"/>')
    a.add('<g fill="#fff">' + "".join(st) + "</g>")
    a.add(f'<path d="{sparkle(40, 150, 7)}" fill="#ffe7a8"/><path d="{sparkle(222, 120, 5)}" fill="#fff"/>')
    a.add('<circle cx="212" cy="54" r="12" fill="#ff7a6b"/><path d="M212 42A12 12 0 0 1 212 66A8 12 0 0 0 212 42Z" fill="#d9503f"/>'
          '<ellipse cx="212" cy="54" rx="22" ry="6" fill="none" stroke="#ffc1b5" stroke-width="2" transform="rotate(-18 212 54)"/>')
    suit = a.lg("suit", [(0, "#ffffff"), (1, "#c5cede")])
    a.add(f'<path d="{TORSO}" fill="{suit}"/><path d="M62 214C70 206 80 202 92 200L96 256H74Z" fill="#ff6f61"/><path d="M194 214C186 206 176 202 164 200L160 256H182Z" fill="#ff6f61"/>')
    a.add('<rect x="114" y="208" width="28" height="22" rx="5" fill="#dbe2ee" stroke="#aab5c8" stroke-width="1.6"/>'
          '<circle cx="121" cy="219" r="3" fill="#18b9bd"/><circle cx="131" cy="219" r="3" fill="#ffb547"/><rect x="117" y="225" width="22" height="2.4" rx="1" fill="#aab5c8"/>')
    a.add('<circle cx="170" cy="226" r="13" fill="#5b3fd6" stroke="#ffb547" stroke-width="3"/>'
          f'<path d="{star(170, 226.5, 8, 3.3)}" fill="#ffd45c"/>')
    a.add('<ellipse cx="128" cy="193" rx="60" ry="15" fill="#ff6f61"/><ellipse cx="128" cy="190" rx="60" ry="12" fill="#ff8f80"/>')
    # visor interior
    inside = a.rg("inside", [(0, "#2a3878"), (1, "#0b1233")], cx=0.5, cy=0.45, r=0.6)
    a.add(f'<ellipse cx="128" cy="124" rx="58" ry="57" fill="{inside}"/>')
    hair = "#1a1210"
    inner = face(a, skin, shade, eyes="wide", brows="raised", mth="o", brow="#1a1210")
    cap = "M84 112C80 80 102 64 128 64C154 64 176 80 172 112C164 98 150 92 128 92C106 92 92 98 84 112Z"
    curls = "".join(f'<circle cx="{x}" cy="{y}" r="6.5"/>' for x, y in ((92, 96), (104, 88), (118, 84), (132, 84), (146, 86), (158, 90), (166, 98)))
    a.add(f'<g transform="translate(128 130) scale(.86) translate(-128 -122)">{inner}<path d="{cap}" fill="{hair}"/><g fill="{hair}">{curls}</g></g>')
    a.add('<ellipse cx="128" cy="124" rx="58" ry="57" fill="#9fd4ff" fill-opacity=".1"/>'
          '<path d="M84 112C86 92 100 76 120 70" fill="none" stroke="#fff" stroke-opacity=".55" stroke-width="6" stroke-linecap="round"/>'
          '<path d="M80 128L81 122" stroke="#fff" stroke-opacity=".55" stroke-width="5" stroke-linecap="round"/>'
          '<path d="M164 168C172 160 178 150 180 138" fill="none" stroke="#fff" stroke-opacity=".25" stroke-width="4" stroke-linecap="round"/>')
    shell = a.lg("shell", [(0, "#ffffff"), (0.6, "#e3e9f3"), (1, "#a9b5c9")], 0, 0, 0.3, 1)
    a.add(f'<path fill-rule="evenodd" d="{circ(128, 120, 78)}{circ(128, 124, 58)}" fill="{shell}"/>')
    a.add('<path fill="none" stroke="#9aa6bb" stroke-width="2" d="' + circ(128, 124, 58) + '"/>'
          '<path d="M66 80C80 56 104 46 128 45" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round"/>'
          '<circle cx="58" cy="138" r="6" fill="#ff6f61"/><circle cx="58" cy="138" r="2.4" fill="#fff"/>'
          '<circle cx="198" cy="138" r="6" fill="#18b9bd"/><circle cx="198" cy="138" r="2.4" fill="#fff"/>')
    return a


# 5 --------------------------------------------------------------------- coder
def coder():
    a = Av("coder", "The Coder")
    skin, shade = "#f9d7c0", "#e2ad8f"
    a.add(f'<rect width="256" height="256" fill="{a.lg("bg", [(0, "#3a4b66"), (1, "#1c2738")])}"/>')
    g = []
    rnd = random.Random(3)
    for col_x in (18, 34, 222, 238):
        y = rnd.uniform(6, 20)
        while y < 250:
            if rnd.random() < 0.5:
                g.append(f'<rect x="{col_x - 1.5}" y="{n(y)}" width="3" height="10" rx="1.5"/>')
            else:
                g.append(f'<ellipse cx="{col_x}" cy="{n(y + 5)}" rx="3.6" ry="5" fill="none" stroke="#3dff9a" stroke-width="2.2"/>')
            y += rnd.uniform(16, 26)
    a.add('<g fill="#3dff9a" opacity=".22">' + "".join(g) + "</g>")
    a.add('<g fill="none" stroke="#3dff9a" stroke-opacity=".3" stroke-width="4" stroke-linecap="round" stroke-linejoin="round">'
          '<path d="M70 40Q58 40 58 52V60Q58 66 50 68Q58 70 58 76V84Q58 96 70 96"/>'
          '<path d="M186 40Q198 40 198 52V60Q198 66 206 68Q198 70 198 76V84Q198 96 186 96"/></g>')
    hood = a.lg("hood", [(0, "#4a5874"), (1, "#2b3447")])
    a.add(f'<path d="M58 210C50 140 70 62 128 54C186 62 206 140 198 210Z" fill="{hood}"/>'
          '<path d="M74 200C68 140 86 74 128 68C170 74 188 140 182 200Z" fill="#1f2636"/>')
    hair = "#1b1e2b"
    a.add(f'<path d="M78 166C70 116 82 74 128 70C174 74 186 116 178 166C170 172 162 166 160 156L96 156C94 166 86 172 78 166Z" fill="{hair}"/>')
    a.add('<path d="M98 196C98 178 158 178 158 196" fill="none" stroke="#151a24" stroke-width="7"/>')
    a.add(neck(skin, shade))
    hoodie = a.lg("hoodie", [(0, "#465470"), (1, "#2d3649")])
    a.add(f'<path d="{TORSO}" fill="{hoodie}"/><path d="M94 192Q128 218 162 192" fill="none" stroke="#232a39" stroke-width="5"/>'
          '<path d="M116 202L112 238M140 202L144 236" stroke="#d6dbe6" stroke-width="3" stroke-linecap="round"/>'
          '<rect x="109" y="236" width="6" height="9" rx="2" fill="#3dff9a"/><rect x="141" y="234" width="6" height="9" rx="2" fill="#3dff9a"/>')
    for cx, rot in ((99, -24), (157, 24)):
        a.add(f'<g transform="rotate({rot} {cx} 200)"><ellipse cx="{cx}" cy="200" rx="12" ry="14.5" fill="#151a24"/>'
              f'<ellipse cx="{cx}" cy="200" rx="7.5" ry="9.5" fill="#262e3e" stroke="#3dff9a" stroke-width="2.4"/></g>')
    hx = [(164 + 12 * math.cos(math.radians(30 + 60 * k)), 228 + 12 * math.sin(math.radians(30 + 60 * k))) for k in range(6)]
    a.add(f'<path d="{pts(hx)}" fill="#7c5cff" stroke="#b4a2ff" stroke-width="1.6"/>'
          '<path d="M159 224L155 228L159 232M169 224L173 228L169 232M166 222L162 234" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>')
    a.add(face(a, skin, shade, eyes="open", brows="flat", mth="half", brow="#1b1e2b", lashes=True))
    fringe = "M84 120C80 84 100 64 128 64C156 64 178 84 172 120C170 108 166 100 162 96C152 102 134 103 120 98C110 104 98 106 90 104C87 109 85 114 84 120Z"
    a.add(f'<path d="{fringe}" fill="{hair}"/><path d="M100 80C110 72 130 70 146 74" fill="none" stroke="#4d5f86" stroke-width="3" stroke-linecap="round"/>')
    # glasses with code reflections
    a.add('<g fill="#3dff9a" fill-opacity=".07" stroke="#0e1219" stroke-width="3.2"><rect x="96" y="114" width="30" height="23" rx="8"/><rect x="130" y="114" width="30" height="23" rx="8"/></g>'
          '<path d="M126 122Q128 119 130 122M96 120L86 118M160 120L170 118" fill="none" stroke="#0e1219" stroke-width="3"/>'
          '<g stroke="#3dff9a" stroke-opacity=".7" stroke-width="1.6" stroke-linecap="round"><path d="M101 119H110M101 123H106M134 119H146M134 123H139M150 132H155"/></g>')
    return a


CHARS1 = [explorer, scientist, inventor, astronaut, coder]
