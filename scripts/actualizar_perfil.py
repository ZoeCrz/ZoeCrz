"""Generate the Midnight profile SVG from public GitHub data."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from html import escape
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from pathlib import Path
import base64
import json
import math
import os
import re

ROOT = Path(__file__).resolve().parents[1]
USER = os.getenv("PROFILE_USER", "ZoeCrz")
PROFILE = f"https://github.com/{USER}"
API = "https://api.github.com"
FAVORITES = ("archivault-portafolio", "techparts", "techhand", "som-3d")
PALETTE = {"Python": "#65b4fb", "JavaScript": "#f4cf70", "ASP.NET": "#c387ff",
           "C#": "#a387ff", "HTML": "#fb8a79", "CSS": "#a88aff", "Java": "#f3ac6a"}
ACCENTS = ("#83aaff", "#ca93f9", "#70d7ee", "#f6b78a", "#a5adef")
FG = "#eef2ff"
MUTED = "#acbadb"


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
        rows.extend(r for r in batch if not r["private"] and r["name"].lower() != USER.lower())
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


def public_data():
    profile = api(f"/users/{quote(USER, safe='')}")
    repos = repositories()
    languages = Counter()
    def repo_languages(repo):
        path = f"/repos/{quote(USER, safe='')}/{quote(repo['name'], safe='')}/languages"
        return api(path)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for counts in pool.map(repo_languages, (repo for repo in repos if not repo["fork"])):
            languages.update(counts)
    calendar = Calendar()
    calendar.feed(fetch(f"https://github.com/users/{quote(USER, safe='')}/contributions")[0].decode())
    prs = api("/search/issues", q=f"author:{USER} is:pr is:open is:public", per_page=3)
    return profile, repos, languages, calendar.counts(), prs


def avatar_uri(profile):
    url = profile.get("avatar_url", "")
    if urlsplit(url).netloc != "avatars.githubusercontent.com" or not url.startswith("https://"):
        raise ValueError("URL de avatar inesperada")
    data, mime = fetch(url)
    if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        raise ValueError("Formato de avatar no compatible")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def short(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit-1].rstrip() + "…"


def language(repo):
    return repo.get("language") or "Sin clasificar"


def color(name, index=0):
    return PALETTE.get(name, ACCENTS[index % len(ACCENTS)])


class Dashboard:
    def __init__(self):
        self.items = []
        self.y = 24

    def add(self, s):
        self.items.append(s)

    def text(self, x, y, value, *, size=18, fill=FG, weight=500, mono=False, anchor="start"):
        family = "ui-monospace,monospace" if mono else "system-ui,sans-serif"
        self.add(f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" fill="{fill}" font-weight="{weight}" text-anchor="{anchor}">{escape(str(value))}</text>')

    def rect(self, x, y, w, h, *, fill, stroke="none", radius=10):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}"/>')

    def circle(self, x, y, radius, *, fill, stroke="none"):
        self.add(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" fill="{fill}" stroke="{stroke}"/>')

    def section(self, tag, title, height):
        y = self.y
        self.rect(24, y, 852, height, fill="url(#card)", stroke="#414e86", radius=19)
        self.text(48, y+32, tag, size=13, fill="#a4afff", weight=750, mono=True)
        self.text(48, y+65, title, size=27, weight=750)
        self.y += height + 18
        return y

    def poly(self, points, fill, stroke="none", title=None):
        coords = " ".join(f"{x:.2f},{y:.2f}" for x,y in points)
        tip = f"<title>{escape(title)}</title>" if title else ""
        self.add(f'<polygon points="{coords}" fill="{fill}" stroke="{stroke}">{tip}</polygon>')

    def render(self, name):
        height = self.y + 6
        return f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="900" height="{height}" viewBox="0 0 900 {height}" role="img" aria-labelledby="profile-title profile-desc">
<title id="profile-title">Perfil Midnight de {escape(name)}</title>
<desc id="profile-desc">Ficha RPG, misiones, mapa de metro, galaxia, proyectos destacados, inventario tecnológico y ciudad de contribuciones públicas.</desc>
<defs>
  <linearGradient id="midnight" x1="0" x2="1" y1="0" y2="1" gradientUnits="objectBoundingBox">
    <stop stop-color="#080c20"/><stop offset=".52" stop-color="#111a36"/><stop offset="1" stop-color="#1a1230"/>
  </linearGradient>
  <linearGradient id="card" x1="0%" x2="100%" y1="0%" y2="100%">
    <stop stop-color="#182142"/><stop offset=".52" stop-color="#10162d"/><stop offset="1" stop-color="#241936"/>
  </linearGradient>
  <linearGradient id="xp"><stop stop-color="#7ba6ff"/><stop offset="1" stop-color="#d993ff"/></linearGradient>
  <radialGradient id="glow"><stop stop-color="#a78bfa" stop-opacity=".45"/><stop offset="1" stop-color="#a78bfa" stop-opacity="0"/></radialGradient>
  <clipPath id="avatar"><rect x="50" y="113" width="132" height="132" rx="20"/></clipPath>
</defs>
<rect width="900" height="{height}" rx="24" fill="url(#midnight)"/>
{''.join(self.items)}
</svg>
'''


def draw_hero(d, profile, repos, languages, image):
    name = profile.get("name") or profile["login"]
    y = d.section("01  /  CHARACTER SHEET", short(name, 45), 274)
    d.rect(48, y+87, 136, 136, fill="#0b1028", stroke="#8e9cff", radius=22)
    d.add(f'<image x="50" y="{y+89}" width="132" height="132" xlink:href="{image}" clip-path="url(#avatar)" preserveAspectRatio="xMidYMid slice"/>')
    d.text(50, y+246, "@"+profile["login"], size=16, fill="#c1cbff", mono=True)
    top = languages.most_common(1)
    lead = top[0][0] if top else "Exploración"
    d.text(216, y+112, f"Clase principal · {short(lead, 24)}", size=20, weight=700)
    stats = (("Repositorios del mapa", len(repos)), ("Estrellas recibidas", sum(r["stargazers_count"] for r in repos)),
             ("Seguidores", profile["followers"]))
    for i,(label,num) in enumerate(stats):
        x=216+i*204
        d.rect(x, y+130, 184, 63, fill="#0b1229", stroke="#323e69")
        d.text(x+12, y+159, num, size=25, weight=750)
        d.text(x+12, y+180, label, size=13, fill=MUTED)
    # XP is a presentation score, explicitly distinct from GitHub contribution counts.
    xp = len(repos)*10 + sum(r["stargazers_count"] for r in repos)*2 + profile["followers"]
    level, remainder = divmod(xp, 30)
    d.text(216, y+218, f"Nivel {level+1} · XP de portafolio", size=14, fill=MUTED, mono=True)
    d.text(808, y+218, f"{remainder}/30", size=14, fill=MUTED, anchor="end", mono=True)
    d.rect(216, y+232, 592, 12, fill="#303b61", radius=6)
    if remainder:
        d.rect(216, y+232, round(592*remainder/30), 12, fill="url(#xp)", radius=6)


def draw_quests(d, prs):
    items = prs["items"]
    y = d.section("02  /  QUEST LOG", "Misiones abiertas", 126+28*len(items))
    count = prs["total_count"]
    d.text(48, y+98, f"{count} pull requests públicos abiertos" if count else "Sin pull requests públicos abiertos.", fill=MUTED)
    for i,p in enumerate(items):
        d.text(52, y+126+i*28, f"›  {short(p['title'], 84)}", size=16)


def draw_metro(d, repos):
    groups = defaultdict(list)
    for repo in repos:
        groups[language(repo)].append(repo)
    rows = [(lang, group[i:i+3]) for lang,group in sorted(groups.items())
            for i in range(0, len(group), 3)]
    y = d.section("03  /  COMMIT METRO", "Mapa de proyectos", 123+len(rows)*62)
    d.text(48, y+92, "Una línea por lenguaje principal y una estación por repositorio.", size=16, fill=MUTED)
    colors = {lang: color(lang,i) for i,lang in enumerate(sorted(groups))}
    hub_y=y+122+(len(rows)-1)*31
    for i,(lang,group) in enumerate(rows):
        sy=y+121+i*62
        ink=colors[lang]
        d.rect(48, sy-11, 124, 25, fill="#0b132b", stroke=ink, radius=12)
        d.text(110, sy+6, short(lang, 15), fill=ink, size=13, weight=750, anchor="middle", mono=True)
        d.add(f'<path d="M190 {sy+1} H706 Q736 {sy+1} 756 {(sy+1+hub_y)/2} T828 {hub_y}" fill="none" stroke="{ink}" stroke-width="4" stroke-linecap="round"/>')
        for j,repo in enumerate(group):
            x=264+j*180
            d.circle(x, sy+1, 7, fill="#141b36", stroke=ink)
            d.text(x, sy+27, short(repo["name"], 23), size=15, anchor="middle")

    if rows:
        d.circle(828,hub_y,10,fill="#121a36",stroke="#d8dfff")


def draw_galaxy(d, repos):
    y=d.section("04  /  REPO GALAXY", "Galaxia de repositorios", 370)
    d.text(48, y+92, "Cada punto con nombre es un repositorio. Su tamaño crece con las estrellas.", size=16, fill=MUTED)
    cx,cy=450,y+227
    d.circle(cx,cy,107,fill="url(#glow)")
    for i in range(78):
        angle=i*2.399963
        radius=34+i*2.85
        d.circle(cx+radius*math.cos(angle),cy+radius*.47*math.sin(angle),1+(i%4)/4,fill=("#303d6c","#5667a0","#7c73ac")[i%3])
    shown=repos[:12]
    for i,repo in enumerate(shown):
        side=-1 if i%2==0 else 1
        row=i//2
        x=cx+side*(190+38*(row%2))
        sy=y+129+row*37
        ink=color(language(repo),i)
        d.circle(x,sy,5+min(10,math.sqrt(repo["stargazers_count"])),fill=ink)
        d.text(x+side*12,sy+5,short(repo["name"],24),size=14,anchor="start" if side>0 else "end")
    if len(repos)>len(shown):
        d.text(48,y+347,f"+ {len(repos)-len(shown)} repositorios en el mapa",size=14,fill=MUTED)


def draw_projects(d, repos):
    by_name={r["name"]:r for r in repos}
    candidates=[by_name[n] for n in FAVORITES if n in by_name]
    selected=(candidates+[r for r in repos if r not in candidates])[:4]
    y=d.section("05  /  BRIGHTEST STARS", "Proyectos destacados", 92+math.ceil(len(selected)/2)*110)
    for i,r in enumerate(selected):
        x=48+i%2*404
        sy=y+82+i//2*110
        d.rect(x,sy,394,98,fill="#10152c",stroke="#49558b",radius=13)
        d.text(x+15,sy+27,short(r["name"],32),size=18,weight=750,mono=True)
        d.text(x+15,sy+53,short(r.get("description") or "Repositorio público",44),size=15,fill=MUTED)
        d.text(x+15,sy+78,language(r),size=13,fill=color(language(r),i),mono=True)


def draw_inventory(d, languages):
    top=languages.most_common(7)
    total=sum(languages.values())
    y=d.section("06  /  INVENTORY", "Inventario tecnológico", 112+len(top)*33)
    d.text(48,y+91,"Distribución por bytes de código en repositorios propios del mapa.",size=16,fill=MUTED)
    for i,(lang,amount) in enumerate(top):
        sy=y+110+i*33
        ink=color(lang,i)
        for dx,dy in ((0,1),(1,0),(1,1),(1,2),(2,1)):
            d.rect(49+dx*5,sy+dy*5,5,5,fill=ink,radius=0)
        d.text(77,sy+13,short(lang,19),size=15)
        ratio=amount/total
        d.text(255,sy+13,f"{ratio:.1%}",size=14,fill=MUTED,anchor="end",mono=True)
        d.rect(278,sy+3,548,10,fill="#303b61",radius=5)
        d.rect(278,sy+3,max(1,round(548*ratio)),10,fill=ink,radius=5)


def draw_city(d, counts):
    y=d.section("07  /  CONTRIBUTION CITY", "La ciudad que construí este año", 409)
    start,end=min(counts),max(counts)
    active=sum(v>0 for v in counts.values())
    peak=max(counts.values())
    d.text(48,y+94,f"{sum(counts.values())} contribuciones · {active} días activos · máximo diario {peak}",size=16,fill=MUTED)
    d.text(48,y+118,f"{start.isoformat()}  →  {end.isoformat()} · Cada celda es un día.",size=14,fill=MUTED,mono=True)
    first=start-timedelta(days=(start.weekday()+1)%7)
    for day,n in counts.items():
        week,row=divmod((day-first).days,7)
        x=150+week*12.6-row*9.8
        base=y+195+week*2.62+row*8.3
        height=(8+60*math.sqrt(n/max(1,peak))) if n else 0
        top=base-height
        a,b,c,e=(x,top),(x+12.4,top+2.6),(x+2.8,top+10.7),(x-9.6,top+8.1)
        if height:
            d.poly((e,c,(c[0],c[1]+height),(e[0],e[1]+height)),"#4957a9")
            d.poly((b,c,(c[0],c[1]+height),(b[0],b[1]+height)),"#30447f")
        d.poly((a,b,c,e),"#b597ff" if height else "#26395c","#182446",f"{day.isoformat()} · {n} contribuciones")
    d.text(48,y+390,"La altura representa el volumen de actividad visible en el calendario de GitHub.",size=14,fill=MUTED)


def update_readme(repos, profile):
    path=ROOT/"README.md"
    content=path.read_text(encoding="utf-8")
    rows=["<!-- PROJECTS:START -->"]
    for r in repos:
        name=r["name"]
        description=short(r.get("description") or "Repositorio público.",140)
        # Escape Markdown syntax from externally configured public descriptions.
        desc=re.sub(r"([\\`*_[\]<>!&])",r"\\\1",description)
        rows.append(f"- [{name}]({PROFILE}/{quote(name, safe='')}) · {desc}")
    blog=(profile.get("blog") or "").strip()
    if blog and not urlsplit(blog).scheme:
        blog="https://"+blog
    if re.fullmatch(r"https?://[^\s<>()[\]]+",blog) and urlsplit(blog).netloc:
        rows.append(f"\n[Sitio web]({blog})")
    rows.append("<!-- PROJECTS:END -->")
    updated,count=re.subn(r"<!-- PROJECTS:START -->.*?<!-- PROJECTS:END -->",lambda _: "\n".join(rows),content,flags=re.S)
    if count!=1:
        raise ValueError("Marcadores de repositorios incompletos")
    return updated


def main():
    profile,repos,languages,counts,prs=public_data()
    dashboard=Dashboard()
    draw_hero(dashboard,profile,repos,languages,avatar_uri(profile))
    draw_quests(dashboard,prs)
    draw_metro(dashboard,repos)
    draw_galaxy(dashboard,repos)
    draw_projects(dashboard,repos)
    draw_inventory(dashboard,languages)
    draw_city(dashboard,counts)
    y=dashboard.section("08  /  TRANSMISSION","Conecta conmigo",109)
    dashboard.text(48,y+93,f"GitHub · @{profile['login']} · enlaces debajo del panel",size=16,fill=MUTED)
    readme=update_readme(repos,profile)
    (ROOT/"assets/perfil-midnight.svg").write_text(dashboard.render(profile.get("name") or USER),encoding="utf-8")
    (ROOT/"README.md").write_text(readme,encoding="utf-8")
    print(f"Panel generado: {len(repos)} repositorios, {len(counts)} días del calendario público.")


if __name__ == "__main__":
    main()
