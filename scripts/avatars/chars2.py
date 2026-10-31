import math
import random

from lib import (Av, HEAD, TORSO, face, neck, mirror, mix, n, star, sparkle, gear, circ, blob, smooth_open,
                 smooth_closed, pts)


# 6 ------------------------------------------------------------------- scholar
def scholar():
    a = Av("scholar", "The Scholar")
    skin, shade = "#9a6440", "#784828"
    a.add(f'<rect width="256" height="256" fill="{a.lg("bg", [(0, "#8d3549"), (1, "#3c0f1f")])}"/>')
    rnd = random.Random(11)
    books = []
    for sy in (84, 164, 244):
        x = 2
        while x < 254:
            w = rnd.choice((8, 10, 12, 14))
            h = rnd.uniform(42, 64)
            c = rnd.choice(("#e8c27a", "#2f8f88", "#7a5ac0", "#c2465c", "#f2dcae"))
            if rnd.random() < 0.12:
                books.append(f'<rect x="{n(x + 4)}" y="{n(sy - h + 6)}" width="{w}" height="{n(h)}" rx="1.5" fill="{c}" transform="rotate(12 {n(x + 4)} {sy})"/>')
                x += w + 10
                continue
            books.append(f'<rect x="{n(x)}" y="{n(sy - h)}" width="{w}" height="{n(h)}" rx="1.5" fill="{c}"/>')
            x += w + rnd.choice((1, 2, 2, 3))
        books.append(f'<rect x="0" y="{sy}" width="256" height="6" fill="#f2dcae"/>')
    a.add('<g opacity=".2">' + "".join(books) + "</g>")
    a.add(f'<circle cx="128" cy="116" r="112" fill="{a.rg("glow", [(0, "#ffd9a0", 0.42), (1, "#ffd9a0", 0)])}"/>')
    # quill behind the ear
    a.add('<path d="M174 142C176 110 188 82 210 60C208 88 198 116 178 144Z" fill="#f7efe0" stroke="#d6c39d" stroke-width="1.5" stroke-linejoin="round"/>'
          '<path d="M176 144L208 64" stroke="#b08a4e" stroke-width="1.8" stroke-linecap="round"/>'
          '<path d="M185 116l9 -4M190 100l9 -4M196 86l7 -3M183 108l-4 -6M189 92l-4 -6" stroke="#d6c39d" stroke-width="1.4" stroke-linecap="round"/>')
    a.add(neck(skin, shade))
    a.defs.append('<pattern id="scholar-tweed" width="8" height="8" patternUnits="userSpaceOnUse"><path d="M0 8L4 0L8 8" fill="none" stroke="#000" stroke-opacity=".13" stroke-width="1.3"/></pattern>')
    jacket = a.lg("jacket", [(0, "#9c7a52"), (1, "#6a4e34")])
    a.add(f'<path d="{TORSO}" fill="{jacket}"/><path d="{TORSO}" fill="url(#scholar-tweed)"/>'
          '<ellipse cx="72" cy="226" rx="10" ry="14" fill="#6e5236" stroke="#d2b07e" stroke-width="1.4" stroke-dasharray="3 2.4" transform="rotate(18 72 226)"/>'
          '<path d="M106 192L128 228L150 192Z" fill="#f3efe6"/>')
    lap = "M96 192L108 192L126 236L114 246L88 206Z"
    a.add(f'<path d="{lap}" fill="#6a4e33"/><path d="{mirror(lap)}" fill="#6a4e33"/>')
    a.add('<path d="M102 184C112 200 144 200 154 184L160 198C148 216 108 216 96 198Z" fill="#9a2a40"/>'
          '<path d="M99 191C112 207 144 207 157 191" fill="none" stroke="#e2b04a" stroke-width="2.6"/>'
          '<path d="M108 204L130 206L126 250L104 248Z" fill="#8a2238"/>'
          '<path d="M107 220L128.5 221.5M106.5 228L128 229.5" stroke="#e2b04a" stroke-width="3"/>'
          '<path d="M106 248v6M111 248.5v6M116 249v6M121 249.5v6" stroke="#8a2238" stroke-width="2.4" stroke-linecap="round"/>')
    a.add('<rect x="154" y="226" width="16" height="5" rx="1.5" fill="#c2465c"/><rect x="156" y="221" width="13" height="5" rx="1.5" fill="#2f8f88"/>'
          '<rect x="154.5" y="216" width="15" height="5" rx="1.5" fill="#e2b04a"/><path d="M153 232H171" stroke="#e2b04a" stroke-width="1.6"/>')
    a.add(face(a, skin, shade, eyes="open", brows=("soft", "raised"), mth="soft", brow="#1c1310", blush_op=0.22))
    hair = "#1c1310"
    cap = "M84 114C80 80 102 62 128 62C154 62 176 80 172 114C168 100 160 92 150 89C136 93 118 93 104 89C96 93 88 102 84 114Z"
    curls = "".join(f'<circle cx="{n(128 + 44 * math.cos(math.radians(t)))}" cy="{n(98 + 34 * math.sin(math.radians(t)))}" r="7.5"/>' for t in range(192, 352, 16))
    a.add(f'<path d="{cap}" fill="{hair}"/><g fill="{hair}">{curls}</g>'
          '<path d="M104 72Q114 66 124 66" fill="none" stroke="#3e2c25" stroke-width="3" stroke-linecap="round"/>')
    # round spectacles
    a.add('<g fill="#fff" fill-opacity=".14" stroke="#e0ae48" stroke-width="2.8"><circle cx="111" cy="126" r="12"/><circle cx="145" cy="126" r="12"/></g>'
          '<path d="M123 125Q128 121 133 125M99 124L86 121M157 124L170 121" fill="none" stroke="#e0ae48" stroke-width="2.6"/>'
          '<path d="M104 120Q106 117 109 116.5M138 120Q140 117 143 116.5" fill="none" stroke="#fff" stroke-opacity=".7" stroke-width="1.8" stroke-linecap="round"/>')
    return a


def bamboo(x, top, w, seed):
    rnd = random.Random(seed)
    o = [f'<rect x="{x}" y="{top}" width="{w}" height="{256 - top}" rx="{w / 2}"/>']
    y = top + rnd.uniform(18, 30)
    while y < 250:
        o.append(f'<rect x="{x - 1.5}" y="{n(y)}" width="{w + 3}" height="3" rx="1.5"/>')
        if rnd.random() < 0.55:
            s = 1 if rnd.random() < 0.5 else -1
            bx = x + (w if s > 0 else 0)
            o.append(f'<path d="M{n(bx)} {n(y)}q{n(s * 14)} -10 {n(s * 30)} -6q{n(-s * 14)} 8 {n(-s * 30)} 6Z"/>')
        y += rnd.uniform(26, 36)
    return "".join(o)


# 7 --------------------------------------------------------------------- ninja
def ninja():
    a = Av("ninja", "The Ninja")
    skin, shade = "#d8a274", "#b67c50"
    a.add(f'<rect width="256" height="256" fill="{a.lg("bg", [(0, "#434b72"), (1, "#1d2236")])}"/>')
    a.add(f'<circle cx="184" cy="74" r="70" fill="{a.rg("moonglow", [(0, "#fff1cf", 0.35), (1, "#fff1cf", 0)])}"/>'
          '<circle cx="184" cy="74" r="38" fill="#f6ead0"/><g fill="#e3d3b0"><circle cx="172" cy="64" r="7"/><circle cx="196" cy="84" r="5"/><circle cx="194" cy="58" r="3.5"/></g>')
    a.add('<g fill="#0b0d16" fill-opacity=".72">' + bamboo(22, 20, 10, 1) + bamboo(46, 70, 8, 2) + bamboo(222, 40, 10, 3) + "</g>")
    # knot tails behind
    a.add('<path d="M170 104C190 96 206 108 224 98C218 112 204 120 182 116Z" fill="#a51c2c"/>'
          '<path d="M172 114C188 120 196 136 214 142C200 148 186 140 176 124Z" fill="#c42234"/>')
    gi = a.lg("gi", [(0, "#3a3e4b"), (1, "#1d2028")])
    a.add(f'<path d="{TORSO}" fill="{gi}"/>')
    b1 = "M100 192L116 192L138 240L126 250Z"
    a.add(f'<path d="{mirror(b1)}" fill="#a51c2c"/><path d="{b1}" fill="#c42234"/>'
          '<path d="M60 256C66 236 78 222 94 214" fill="none" stroke="#14161c" stroke-width="2"/>')
    hood = a.lg("hood", [(0, "#454a59"), (1, "#24272f")], 0, 0, 0.3, 1)
    a.add(f'<path d="M76 132C72 80 98 54 128 54C158 54 184 80 180 132C180 164 162 190 128 194C94 190 76 164 76 132Z" fill="{hood}"/>')
    a.add(f'<path d="M92 113C106 106 150 106 164 113C167 122 165 132 161 139L95 139C91 132 89 122 92 113Z" fill="{a.lg("skin2", [(0, skin), (1, shade)])}"/>')
    a.add(face(a, skin, shade, eyes="narrow", brows="focused", mth=None, nose=False, blush=False, ears=False, head=False, brow="#16110e"))
    a.add('<path d="M90 138C108 145 148 145 166 138L166 144C148 151 108 151 90 144Z" fill="#2a2d36"/>'
          '<g fill="none" stroke="#121419" stroke-opacity=".7" stroke-width="2.2" stroke-linecap="round"><path d="M104 160Q128 168 152 160"/><path d="M112 174Q128 179 144 174"/></g>'
          '<path d="M122 150Q128 144 134 150" fill="none" stroke="#5a5f70" stroke-width="2" stroke-linecap="round"/>')
    band = a.lg("band", [(0, "#ef3a4c"), (1, "#a51c2c")])
    a.add(f'<path d="M78 106C100 90 156 90 180 106L180 117C156 101 100 101 78 117Z" fill="{band}"/>'
          '<path d="M86 104C104 94 152 94 172 104" fill="none" stroke="#ff7a86" stroke-width="1.6" stroke-opacity=".7"/>'
          '<ellipse cx="176" cy="110" rx="7" ry="8" fill="#a51c2c"/>')
    a.add(f'<path d="{sparkle(64, 46, 5)}" fill="#fff" fill-opacity=".6"/><path d="{sparkle(108, 28, 3.5)}" fill="#fff" fill-opacity=".5"/>')
    return a


# 8 -------------------------------------------------------------------- knight
def knight():
    a = Av("knight", "The Knight")
    skin, shade = "#eebb96", "#cf946f"
    a.add(f'<rect width="256" height="256" fill="{a.lg("bg", [(0, "#4f72e6"), (1, "#1a2a78")])}"/>')
    ban = "M22 0H52V108L37 94L22 108Z"
    a.add(f'<g fill="#1b2f8c" stroke="#ffcf4d" stroke-opacity=".55" stroke-width="2"><path d="{ban}"/><path d="{mirror(ban)}"/></g>'
          f'<g fill="#ffcf4d" fill-opacity=".6"><path d="{star(37, 58, 9, 3.8)}"/><path d="{star(219, 58, 9, 3.8)}"/></g>')
    wall = "M0 256V196"
    x = 0
    while x < 256:
        wall += f"H{x + 12}V184H{x + 24}V196"
        x += 24
    wall += "V256Z"
    a.add(f'<path d="{wall}" fill="#13206a" fill-opacity=".55"/>')
    plume = a.lg("plume", [(0, "#ff7676"), (1, "#c92a45")], 0, 0, 1, 1)
    a.add(f'<path d="M122 56C116 36 128 22 150 20C144 27 142 34 144 42C152 32 166 30 178 34C168 38 160 46 158 58C146 56 134 56 122 56Z" fill="{plume}"/>'
          '<path d="M130 50C132 38 138 30 146 26M146 50C152 42 160 38 170 36" fill="none" stroke="#ffb0b0" stroke-width="2" stroke-linecap="round"/>')
    back = a.lg("back", [(0, "#c2cbda"), (1, "#6e7b93")])
    a.add(f'<path d="M74 188C66 116 80 50 128 46C176 50 190 116 182 188Z" fill="{back}"/>')
    tab = a.lg("tab", [(0, "#3157d1"), (1, "#1a2f86")])
    a.add(f'<path d="{TORSO}" fill="{tab}"/><path d="M94 196C116 206 140 206 162 196" fill="none" stroke="#ffcf4d" stroke-width="3"/>')
    steel = a.lg("steel", [(0, "#f6f8fb"), (0.55, "#cdd5e1"), (1, "#8c98ae")], 0, 0, 0.4, 1)
    pa = "M34 254C34 214 58 194 96 194C102 206 100 218 92 228C70 228 50 238 40 256Z"
    a.add(f'<path d="{pa}" fill="{steel}" stroke="#7d899f" stroke-width="1.6"/><path d="{mirror(pa)}" fill="{steel}" stroke="#7d899f" stroke-width="1.6"/>'
          '<path d="M44 240C56 228 72 222 90 222M212 240C200 228 184 222 166 222" fill="none" stroke="#8c98ae" stroke-width="2"/>')
    a.add('<path d="M100 172C110 182 146 182 156 172L162 196C140 206 116 206 94 196Z" fill="' + steel + '" stroke="#7d899f" stroke-width="1.6"/>'
          '<path d="M98 186C118 194 138 194 158 186" fill="none" stroke="#8c98ae" stroke-width="1.8"/>')
    a.add('<path d="M164 200H190V213Q190 228 177 235Q164 228 164 213Z" fill="#2141b0" stroke="#ffcf4d" stroke-width="2.4" stroke-linejoin="round"/>'
          f'<path d="{star(177, 215, 7, 2.9)}" fill="#ffcf4d"/>')
    # braid
    a.add('<g fill="#e7b552" stroke="#b9862e" stroke-width="1.4">' + "".join(
        f'<ellipse cx="{x}" cy="{y}" rx="6" ry="7.5"/>' for x, y in ((86, 178), (84, 189), (82, 200), (80, 211))) + '</g>'
          '<rect x="74" y="217" width="11" height="4" rx="2" fill="#2141b0"/><path d="M75 221L72 231L80 228L84 232L84 221Z" fill="#e7b552"/>')
    a.add(face(a, skin, shade, eyes="open", brows="determined", mth="smile", brow="#a8741f", lashes=True))
    a.add('<path d="M94 108C100 114 108 115 114 109C110 104 103 102 96 103Z" fill="#e7b552"/>')
    a.add(f'<path d="M84 114C84 78 102 58 128 58C154 58 172 78 172 114C160 103 146 99 128 99C110 99 96 103 84 114Z" fill="{steel}" stroke="#7d899f" stroke-width="1.6"/>'
          '<path d="M128 60V99" stroke="#fff" stroke-width="3"/><path d="M131 61V99" stroke="#9aa6bb" stroke-width="1.6"/>')
    a.add('<path d="M88 94C100 74 156 74 168 94L166 101C154 86 102 86 90 101Z" fill="#e5eaf2" stroke="#7d899f" stroke-width="1.6" stroke-linejoin="round"/>'
          '<path d="M102 88L120 85M136 85L154 88" stroke="#3d475b" stroke-width="2.6" stroke-linecap="round"/>')
    cg = "M84 110C92 114 97 122 98 136C98 150 101 162 107 172C96 180 86 178 80 170C77 150 78 126 84 110Z"
    a.add(f'<path d="{cg}" fill="{steel}" stroke="#7d899f" stroke-width="1.6"/><path d="{mirror(cg)}" fill="{steel}" stroke="#7d899f" stroke-width="1.6"/>'
          '<g fill="#ffcf4d"><circle cx="88" cy="118" r="2.6"/><circle cx="168" cy="118" r="2.6"/><circle cx="90" cy="164" r="2.2"/><circle cx="166" cy="164" r="2.2"/></g>'
          '<path d="M98 66C106 62 114 60 122 60" fill="none" stroke="#fff" stroke-width="3.4" stroke-linecap="round"/>'
          '<rect x="123" y="50" width="10" height="9" rx="2" fill="#ffcf4d"/>')
    return a


def spiral(cx, cy, r0, r1, turns, start=0.0):
    P = []
    steps = int(turns * 14)
    for i in range(steps + 1):
        t = i / steps
        ang = start + t * turns * 2 * math.pi
        r = r0 + (r1 - r0) * t
        P.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return smooth_open(P)


# 9 -------------------------------------------------------------------- wizard
def wizard():
    a = Av("wizard", "The Wizard")
    skin, shade = "#f7d4bd", "#e0a98a"
    a.add(f'<rect width="256" height="256" fill="{a.lg("bg", [(0, "#4f3ac4"), (1, "#150c44")])}"/>')
    a.add('<g fill="none" stroke="#ffcf6a" stroke-opacity=".22" stroke-width="3" stroke-linecap="round">'
          f'<path d="{spiral(50, 150, 3, 36, 2.2)}"/><path d="{spiral(214, 52, 3, 30, 2, 1.5)}"/><path d="M12 92C40 70 52 40 44 14"/><path d="M244 150C222 170 220 204 236 236"/></g>')
    a.add('<g fill="#fff">' + "".join(f'<path d="{sparkle(x, y, r)}" fill-opacity="{o}"/>' for x, y, r, o in ((38, 46, 6, .7), (226, 104, 5, .6), (26, 206, 4, .5), (82, 22, 3.5, .5), (176, 22, 4, .6))) + "</g>")
    a.add(neck(skin, shade))
    robe = a.lg("robe", [(0, "#4b3ac0"), (1, "#22176e")])
    a.add(f'<path d="{TORSO}" fill="{robe}"/><path d="M122 210L114 256M134 210L142 256" stroke="#ffcc4d" stroke-width="2.6"/>')
    cl = "M100 198C88 188 76 176 70 160C88 164 106 176 122 198Z"
    a.add(f'<path d="{cl}" fill="#2d2180" stroke="#ffcc4d" stroke-width="2.2" stroke-linejoin="round"/><path d="{mirror(cl)}" fill="#2d2180" stroke="#ffcc4d" stroke-width="2.2" stroke-linejoin="round"/>'
          '<circle cx="128" cy="205" r="8.5" fill="#ffcc4d"/><circle cx="128" cy="205" r="4.4" fill="#18b9bd"/><circle cx="126.6" cy="203.6" r="1.4" fill="#fff"/>')
    # wand + hand
    a.add(f'<circle cx="205" cy="163" r="28" fill="{a.rg("tip", [(0, "#fff6c9", 0.95), (0.35, "#ffcf6a", 0.5), (1, "#ffcf6a", 0)])}"/>'
          '<path d="M178 234L203 168" stroke="#5a3418" stroke-width="5.5" stroke-linecap="round"/><path d="M200.5 174.5L203.5 166.5" stroke="#ffcc4d" stroke-width="6.5" stroke-linecap="round"/>'
          f'<path d="M160 256C160 238 168 226 180 224C192 224 200 234 204 256Z" fill="{robe}"/><path d="M168 230C176 226 188 226 196 232" fill="none" stroke="#ffcc4d" stroke-width="2.4"/>'
          f'<path d="M172 214C172 206 178 202 186 202C194 202 197 208 197 214C197 222 191 227 184 227C176 227 172 221 172 214Z" fill="{skin}"/>'
          f'<path d="M176 209C181 207.5 187 207.5 193 209M175 215C180 213.5 187 213.5 194 215M176 221C181 219.5 186 219.5 191 221" fill="none" stroke="{shade}" stroke-width="1.6" stroke-linecap="round"/>'
          f'<path d="M187 203C194 200 199 206 194 212" fill="{skin}" stroke="{shade}" stroke-width="1.8" stroke-linecap="round"/>'
          f'<path d="{sparkle(204, 163, 11)}" fill="#fff"/><path d="{sparkle(220, 146, 5)}" fill="#ffe28a"/><path d="{sparkle(188, 146, 4)}" fill="#ffe28a"/><path d="{sparkle(222, 178, 3.5)}" fill="#fff"/>')
    a.add(face(a, skin, shade, eyes=("open", "wink"), brows=("soft", "raised"), mth="grin", brow="#b4501d", freckles=True))
    hair = "#d8692a"
    tuft = "M84 96C76 108 78 124 86 136C88 124 92 112 100 100Z"
    a.add(f'<path d="{tuft}" fill="{hair}"/><path d="{mirror(tuft)}" fill="{hair}"/>'
          f'<path d="M98 97Q105 111 113 100Q119 110 127 99Q133 108 141 99L141 95L98 95Z" fill="{hair}"/>')
    hat = a.lg("hat", [(0, "#5f4ad6"), (1, "#2b1f82")], 0, 0, 1, 1)
    a.add('<path d="M54 98C64 80 192 80 202 98C190 112 66 112 54 98Z" fill="#2b1f7a"/><path d="M60 96C76 86 180 86 196 96" fill="none" stroke="#6a56e0" stroke-width="2"/>'
          f'<path d="M88 94C94 68 104 46 118 28C122 22 128 24 128 30C136 50 154 72 168 94Z" fill="{hat}"/>'
          f'<path d="M119 29C110 19 98 18 90 26C100 24 107 28 112 38Z" fill="#3a2b9c"/><circle cx="90" cy="26" r="3.6" fill="#ffcc4d"/>'
          '<path d="M89 86C110 94 146 94 167 86L169 94C146 102 110 102 87 94Z" fill="#ffcc4d"/>'
          f'<g fill="#ffcc4d"><path d="{star(128, 62, 8.5, 3.5)}"/><path d="{star(150, 78, 5, 2.1)}"/><path d="M112 66A7 7 0 1 0 112 80A5.4 5.4 0 1 1 112 66Z"/><circle cx="138" cy="44" r="1.8"/><circle cx="104" cy="82" r="1.6"/></g>')
    return a


# 10 -------------------------------------------------------------------- artist
def artist():
    a = Av("artist", "The Artist")
    skin, shade = "#b77b52", "#94593a"
    a.add(f'<rect width="256" height="256" fill="{a.lg("bg", [(0, "#ff8cc4"), (1, "#d63f80")])}"/>')
    a.add('<g fill="none" stroke-linecap="round">'
          '<path d="M10 64C40 42 70 46 98 28" stroke="#ffd23f" stroke-opacity=".45" stroke-width="15"/>'
          '<path d="M164 236C196 214 222 218 252 198" stroke="#18b9bd" stroke-opacity=".38" stroke-width="16"/>'
          '<path d="M206 30C222 46 234 66 244 94" stroke="#7c5cff" stroke-opacity=".35" stroke-width="12"/>'
          '<path d="M8 150C20 176 18 198 30 224" stroke="#fff" stroke-opacity=".22" stroke-width="10"/></g>'
          '<g fill="#fff" fill-opacity=".35"><circle cx="230" cy="130" r="5"/><circle cx="216" cy="146" r="3"/><circle cx="34" cy="100" r="4"/></g>')
    hair, hair2 = "#3a2416", "#5c3a24"
    back = "M66 196C52 160 56 112 72 86C88 60 168 60 184 86C200 112 204 160 190 196C180 204 168 198 164 188L92 188C88 198 76 204 66 196Z"
    a.add(f'<path d="{back}" fill="{hair}"/><g fill="{hair}">' + "".join(f'<circle cx="{x}" cy="{y}" r="14"/>' for x, y in ((64, 118), (58, 148), (62, 178), (192, 118), (198, 148), (194, 178))) + "</g>"
          f'<g fill="none" stroke="{hair2}" stroke-width="3" stroke-linecap="round"><path d="M62 140q6 6 0 14M64 170q6 6 0 14M194 140q-6 6 0 14M192 170q-6 6 0 14"/></g>')
    # paintbrush behind the ear
    a.add('<path d="M66 96L98 150" stroke="#18b9bd" stroke-width="5.5" stroke-linecap="round"/><path d="M61 87L67 97" stroke="#c9d1dc" stroke-width="7.5"/>'
          '<path d="M60 86C53 80 52 70 55 62C62 66 66 76 65 84Z" fill="#7c5cff"/>')
    a.add(neck(skin, shade))
    smock = a.lg("smock", [(0, "#fff8ee"), (1, "#efd9c6")])
    a.add(f'<path d="{TORSO}" fill="{smock}"/>')
    for (x, y, r, c, sd) in ((78, 232, 10, "#18b9bd", 1), (178, 236, 11, "#7c5cff", 2), (150, 250, 6, "#ff5fa2", 3), (100, 250, 6, "#ffc93c", 4), (196, 214, 4, "#ff5fa2", 5)):
        a.add(f'<path d="{blob(x, y, r, r, 0.22, 5, sd, 10, seed=sd)}" fill="{c}"/>')
    col = ("M96 190C104 186 112 194 120 190C126 196 132 196 138 190C146 194 152 186 160 190C166 200 162 210 156 208"
           "C154 216 148 218 146 210C138 214 132 222 128 214C122 220 114 216 112 208C106 214 98 212 100 204C92 204 90 196 96 190Z")
    a.add(f'<path d="{col}" fill="#ffc93c"/><g><circle cx="110" cy="200" r="3.2" fill="#ff5fa2"/><circle cx="146" cy="202" r="3.2" fill="#18b9bd"/><circle cx="128" cy="207" r="2.6" fill="#7c5cff"/></g>')
    a.add(face(a, skin, shade, eyes="happy", brows="raised", mth="open", brow="#2a180e", lashes=True, blush_op=0.3))
    a.add('<path d="M148 151Q156 146 165 150" fill="none" stroke="#7c5cff" stroke-width="4.4" stroke-linecap="round" stroke-opacity=".9"/>'
          '<circle cx="160" cy="139" r="2.8" fill="#18b9bd"/><circle cx="98" cy="153" r="2.2" fill="#ffc93c"/>'
          '<circle cx="171" cy="140" r="3.4" fill="none" stroke="#18b9bd" stroke-width="2"/>')
    a.add(f'<path d="M84 114C82 92 96 84 128 84C160 84 174 92 172 114C166 104 158 100 150 101C142 96 136 96 128 100C120 96 112 96 106 101C98 100 90 104 84 114Z" fill="{hair}"/>'
          f'<g fill="{hair}"><circle cx="93" cy="104" r="8"/><circle cx="106" cy="99" r="7"/><circle cx="150" cy="99" r="7"/><circle cx="163" cy="104" r="8"/></g>')
    beret = a.lg("beret", [(0, "#ffdc6a"), (1, "#eea510")])
    a.add(f'<path d="M78 96C72 72 104 52 144 54C184 56 200 80 184 96C162 106 102 108 78 96Z" fill="{beret}"/>'
          '<path d="M80 94C104 104 160 104 182 94L182 100C160 110 104 110 80 100Z" fill="#d98e0c"/>'
          '<path d="M142 56C141 48 147 44 152 49" fill="none" stroke="#d98e0c" stroke-width="4" stroke-linecap="round"/>'
          '<path d="M100 70C112 62 128 60 140 61" fill="none" stroke="#fff" stroke-opacity=".5" stroke-width="3.4" stroke-linecap="round"/>'
          '<circle cx="164" cy="74" r="4" fill="#ff5fa2"/><circle cx="172" cy="82" r="2.2" fill="#7c5cff"/>')
    return a


CHARS2 = [scholar, ninja, knight, wizard, artist]
