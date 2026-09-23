"""SV Hahnbach live site updater.

Pulls official BFV PDF exports (team schedule + league overview with table),
parses them into the DATA schema used by index.html, injects into the template,
commits and pushes to GitHub Pages.

Safety: if any team's data looks broken, NOTHING is pushed (exit 1).
"""
import datetime
import io
import json
import os
import re
import subprocess
import sys

CLUB_ID = "00ES8GNJRS00001CVV0AG08LVUPGND5I"
BASE = "https://service.bfv.de/rest/pdfexport"

# team -> staffel id (from bfv.de team pages, season 26/27)
STAFFEL = {
    "Herren": "03106RABB4000004VS5489BTVT51B5B5-G",
    "Herren II": "0313EJTJOC000006VS5489BUVSBBVPEU-G",
    "U19 (A-Junioren)": "031ER42KFG000005VS5489BTVUS470OH-G",
    "U17 (B-Junioren)": "031NLRSBD4000004VS5489BTVV1LNS7C-G",
    "U15 (C-Junioren)": "031NM0K9P4000004VS5489BTVV1LNS7C-G",
    "U13 (D-Junioren)": "031NMGEI8C000004VS5489BTVV1LNS7C-G",
    "U13 II (D-Junioren)": "031NMI52CO000004VS5489BTVV1LNS7C-G",
    "U13 III (D-Junioren)": "031NMIKURC000004VS5489BTVV1LNS7C-G",
    "U11 (E-Junioren)": "031N49DFR8000007VS5489BUVT2M8LCU-G",
    "U11 2 (E-Junioren)": "031N49HRRC000007VS5489BUVT2M8LCU-G",
    # Kinderfestival teams (no league table):
    "U9 (F-Junioren)": None,
    "U9 II (F-Junioren)": None,
    "U7 (G-Junioren)": None,
}

JUNK = re.compile(
    r"TORREICHSTES|HÖCHSTER|HÖCHSTE|ZUSAMMENFASSUNG|SPIELFREI|Abgesetzt"
    r"|FAVORITEN|[\ue000-\uf8ff]"
)
GAME_RE = re.compile(
    r"^\s*(\d{3})\s+(\d{2}\.\d{2}\.\d{4})(?:\s+(\d{2}:\d{2}))?\s+(.+?)\s+-\s+(.+?)(?:\s+(\d+:\d+))?\s*$"
)
TABLE_RE = re.compile(
    r"^\s*(\d+)\s+(.+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+:\d+)\s+(-?\d+)\s+(\d+)\b"
)


def parse_spielplan(text):
    """Parse Mannschaftsspielplan PDF text into game dicts."""
    out = []
    for line in text.split("\n"):
        m = GAME_RE.match(line)
        if not m:
            continue
        home, away = m.group(4).strip(), m.group(5).strip()
        if JUNK.search(home) or JUNK.search(away):
            continue
        out.append(
            dict(
                no=m.group(1),
                date=m.group(2),
                time=(m.group(3) or "").strip(),
                home=home,
                away=away,
                score=(m.group(6) or "").strip(),
            )
        )
    return out


def parse_tabelle(text):
    """Parse league table rows from Spieltagsuebersicht PDF text."""
    rows = []
    for line in text.split("\n"):
        m = TABLE_RE.match(line)
        if not m:
            continue
        rows.append(
            dict(
                pos=m.group(1),
                name=m.group(2).strip(),
                sp=m.group(3),
                g=m.group(4),
                u=m.group(5),
                v=m.group(6),
                tor=m.group(7),
                diff=m.group(8),
                pkt=m.group(9),
            )
        )
    return rows


def fetch(url):
    r = subprocess.run(
        ["curl", "-sfL", "-A", "Mozilla/5.0", url], capture_output=True, timeout=60
    )
    if r.returncode != 0:
        raise RuntimeError(f"fetch failed ({r.returncode}): {url}")
    return r.stdout


def pdf_text(raw):
    from pypdf import PdfReader

    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(raw)).pages)


def _d(s):
    dd, mm, yy = map(int, s.split("."))
    return datetime.date(yy, mm, dd)


def build_data(today=None):
    today = today or datetime.date.today()
    meta_all = json.load(open("meta.json", encoding="utf-8"))
    data = {}
    for team, staffel in STAFFEL.items():
        meta = meta_all[team]
        if staffel is None:
            data[team] = dict(meta=meta, games=[], results=[], standings=[])
            continue
        games = parse_spielplan(
            pdf_text(fetch(f"{BASE}/Spielplan?staffel={staffel}&id={CLUB_ID}"))
        )
        mine = [g for g in games if "Hahnbach" in g["home"] or "Hahnbach" in g["away"]]
        upcoming = [
            g for g in mine if _d(g["date"]) >= today and not g["score"]
        ][:6]
        results = [g for g in mine if g["score"] and _d(g["date"]) <= today]
        standings = parse_tabelle(
            pdf_text(fetch(f"{BASE}/spieltagsuebersicht?staffel={staffel}"))
        )
        # keep meta.platz/tv in sync with the fresh table
        for r in standings:
            if "Hahnbach" in r["name"]:
                meta = dict(meta)
                meta["platz"] = int(r["pos"])
                meta["tv"] = r["tor"]
                break
        data[team] = dict(meta=meta, games=upcoming, results=results, standings=standings)
    return data


def sanity_check(data):
    """Refuse to deploy suspicious data."""
    problems = []
    for team, d in data.items():
        if STAFFEL[team] is None:
            continue
        if not d["games"]:
            problems.append(f"{team}: no upcoming games")
        if len(d["standings"]) < 4:
            problems.append(f"{team}: table has <4 rows ({len(d['standings'])})")
        if not any("Hahnbach" in r["name"] for r in d["standings"]):
            problems.append(f"{team}: Hahnbach missing from table")
    return problems


def render(data):
    badges = json.load(open("badges.json", encoding="utf-8"))
    html = open("index.html", encoding="utf-8").read()
    html = re.sub(
        r"/\*__DATA__\*/\{.*?\};(?=\nconst BADGES)",
        "/*__DATA__*/" + json.dumps(data, ensure_ascii=False) + ";",
        html, count=1, flags=re.S,
    )
    html = re.sub(
        r"/\*__BADGES__\*/\{.*?\};",
        "/*__BADGES__*/" + json.dumps(badges, ensure_ascii=False) + ";",
        html, count=1, flags=re.S,
    )
    html = re.sub(
        r'(<span id="stand">)[^<]*(</span>)',
        r"\g<1>" + datetime.date.today().strftime("%d.%m.%Y") + r"\g<2>",
        html,
    )
    return html


def push_via_api(html):
    """Commit index.html through the GitHub API (for GitHub Actions runs)."""
    import base64
    import urllib.request

    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ.get("GITHUB_REPO", "CR1983gh/sv-hahnbach")
    api = f"https://api.github.com/repos/{repo}/contents/index.html"

    def req(url, data=None, method=None):
        r = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        })
        return json.load(urllib.request.urlopen(r))

    sha = req(api, method="GET")["sha"]
    body = json.dumps({
        "message": f"data: auto-update {datetime.date.today()}",
        "content": base64.b64encode(html.encode("utf-8")).decode(),
        "sha": sha,
        "branch": "main",
    }).encode()
    req(api, data=body, method="PUT")


def main():
    try:
        data = build_data()
    except Exception as e:  # noqa: BLE001
        print(f"ERROR building data: {e}", file=sys.stderr)
        return 1
    problems = sanity_check(data)
    if problems:
        print("SANITY CHECK FAILED, not deploying:", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 1
    html = render(data)
    if os.environ.get("GITHUB_TOKEN"):
        # GitHub Actions: push via API (no git credentials needed)
        current = open("index.html", encoding="utf-8").read()
        if html == current:
            print("No changes today.")
            return 0
        push_via_api(html)
        print(f"Deployed update for {datetime.date.today()}.")
        return 0
    open("index.html", "w", encoding="utf-8").write(html)
    subprocess.run(["git", "add", "index.html"], check=True)
    r = subprocess.run(
        ["git", "commit", "-m", f"data: auto-update {datetime.date.today()}"],
        capture_output=True,
        text=True,
    )
    if "nothing to commit" in (r.stdout + r.stderr):
        print("No changes today.")
        return 0
    subprocess.run(["git", "push", "origin", "main"], check=True)
    print(f"Deployed update for {datetime.date.today()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
