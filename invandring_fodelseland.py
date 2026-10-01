#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "pandas", "plotly", "duckdb"]
# ///
"""Invandring till Sverige efter födelseland och år (SCB, BE0101J).

Hämtar data från SCB:s PxWeb-API, lagrar dem i en DuckDB-fil och skriver en
HTML-sida med två diagram: staplad yta (antal eller andel) och rangordning per
år (bump chart).

Åren 2000-2024 ligger i tabellen ImmiEmiFod och 2025 i ImmiEmiFodCKM, som
redovisas med kontrollerad slumpmässig avrundning. Båda läses och slås samman.

    uv run invandring_fodelseland.py --refresh

Utan --refresh används de data som redan ligger i DuckDB-filen.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import requests
from plotly.colors import qualitative

BASE = "https://api.scb.se/OV0104/v1/doris/sv/ssd/BE/BE0101/BE0101J"
TABLES = ["ImmiEmiFod", "ImmiEmiFodCKM"]   # 2000-2024 respektive 2025 (CKM)
START_YEAR = 2005

TOP_N = 13                  # antal serier som visas separat, resten blir "Övriga"
RANK_BY = "peak"            # "peak" = största andel ett enskilt år, "total" = summa över perioden
ALWAYS_INCLUDE = ("USA", "Storbritannien", "Ryssland")   # visas alltid separat
INCLUDE_SWEDEN = True       # återinvandrade födda i Sverige
GROUP_EU = True             # slå ihop EU-länderna till en serie
EU_LABEL = "EU utom Sverige"
OTHER_LABEL = "Övriga"
OTHER_COLOR = "#b4b8c2"     # neutral grå, så att restposten inte krockar med palettens färger

DB = Path("data/immigration.duckdb")
OUT = Path("docs/index.html")

# EU-27 enligt dagens medlemskap, tillämpat på alla år. Storbritannien ingår inte
# och redovisas separat, vilket gör att utträdet 2020 inte bryter serien.
EU27 = {
    "Belgien", "Bulgarien", "Cypern", "Danmark", "Estland", "Finland", "Frankrike",
    "Grekland", "Irland", "Italien", "Kroatien", "Lettland", "Litauen", "Luxemburg",
    "Malta", "Nederländerna", "Polen", "Portugal", "Rumänien", "Slovakien",
    "Slovenien", "Spanien", "Sverige", "Tjeckien", "Tyskland", "Ungern", "Österrike",
}
RENAME = {
    "Förenade kungariket (Storbritannien och Nordirland)": "Storbritannien",
    "Förenta staterna (USA)": "USA",
}

TOTAL_PREFIXES = ("totalt", "samtliga")


# --------------------------------------------------------------------------- #
# Hämtning
# --------------------------------------------------------------------------- #

def is_total(text: str) -> bool:
    return text.strip().lower().startswith(TOTAL_PREFIXES)


def fetch_table(table: str) -> pd.DataFrame:
    """Hämtar en tabell och returnerar långt format: year, country, value, source."""
    url = f"{BASE}/{table}"
    meta = requests.get(url, timeout=30).json()
    query, country_var, labels = [], None, {}
    for v in meta["variables"]:
        code, vals, texts = v["code"], v["values"], v["valueTexts"]
        pairs = list(zip(vals, texts))
        if code == "ContentsCode":
            sel = [c for c, t in pairs if t.lower().startswith("invandr")]
        elif v.get("time"):
            sel = [c for c in vals if int(c) >= START_YEAR]
        elif "delseland" in code or "delseland" in v["text"]:
            country_var = code
            labels = dict(pairs)
            sel = [c for c, t in pairs if not is_total(t)]
        else:
            # Könsdimensionen har ett förräknat totalvärde i CKM-tabellen men inte
            # i den äldre. Välj totalen när den finns, annars alla delvärden.
            totals = [c for c, t in pairs if is_total(t)]
            sel = totals[:1] or vals
        if not sel:
            return pd.DataFrame(columns=["year", "country", "value", "source"])
        query.append({"code": code, "selection": {"filter": "item", "values": sel}})

    resp = requests.post(url, json={"query": query, "response": {"format": "json"}}, timeout=120)
    resp.raise_for_status()
    body = resp.json()
    dims = [c["code"] for c in body["columns"] if c["type"] != "c"]
    rows = [dict(zip(dims, d["key"]), value=d["values"][0]) for d in body["data"]]

    df = pd.DataFrame(rows)
    time_col = next(c["code"] for c in body["columns"] if c["type"] == "t")
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)
    df["year"] = df[time_col].astype(int)
    df["country"] = df[country_var].map(labels)
    out = df.groupby(["year", "country"], as_index=False)["value"].sum()
    out["source"] = table
    return out


def refresh(con: duckdb.DuckDBPyConnection) -> None:
    """Hämtar samtliga tabeller från SCB och ersätter innehållet i DuckDB."""
    parts = [fetch_table(t) for t in TABLES]
    raw = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    # Samma år ska inte kunna komma från två tabeller.
    raw = raw.sort_values("source").drop_duplicates(["year", "country"], keep="first")
    con.register("raw", raw)
    con.execute("""
        CREATE OR REPLACE TABLE immigration AS
        SELECT CAST(year AS INTEGER)  AS year,
               CAST(country AS VARCHAR) AS country,
               CAST(value AS BIGINT)  AS value,
               CAST(source AS VARCHAR) AS source
        FROM raw
        ORDER BY year, country
    """)
    con.unregister("raw")
    n, y0, y1 = con.execute("SELECT count(*), min(year), max(year) FROM immigration").fetchone()
    print(f"Hämtade {n} rader från SCB, {y0}–{y1}")


def load(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Läser ut serierna ur DuckDB, med namnbyten och EU-gruppering."""
    eu = sorted(EU27 - {"Sverige"})
    con.execute("CREATE OR REPLACE TEMP TABLE rename_map (src VARCHAR, dst VARCHAR)")
    con.executemany("INSERT INTO rename_map VALUES (?, ?)", list(RENAME.items()))
    return con.execute("""
        WITH named AS (
            SELECT i.year,
                   COALESCE(r.dst, i.country) AS country,
                   i.value
            FROM immigration i
            LEFT JOIN rename_map r ON r.src = i.country
            WHERE $include_sweden OR i.country <> 'Sverige'
        )
        SELECT year,
               CASE WHEN $group_eu AND country IN (SELECT unnest($eu)) THEN $eu_label
                    ELSE country END AS country,
               sum(value) AS value
        FROM named
        GROUP BY 1, 2
        ORDER BY 1, 2
    """, {"include_sweden": INCLUDE_SWEDEN, "group_eu": GROUP_EU,
          "eu": eu, "eu_label": EU_LABEL}).df()


# --------------------------------------------------------------------------- #
# Diagram
# --------------------------------------------------------------------------- #

def select(wide: pd.DataFrame) -> list[str]:
    """Väljer vilka serier som får en egen yta.

    "total" rangordnar efter summan över hela perioden och missar då länder med
    en kort men kraftig topp: Ukraina hamnar på plats 11 trots 28 065 invandrade
    2024. "peak" rangordnar i stället efter största andel ett enskilt år.
    """
    score = wide.div(wide.sum(axis=1), axis=0).max() if RANK_BY == "peak" else wide.sum()
    picked = [c for c in ALWAYS_INCLUDE if c in wide.columns]
    for c in score.sort_values(ascending=False).index:
        if len(picked) >= TOP_N:
            break
        if c not in picked:
            picked.append(c)
    return picked


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def palette(series: list[str]) -> dict[str, str]:
    """En färg per serie, delad av båda diagrammen.

    Plotlys standardpalett har tio färger. Med fler serier än så återanvänds de,
    och det översta och nedersta bandet i ytdiagrammet blir omöjliga att skilja åt.
    """
    colors = qualitative.Dark24
    out = {c: colors[i % len(colors)] for i, c in enumerate(c for c in series if c != OTHER_LABEL)}
    out[OTHER_LABEL] = OTHER_COLOR
    return out


def figures(df: pd.DataFrame) -> tuple[go.Figure, go.Figure, list[str], int, int]:
    wide = df.pivot_table(index="year", columns="country", values="value", aggfunc="sum").fillna(0)
    ranks = wide.rank(axis=1, ascending=False, method="first")

    top = select(wide)
    stacked = wide[top].copy()
    stacked[OTHER_LABEL] = wide.drop(columns=top).sum(axis=1)
    order = stacked.sum().sort_values(ascending=False).index.drop(OTHER_LABEL).tolist()
    stacked = stacked[order + [OTHER_LABEL]]
    color = palette(list(stacked.columns))

    y0, y1 = int(stacked.index.min()), int(stacked.index.max())

    # Staplad yta med växling mellan antal och andel
    area = go.Figure()
    for c in stacked.columns:
        area.add_trace(go.Scatter(
            x=stacked.index, y=stacked[c], name=c, stackgroup="one", mode="lines",
            line=dict(width=0.5, color=color[c]), fillcolor=rgba(color[c], 0.75),
            hovertemplate="%{x}: %{y:,.0f}<extra>" + c + "</extra>"))
    area.update_layout(
        title=dict(text=f"Invandrade till Sverige efter födelseland, {y0}–{y1}",
                   x=0, xanchor="left", y=0.97, yanchor="top"),
        yaxis_title="Antal invandrade", hovermode="x unified", template="plotly_white",
        height=620, margin=dict(t=130, l=70, r=40),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(traceorder="reversed"),
        # Knapparna behåller fasta färger, annars blir texten oläslig när sidan
        # växlar till mörkt läge och den globala textfärgen blir ljus.
        updatemenus=[dict(type="buttons", direction="right", x=0, xanchor="left",
                          y=1.08, yanchor="bottom", bgcolor="#ffffff",
                          bordercolor="#c8ccd4", font=dict(color="#1c1f26"), buttons=[
            dict(label="Antal", method="update",
                 args=[{"groupnorm": ""}, {"yaxis.title.text": "Antal invandrade"}]),
            dict(label="Andel (%)", method="update",
                 args=[{"groupnorm": "percent"}, {"yaxis.title.text": "Andel av invandrade, %"}]),
        ])])

    # Rangordning per år bland samtliga serier
    bump = go.Figure()
    for c in top:
        bump.add_trace(go.Scatter(
            x=ranks.index, y=ranks[c], name=c, mode="lines+markers",
            line=dict(color=color[c]), marker=dict(color=color[c]), customdata=wide[c],
            hovertemplate="%{x}: plats %{y:.0f} (%{customdata:,.0f})<extra>" + c + "</extra>"))
    bump.update_layout(
        title=dict(text="Rangordning per år" + (" (EU räknat som en grupp)" if GROUP_EU else
                                                " bland födelseländer"), x=0, xanchor="left"),
        yaxis=dict(title="Plats", dtick=1, range=[TOP_N + 10.5, 0.5]),
        height=600, margin=dict(t=70, l=70, r=40), template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")

    return area, bump, top, y0, y1


PAGE = """<!doctype html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Invandring till Sverige efter födelseland</title>
<meta name="description" content="Invandrade till Sverige efter födelseland {y0}–{y1}, enligt SCB:s statistikdatabas.">
<style>
  :root {{ color-scheme: light dark; --bg:#ffffff; --fg:#1c1f26; --muted:#5b6170; --rule:#e3e6ec; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#14171c; --fg:#e8eaf0; --muted:#9aa2b1; --rule:#2a2f38; }}
  }}
  html {{ background: var(--bg); }}
  body {{ margin:0 auto; padding:2.5rem 16px 4rem; max-width:1100px; background:var(--bg);
         color:var(--fg); font:16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  h1 {{ font-size:1.75rem; line-height:1.25; margin:0 0 .5rem; }}
  p.lead {{ color:var(--muted); margin:0 0 2rem; max-width:65ch; }}
  .chart {{ margin:0 0 3rem; }}
  footer {{ border-top:1px solid var(--rule); padding-top:1rem; color:var(--muted); font-size:.85rem; max-width:80ch; }}
  footer a {{ color:inherit; }}
</style>
</head>
<body>
<h1>Invandring till Sverige efter födelseland, {y0}–{y1}</h1>
<p class="lead">Antal invandrade per år och födelseland enligt SCB:s statistikdatabas.
Växla mellan antal och andel i det övre diagrammet. De {n} största serierna redovisas
separat, övriga födelseländer summeras.</p>
<div class="chart">{area}</div>
<div class="chart">{bump}</div>
<footer>
<p>Källa: SCB, Statistikdatabasen, Invandrare och utvandrare efter födelseland och kön
(tabellerna ImmiEmiFod och ImmiEmiFodCKM). Uppgifterna för {y1} redovisas med kontrollerad
slumpmässig avrundning, vilket gör att små tal är något osäkra och att delarna inte summerar
exakt till totalen. EU avser dagens 27 medlemsländer utom Sverige, tillämpat på alla år;
Storbritannien redovisas separat för hela perioden. Serien Sverige är återinvandrade personer
födda i Sverige.</p>
<p>Diagrammen genereras av <a href="https://github.com/{repo}">{repo}</a>.</p>
</footer>
<script>
// Plotly har ingen egen koppling till prefers-color-scheme. Diagrammen ritas med
// genomskinlig bakgrund och får text- och rutnätsfärg härifrån i stället.
(function () {{
  var mq = window.matchMedia("(prefers-color-scheme: dark)");
  function apply() {{
    var dark = mq.matches;
    var fg = dark ? "#e8eaf0" : "#1c1f26";
    var grid = dark ? "rgba(232,234,240,0.14)" : "rgba(28,31,38,0.12)";
    document.querySelectorAll(".js-plotly-plot").forEach(function (gd) {{
      Plotly.relayout(gd, {{
        "font.color": fg, "title.font.color": fg, "legend.font.color": fg,
        "xaxis.gridcolor": grid, "yaxis.gridcolor": grid,
        "xaxis.linecolor": grid, "yaxis.linecolor": grid,
        "xaxis.zerolinecolor": grid, "yaxis.zerolinecolor": grid
      }});
    }});
  }}
  apply();
  mq.addEventListener ? mq.addEventListener("change", apply) : mq.addListener(apply);
}})();
</script>
</body>
</html>
"""

REPO = "oluies/se-immigration"


def build(df: pd.DataFrame) -> None:
    area, bump, top, y0, y1 = figures(df)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(PAGE.format(
        y0=y0, y1=y1, n=len(top), repo=REPO,
        area=area.to_html(full_html=False, include_plotlyjs="cdn",
                          config={"displaylogo": False, "responsive": True}),
        bump=bump.to_html(full_html=False, include_plotlyjs=False,
                          config={"displaylogo": False, "responsive": True}),
    ), encoding="utf-8")
    print(f"Skrev {OUT} ({y1 - y0 + 1} år, {len(top)} serier redovisade separat)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true", help="hämta om data från SCB")
    ap.add_argument("--db", type=Path, default=DB, help=f"DuckDB-fil (standard: {DB})")
    args = ap.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(args.db)) as con:
        exists = con.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'immigration'"
        ).fetchone()[0]
        if args.refresh or not exists:
            refresh(con)
        build(load(con))


if __name__ == "__main__":
    main()
