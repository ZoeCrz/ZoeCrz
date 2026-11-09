"""Compose the native GitSkins panels over one Midnight background."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlencode
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
    "social": "midnight",
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
            return sorted(rows, key=lambda r: (-r["stargazers_count"], r["name"].lower()))
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
    if section in {"sheet-main", "galaxy", "projects", "stack", "social", "city"}:
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


def exclude_profile_repository(root):
    profile_url = f"https://github.com/{USER}/{USER}".casefold()
    for group in root.iter(f"{{{SVG}}}g"):
        cards = group.findall(f"{{{SVG}}}a")
        excluded = [
            card for card in cards
            if (card.get("href") or card.get(f"{{{XLINK}}}href") or "").rstrip("/").casefold() == profile_url
        ]
        if not excluded:
            continue

        contents = [card.find(f"{{{SVG}}}g") for card in cards]
        slots = [content.get("transform") if content is not None else None for content in contents]
        remaining = [content for card, content in zip(cards, contents) if card not in excluded]
        if all(slots):
            for content, slot in zip(remaining, slots):
                content.set("transform", slot)
        for card in excluded:
            group.remove(card)
        for label in group.findall(f"{{{SVG}}}text"):
            if re.fullmatch(r"\d+ projects?", label.text or ""):
                count = len(remaining)
                label.text = f"{count} {'project' if count == 1 else 'projects'}"


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
        panels = {section: task.result() for section, task in panels.items()}
        stars = sum(repo["stargazers_count"] for repo in repos.result())
        counts = calendar.result()

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
    exclude_profile_repository(panels["projects"])
    board.add_panel(panels["projects"])
    board.heading("Elements")
    board.add_panel(panels["stack"])
    board.heading("The city I built this year")
    board.add_panel(panels["city"])
    board.line("Every building is a day; taller means more contributions.", size=17, center=True)
    board.heading("Transmission")
    board.add_panel(panels["social"])
    board.line(f"{name} · Character Sheet, Commit Metro, Repo Galaxy y Contribution City con GitSkins",
               size=15, center=True)
    rendered = board.render()
    (ROOT / "assets/perfil-midnight.svg").write_text(rendered, encoding="utf-8")
    print(f"Panel Midnight con {len(panels)} diseños nativos y {len(counts)} días de actividad pública.")


if __name__ == "__main__":
    main()
