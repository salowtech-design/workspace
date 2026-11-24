#!/usr/bin/env python3
"""Generate static/css/iam-utilities.css.

The IAM console templates use utility class names (Tailwind-style) but no Tailwind CSS is
shipped and the CSP forbids runtime CDNs. This script scans templates/accounts/admin and
writes a small, self-hosted stylesheet that implements exactly the classes in use, mapped to
the Salowtech brand tokens (light surface, ink text, maroon accent). Re-run after adding
classes:  python scripts/gen_iam_utilities.py
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "static/css/iam-utilities.css"
BP = {"sm": 640, "md": 768, "lg": 1024}

# hue -> (50 light tint, 200 border tint, 600 solid, 700 strong text)
HUES = {
    "cyan": ("#E6F4F7", "#9AD1DC", "#0E7490", "#155E75"),
    "indigo": ("#ECEEFB", "#B4BBEA", "#4F46E5", "#3730A3"),
    "emerald": ("#E7F5EC", "#9FD5B4", "#15803D", "#166534"),
    "amber": ("#FDF3DC", "#E8C47A", "#B45309", "#92400E"),
    "rose": ("#FBE9EA", "#E6A7AB", "#BE123C", "#9F1239"),
    "purple": ("#F2EAFB", "#CBB0EC", "#7E22CE", "#6B21A8"),
}
INK, MUTED, FAINT = "#2B231A", "#5F5246", "#8A7C6E"
SURFACE, INSET, SAND, LINE = "#FFFFFF", "#FAF7F0", "#F5EFEB", "#E4D9CF"
GRAYS = {
    "100": INK,
    "200": INK,
    "300": INK,
    "400": MUTED,
    "500": MUTED,
    "600": FAINT,
    "700": LINE,
    "800": LINE,
    "900": SURFACE,
    "950": SURFACE,
}
SIZES = {
    "text-xs": ".75rem/1rem",
    "text-sm": ".875rem/1.25rem",
    "text-base": "1rem/1.5rem",
    "text-lg": "1.125rem/1.75rem",
    "text-xl": "1.25rem/1.75rem",
    "text-2xl": "1.5rem/2rem",
    "text-3xl": "1.875rem/2.25rem",
}
MAXW = {"xs": "20rem", "md": "28rem", "lg": "32rem", "2xl": "42rem", "7xl": "80rem"}
SHADOW = "0 1px 3px rgba(43,35,26,.12),0 1px 2px rgba(43,35,26,.08)"


def sp(v):
    return f"{float(v) * 0.25:g}rem"


def esc(c):
    return re.sub(r"([^a-zA-Z0-9_-])", r"\\\1", c)


def gray(shade, alpha=None):
    base = GRAYS.get(shade, MUTED)
    return base


def color(name, alpha=None, kind="bg"):
    """Map a Tailwind colour token (e.g. cyan-400, gray-800, white) to a brand colour."""
    if name == "white":
        return "#FFFFFF" if kind == "bg" else INK
    if name == "transparent":
        return "transparent"
    if name == "black":
        return INSET if kind == "bg" else INK
    h, _, shade = name.partition("-")
    if h == "gray":
        if kind == "bg":
            c = {"900": SURFACE, "950": SURFACE, "800": SAND, "700": SAND}.get(shade, SAND)
        elif kind == "border":
            c = LINE
        else:
            c = GRAYS.get(shade, MUTED)
    elif h in HUES:
        t50, t200, t600, t700 = HUES[h]
        n = int(shade)
        if kind == "bg":
            c = t50 if n >= 800 else (t200 if n <= 300 else t600)
            if n >= 900 and alpha is not None and alpha >= 60:
                c = t50
        elif kind == "border":
            c = t200 if n >= 600 else t600
        else:
            c = t700 if n <= 500 else t600
    else:
        return None
    return c


def parse_color(rest, kind):
    m = re.match(r"^(.*?)(?:/(\d+))?$", rest)
    name, alpha = m.group(1), int(m.group(2)) if m.group(2) else None
    if name.startswith("[#"):
        return (
            name[1:-1].upper()
            if kind == "bg" and False
            else (INSET if name == "[#0B0F19]" else SURFACE)
        )
    return color(name, alpha, kind)


def base_rule(c):
    """Return declaration string for a base utility, or None."""
    simple = {
        "block": "display:block",
        "inline": "display:inline",
        "inline-block": "display:inline-block",
        "flex": "display:flex",
        "inline-flex": "display:inline-flex",
        "grid": "display:grid",
        "hidden": "display:none",
        "flex-col": "flex-direction:column",
        "flex-wrap": "flex-wrap:wrap",
        "flex-1": "flex:1 1 0%",
        "flex-shrink-0": "flex-shrink:0",
        "items-center": "align-items:center",
        "items-start": "align-items:flex-start",
        "justify-between": "justify-content:space-between",
        "justify-center": "justify-content:center",
        "relative": "position:relative",
        "absolute": "position:absolute",
        "overflow-hidden": "overflow:hidden",
        "overflow-x-auto": "overflow-x:auto",
        "truncate": "overflow:hidden;text-overflow:ellipsis;white-space:nowrap",
        "whitespace-nowrap": "white-space:nowrap",
        "select-all": "user-select:all",
        "uppercase": "text-transform:uppercase",
        "italic": "font-style:italic",
        "underline": "text-decoration:underline",
        "hover:underline": "text-decoration:underline",
        "font-mono": "font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace",
        "font-medium": "font-weight:500",
        "font-semibold": "font-weight:600",
        "font-bold": "font-weight:700",
        "text-left": "text-align:left",
        "text-center": "text-align:center",
        "text-right": "text-align:right",
        "leading-relaxed": "line-height:1.625",
        "tracking-tight": "letter-spacing:-.025em",
        "tracking-wider": "letter-spacing:.05em",
        "tracking-widest": "letter-spacing:.1em",
        "mx-auto": "margin-left:auto;margin-right:auto",
        "min-h-screen": "min-height:100vh",
        "w-full": "width:100%",
        "min-w-[200px]": "min-width:200px",
        "rounded": "border-radius:.25rem",
        "rounded-lg": "border-radius:.5rem",
        "rounded-xl": "border-radius:.75rem",
        "rounded-2xl": "border-radius:1rem",
        "rounded-full": "border-radius:9999px",
        "rounded-t-lg": "border-radius:.5rem .5rem 0 0",
        "border": "border-width:1px;border-style:solid",
        "border-2": "border-width:2px;border-style:solid",
        "border-b": "border-bottom-width:1px;border-bottom-style:solid",
        "border-t": "border-top-width:1px;border-top-style:solid",
        "border-l-2": "border-left-width:2px;border-left-style:solid",
        "border-b-2": "border-bottom-width:2px;border-bottom-style:solid",
        "shadow-lg": f"box-shadow:{SHADOW}",
        "shadow-xl": f"box-shadow:{SHADOW}",
        "shadow-2xl": f"box-shadow:{SHADOW}",
        "transition-all": "transition:all .15s",
        "transition-colors": "transition:color,background-color,border-color .15s",
        "animate-pulse": "animation:iam-pulse 2s cubic-bezier(.4,0,.6,1) infinite",
        "bg-gradient-to-r": f"background-image:linear-gradient(to right,{HUES['cyan'][2]},{HUES['indigo'][2]})",
        "divide-y": None,
        "focus:outline-none": "outline:none",
        "focus:ring-1": None,
        "focus:ring-2": None,
        "focus:ring-offset-2": None,
        "placeholder-gray-500": None,
        "md:flex-row": "flex-direction:row",
        "md:items-center": "align-items:center",
        "md:justify-between": "justify-content:space-between",
        "md:grid": None,
    }
    if c in simple:
        return simple[c]
    m = re.match(r"^(p|px|py|pt|pb|pl|pr|m|mt|mb|ml|mr|mx|my)-([\d.]+)$", c)
    if m:
        side = {
            "": [""],
            "x": ["-left", "-right"],
            "y": ["-top", "-bottom"],
            "t": ["-top"],
            "b": ["-bottom"],
            "l": ["-left"],
            "r": ["-right"],
        }[m.group(1)[1:]]
        prop = "padding" if m.group(1)[0] == "p" else "margin"
        return ";".join(f"{prop}{s}:{sp(m.group(2))}" for s in side)
    m = re.match(r"^(w|h)-([\d.]+)$", c)
    if m:
        return f"{'width' if m.group(1) == 'w' else 'height'}:{sp(m.group(2))}"
    m = re.match(r"^gap-(\d+)$", c)
    if m:
        return f"gap:{sp(m.group(1))}"
    m = re.match(r"^(?:md:|sm:|lg:)?grid-cols-(\d)$", c)
    if m:
        return f"grid-template-columns:repeat({m.group(1)},minmax(0,1fr))"
    m = re.match(r"^md:col-span-(\d)$", c)
    if m:
        return f"grid-column:span {m.group(1)} / span {m.group(1)}"
    m = re.match(r"^max-w-(\w+)$", c)
    if m and m.group(1) in MAXW:
        return f"max-width:{MAXW[m.group(1)]}"
    if c in SIZES:
        s, lh = SIZES[c].split("/")
        return f"font-size:{s};line-height:{lh}"
    m = re.match(r"^text-\[(\d+)px\]$", c)
    if m:
        return f"font-size:{m.group(1)}px;line-height:1.3"
    m = re.match(r"^(top|left|right|bottom)-([\d.]+)$", c)
    if m:
        return f"{m.group(1)}:{sp(m.group(2))}"
    m = re.match(r"^(lg|sm):px-(\d+)$", c)
    if m:
        return f"padding-left:{sp(m.group(2))};padding-right:{sp(m.group(2))}"
    m = re.match(r"^text-(.+)$", c)
    if m:
        col = parse_color(m.group(1), "text")
        return f"color:{col}" if col else None
    m = re.match(r"^bg-(.+)$", c)
    if m and not m.group(1).startswith("gradient"):
        col = parse_color(m.group(1), "bg")
        return f"background-color:{col}" if col else None
    m = re.match(r"^border-(.+)$", c)
    if m:
        col = parse_color(m.group(1), "border")
        return f"border-color:{col}" if col else None
    m = re.match(r"^(?:from|to)-(.+)$", c)
    if m:
        return None
    m = re.match(r"^shadow-(.+)$", c)
    if m:
        return f"box-shadow:{SHADOW}"
    return None


def selector_for(cls):
    """Return (selector, declarations, media) for a class with optional variants."""
    parts = cls.split(":")
    base = parts[-1]
    variants = parts[:-1]
    if base.startswith("[") or not base:
        return None
    key = ":".join(variants + [base])
    decl = (
        base_rule(key)
        if variants
        and key
        in (
            "md:flex-row",
            "md:items-center",
            "md:justify-between",
            "hover:underline",
            "focus:outline-none",
        )
        else None
    )
    if decl is None:
        decl = base_rule(base)
    if decl is None:
        return None
    if base.startswith(("space-x-", "space-y-")):
        return None
    sel = "." + esc(cls)
    media = None
    for v in variants:
        if v == "hover":
            sel += ":hover"
        elif v == "focus":
            sel += ":focus"
        elif v == "group-hover":
            sel = ".group:hover " + sel
        elif v in BP:
            media = BP[v]
    return sel, decl, media


def main():
    classes = set()
    for f in (ROOT / "templates/accounts/admin").rglob("*.html"):
        for m in re.finditer(r'class="([^"]*)"', f.read_text(encoding="utf-8")):
            classes.update(t for t in m.group(1).split() if "{" not in t and "%" not in t)
    base, medias = [], {}
    for cls in sorted(classes):
        m = re.match(r"^space-([xy])-(\d+)$", cls)
        if m:
            prop = "margin-left" if m.group(1) == "x" else "margin-top"
            base.append(f".{esc(cls)}>:not([hidden])~:not([hidden]){{{prop}:{sp(m.group(2))}}}")
            continue
        if cls == "divide-y":
            base.append(
                f".divide-y>:not([hidden])~:not([hidden]){{border-top-width:1px;border-style:solid;border-color:{LINE}}}"
            )
            continue
        if cls.startswith("divide-gray"):
            base.append(f".{esc(cls)}>:not([hidden])~:not([hidden]){{border-color:{LINE}}}")
            continue
        if cls.startswith("focus:ring"):
            if cls in ("focus:ring-1", "focus:ring-2"):
                base.append(f".{esc(cls)}:focus{{box-shadow:0 0 0 {cls[-1]}px #7D3024}}")
            continue
        if (
            cls.startswith("from-")
            or cls.startswith("to-")
            or cls.startswith("hover:from-")
            or cls.startswith("hover:to-")
        ):
            continue
        r = selector_for(cls)
        if not r:
            continue
        sel, decl, media = r
        rule = f"{sel}{{{decl}}}"
        (medias.setdefault(media, []) if media else base).append(rule)
    # solid-coloured controls keep white text
    solid = []
    for h, (_, _, _t600, _) in HUES.items():
        for n in (500, 600, 700):
            solid.append(f".bg-{h}-{n}.text-white,.hover\\:bg-{h}-{n}.text-white:hover")
    solid.append(".bg-gradient-to-r.text-white")
    out = [
        "/* GENERATED by scripts/gen_iam_utilities.py - do not edit by hand. */",
        "@keyframes iam-pulse{50%{opacity:.4}}",
        "*,*::before,*::after{box-sizing:border-box}",
        "input,select,textarea{font:inherit;color:inherit}",
        f"input::placeholder,textarea::placeholder{{color:{FAINT}}}",
        "table{border-collapse:collapse}svg{max-width:100%}",
    ]
    out += base
    out += [
        "/* IAM shell: dark sidebar needs light link text (the default link colour is dark brown). */",
        ".sidebar.s-ce369df9 .nav-item a{color:#E8DFD6}",
        ".sidebar.s-ce369df9 .nav-item a:hover,.sidebar.s-ce369df9 .nav-item.active a{background:rgba(255,255,255,.1);color:#FFFFFF}",
        ".sidebar.s-ce369df9 .s-e2e546fd{color:#F5EFEB}",
        ".sidebar.s-ce369df9 .s-f8246472{color:#FFFFFF}",
        ".sidebar.s-ce369df9 .s-3564cde4{color:#CBBFB3}",
        ".main-content svg{max-width:100%}",
        "/* Overflow containment: flex/grid children may shrink, wide tables scroll inside their card. */",
        ".main-content,.main-content [class*=grid-cols] > *,.main-content .space-y-6 > *,.main-content [class*=max-w-] > *{min-width:0}",
        ".main-content .overflow-x-auto{overflow-x:auto;max-width:100%}",
        ".main-content table{max-width:100%}",
        ".main-content .card,.main-content .table-container,.main-content [class^=s-]{min-width:0;max-width:100%}",
        ".main-content .table-container{overflow-x:auto}",
        ".main-content [role=tablist]{display:flex;overflow-x:auto;max-width:100%;-webkit-overflow-scrolling:touch}",
        ".main-content pre,.main-content code{overflow-wrap:anywhere;white-space:pre-wrap}",
        ".main-content .flex.overflow-x-auto{flex-wrap:nowrap}",
    ]
    out.append(",".join(solid) + "{color:#FFFFFF}")
    out.append(".bg-gradient-to-r{background-image:linear-gradient(to right,#7D3024,#5E2219)}")
    for bp in sorted(medias):
        out.append(f"@media (min-width:{bp}px){{" + "".join(medias[bp]) + "}")
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(
        f"{len(classes)} classes scanned, wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size} bytes)"
    )


if __name__ == "__main__":
    main()
