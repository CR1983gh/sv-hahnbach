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


def _ics_unesc(s):
    """ICS-Wert-Escapes aufloesen (\\, \\; \\: \\N \\n)."""
    return re.sub(r"\\([,;:\\nN])", r"\1", s)


def parse_ics(raw):
    """Parse BFV icsexport 'Aktuelle Spiele' (next games) into game dicts.
    These turn up here BEFORE the PDF Spielplan shows rescheduled games."""
    from zoneinfo import ZoneInfo
    text = raw.decode("utf-8", errors="ignore")
    # unfold folded lines (\r\n + space)
    text = text.replace("\r\n ", "").replace("\n ", "")
    out = []
    ev = {}
    for line in text.splitlines():
        line = line.strip()
        if line == "BEGIN:VEVENT":
            ev = {}
        elif line == "END:VEVENT":
            summ = ev.get("SUMMARY")
            if summ and ev.get("DTSTART"):
                # SUMMARY: "Home-Away, Meisterschaften, Staffel ..." — erst am
                # unescapeten Komma teilen, dann am Trenn-Bindestrich
                first = summ.split("\\,")[0].split(",")[0]
                cands = []
                for m in re.finditer(r"-", first):
                    home, away = first[:m.start()], first[m.end():]
                    if "Hahnbach" in home or "Hahnbach" in away:
                        hb_len = len(home) if "Hahnbach" in home else len(away)
                        cands.append((hb_len, home, away))
                if cands:
                    _, home, away = min(cands)
                    if "SPIELFREI" not in home and "SPIELFREI" not in away:
                        try:
                            z = datetime.datetime.strptime(ev["DTSTART"], "%Y%m%dT%H%M%SZ")
                            loc = z.replace(tzinfo=datetime.timezone.utc).astimezone(ZoneInfo("Europe/Berlin"))
                        except ValueError:
                            loc = None
                        if loc:
                            out.append(dict(
                                no="", date=loc.strftime("%d.%m.%Y"),
                                time=loc.strftime("%H:%M"),
                                home=_ics_unesc(home).strip(), away=_ics_unesc(away).strip(), score="",
                            ))
            ev = {}
        elif ":" in line and line.split(":", 1)[0] in ("SUMMARY", "DTSTART"):
            ev[line.split(":", 1)[0]] = line.split(":", 1)[1]
    return out


def build_data(today=None):
    today = today or datetime.date.today()
    meta_all = json.load(open("meta.json", encoding="utf-8"))
    data = {}
    for team, staffel in STAFFEL.items():
        meta = meta_all[team]
        meta = dict(meta)
        meta["staffel"] = staffel
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
        # ICS "Aktuelle Spiele": enthaelt kurzfristig angesetzte/verlegte Spiele,
        # die im PDF-Spielplan noch fehlen (z.B. U13 II am 14.11.)
        try:
            ics_games = parse_ics(fetch(f"{BASE.replace('pdfexport','icsexport')}/Spielplan?staffel={staffel}&id={CLUB_ID}"))
        except Exception:  # noqa: BLE001
            ics_games = []
        _norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()  # noqa: E731
        for g in ics_games:
            if _d(g["date"]) < today or not ("Hahnbach" in g["home"] or "Hahnbach" in g["away"]):
                continue
            dup = any(
                o["date"] == g["date"]
                and (
                    (_norm(o["home"]), _norm(o["away"])) == (_norm(g["home"]), _norm(g["away"]))
                    or (_norm(o["home"]) in _norm(g["away"]) and _norm(g["home"]) in _norm(o["away"]))
                )
                for o in upcoming
            )
            if not dup:
                upcoming.append(g)
        upcoming.sort(key=lambda g: (_d(g["date"]), g["time"]))
        upcoming = upcoming[:6]
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


# ---- Liga-Pyramide: live vom BFV (next.bfv.de API, gleiche Quelle wie bfv.de) ----
BFV_API = "https://next.bfv.de/bfv-api/v1/public"
AMBERG_AREA = "00ES8GNJPC00000FVV0AG08LVUPGND5I"
TEAMTYPE = {
    "U19 (A-Junioren)": "360",
    "U17 (B-Junioren)": "361",
    "U15 (C-Junioren)": "362",
    "U13 (D-Junioren)": "363",
}
# Levels, die ueberkreislich sind und daher immer gezeigt werden (alle Vereine
# des Landkreises koennen dort landen); untere Levels nur Kreis Amberg/Weiden
SUPRA_LEVELS = {"1", "17", "520", "387", "1107"}


def current_season_code(today=None):
    today = today or datetime.date.today()
    y = today.year + (1 if today.month >= 7 else 0)
    return f"{(y - 1) % 100:02d}{y % 100:02d}"


def _collapse_groups(items, level_name):
    """Mehrere Gruppen-Staffeln, die sich nur durch Gruppe A-H unterscheiden,
    zu einer Zeile zusammenfassen (sonst 8 Reihen Bundesliga Muell)."""
    if len(items) <= 4:
        return items
    import collections
    base = collections.defaultdict(list)
    for name, sid in items:
        key = re.sub(r"\b[A-Z]\b", "#", name)
        base[key].append((name, sid))
    out = []
    for key, group in base.items():
        if len(group) > 4:
            out.append((f"{level_name} (alle Staffeln)", None))
        else:
            out.extend(group)
    return out


def build_pyramide(today=None):
    season = current_season_code(today)
    pyr = {}
    for label, ma in TEAMTYPE.items():
        d = json.loads(fetch(f"{BFV_API}/getBfvWamTwo/wettkampftyp/1/saison/{season}/mannschaftsart/{ma}").decode())
        rows = []
        st = 0
        for lvl in d["leagueLevels"]:
            lid = lvl["id"]
            t = json.loads(fetch(
                f"{BFV_API}/getBfvWamThree/wettkampftyp/1/saison/{season}/spielklasse/{lid}/mannschaftsart/{ma}"
            ).decode())
            comps = t.get("competitions", {}).get(lid, {})
            items = []
            for aid, svs in comps.items():
                for sv in svs:
                    name = sv["value"]
                    keep = aid == AMBERG_AREA or "Oberpfalz" in name or lid in SUPRA_LEVELS
                    if keep:
                        items.append((name, sv["id"]))
            if not items:
                continue  # Level existiert in dieser Saison (fuer uns) nicht, z.B. U13-BOL Oberpfalz
            if lid == "1107":
                # User-Wunsch: erst die komplette Bundesliga-NLZ-Foerderliga,
                # darunter die komplette BFV-NLZ-Foerderliga; hoechster Jahrgang
                # zuerst; die vielen BFV-NLZ-Regionen pro Jahrgang zu einer
                # Zeile buendeln
                import collections
                by_age = collections.defaultdict(lambda: {'buli': [], 'bfv': []})
                for name, sid in items:
                    m = re.match(r"U\s*(\d+)", name)
                    age = m.group(1) if m else "99"
                    (by_age[age]['buli'] if "BuLi" in name else by_age[age]['bfv']).append((name, sid))
                ages = sorted(by_age, key=lambda a: -int(a) if a.isdigit() else 0)
                st += 1
                for age in ages:
                    for name, sid in sorted(by_age[age]['buli']):
                        rows.append({"st": str(st), "name": name, "id": sid, "ebene": "Bundes"})
                st += 1
                for age in ages:
                    names = [n for n, _ in by_age[age]['bfv']]
                    lbl = f"Förderliga BFV-NLZ U{age} (alle Regionen)" if len(names) > 1 else (names[0] if names else "")
                    if lbl:
                        rows.append({"st": str(st), "name": lbl, "id": None, "ebene": "Verband"})
                continue
            items = _collapse_groups(items, lvl["value"])
            # BuLi-NLZ Foerderliga immer vor den BFV-NLZ-Staffeln
            items.sort(key=lambda x: 0 if "BuLi" in x[0] else 1)
            st += 1
            ebene = {"1": "Bundes", "17": "Region", "520": "Verband", "387": "Verband",
                     "1107": "Verband", "389": "Bezirk", "390": "Bezirk"}.get(lid, "Kreis")
            for name, sid in items:
                rows.append({"st": str(st), "name": name, "id": sid, "ebene": ebene})
        if rows:
            pyr[label] = rows
    return pyr


def sanity_check(data):
    """Refuse to deploy suspicious data."""
    problems = []
    for team, d in data.items():
        if STAFFEL[team] is None:
            continue
        if not d["games"]:
            # Saison beendet ist kein Datenfehler: leerer Spielplan erlaubt,
            # wenn bereits Ergebnisse vorliegen (dann fehlen keine Zukunftsspiele,
            # sondern die Runde ist durch). Ohne Ergebnisse = echter Fehler.
            if not d["results"]:
                problems.append(f"{team}: no upcoming games")
        if len(d["standings"]) < 4:
            problems.append(f"{team}: table has <4 rows ({len(d['standings'])})")
        if not any("Hahnbach" in r["name"] for r in d["standings"]):
            problems.append(f"{team}: Hahnbach missing from table")
    return problems


def render(data, pyramide=None):
    badges = json.load(open("badges.json", encoding="utf-8"))
    html = open("index.html", encoding="utf-8").read()
    if pyramide is None:
        try:
            pyramide = json.load(open("ligapyramide.json", encoding="utf-8"))
        except Exception:
            pyramide = None
    if pyramide:
        html = re.sub(
            r"/\*__PYRAMIDE__\*/\{.*?\};(?=\nconst TEAM_JG)",
            "/*__PYRAMIDE__*/" + json.dumps(pyramide, ensure_ascii=False) + ";",
            html, count=1, flags=re.S,
        )
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
    pyramide = None
    try:
        pyramide = build_pyramide()
        json.dump(pyramide, open("ligapyramide.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"Pyramide built: {sum(len(v) for v in pyramide.values())} Ligen.")
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: pyramide build failed, keeping previous: {e}", file=sys.stderr)
    html = render(data, pyramide)
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
