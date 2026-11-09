"""Compose the native GitSkins panels over one Midnight background."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from textwrap import shorten, wrap
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
import json
import os
import re

ROOT = Path(__file__).resolve().parents[1]
USER = os.getenv("PROFILE_USER", "ZoeCrz")
API = "https://api.github.com"
GITSKINS = "https://www.gitskins.com/api/section/"
SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
ET.register_namespace("", SVG)
ET.register_namespace("xlink", XLINK)
THEMES = {
    "sheet-main": None,
    "sheet-quests": None,
    "sheet-inventory": None,
    "metro-map": None,
    "galaxy": "midnight",
    "projects": "midnight",
    "stack": "midnight",
    "city": "aurora",
}
TAGS = set("""
svg defs style title desc g rect circle ellipse line path polygon polyline text
tspan linearGradient radialGradient stop clipPath mask a animate animateTransform
animateMotion mpath
""".split())
ANIMATED_ATTRIBUTES = {"gradientTransform", "opacity", "stroke-dasharray",
                       "stroke-opacity", "r", "transform", "height"}


def fetch(url, *, api=False):
    headers = {"User-Agent": "Midnight GitHub profile"}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        token = os.getenv("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=25) as response:
        data = response.read(4_000_001)
        if len(data) > 4_000_000:
            raise ValueError("Respuesta demasiado grande")
        return data, response.headers.get_content_type()


def api(path, **params):
    query = "?" + urlencode(params) if params else ""
    return json.loads(fetch(API + path + query, api=True)[0])


def repositories():
    rows = []
    for page in range(1, 101):
        batch = api(f"/users/{quote(USER, safe='')}/repos", per_page=100, page=page, type="owner")
        rows.extend(r for r in batch if not r["private"])
        if len(batch) < 100:
            return rows
    raise ValueError("Paginación incompleta de repositorios")


class Calendar(HTMLParser):
    """Associate public day cells with their contribution tooltips."""
    def __init__(self):
        super().__init__()
        self.days = {}
        self.tooltips = {}
        self.target = None
        self.fragments = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "td" and "ContributionCalendar-day" in attrs.get("class", "") and attrs.get("data-date"):
            self.days[attrs["id"]] = date.fromisoformat(attrs["data-date"])
        if tag == "tool-tip":
            self.target = attrs.get("for")
            self.fragments = []

    def handle_data(self, data):
        if self.target:
            self.fragments.append(data)

    def handle_endtag(self, tag):
        if tag == "tool-tip" and self.target:
            self.tooltips[self.target] = " ".join("".join(self.fragments).split())
            self.target = None

    def counts(self):
        values = {}
        for key, day in self.days.items():
            match = re.match(r"^(No|[0-9][0-9,]*) contributions? on\b", self.tooltips.get(key, ""))
            if not match:
                raise ValueError(f"No se pudo leer el calendario público del {day}")
            number = match[1]
            values[day] = 0 if number == "No" else int(number.replace(",", ""))
        if len(values) < 350:
            raise ValueError("Calendario público incompleto")
        return dict(sorted(values.items()))


def element(parent, tag, **attrs):
    return ET.SubElement(parent, f"{{{SVG}}}{tag}", {k.replace("_", "-"): str(v) for k, v in attrs.items()})


def safe_css(value):
    # Only internal SVG paint/animation references are needed by these panels.
    local_url = r"url\(\s*['\"]?#[\w.-]+['\"]?\s*\)"
    scrubbed = re.sub(local_url, "", value, flags=re.I)
    return not re.search(r"@import|url\s*\(|javascript:|expression\s*\(|[\\<>]", scrubbed, re.I)


def panel(section, avatar):
    params = {"username": USER.lower(), "mode": "dark"}
    if THEMES[section]:
        params["theme"] = THEMES[section]
    if section in {"sheet-main", "galaxy", "projects", "stack", "city"}:
        params["avatar"] = avatar
    payload, mime = fetch(GITSKINS + section + "?" + urlencode(params))
    if mime != "image/svg+xml" or re.search(rb"<!DOCTYPE|<!ENTITY", payload, re.I):
        raise ValueError(f"Respuesta SVG inesperada para {section}")
    root = ET.fromstring(payload)
    if root.tag != f"{{{SVG}}}svg":
        raise ValueError(f"Panel inválido: {section}")
    for node in root.iter():
        name = node.tag.removeprefix(f"{{{SVG}}}")
        if name not in TAGS:
            raise ValueError(f"Elemento SVG no admitido en {section}: {name}")
        if name == "style" and not safe_css(node.text or ""):
            raise ValueError(f"CSS externo o inválido en {section}")
        for key, val in node.attrib.items():
            if key.startswith("{") and key != f"{{{XLINK}}}href":
                raise ValueError(f"Atributo con espacio de nombres no admitido en {section}")
            attr = key.removeprefix(f"{{{XLINK}}}")
            if attr.lower().startswith("on") or attr.lower() in {"src", "srcset", "xml:base"}:
                raise ValueError(f"Atributo no admitido en {section}: {attr}")
            if attr == "href":
                repo_link = name == "a" and re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+", val)
                if not (re.fullmatch(r"#[\w.-]+", val) or repo_link):
                    raise ValueError(f"Enlace no admitido en {section}")
            elif not safe_css(val):
                raise ValueError(f"Valor de atributo no admitido en {section}: {attr}")
        if name in {"animate", "animateTransform"} and node.get("attributeName") not in ANIMATED_ATTRIBUTES:
            raise ValueError(f"Animación no admitida en {section}")
    return root


def project_card(parent, repo, index):
    x = 28 + 410 * (index % 2)
    y = 76 + 166 * (index // 2)
    card = element(parent, "g", transform=f"translate({x} {y})")
    element(card, "rect", width=394, height=150, rx=13,
            fill="#08080c", fill_opacity=".55", stroke="#818cf8", stroke_opacity=".65")
    element(card, "line", x1=0, x2=394, y1=29, y2=29, stroke="#818cf8", stroke_opacity=".45")
    element(card, "circle", cx=378, cy=15, r=4, fill="#3fb950")
    name = repo["name"]
    label = name if len(name) <= 31 else name[:30] + "…"
    element(card, "text", x=16, y=19, fill="#a5b4fc", font_family=MONO, font_size=11).text = f"• {label}"
    element(card, "text", x=18, y=56, fill="#e0e7ff", font_family=MONO,
            font_size=17, font_weight=700).text = label
    description = repo.get("description") or "Sin descripción"
    for row, line in enumerate(wrap(description, width=48, max_lines=2, placeholder="…")):
        element(card, "text", x=18, y=78 + row * 16, fill="#a5b4fc", font_family=MONO, font_size=12).text = line
    language = shorten(repo.get("language") or "Sin lenguaje", width=32, placeholder="…")
    element(card, "rect", x=18, y=110, width=20 + 7 * len(language), height=22, rx=11,
            fill="#818cf8", fill_opacity=".16", stroke="#818cf8", stroke_opacity=".45")
    element(card, "text", x=28, y=125, fill="#b4bcff", font_family=MONO, font_size=11).text = language
    element(card, "text", x=374, y=125, text_anchor="end", fill="#d7b9fc", font_family=MONO,
            font_size=13).text = f"★ {repo['stargazers_count']:,}"


def projects_panel(root, featured):
    # Keep GitSkins' backdrop, frame and header while expanding its four-card grid.
    group = next(g for g in root.iter(f"{{{SVG}}}g") if g.get("id", "").startswith("gs-projects-"))
    x, y, width, old_height = map(float, root.attrib["viewBox"].split())
    height = 92 + 166 * max(1, (len(featured) + 1) // 2)
    root.set("viewBox", f"{x:g} {y:g} {width:g} {height}")
    for card in group.findall(f"{{{SVG}}}a"):
        group.remove(card)
    for rect in group.findall(f"{{{SVG}}}rect"):
        rect.set("height", f"{float(rect.attrib['height']) + height - old_height:g}")
    for orb in group.findall(f"{{{SVG}}}ellipse"):
        orb.set("cy", f"{float(orb.attrib['cy']) * height / old_height:g}")
    for label in group.findall(f"{{{SVG}}}text"):
        if re.fullmatch(r"\d+ projects?", label.text or ""):
            count = len(featured)
            label.text = f"{count} {'project' if count == 1 else 'projects'}"
        elif label.text == "gitskins.com":
            label.set("y", str(height - 11))
    for index, repo in enumerate(featured):
        project_card(group, repo, index)
    if not featured:
        element(group, "text", x=30, y=100, fill="#a5b4fc", font_family=MONO,
                font_size=14).text = "Todavía no hay proyectos públicos"


def transmission_links(profile, socials):
    labels = {"linkedin": "LinkedIn", "instagram": "Instagram", "youtube": "YouTube",
              "github": "GitHub", "mastodon": "Mastodon", "bluesky": "Bluesky"}
    candidates = [{"provider": "github", "url": profile["html_url"]}, *socials]
    if profile.get("blog"):
        blog = profile["blog"].strip()
        candidates.append({"provider": "website", "url": blog if "://" in blog else f"https://{blog}"})
    seen = set()
    for account in candidates:
        url = account["url"].strip()
        try:
            parsed = urlsplit(url)
            valid = parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username
        except ValueError:
            continue
        key = url.rstrip("/")
        if not valid or key in seen or any(ch.isspace() for ch in url):
            continue
        seen.add(key)
        provider = account["provider"].lower()
        label = labels.get(provider) or parsed.hostname.removeprefix("www.")
        yield label, url


def write_transmission(links):
    folder = ROOT / "assets/transmission"
    folder.mkdir(exist_ok=True)
    badges = []
    active = set()
    for index, (label, url) in enumerate(links, start=1):
        path = folder / f"link-{index}.svg"
        active.add(path)
        svg = ET.Element(f"{{{SVG}}}svg", {"viewBox": "0 0 210 56", "width": "210", "height": "56"})
        defs = element(svg, "defs")
        gradient = element(defs, "linearGradient", id="midnight", x2="100%", y2="100%")
        for offset, color in (("0%", "#0b1020"), ("62%", "#222344"), ("100%", "#0c1020")):
            element(gradient, "stop", offset=offset, stop_color=color)
        element(svg, "rect", x=1, y=1, width=208, height=54, rx=27, fill="url(#midnight)", stroke="#818cf8")
        element(svg, "circle", cx=27, cy=28, r=14, fill="#818cf8", fill_opacity=".24")
        element(svg, "text", x=27, y=33, text_anchor="middle", font_family=MONO,
                font_size=15, fill="#e0e7ff").text = label[:1].upper()
        element(svg, "text", x=50, y=33, font_family=MONO, font_size=14,
                font_weight=700, fill="#e0e7ff").text = shorten(label, width=15, placeholder="…")
        element(svg, "text", x=181, y=33, font_family=MONO, font_size=16, fill="#a5b4fc").text = "↗"
        path.write_text(ET.tostring(svg, encoding="unicode") + "\n", encoding="utf-8")
        badges.append(f'<a href="{escape(url, quote=True)}"><img src="assets/transmission/{path.name}" '
                      f'alt="{escape(label, quote=True)}" width="210" height="56"></a>')
    for stale in set(folder.glob("link-*.svg")) - active:
        stale.unlink()
    readme = (
        '<!-- Generado por scripts/actualizar_perfil.py -->\n\n'
        f'[![Portafolio Midnight](assets/perfil-midnight.svg)](https://github.com/{USER}?tab=repositories)\n\n'
        '<p align="center">\n  ' + "\n  ".join(badges) + "\n</p>\n"
    )
    (ROOT / "README.md").write_text(readme, encoding="utf-8")


class Midnight:
    width = 944
    content_width = 880

    def __init__(self):
        self.root = ET.Element(f"{{{SVG}}}svg", {"role": "img", "aria-labelledby": "portfolio-title portfolio-description"})
        element(self.root, "title", id="portfolio-title").text = f"Portafolio Midnight de {USER}"
        element(self.root, "desc", id="portfolio-description").text = (
            "Paneles originales de GitSkins: ficha RPG, misiones, inventario, mapa de metro, "
            "galaxia, proyectos, tecnologías, transmisión y ciudad de contribuciones."
        )
        defs = element(self.root, "defs")
        background = element(defs, "linearGradient", id="portfolio-midnight",
                             x1="0%", x2="80%", y1="0%", y2="100%")
        for offset, color in (("0%", "#0b1020"), ("53%", "#11162b"), ("100%", "#0c1020")):
            element(background, "stop", offset=offset, stop_color=color)
        self.backdrop = element(self.root, "rect", width=self.width, rx=22, fill="url(#portfolio-midnight)")
        self.y = 34
        self.sections = 0

    def line(self, value, *, size=20, color="#c7d0e8", weight=500, center=False):
        element(self.root, "text", x=self.width//2 if center else 32, y=self.y+size,
                fill=color, font_size=size, font_weight=weight,
                font_family="system-ui, -apple-system, sans-serif",
                text_anchor="middle" if center else "start").text = value
        self.y += size + 15

    def heading(self, value):
        self.y += 12
        self.line(value, size=34, color="#f0f6fc", weight=730)
        element(self.root, "line", x1=32, y1=self.y-5, x2=912, y2=self.y-5, stroke="#3b4567")
        self.y += 7

    def add_panel(self, root):
        x, y, width, height = map(float, root.attrib["viewBox"].split())
        if min(width, height) <= 0 or max(width, height) > 4000:
            raise ValueError("Dimensiones de panel inesperadas")
        scale = self.content_width / width
        scaled = round(height * scale)
        self.sections += 1
        clip_id = f"portfolio-panel-{self.sections}"
        clip = element(self.root.find(f"{{{SVG}}}defs"), "clipPath", id=clip_id)
        element(clip, "rect", x=32, y=self.y, width=self.content_width, height=scaled)
        frame = element(self.root, "g", clip_path=f"url(#{clip_id})",
                        aria_label=root.get("aria-label", "Panel de GitSkins"))
        content = element(frame, "g", transform=f"translate(32 {self.y}) scale({scale:.9f}) translate({-x} {-y})")
        content.extend(list(root))
        self.y += scaled + 16

    def render(self):
        height = self.y + 15
        self.root.attrib.update(width=str(self.width), height=str(height), viewBox=f"0 0 {self.width} {height}")
        self.backdrop.set("height", str(height))
        # Check ID uniqueness because CSS and animation references share one SVG document.
        ids = [node.get("id") for node in self.root.iter() if node.get("id")]
        if len(ids) != len(set(ids)):
            raise ValueError("Identificadores de panel duplicados")
        svg = ET.tostring(self.root, encoding="unicode")
        return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


def longest_streak(counts):
    longest = streak = 0
    previous = None
    for day, number in counts.items():
        if previous != day - timedelta(days=1):
            streak = 0
        streak = streak + 1 if number else 0
        longest = max(longest, streak)
        previous = day
    return longest


def public_calendar():
    calendar = Calendar()
    calendar.feed(fetch(f"https://github.com/users/{quote(USER, safe='')}/contributions")[0].decode())
    return calendar.counts()


def main():
    profile = api(f"/users/{quote(USER, safe='')}")
    name = profile.get("name") or profile["login"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        panels = {section: pool.submit(panel, section, profile["avatar_url"]) for section in THEMES}
        repos = pool.submit(repositories)
        calendar = pool.submit(public_calendar)
        socials = pool.submit(api, f"/users/{quote(USER, safe='')}/social_accounts", per_page=100)
        panels = {section: task.result() for section, task in panels.items()}
        repos = repos.result()
        counts = calendar.result()
        links = list(transmission_links(profile, socials.result()))

    stars = sum(repo["stargazers_count"] for repo in repos)
    featured = sorted(
        (repo for repo in repos if repo["name"].casefold() != USER.casefold() and not repo["fork"]),
        key=lambda repo: (repo["created_at"], repo["stargazers_count"], repo["name"].casefold()),
        reverse=True,
    )[:6]

    board = Midnight()
    board.line(name, size=44, weight=750, color="#f0f6fc", center=True)
    board.add_panel(panels["sheet-main"])
    board.add_panel(panels["sheet-quests"])
    board.add_panel(panels["sheet-inventory"])
    board.add_panel(panels["metro-map"])
    board.add_panel(panels["galaxy"])
    board.line(f"{profile['public_repos']} repos públicos · {stars} estrellas · {sum(counts.values())} contribuciones en el último año",
               size=18, center=True)
    board.line(f"Racha más larga: {longest_streak(counts)} días · {profile['followers']} seguidores",
               size=17, center=True)
    board.heading("Brightest stars")
    projects_panel(panels["projects"], featured)
    board.add_panel(panels["projects"])
    board.heading("Elements")
    board.add_panel(panels["stack"])
    board.heading("The city I built this year")
    board.add_panel(panels["city"])
    board.line("Every building is a day; taller means more contributions.", size=17, center=True)
    board.line(f"{name} · Character Sheet, Commit Metro, Repo Galaxy y Contribution City con GitSkins",
               size=15, center=True)
    board.heading("Transmission")
    rendered = board.render()
    write_transmission(links)
    (ROOT / "assets/perfil-midnight.svg").write_text(rendered, encoding="utf-8")
    print(f"Panel Midnight con {len(featured)} proyectos, {len(links)} enlaces y {len(counts)} días de actividad pública.")


if __name__ == "__main__":
    main()
