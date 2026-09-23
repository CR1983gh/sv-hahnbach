import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from update_site import parse_spielplan, parse_tabelle

SPIELPLAN_SAMPLE = """SP.-NR DATUM UHRZEIT PARTIE ERGEBNIS
083 20.09.2026 15:15 SV Hahnbach - SV Etzenricht 1:0
089 27.09.2026 15:15 FC Wernberg - SV Hahnbach
004 20.09.2026 SPIELFREI - (SG) SV Hahnbach
002 16.09.2026 17:30 (SG) SV Hahnbach II Abgesetzt TuS Rosenberg II
011 20.09.2026 11:30 TSV Kareth-Lappersdorf 2 - (SG) SV Hahnbach 2:1
"""

TABELLE_SAMPLE = """PL. VEREIN SP. S U N VERH. DIFF. PKT. TREND
1 SpVgg SV Weiden II 11 7 3 1 32:13 19 24 #
7 SV Hahnbach 11 4 4 3 15:14 1 16 #
16 FC Weiden-Ost 11 2 2 7 19:32 -13 8 #
"""


def test_parse_spielplan_basic():
    games = parse_spielplan(SPIELPLAN_SAMPLE)
    assert len(games) == 3, f"expected 3 (junk filtered), got {len(games)}: {games}"
    g0 = games[0]
    assert g0["home"] == "SV Hahnbach"
    assert g0["away"] == "SV Etzenricht"
    assert g0["score"] == "1:0"
    assert g0["date"] == "20.09.2026"


def test_parse_spielplan_upcoming_no_score():
    games = parse_spielplan(SPIELPLAN_SAMPLE)
    g1 = games[1]
    assert g1["home"] == "FC Wernberg"
    assert g1["score"] == ""


def test_junk_filtered():
    games = parse_spielplan(SPIELPLAN_SAMPLE)
    text = " ".join(g["home"] + g["away"] for g in games)
    assert "SPIELFREI" not in text
    assert "Abgesetzt" not in text


def test_hyphenated_team_not_split():
    games = parse_spielplan(SPIELPLAN_SAMPLE)
    hy = [g for g in games if "Kareth" in g["home"]]
    assert len(hy) == 1
    assert hy[0]["home"] == "TSV Kareth-Lappersdorf 2"
    assert hy[0]["away"] == "(SG) SV Hahnbach"
    assert hy[0]["score"] == "2:1"


def test_parse_tabelle():
    rows = parse_tabelle(TABELLE_SAMPLE)
    assert len(rows) == 3
    assert rows[0]["name"] == "SpVgg SV Weiden II"
    assert rows[0]["pkt"] == "24"
    hah = [r for r in rows if "Hahnbach" in r["name"]][0]
    assert hah["pos"] == "7"
    assert hah["tor"] == "15:14"
    assert hah["diff"] == "1"
