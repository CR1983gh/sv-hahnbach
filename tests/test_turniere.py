from update_site import parse_turniere, clean_team

SAMPLE = """
VEREINSSPIELPLAN
SV HAHNBACH
TYP            KLASSE                DATUM        UHRZEIT                                                       PARTIE
                                                                              D-Junioren

HM            Hallen-             21.11.2026       14:30                 FSV Gärbershof (FB, DJ)                   -            (SG) SV Hahnbach (FB, DJ)
          Vereinsturnier
                                                                          Krötenseehalle Sulzbach-Rosenberg,Dieselstr. 29,92237 Sulzbach-Rosenberg

HM            Hallen-             21.11.2026       15:10               (SG) SV Hahnbach (FB, DJ)                   -             TuS Rosenberg II (FB, DJ)
          Vereinsturnier
                                                                          Krötenseehalle Sulzbach-Rosenberg,Dieselstr. 29,92237 Sulzbach-Rosenberg
                                                                              C-Junioren

HM            Hallen-             13.12.2026       10:14                     (SG) SV Hahnbach                      -                     FC Amberg
          Bezirksturnier
                                                                                Stadthalle Maxhütte-Haidhof,Nordgaustr. 1,93142 Maxhütte-Haidhof
"""


def test_parse_turniere_basic():
    ts = parse_turniere(SAMPLE)
    assert len(ts) == 3
    d = [t for t in ts if t["kat"] == "D-Junioren"]
    assert len(d) == 2
    assert d[0]["date"] == "21.11.2026" and d[0]["time"] == "14:30"
    assert d[0]["home"] == "FSV Gärbershof"
    assert d[0]["away"] == "(SG) SV Hahnbach"
    assert d[0]["tname"] == "Vereinsturnier"
    assert d[0]["venue"].startswith("Krötenseehalle")
    assert d[1]["home"] == "(SG) SV Hahnbach" and d[1]["away"] == "TuS Rosenberg II"


def test_parse_turniere_kat_and_type():
    ts = parse_turniere(SAMPLE)
    c = [t for t in ts if t["kat"] == "C-Junioren"]
    assert len(c) == 1 and c[0]["tname"] == "Bezirksturnier"


def test_clean_team_fb_tags():
    assert clean_team("FSV Gärbershof (FB, DJ)") == "FSV Gärbershof"
    assert clean_team("(SG) SV Hahnbach (FB, CJ)") == "(SG) SV Hahnbach"
    assert clean_team("TuS Rosenberg (FB)") == "TuS Rosenberg"
    assert clean_team("(SG) SV Hahnbach") == "(SG) SV Hahnbach"
