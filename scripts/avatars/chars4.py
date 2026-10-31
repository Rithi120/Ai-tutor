import math
import random

from lib import (Av, HEAD, TORSO, face, neck, mirror, mix, n, star, sparkle, gear, circ, blob, smooth_open,
                 smooth_closed, pts)


def embers(seed, count, box_skip=(70, 50, 186, 256)):
    rnd = random.Random(seed)
    o = []
    while len(o) < count:
        x, y = rnd.uniform(10, 246), rnd.uniform(10, 246)
        if box_skip[0] < x < box_skip[2] and box_skip[1] < y < box_skip[3]:
            continue
        r = rnd.choice((1.2, 1.6, 2.2, 2.8))
        o.append(f'<circle cx="{n(x)}" cy="{n(y)}" r="{n(r * 2.6)}" fill="#ff9a3d" fill-opacity=".18"/><circle cx="{n(x)}" cy="{n(y)}" r="{r}" fill="#ffc35a"/>')
    return "".join(o)


# 16 ---------------------------------------------------------- dragon guardian
def dragon_guardian():
    a = Av("dragon-guardian", "The Dragon Guardian")
    skin, shade = "#c88b5c", "#a46a3e"
    a.add(f'<rect width="256" height="256" fill="{a.rg("bg", [(0, "#1c9a71"), (0.6, "#0d5440"), (1, "#062e22")], cx=0.5, cy=0.42, r=0.75)}"/>')
    a.defs.append('<pattern id="dragon-guardian-bgscale" width="24" height="14" patternUnits="userSpaceOnUse"><path d="M0 14Q12 0 24 14M-12 7Q0 -7 12 7M12 7Q24 -7 36 7" fill="none" stroke="#7dffcf" stroke-opacity=".07" stroke-width="1.6"/></pattern>')
    a.add('<rect width="256" height="256" fill="url(#dragon-guardian-bgscale)"/>')
    a.add(f'<rect width="256" height="256" fill="{a.rg("ember", [(0, "#ff7a2a", 0.45), (1, "#ff7a2a", 0)], cx=0.5, cy=1.05, r=0.7)}"/>')
    a.add(embers(16, 16))
    hair = "#1a1410"
    a.add(f'<path d="M82 126C70 150 64 184 72 214C86 198 92 166 94 136Z" fill="{hair}"/>')
    a.add(neck(skin, shade))
    arm = a.lg("armor", [(0, "#27c48a"), (1, "#0a5c41")])
    a.defs.append('<pattern id="dragon-guardian-scale" width="12" height="9" patternUnits="userSpaceOnUse"><path d="M0 9Q6 -1 12 9M-6 4.5Q0 -5.5 6 4.5M6 4.5Q12 -5.5 18 4.5" fill="none" stroke="#063d2b" stroke-opacity=".55" stroke-width="1.4"/></pattern>')
    a.add(f'<path d="{TORSO}" fill="{arm}"/><path d="{TORSO}" fill="url(#dragon-guardian-scale)"/>')
    p1 = "M34 252C36 222 56 204 84 200C90 212 88 224 80 232C62 232 46 240 34 252Z"
    p2 = "M50 224C58 208 74 200 96 198C100 206 98 214 92 220C78 218 62 220 50 224Z"
    pl = a.lg("plate", [(0, "#3ee0a2"), (1, "#0d7552")], 0, 0, 0.5, 1)
    for p in (p1, p2):
        for q in (p, mirror(p)):
            a.add(f'<path d="{q}" fill="{pl}" stroke="#e8b84a" stroke-width="2" stroke-linejoin="round"/>')
    a.add('<g fill="none" stroke="#ff9a3d" stroke-width="1.8" stroke-linecap="round"><path d="M46 238C58 228 70 224 82 222"/><path d="M210 238C198 228 186 224 174 222"/><path d="M104 226L112 256M152 226L144 256"/></g>')
    a.add('<path d="M98 184C110 196 146 196 158 184L166 204C142 218 114 218 90 204Z" fill="#0d6b4b" stroke="#e8b84a" stroke-width="2.2" stroke-linejoin="round"/>'
          f'<circle cx="128" cy="206" r="16" fill="{a.rg("gemglow", [(0, "#ffd27a", 0.9), (1, "#ff7a2a", 0)])}"/>'
          '<path d="M128 197L135 206L128 215L121 206Z" fill="#ff8a3d" stroke="#ffe08a" stroke-width="1.6"/><path d="M128 199.5L131 204L128 205Z" fill="#fff"/>')
    a.add(face(a, skin, shade, eyes="open", brows="focused", mth="soft", brow="#1a1410"))
    a.add(f'<path d="M84 118C80 82 102 62 128 62C154 62 176 82 172 118C168 98 158 88 144 84C128 88 110 88 98 94C92 102 86 110 84 118Z" fill="{hair}"/>'
          f'<path d="M92 96C84 116 86 140 92 158C95 140 95 118 102 100Z" fill="{hair}"/>'
          '<path d="M108 72C120 66 138 66 150 70M118 80C130 76 144 76 156 80" fill="none" stroke="#3b2e26" stroke-width="2.4" stroke-linecap="round"/>')
    # baby dragon on the viewer's right shoulder
    body = a.lg("dbody", [(0, "#ffa75a"), (1, "#e05a24")], 0, 0, 0.4, 1)
    a.add('<path d="M190 178L218 150L215 166L228 164L217 182L228 188L198 194Z" fill="#b8441b" stroke="#8a2f10" stroke-width="1.4" stroke-linejoin="round"/>'
          '<path d="M198 182L215 166M200 186L217 182" stroke="#e06a2c" stroke-width="1.6"/>'
          f'<path d="M174 204C158 214 152 228 160 240" fill="none" stroke="#e8682c" stroke-width="6" stroke-linecap="round"/><path d="M154 238L166 236L160 248Z" fill="#e8682c"/>'
          f'<ellipse cx="186" cy="194" rx="16" ry="15" fill="{body}"/><ellipse cx="182" cy="198" rx="8.5" ry="10" fill="#ffd38a"/>'
          '<path d="M176 194H188M176 200H188M177 206H187" stroke="#e8a85a" stroke-width="1.4"/>'
          '<ellipse cx="174" cy="207" rx="5.5" ry="3.6" fill="#c44d1f"/><ellipse cx="195" cy="208" rx="5.5" ry="3.6" fill="#c44d1f"/>')
    a.add('<path d="M186 147L179 133L192 143Z" fill="#ffe6b0"/><path d="M201 146L207 131L209 148Z" fill="#ffe6b0"/>'
          '<path d="M209 154L223 148L214 165Z" fill="#c44d1f"/>'
          f'<ellipse cx="195" cy="160" rx="17" ry="15" fill="{body}"/><ellipse cx="183" cy="167" rx="10" ry="7.5" fill="#ffb36a"/>'
          '<circle cx="178.5" cy="165" r="1.3" fill="#7a2a10"/><circle cx="184.5" cy="164" r="1.3" fill="#7a2a10"/>'
          '<ellipse cx="191" cy="155" rx="3.8" ry="4.6" fill="#2a1a14"/><ellipse cx="203" cy="154" rx="3.8" ry="4.6" fill="#2a1a14"/>'
          '<circle cx="192.4" cy="153.4" r="1.6" fill="#fff"/><circle cx="204.4" cy="152.4" r="1.6" fill="#fff"/>'
          '<path d="M178 171Q184 175 190 170.5" fill="none" stroke="#7a2a10" stroke-width="1.8" stroke-linecap="round"/>'
          '<ellipse cx="206" cy="164" rx="3.6" ry="2.2" fill="#ff6f7d" fill-opacity=".5"/>')
    a.add(f'<g fill="#ffe08a"><path d="{sparkle(222, 128, 4)}"/><path d="{sparkle(170, 132, 3)}"/></g>')
    return a


# 17 ----------------------------------------------------------- cyber guardian
def cyber_guardian():
    a = Av("cyber-guardian", "The Cyber Guardian")
    skin, shade = "#efc3a0", "#d49a76"
    a.add(f'<rect width="256" height="256" fill="{a.lg("bg", [(0, "#2c1670"), (1, "#0d1236")])}"/>')
    grid = "".join(f"M0 {y}H256" for y in (196, 208, 224, 246))
    grid += "".join(f"M128 176L{n(128 + k * 70)} 256" for k in range(-4, 5))
    a.add(f'<path d="{grid}" stroke="#ff3dcf" stroke-opacity=".35" stroke-width="1.4"/>')
    a.defs.append('<pattern id="cyber-guardian-hex" width="18" height="31.2" patternUnits="userSpaceOnUse">'
                  '<path d="M9 0L18 5.2V15.6L9 20.8L0 15.6V5.2ZM9 20.8V31.2" fill="none" stroke="#2de2ff" stroke-opacity=".32" stroke-width="1"/></pattern>')
    a.add(f'<circle cx="128" cy="122" r="104" fill="{a.rg("aura", [(0, "#ff3dcf", 0.35), (0.6, "#7c5cff", 0.18), (1, "#2de2ff", 0.05)])}"/>'
          '<circle cx="128" cy="122" r="104" fill="url(#cyber-guardian-hex)"/>'
          '<circle cx="128" cy="122" r="104" fill="none" stroke="#2de2ff" stroke-opacity=".55" stroke-width="2"/>'
          '<path d="M44 62A104 104 0 0 1 128 18" fill="none" stroke="#fff" stroke-opacity=".6" stroke-width="3" stroke-linecap="round"/>')
    a.add(neck(skin, shade))
    arm = a.lg("arm", [(0, "#262f7a"), (1, "#0d1238")])
    a.add(f'<path d="{TORSO}" fill="{arm}"/><path d="M96 202H160L172 256H84Z" fill="#283286" stroke="#4352c4" stroke-width="1.6"/>')
    sh = "M36 246C40 220 58 204 88 198L98 214L60 234Z"
    a.add(f'<path d="{sh}" fill="#323ea0" stroke="#5b6ae0" stroke-width="1.6" stroke-linejoin="round"/><path d="{mirror(sh)}" fill="#323ea0" stroke="#5b6ae0" stroke-width="1.6" stroke-linejoin="round"/>'
          '<path d="M100 176L110 198H146L156 176L164 202C140 214 116 214 92 202Z" fill="#161c52" stroke="#4352c4" stroke-width="1.6" stroke-linejoin="round"/>')
    a.add('<g fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
          '<path d="M104 214H116L122 222V244" stroke="#2de2ff"/><path d="M152 214H140L134 222V248" stroke="#ff3dcf"/>'
          '<path d="M52 234L70 222H86" stroke="#2de2ff"/><path d="M204 234L186 222H170" stroke="#ff3dcf"/><path d="M110 204L146 204" stroke="#7c5cff"/></g>'
          '<g fill="#fff"><circle cx="122" cy="244" r="2.4"/><circle cx="134" cy="248" r="2.4"/><circle cx="86" cy="222" r="2.4"/><circle cx="170" cy="222" r="2.4"/></g>')
    hx = pts([(128 + 9 * math.cos(math.radians(30 + 60 * k)), 228 + 9 * math.sin(math.radians(30 + 60 * k))) for k in range(6)])
    a.add(f'<circle cx="128" cy="228" r="18" fill="{a.rg("emb", [(0, "#ff8be6", 0.9), (1, "#ff3dcf", 0)])}"/><path d="{hx}" fill="#ffd1f4" stroke="#ff3dcf" stroke-width="2"/>')
    a.add(face(a, skin, shade, eyes="open", brows=(None, None), mth="smirk", lashes=True))
    hair = a.lg("hair", [(0, "#f4f6ff"), (1, "#a6afe4")], 0, 0, 1, 1)
    a.add('<path d="M84 118C82 104 84 94 90 88L98 100C92 106 88 112 86 122Z" fill="#7d85ad"/>'
          f'<path d="M86 102C82 72 106 54 136 56C166 58 188 80 181 108L178 126L172 114L170 146L162 118L156 128L150 100C130 92 106 94 90 106Z" fill="{hair}"/>'
          '<path d="M108 66C130 58 158 62 172 80M118 76C140 70 160 78 170 96" fill="none" stroke="#c9b8ff" stroke-width="2.6" stroke-linecap="round"/>'
          '<path d="M164 104C170 116 170 132 168 146" fill="none" stroke="#ff3dcf" stroke-width="3" stroke-linecap="round"/>')
    vis = a.lg("visor", [(0, "#ff3dcf", 0.8), (0.5, "#7c5cff", 0.74), (1, "#2de2ff", 0.8)], 0, 0, 1, 0)
    a.add(f'<path d="M76 114C96 106 160 106 180 114L182 128C164 142 146 142 128 136C110 142 92 142 74 128Z" fill="{vis}"/>'
          '<path d="M80 115C100 109 156 109 176 115" fill="none" stroke="#fff" stroke-opacity=".85" stroke-width="1.8" stroke-linecap="round"/>'
          '<path d="M78 127C94 138 112 138 128 133C144 138 162 138 178 127" fill="none" stroke="#2de2ff" stroke-opacity=".9" stroke-width="1.4"/>'
          '<g fill="#fff"><circle cx="113.6" cy="123" r="2.2"/><circle cx="147.6" cy="123" r="2.2"/></g>'
          '<path d="M158 119H168M160 123H166M90 119H96" stroke="#fff" stroke-opacity=".7" stroke-width="1.4" stroke-linecap="round"/>'
          '<rect x="72" y="118" width="8" height="14" rx="3" fill="#161c52" stroke="#ff3dcf" stroke-width="1.6"/>')
    a.add(f'<g fill="#bff6ff"><path d="{sparkle(48, 168, 5)}"/><path d="{sparkle(208, 64, 6)}"/></g>')
    return a


# 18 ----------------------------------------------------------- time traveller
def time_traveller():
    a = Av("time-traveller", "The Time Traveller")
    skin, shade = "#f6d6bd", "#e0ae8c"
    a.add(f'<rect width="256" height="256" fill="{a.rg("bg", [(0, "#24b4ad"), (0.6, "#0d5a5c"), (1, "#04262a")], cx=0.5, cy=0.45, r=0.75)}"/>')
    a.add('<g fill="none" stroke-linecap="round">'
          '<circle cx="128" cy="118" r="112" stroke="#18b9bd" stroke-opacity=".25" stroke-width="10" stroke-dasharray="40 18"/>'
          '<circle cx="128" cy="118" r="80" stroke="#e6b06a" stroke-opacity=".25" stroke-width="4" stroke-dasharray="70 20"/>'
          '<circle cx="128" cy="118" r="98" stroke="#e6b06a" stroke-opacity=".5" stroke-width="3"/></g>')
    ticks = []
    for k in range(12):
        ang = math.radians(-90 + k * 30)
        r0 = 86 if k % 3 == 0 else 90
        ticks.append(f"M{n(128 + r0 * math.cos(ang))} {n(118 + r0 * math.sin(ang))}L{n(128 + 97 * math.cos(ang))} {n(118 + 97 * math.sin(ang))}")
    a.add(f'<path d="{"".join(ticks)}" stroke="#f0c07a" stroke-opacity=".75" stroke-width="3.4" stroke-linecap="round"/>')
    a.add(f'<circle cx="128" cy="118" r="76" fill="{a.rg("portal", [(0, "#d8fff8", 0.75), (0.5, "#5fe3d9", 0.25), (1, "#5fe3d9", 0)])}"/>')
    a.add('<path d="M128 118L56 76M128 118L208 72" stroke="#f0c07a" stroke-opacity=".8" stroke-width="4" stroke-linecap="round"/>'
          '<path d="M50 72L62 72L56 82Z" fill="#f0c07a" transform="rotate(-30 56 76)"/><path d="M202 68L214 68L208 78Z" fill="#f0c07a" transform="rotate(60 208 72)"/>')
    a.add('<g fill="#e6b06a" fill-opacity=".3" fill-rule="evenodd">' + f'<path d="{gear(28, 228, 26, 10)}"/><path d="{gear(228, 28, 22, 9, rot=6)}"/><path d="{gear(232, 222, 14, 8)}"/>' + "</g>")
    hair = a.lg("hair", [(0, "#f5cf74"), (1, "#c98f32")], 0, 0, 1, 1)
    a.add(f'<path d="M78 152C66 112 80 66 124 60C168 56 196 82 202 116C206 140 216 156 228 164C204 172 186 160 180 146L176 128Z" fill="{hair}"/>'
          f'<path d="M184 96C200 94 214 102 226 114C210 110 196 108 184 110Z" fill="{hair}"/>')
    a.add(neck(skin, shade))
    jk = a.lg("jacket", [(0, "#9a6238"), (1, "#5a321b")])
    a.add(f'<path d="{TORSO}" fill="{jk}"/><path d="M108 190L128 216L148 190Z" fill="#18b9bd"/>'
          '<path d="M128 236V256" stroke="#3e2210" stroke-width="2.4"/>')
    fur = "M58 232C66 206 88 192 108 190L128 216L148 190C168 192 190 206 198 232C186 226 170 220 156 218L128 238L100 218C86 220 70 226 58 232Z"
    a.add(f'<path d="{fur}" fill="#f1e4cc"/>')
    fl = []
    for x, y in ((62, 226), (70, 212), (82, 202), (96, 194), (108, 192), (160, 194), (174, 202), (186, 212), (194, 226), (148, 192)):
        fl.append(f'<circle cx="{x}" cy="{y}" r="7.5"/>')
    a.add('<g fill="#f1e4cc">' + "".join(fl) + '</g><path d="M80 214q6 -4 12 -2M168 214q-6 -4 -12 -2M100 204q4 -3 8 -2" fill="none" stroke="#d6c4a2" stroke-width="2" stroke-linecap="round"/>')
    a.add('<path d="M102 222C112 238 140 238 152 220" fill="none" stroke="#ffcf5c" stroke-width="2.4" stroke-dasharray="2.4 2.6"/>'
          '<circle cx="96" cy="230" r="11" fill="#e8b04a" stroke="#a8742a" stroke-width="2"/><circle cx="96" cy="230" r="7.6" fill="#fff6e2"/>'
          '<rect x="93.5" y="216" width="5" height="4" rx="1.4" fill="#a8742a"/>'
          '<path d="M96 230V225M96 230L99.5 232" stroke="#5a3a1e" stroke-width="1.6" stroke-linecap="round"/>')
    a.add(face(a, skin, shade, eyes="open", brows=("raised", "soft"), mth="grin", brow="#a6782e", lashes=True))
    a.add(f'<path d="M84 118C80 84 100 64 130 64C160 64 178 84 174 116C166 102 150 92 130 90C114 94 98 102 84 118Z" fill="{hair}"/>'
          '<path d="M104 74C120 66 144 66 160 74M150 90C162 94 170 102 174 112" fill="none" stroke="#fde2a0" stroke-width="2.6" stroke-linecap="round"/>')
    a.add('<path d="M80 98C100 84 156 84 176 98L176 106C156 92 100 92 80 106Z" fill="#5a3a22"/>'
          '<path d="M124 88Q128 85 132 88" fill="none" stroke="#b9793a" stroke-width="4"/>')
    for gx, (hx, hy), (mx, my) in ((109, (0, -8.5), (6, 3)), (147, (-6.5, -5.5), (2, 8.5))):
        tk = "".join(f"M{n(gx + 9.5 * math.cos(math.radians(t)))} {n(88 + 9.5 * math.sin(math.radians(t)))}L{n(gx + 12 * math.cos(math.radians(t)))} {n(88 + 12 * math.sin(math.radians(t)))}" for t in range(0, 360, 90))
        a.add(f'<circle cx="{gx}" cy="88" r="15.5" fill="#fbf1dc" stroke="#b9793a" stroke-width="4.6"/><circle cx="{gx}" cy="88" r="17.6" fill="none" stroke="#ffd9a0" stroke-width="1.2"/>'
              f'<path d="{tk}" stroke="#7a4a22" stroke-width="1.8"/>'
              f'<path d="M{gx} 88l{hx} {hy}M{gx} 88l{mx} {my}" stroke="#3e2a1a" stroke-width="2" stroke-linecap="round"/><circle cx="{gx}" cy="88" r="1.8" fill="#c0392b"/>'
              f'<path d="M{gx - 10} 82Q{gx - 7} 76 {gx - 1} 75" fill="none" stroke="#fff" stroke-opacity=".8" stroke-width="2" stroke-linecap="round"/>')
    a.add(f'<g fill="#fff6d0"><path d="{sparkle(42, 150, 6)}"/><path d="{sparkle(214, 176, 5)}"/><path d="{sparkle(70, 36, 4)}"/></g>')
    return a


# 19 --------------------------------------------------------------- mastermind
def mastermind():
    a = Av("mastermind", "The Mastermind")
    skin, shade = "#6a3d26", "#4d2918"
    a.add(f'<rect width="256" height="256" fill="{a.rg("bg", [(0, "#6a34c4"), (0.55, "#341274"), (1, "#1a0838")], cx=0.5, cy=0.44, r=0.75)}"/>')
    w = []
    for k in range(24):
        a0, a1 = math.radians(k * 15), math.radians(k * 15 + 4)
        w.append(pts([(128, 112), (128 + 260 * math.cos(a0), 112 + 260 * math.sin(a0)), (128 + 260 * math.cos(a1), 112 + 260 * math.sin(a1))]))
    a.add(f'<path d="{"".join(w)}" fill="#ffd36a" fill-opacity=".12"/>')
    a.add(f'<circle cx="128" cy="112" r="100" fill="{a.rg("aura", [(0, "#ffe9a8", 0.75), (0.45, "#ffcf5c", 0.32), (1, "#c58bff", 0)])}"/>'
          '<circle cx="128" cy="112" r="74" fill="none" stroke="#ffd36a" stroke-opacity=".6" stroke-width="2"/>'
          '<circle cx="128" cy="112" r="84" fill="none" stroke="#ffd36a" stroke-opacity=".35" stroke-width="1.6" stroke-dasharray="3 6"/>')
    runes = []
    for ang, kind in ((200, "tri"), (240, "dia"), (300, "eye"), (340, "hex"), (160, "sq"), (20, "circ")):
        x = 128 + 90 * math.cos(math.radians(ang))
        y = 116 + 90 * math.sin(math.radians(ang))
        if kind == "tri":
            d = pts([(x, y - 9), (x + 8, y + 6), (x - 8, y + 6)])
        elif kind == "dia":
            d = pts([(x, y - 10), (x + 7, y), (x, y + 10), (x - 7, y)]) + f"M{n(x)} {n(y - 4)}V{n(y + 4)}"
        elif kind == "eye":
            d = pts([(x, y - 9), (x + 9, y + 7), (x - 9, y + 7)]) + circ(x, y + 2, 2.6)
        elif kind == "hex":
            d = pts([(x + 8 * math.cos(math.radians(60 * k)), y + 8 * math.sin(math.radians(60 * k))) for k in range(6)])
        elif kind == "sq":
            d = pts([(x, y - 9), (x + 9, y), (x, y + 9), (x - 9, y)]) + circ(x, y, 3)
        else:
            d = circ(x, y, 8) + circ(x, y, 2.4)
        runes.append(d)
    rd = "".join(runes)
    a.add(f'<path d="{rd}" fill="none" stroke="#ffd36a" stroke-opacity=".3" stroke-width="6" stroke-linejoin="round"/>'
          f'<path d="{rd}" fill="none" stroke="#fff0b8" stroke-width="2.2" stroke-linejoin="round"/>')
    cape = a.lg("cape", [(0, "#5a22a8"), (1, "#210848")])
    wing = "M84 206C60 178 50 136 56 92C72 116 88 150 106 186Z"
    lining = "M92 196C74 172 66 140 66 112C78 134 90 160 104 184Z"
    for p in (wing, mirror(wing)):
        a.add(f'<path d="{p}" fill="{cape}" stroke="#ffcf5c" stroke-width="2.4" stroke-linejoin="round"/>')
    a.add(f'<path d="{lining}" fill="#8a3ae0"/><path d="{mirror(lining)}" fill="#8a3ae0"/>')
    a.add(neck(skin, shade))
    robe = a.lg("robe", [(0, "#45188c"), (1, "#1a0740")])
    a.add(f'<path d="{TORSO}" fill="{robe}"/><path d="M104 194L128 236L152 194Z" fill="#17072e"/>'
          '<path d="M98 194L128 240L158 194" fill="none" stroke="#ffcf5c" stroke-width="3"/>'
          '<path d="M60 236C70 222 84 214 96 212M196 236C186 222 172 214 160 212" fill="none" stroke="#ffcf5c" stroke-opacity=".7" stroke-width="2" stroke-dasharray="1 4" stroke-linecap="round"/>'
          '<path d="M100 212C112 230 144 230 156 212" fill="none" stroke="#ffcf5c" stroke-width="2.4" stroke-dasharray="3 2.4"/>'
          '<g fill="#ffcf5c" stroke="#b8800e" stroke-width="1.2"><circle cx="100" cy="210" r="7"/><circle cx="156" cy="210" r="7"/></g>'
          '<circle cx="100" cy="210" r="3" fill="#8a3ae0"/><circle cx="156" cy="210" r="3" fill="#8a3ae0"/>')
    a.add(face(a, skin, shade, eyes="calm", brows="soft", mth="half", brow="#120805", iris="#120805", blush_op=0.14))
    a.add('<path d="M86 110C84 82 102 64 128 64C154 64 172 82 170 110C162 96 146 88 128 88C110 88 94 96 86 110Z" fill="#140a06" fill-opacity=".7"/>'
          '<path d="M80 120Q78 126 81 132M176 120Q178 126 175 132" fill="none" stroke="#ffcf5c" stroke-width="2.6" stroke-linecap="round"/>')
    a.add(f'<circle cx="128" cy="100" r="15" fill="{a.rg("gemglow", [(0, "#e8d4ff", 0.95), (0.5, "#b28bff", 0.4), (1, "#b28bff", 0)])}"/>'
          f'<path d="M128 91L134.5 100L128 109L121.5 100Z" fill="{a.lg("gem", [(0, "#d2b8ff"), (1, "#18b9bd")], 0, 0, 1, 1)}" stroke="#ffcf5c" stroke-width="1.6" stroke-linejoin="round"/>'
          '<path d="M128 93.5L131 99L128 99Z" fill="#fff"/>')
    a.add(f'<g fill="#fff3c4"><path d="{sparkle(196, 40, 6)}"/><path d="{sparkle(52, 196, 5)}"/><path d="{sparkle(218, 196, 4)}"/></g>')
    return a


# 20 ------------------------------------------------------------------ founder
def founder():
    a = Av("founder", "The Legendary Founder")
    skin, shade = "#a8714a", "#86532f"
    a.add(f'<rect width="256" height="256" fill="{a.rg("bg", [(0, "#8f72ff"), (0.6, "#4a2bc0"), (1, "#1c0d5e")], cx=0.5, cy=0.44, r=0.75)}"/>')
    w = []
    for k in range(16):
        a0, a1 = math.radians(k * 22.5 - 4), math.radians(k * 22.5 + 7)
        w.append(pts([(128, 112), (128 + 260 * math.cos(a0), 112 + 260 * math.sin(a0)), (128 + 260 * math.cos(a1), 112 + 260 * math.sin(a1))]))
    a.add(f'<path d="{"".join(w)}" fill="#ffcf5c" fill-opacity=".3"/>')
    a.add(f'<circle cx="128" cy="112" r="116" fill="{a.rg("sun", [(0, "#fffbe8", 0.95), (0.35, "#ffe08a", 0.6), (0.7, "#ffcf5c", 0.15), (1, "#ffcf5c", 0)])}"/>'
          '<circle cx="128" cy="112" r="80" fill="none" stroke="#fff0b8" stroke-opacity=".75" stroke-width="2.4"/>'
          '<circle cx="128" cy="112" r="90" fill="none" stroke="#ffe08a" stroke-opacity=".5" stroke-width="2" stroke-dasharray="2 6" stroke-linecap="round"/>')
    a.add('<g fill="#fff">' + "".join(f'<path d="{sparkle(x, y, r)}" fill-opacity="{o}"/>' for x, y, r, o in ((40, 70, 8, 0.9), (216, 64, 7, 0.9), (30, 150, 5, 0.7), (228, 150, 5, 0.7), (72, 28, 4, 0.7), (186, 26, 4.5, 0.7))) + "</g>")
    hair = a.lg("hair", [(0, "#4a2c1e"), (1, "#24140d")])
    back = "M66 224C50 186 54 130 70 94C86 60 170 60 186 94C202 130 206 186 190 224C180 232 168 226 164 214L92 214C88 226 76 232 66 224Z"
    a.add(f'<path d="{back}" fill="{hair}"/><g fill="#24140d">' + "".join(f'<circle cx="{x}" cy="{y}" r="11"/>' for x, y in ((60, 150), (58, 180), (66, 208), (196, 150), (198, 180), (190, 208))) + "</g>"
          '<path d="M66 132C62 152 66 172 62 192M190 132C194 152 190 172 194 192" fill="none" stroke="#6b4430" stroke-width="2.6" stroke-linecap="round"/>')
    a.add(neck(skin, shade))
    cl = a.lg("cloak", [(0, "#6a4ae0"), (1, "#2a1678")])
    a.add(f'<path d="{TORSO}" fill="{cl}"/><path d="M104 194L128 240L152 194Z" fill="#fff4dd"/><path d="M114 200L128 226L142 200" fill="none" stroke="#e8c27a" stroke-width="1.6"/>')
    tr = "M104 194C98 214 94 236 92 256"
    a.add(f'<g fill="none" stroke-linecap="round"><path d="{tr}" stroke="#ffcf5c" stroke-width="6"/><path d="{mirror(tr)}" stroke="#ffcf5c" stroke-width="6"/>'
          f'<path d="{tr}" stroke="#b8800e" stroke-width="1.4"/><path d="{mirror(tr)}" stroke="#b8800e" stroke-width="1.4"/></g>')
    fil = "M50 236C54 224 66 220 72 228C76 234 68 238 66 232M70 214C78 206 90 208 90 216M58 252C62 242 72 240 76 246"
    a.add(f'<g fill="none" stroke="#ffcf5c" stroke-opacity=".85" stroke-width="2" stroke-linecap="round"><path d="{fil}"/><path d="{mirror(fil)}"/></g>')
    mc = "M90 200C84 184 92 170 106 166L120 192Z"
    a.add(f'<path d="{mc}" fill="#4a2bc0" stroke="#ffcf5c" stroke-width="2.2" stroke-linejoin="round"/><path d="{mirror(mc)}" fill="#4a2bc0" stroke="#ffcf5c" stroke-width="2.2" stroke-linejoin="round"/>')
    gold = a.lg("gold", [(0, "#fff3b0"), (0.5, "#ffcf5c"), (1, "#d8960f")], 0, 0, 1, 1)
    a.add(f'<path d="{sparkle(128, 214, 22, 3)}" fill="#fff3c4" fill-opacity=".8"/>'
          f'<path d="{star(128, 215, 13, 5.4)}" fill="{gold}" stroke="#a8740c" stroke-width="1.4" stroke-linejoin="round"/>'
          '<circle cx="128" cy="215.5" r="3.6" fill="#18b9bd" stroke="#fff" stroke-width="1"/>')
    a.add(face(a, skin, shade, eyes="open", brows="soft", mth="smile", brow="#24140d", lashes=True, blush_op=0.28))
    a.add('<path d="M85 140v6M171 140v6" stroke="#ffcf5c" stroke-width="2"/><circle cx="85" cy="149" r="3.2" fill="#18b9bd" stroke="#ffcf5c" stroke-width="1.4"/><circle cx="171" cy="149" r="3.2" fill="#18b9bd" stroke="#ffcf5c" stroke-width="1.4"/>')
    lock = "M128 66C104 64 84 78 82 108C80 130 84 152 92 170C94 144 94 120 102 102C110 88 120 80 128 76Z"
    a.add(f'<path d="{lock}" fill="{hair}"/><path d="{mirror(lock)}" fill="{hair}"/>'
          '<path d="M118 70C104 74 94 86 90 104M138 70C152 74 162 86 166 104" fill="none" stroke="#7a4e36" stroke-width="2.4" stroke-linecap="round"/>')
    a.add(f'<path d="M86 102C104 89 152 89 170 102L170 108C152 95 104 95 86 108Z" fill="{gold}" stroke="#a8740c" stroke-width="1"/>'
          '<circle cx="104" cy="97" r="2.6" fill="#18b9bd"/><circle cx="152" cy="97" r="2.6" fill="#18b9bd"/>'
          f'<circle cx="128" cy="86" r="20" fill="{a.rg("starglow", [(0, "#fffbe0", 0.95), (1, "#ffe08a", 0)])}"/>'
          f'<path d="{star(128, 87, 12, 5)}" fill="{gold}" stroke="#a8740c" stroke-width="1.4" stroke-linejoin="round"/>'
          '<circle cx="128" cy="88" r="3.2" fill="#7c5cff" stroke="#fff" stroke-width="1"/>')
    return a


CHARS4 = [dragon_guardian, cyber_guardian, time_traveller, mastermind, founder]
