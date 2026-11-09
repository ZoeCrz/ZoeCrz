"""Compose the native GitSkins panels over one Midnight background."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
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


def project_card(template, repo, languages):
    card = deepcopy(template)
    ns = f"{{{SVG}}}"
    name = repo["name"]
    name = name if len(name) <= 27 else name[:26] + "…"
    text = {node.get("y"): node for node in card.findall(ns + "text")}
    text["19.5"].find(ns + "tspan").tail = f" {name}"
    text["58"].text = name + " "
    descriptions = wrap(repo.get("description") or "Sin descripción", width=40, max_lines=2, placeholder="…")
    for y, line in zip(("79", "95"), (descriptions + ["", ""])[:2]):
        if y not in text:
            text[y] = element(card, "text", x=18, y=y, font_family=MONO, font_size=11.5, fill="#a5b4fc")
        text[y].text = line
    language = shorten(repo.get("language") or "Sin lenguaje", width=24, placeholder="…")
    chip_width = 18 + 6 * len(language)
    chip = next(rect for rect in card.findall(ns + "rect") if rect.get("y") == "112")
    chip.set("width", str(chip_width))
    text["124.5"].set("x", str(18 + chip_width / 2))
    text["124.5"].text = language
    metadata = text["150"].findall(ns + "tspan")
    metadata[1].text = "updated " + repo["updated_at"][:10]
    metadata[0].tail = f" {repo['stargazers_count']:,}"

    colors = {"Python": "#3572a5", "Java": "#b07219", "HTML": "#e34c26", "JavaScript": "#f1e05a",
              "ASP.NET": "#9400ff", "C#": "#178600", "CSS": "#663399", "C++": "#f34b7d"}
    palette = ("#818cf8", "#c084fc", "#67e8f9", "#fbbf24", "#fb7185")
    for ring in card.findall(ns + "circle"):
        if ring.get("stroke-dasharray"):
            card.remove(ring)
    total = sum(languages.values())
    offset = 0
    for i, (lang, count) in enumerate(sorted(languages.items(), key=lambda item: -item[1])[:5]):
        length = count * 157.08 / total
        ring = element(card, "circle", cx=348, cy=92, r=25, fill="none",
                       stroke=colors.get(lang, palette[i]), stroke_width=8,
                       stroke_dasharray=f"{length:.3f} {157.08 - length:.3f}",
                       stroke_dashoffset=f"{-offset:.3f}", transform="rotate(-90 348 92)")
        element(ring, "animate", attributeName="stroke-dasharray", begin=f"{.7 + i * .15:.2f}s",
                dur=".6s", fill="freeze", to=ring.get("stroke-dasharray")).set("from", "0 157.08")
        offset += length
    text["96"].text = f"{max(languages.values()) / total:.0%}" if total else "—"
    card.remove(text["96"])
    card.append(text["96"])
    return card


def projects_panel(root, featured, languages):
    ns = f"{{{SVG}}}"
    group = next(g for g in root.iter(ns + "g") if g.get("id", "").startswith("gs-projects-"))
    cards = {a.get("href", "").casefold(): a.find(ns + "g") for a in group.findall(ns + "a")}
    template = next((card for card in cards.values()
                     if card is not None and {"19.5", "58", "124.5", "150", "96"} <=
                     {node.get("y") for node in card.findall(ns + "text")}), None)
    x, y, width, old_height = map(float, root.attrib["viewBox"].split())
    height = 96 + 178 * max(1, (len(featured) + 1) // 2)
    root.set("viewBox", f"{x:g} {y:g} {width:g} {height}")
    for link in group.findall(ns + "a"):
        group.remove(link)
    for rect in group.findall(ns + "rect"):
        rect.set("height", f"{float(rect.attrib['height']) + height - old_height:g}")
    for orb in group.findall(ns + "ellipse"):
        orb.set("cy", f"{float(orb.attrib['cy']) * height / old_height:g}")
    for label in group.findall(ns + "text"):
        if re.fullmatch(r"\d+ projects?", label.text or ""):
            count = len(featured)
            label.text = f"{count} {'project' if count == 1 else 'projects'}"
        elif label.text == "gitskins.com":
            label.set("y", str(height - 11))
    for index, repo in enumerate(featured):
        card = cards.get(repo["html_url"].casefold())
        if card is None:
            if template is None:
                raise ValueError("GitSkins no devolvió una tarjeta de proyectos reutilizable")
            card = project_card(template, repo, languages[repo["name"]])
        else:
            stars = next(text for text in card.findall(ns + "text") if text.get("y") == "150")
            stars.find(ns + "tspan").tail = f" {repo['stargazers_count']:,}"
        card.set("transform", f"translate({28 + 410 * (index % 2)},{88 + 178 * (index // 2)})")
        card.find(ns + "animate").set("begin", f"{.25 + index * .15:.2f}s")
        group.append(card)
    if not featured:
        element(group, "text", x=30, y=120, fill="#a5b4fc", font_family=MONO,
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


def write_transmission(panel, links, credit):
    folder = ROOT / "assets/transmission"
    folder.mkdir(exist_ok=True)
    ns = f"{{{SVG}}}"
    badges = []
    active = set()
    for first in range(0, len(links), 3):
        row = links[first:first + 3]
        art = deepcopy(panel)
        group = next(g for g in art.iter(ns + "g") if g.get("id", "").startswith("gs-social-"))
        chip = next(g for g in group.findall(ns + "g") if g.get("class") == "aura-chip")
        group.remove(chip)
        for index, (label, url) in enumerate(row):
            button = deepcopy(chip)
            button.set("transform", f"translate({860 * ((index + .5) / len(row) - .5):g} 0)")
            icon, provider, handle = button.findall(ns + "text")
            icon.text = label[:1].upper()
            provider.text = shorten(label, width=15, placeholder="…")
            parsed = urlsplit(url)
            account = parsed.path.strip("/").split("/")[-1]
            value = "@" + account if account else parsed.hostname.removeprefix("www.")
            handle.text = value if len(value) <= 15 else value[:14] + "…"
            group.append(button)
        footer = Midnight()
        footer.y = 0
        footer.add_panel(art)
        if first + len(row) == len(links):
            footer.line(credit, size=15, center=True)
        footer.render()
        height = footer.root.attrib["height"]
        width = footer.width / len(row)
        row_badges = []
        for index, (label, url) in enumerate(row):
            path = folder / f"link-{first + index + 1}.svg"
            active.add(path)
            # Adjacent viewport crops preserve the native background while each link remains clickable.
            footer.root.attrib.update(width=f"{width:.8f}", viewBox=f"{index * width:.8f} 0 {width:.8f} {height}")
            svg = ET.tostring(footer.root, encoding="unicode")
            path.write_text("\n".join(line.rstrip() for line in svg.splitlines()) + "\n", encoding="utf-8")
            row_badges.append(f'<a href="{escape(url, quote=True)}"><img src="assets/transmission/{path.name}" '
                              f'alt="{escape(label, quote=True)}" width="{100 // len(row)}%"></a>')
        badges.append("".join(row_badges))
    for stale in set(folder.glob("link-*.svg")) - active:
        stale.unlink()
    readme = (
        '<!-- Generado por scripts/actualizar_perfil.py -->\n\n'
        '<p align="center">\n'
        f'<a href="https://github.com/{USER}?tab=repositories"><img src="assets/perfil-midnight.svg" '
        'alt="Portafolio Midnight" width="100%"></a><br>\n'
        + "<br>\n".join(badges) + "\n</p>\n"
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

    existing_cards = {a.get("href", "").casefold() for a in panels["projects"].iter(f"{{{SVG}}}a")}
    with ThreadPoolExecutor(max_workers=6) as pool:
        tasks = {
            repo["name"]: pool.submit(api, f"/repos/{quote(USER, safe='')}/{quote(repo['name'], safe='')}/languages")
            for repo in featured if repo["html_url"].casefold() not in existing_cards
        }
        languages = {repo_name: task.result() for repo_name, task in tasks.items()}

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
    projects_panel(panels["projects"], featured, languages)
    board.add_panel(panels["projects"])
    board.heading("Elements")
    board.add_panel(panels["stack"])
    board.heading("The city I built this year")
    board.add_panel(panels["city"])
    board.line("Every building is a day; taller means more contributions.", size=17, center=True)
    board.heading("Transmission")
    rendered = board.render()
    credit = f"{name} · Character Sheet, Commit Metro, Repo Galaxy y Contribution City con GitSkins"
    write_transmission(panels["social"], links, credit)
    (ROOT / "assets/perfil-midnight.svg").write_text(rendered, encoding="utf-8")
    print(f"Panel Midnight con {len(featured)} proyectos, {len(links)} enlaces y {len(counts)} días de actividad pública.")


if __name__ == "__main__":
    main()
