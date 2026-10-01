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
from plotly.subplots import make_subplots

BASE = "https://api.scb.se/OV0104/v1/doris/sv/ssd/BE/BE0101/BE0101J"
TABLES = ["ImmiEmiFod", "ImmiEmiFodCKM"]   # 2000-2024 respektive 2025 (CKM)
START_YEAR = 2005

# Ytdiagrammet bär åtta färger, vilket är hela den validerade paletten. Fler
# färgklasser än så går inte att skilja åt med nedsatt färgseende, oavsett palett,
# så resten av länderna får varsin panel i småmultiplarna i stället, där färg inte
# behöver särskilja någonting.
AREA_N = 8                  # antal färgade ytor, resten summeras som "Övriga"
PANEL_N = 13                # antal länder som får en egen panel i småmultiplarna
PANEL_COLS = 4
RANK_BY = "peak"            # "peak" = största andel ett enskilt år, "total" = summa över perioden
ALWAYS_INCLUDE = ("USA", "Storbritannien", "Ryssland")   # får alltid en egen panel
INCLUDE_SWEDEN = True       # återinvandrade födda i Sverige
GROUP_EU = True             # slå ihop EU-länderna till en serie
EU_LABEL = "EU utom Sverige"
OTHER_LABEL = "Övriga"

# Kategorifärger ur dataviz-riktlinjernas validerade palett, i den ordning som
# klarar kontrollen av intilliggande par. Stegen för mörkt läge är valda för den
# mörka ytan, inte uträknade ur de ljusa.
#   node scripts/validate_palette.js "<hex,...>" --mode light --surface "#fcfcfb"
# Sämsta grannpar: guld/akvamarin ΔE 9,1 för protanopi och rosa/guld ΔE 19,6 för
# normalseende. Höj inte AREA_N över 8 utan att byta palett.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7",
          "#e34948"]

# Sidan ritas alltid mot ljus yta. Ett mörkt läge kräver egna, separat validerade
# steg för varje färg och ett eget bläck i varje ruta — inklusive Plotlys hover,
# som har vit botten och annars ger vit text på vitt.
T = dict(surface="#fcfcfb", page="#f9f9f7", ink="#0b0b0b", second="#52514e",
         muted="#898781", grid="#e1e0d9", axis="#c3c2b7",
         series=SERIES, other="#cfcec6")

DB = Path("data/immigration.duckdb")
REGION_CSV = Path("data/regions.csv")
OUT = Path("docs/index.html")

# Län och SCB:s egna födelseregioner ligger i andra tabeller än födelselandet.
COUNTY_TABLE = "FlyttFodReg"      # utrikes inflyttningar per län, 2002-2024
COUNTY_CONTENTS = "000001EB"      # "Utrikes inflyttningar"
REGION_TABLE = "ImmiCKM"          # invandringar per län och födelseland, 2025
# SCB:s födelseregioner som tillsammans täcker alla invandrade utan överlapp.
# "okänt födelseland" utelämnas: det är samma personer som "okänd födelseregion".
SCB_REGIONS = ["010", "NEXS", "EUexNord", "EurExEUNor", "AFR", "ASI", "NSAOS", "OFR"]

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


def post(table: str, query: list[dict]) -> pd.DataFrame:
    """Kör en fråga mot en tabell och returnerar dimensionerna plus value."""
    resp = requests.post(f"{BASE}/{table}",
                         json={"query": query, "response": {"format": "json"}}, timeout=180)
    resp.raise_for_status()
    body = resp.json()
    dims = [c["code"] for c in body["columns"] if c["type"] != "c"]
    rows = [dict(zip(dims, d["key"]), value=d["values"][0]) for d in body["data"]]
    df = pd.DataFrame(rows)
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)
    return df


def values_of(table: str, var: str) -> dict[str, str]:
    meta = requests.get(f"{BASE}/{table}", timeout=30).json()
    v = next(x for x in meta["variables"] if x["code"] == var)
    return dict(zip(v["values"], v["valueTexts"]))


def fetch_counties() -> pd.DataFrame:
    """Utrikes inflyttningar per län. 2005-2024 ur FlyttFodReg, 2025 ur ImmiCKM.

    FlyttFodReg delar upp på född i Sverige och utrikes född; båda summeras,
    eftersom serien ska motsvara samtliga invandrade precis som resten av sidan.
    Åldersdimensionen har ett förräknat totalvärde, så frågan blir liten.
    """
    labels = values_of(COUNTY_TABLE, "Region")
    lan = [c for c in labels if len(c) == 2 and c != "00"]
    years = [str(y) for y in range(START_YEAR, 2025)]

    old = post(COUNTY_TABLE, [
        {"code": "Region", "selection": {"filter": "item", "values": lan}},
        {"code": "Alder", "selection": {"filter": "item", "values": ["tot"]}},
        {"code": "Kon", "selection": {"filter": "item", "values": ["1", "2"]}},
        {"code": "Fodelseregion", "selection": {"filter": "item", "values": ["09", "11"]}},
        {"code": "ContentsCode", "selection": {"filter": "item", "values": [COUNTY_CONTENTS]}},
        {"code": "Tid", "selection": {"filter": "item", "values": years}},
    ])
    old["source"] = COUNTY_TABLE

    new_labels = values_of(REGION_TABLE, "Region")
    new_lan = [c for c in new_labels if len(c) == 2 and c != "00"]
    new = post(REGION_TABLE, [
        {"code": "Region", "selection": {"filter": "item", "values": new_lan}},
        {"code": "Fodelseland", "selection": {"filter": "item", "values": ["TOT"]}},
        {"code": "Kon", "selection": {"filter": "item", "values": ["TotSa"]}},
        {"code": "Tid", "selection": {"filter": "item", "values": ["2025"]}},
    ])
    new["source"] = REGION_TABLE
    labels.update(new_labels)

    df = pd.concat([old, new], ignore_index=True)
    df["year"] = df["Tid"].astype(int)
    df["county"] = df["Region"].map(labels)
    return df.groupby(["year", "county", "source"], as_index=False)["value"].sum()


def fetch_birth_regions() -> pd.DataFrame:
    """SCB:s egen indelning i födelseregioner. Finns bara för 2025."""
    labels = values_of(REGION_TABLE, "Fodelseland")
    df = post(REGION_TABLE, [
        {"code": "Region", "selection": {"filter": "item", "values": ["00"]}},
        {"code": "Fodelseland", "selection": {"filter": "item", "values": SCB_REGIONS}},
        {"code": "Kon", "selection": {"filter": "item", "values": ["TotSa"]}},
        {"code": "Tid", "selection": {"filter": "item", "values": ["2025"]}},
    ])
    df["year"] = df["Tid"].astype(int)
    df["region"] = df["Fodelseland"].map(labels)
    return df.groupby(["year", "region"], as_index=False)["value"].sum()


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
    df["code"] = df[country_var]
    df["country"] = df[country_var].map(labels)
    out = df.groupby(["year", "code", "country"], as_index=False)["value"].sum()
    out["source"] = table
    return out


def refresh(con: duckdb.DuckDBPyConnection) -> None:
    """Hämtar samtliga tabeller från SCB och ersätter innehållet i DuckDB."""
    parts = [fetch_table(t) for t in TABLES]
    raw = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    # Samma år ska inte kunna komma från två tabeller.
    raw = raw.sort_values("source").drop_duplicates(["year", "code"], keep="first")
    con.register("raw", raw)
    con.execute("""
        CREATE OR REPLACE TABLE immigration AS
        SELECT CAST(year AS INTEGER)   AS year,
               CAST(code AS VARCHAR)    AS code,
               CAST(country AS VARCHAR) AS country,
               CAST(value AS BIGINT)    AS value,
               CAST(source AS VARCHAR)  AS source
        FROM raw
        ORDER BY year, country
    """)
    con.unregister("raw")
    con.register("counties", fetch_counties())
    con.execute("""
        CREATE OR REPLACE TABLE county AS
        SELECT CAST(year AS INTEGER) AS year, CAST(county AS VARCHAR) AS county,
               CAST(value AS BIGINT) AS value, CAST(source AS VARCHAR) AS source
        FROM counties ORDER BY year, county
    """)
    con.unregister("counties")

    con.register("bregions", fetch_birth_regions())
    con.execute("""
        CREATE OR REPLACE TABLE birth_region AS
        SELECT CAST(year AS INTEGER) AS year, CAST(region AS VARCHAR) AS region,
               CAST(value AS BIGINT) AS value
        FROM bregions ORDER BY value DESC
    """)
    con.unregister("bregions")

    # Världsdelsmappningen är vår egen och ligger som granskbar fil i repot.
    con.execute("CREATE OR REPLACE TABLE region_map AS SELECT * FROM read_csv_auto(?)",
                [str(REGION_CSV)])

    n, y0, y1 = con.execute("SELECT count(*), min(year), max(year) FROM immigration").fetchone()
    nc = con.execute("SELECT count(*) FROM county").fetchone()[0]
    nb = con.execute("SELECT count(*) FROM birth_region").fetchone()[0]
    print(f"Hämtade {n} rader födelseland {y0}–{y1}, {nc} rader län, {nb} födelseregioner")


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


def load_continents(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Invandrade per världsdel och år, enligt mappningen i data/regions.csv."""
    return con.execute("""
        SELECT i.year, r.region, sum(i.value) AS value
        FROM immigration i JOIN region_map r ON r.code = i.code
        GROUP BY 1, 2 ORDER BY 1, 2
    """).df()


def load_counties(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("SELECT year, county, value FROM county ORDER BY 1, 2").df()


def load_birth_regions(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("SELECT year, region, value FROM birth_region ORDER BY value DESC").df()


# --------------------------------------------------------------------------- #
# Diagram
# --------------------------------------------------------------------------- #

def select(wide: pd.DataFrame, n: int, forced: tuple[str, ...] = ()) -> list[str]:
    """Väljer de n viktigaste serierna, med eventuella påtvingade först.

    "total" rangordnar efter summan över hela perioden och missar då länder med
    en kort men kraftig topp: Ukraina hamnar på plats 11 trots 28 065 invandrade
    2024. "peak" rangordnar i stället efter största andel ett enskilt år.
    """
    score = wide.div(wide.sum(axis=1), axis=0).max() if RANK_BY == "peak" else wide.sum()
    picked = [c for c in forced if c in wide.columns]
    for c in score.sort_values(ascending=False).index:
        if len(picked) >= n:
            break
        if c not in picked:
            picked.append(c)
    return picked


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def fmt(n: float) -> str:
    return f"{int(round(n)):,}".replace(",", " ")


# --------------------------------------------------------------------------- #
# Diagram
# --------------------------------------------------------------------------- #

def area_figure(stacked: pd.DataFrame, t: dict) -> go.Figure:
    """Staplad yta över de färgade serierna plus restposten.

    Serierna får färg efter sin plats i stapeln, så att två intilliggande band
    alltid är intilliggande slots i paletten — det är den ordningen validatorn
    kontrollerar. Banden skiljs åt av en 2 px linje i ytans egen färg i stället
    för en kontrasterande kantlinje.
    """
    fig = go.Figure()
    for i, c in enumerate(stacked.columns):
        hue = t["other"] if c == OTHER_LABEL else t["series"][i % len(t["series"])]
        fig.add_trace(go.Scatter(
            x=stacked.index, y=stacked[c], name=c, stackgroup="one", mode="lines",
            line=dict(width=2, color=t["surface"]), fillcolor=rgba(hue, 0.95),
            hovertemplate="%{y:,.0f}<extra>" + c + "</extra>"))
    fig.update_layout(
        yaxis_title="Antal invandrade", hovermode="x unified", template="plotly_white",
        hoverlabel=dict(bgcolor="#ffffff", bordercolor=t["axis"],
                        font=dict(color=t["ink"], size=12)),
        font=dict(color=t["ink"]),
        height=520, margin=dict(t=64, l=72, r=24, b=56),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(traceorder="reversed", font=dict(size=12)),
        # Knapparna behåller fasta färger, annars blir texten oläslig när sidan
        # växlar till mörkt läge och den globala textfärgen blir ljus.
        updatemenus=[dict(type="buttons", direction="right", x=0, xanchor="left",
                          y=1.02, yanchor="bottom", bgcolor="#ffffff",
                          bordercolor="#c3c2b7", font=dict(color="#0b0b0b", size=12), buttons=[
            dict(label="Antal", method="update",
                 args=[{"groupnorm": ""}, {"yaxis.title.text": "Antal invandrade"}]),
            dict(label="Andel (%)", method="update",
                 args=[{"groupnorm": "percent"}, {"yaxis.title.text": "Andel av invandrade, %"}]),
        ])])
    # Utan standoff hamnar första årtalet ovanpå y-axelns nolla i hörnet.
    fig.update_xaxes(gridcolor=t["grid"], linecolor=t["axis"], ticklabelstandoff=8,
                     tickfont=dict(color=t["muted"]))
    fig.update_yaxes(gridcolor=t["grid"], linecolor=t["axis"],
                     tickfont=dict(color=t["muted"]))
    return fig


def panels_figure(wide: pd.DataFrame, panels: list[str], title: str, t: dict,
                  cols: int = PANEL_COLS) -> go.Figure:
    """Ett litet linjediagram per serie, alla i samma färg.

    Varje panel har egen y-skala, annars dränker Syrien 2016 allt annat. Toppens
    värde skrivs ut i panelen, så att skalorna går att jämföra ändå.
    """
    rows = -(-len(panels) // cols)
    height = 190 * rows + 54
    # Plotlys vertical_spacing är en andel av rutnätets höjd, så ett fast tal ger
    # för tätt mellan raderna när panelerna är få: rubriken i nästa rad hamnar
    # ovanpå årsetiketterna i raden ovanför. Räkna fram andelen ur en pixelgap.
    grid_h = height - 44 - 24
    vspace = min(46 / grid_h, 0.9 / max(rows - 1, 1))
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=panels,
                        vertical_spacing=vspace, horizontal_spacing=0.055)
    hue = t["series"][0]
    for i, c in enumerate(panels):
        r, col = divmod(i, cols)
        r, col = r + 1, col + 1
        y = wide[c]
        fig.add_trace(go.Scatter(
            x=y.index, y=y, name=c, mode="lines", line=dict(width=2, color=hue),
            fill="tozeroy", fillcolor=rgba(hue, 0.10), showlegend=False,
            hovertemplate="<b>" + c + "</b><br>%{x}: %{y:,.0f}<extra></extra>"), row=r, col=col)
        peak = y.idxmax()
        fig.add_annotation(row=r, col=col, x=peak, y=y[peak], text=fmt(y[peak]),
                           showarrow=False, yshift=11, font=dict(size=10, color=t["muted"]))
        fig.update_yaxes(row=r, col=col, range=[0, y.max() * 1.32], showticklabels=False,
                         showgrid=False, zeroline=True, zerolinecolor=t["axis"],
                         zerolinewidth=1)
        fig.update_xaxes(row=r, col=col, tickvals=[y.index.min(), y.index.max()],
                         showgrid=False, linecolor=t["axis"],
                         tickfont=dict(size=10, color=t["muted"]))
    for a in fig.layout.annotations[:len(panels)]:
        a.font = dict(size=12, color=t["second"])
    fig.update_layout(
        height=height, margin=dict(t=44, l=24, r=24, b=24),
        template="plotly_white", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", hovermode="x",
        font=dict(color=t["ink"]),
        hoverlabel=dict(bgcolor="#ffffff", bordercolor=t["axis"],
                        font=dict(color=t["ink"], size=12)))
    return fig


def rank_figure(wide: pd.DataFrame, panels: list[str], hues: dict[str, str], t: dict) -> go.Figure:
    """Rangordning per år, med landsnamnen utsatta i båda kanterna.

    Identiteten bärs av namnen, inte av färgen, så diagrammet fungerar utan
    färgseende. De sju länder som har egen färg i ytdiagrammet behåller sin färg
    här, resten är neutralt grå — samma land har alltid samma färg på sidan.

    Skalan går hela vägen ned till den sämsta placering någon av serierna når,
    annars försvinner Ukrainas väg från plats 35 till 1 nedanför axeln.
    """
    ranks = wide.rank(axis=1, ascending=False, method="first")[panels]
    x0, x1 = int(ranks.index.min()), int(ranks.index.max())
    worst = int(ranks.max().max())

    fig = go.Figure()
    for c in panels:
        fig.add_trace(go.Scatter(
            x=ranks.index, y=ranks[c], name=c, mode="lines+markers",
            line=dict(width=2, color=hues.get(c, t["muted"])),
            marker=dict(size=6, color=hues.get(c, t["muted"])),
            customdata=wide[c], showlegend=False,
            hovertemplate="<b>" + c + "</b><br>plats %{y:.0f} av "
                          + str(len(wide.columns)) + " (%{customdata:,.0f})<extra></extra>"))
        for x, anchor, shift in ((x0, "right", -10), (x1, "left", 10)):
            fig.add_annotation(x=x, y=ranks[c][x], text=c, showarrow=False,
                               xanchor=anchor, xshift=shift,
                               font=dict(size=11.5, color=t["second"]))

    # Namnen ritas innanför rutan, så x-axeln behöver tomrum i båda kanterna som
    # rymmer den längsta etiketten. Mätt i år, eftersom skalan är år.
    pad = 0.30 * (x1 - x0)
    fig.update_layout(
        yaxis=dict(title="Plats bland alla födelseländer", range=[worst + 1.5, 0.5],
                   tickvals=[1, 5, 10, 15, 20, 25, 30, 35], gridcolor=t["grid"],
                   linecolor=t["axis"], tickfont=dict(color=t["muted"])),
        xaxis=dict(range=[x0 - pad, x1 + pad], tickvals=list(range(x0, x1 + 1, 5)),
                   gridcolor=t["grid"], linecolor=t["axis"],
                   tickfont=dict(color=t["muted"])),
        height=730, margin=dict(t=24, l=76, r=28, b=52),
        template="plotly_white", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", hovermode="closest",
        font=dict(color=t["ink"]),
        hoverlabel=dict(bgcolor="#ffffff", bordercolor=t["axis"],
                        font=dict(color=t["ink"], size=12)))
    return fig


def region_bar_figure(br: pd.DataFrame, t: dict) -> go.Figure:
    """SCB:s egna födelseregioner. Bara ett år finns publicerat så här, så det
    blir liggande staplar med utsatta värden i stället för en tidsserie."""
    br = br.sort_values("value").copy()
    year = int(br["year"].iloc[0])
    br["label"] = br["region"].str.replace("Nord- och Sydamerika, ",
                                           "Nord- och Sydamerika,<br>", regex=False)
    fig = go.Figure(go.Bar(
        x=br["value"], y=br["label"], orientation="h",
        marker=dict(color=t["series"][0]),
        text=[fmt(v) for v in br["value"]], textposition="outside",
        textfont=dict(color=t["second"], size=12),
        customdata=br["region"],
        hovertemplate="<b>%{customdata}</b><br>%{x:,.0f} invandrade<extra></extra>"))
    fig.update_layout(
        height=330, margin=dict(t=20, l=16, r=40, b=50),
        template="plotly_white", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", font=dict(color=t["ink"]),
        bargap=0.45,
        # Värdet skrivs utanför stapeln, så skalan måste rymma det. Utan det här
        # klipps etiketten på den längsta stapeln av vid diagrammets högerkant.
        xaxis=dict(title="Antal invandrade", range=[0, br["value"].max() * 1.16],
                   gridcolor=t["grid"], linecolor=t["axis"],
                   tickfont=dict(color=t["muted"])),
        yaxis=dict(linecolor=t["axis"], automargin=True,
                   tickfont=dict(color=t["ink"], size=12)),
        hoverlabel=dict(bgcolor="#ffffff", bordercolor=t["axis"],
                        font=dict(color=t["ink"], size=12)))
    return fig


def simple_table(wide: pd.DataFrame, cols: list[str], caption: str) -> str:
    out = wide[cols].copy()
    out["Totalt"] = wide.sum(axis=1)
    head = "".join(f"<th scope='col'>{c}</th>" for c in out.columns)
    body = "".join(
        f"<tr><th scope='row'>{y}</th>"
        + "".join(f"<td>{fmt(out.loc[y, c])}</td>" for c in out.columns)
        + "</tr>" for y in out.index)
    return (f"<table><caption>{caption}</caption>"
            f"<thead><tr><th scope='col'>År</th>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table>")


def table_html(wide: pd.DataFrame, panels: list[str]) -> str:
    """Tabellvyn. Riktlinjerna kräver en för varje diagram, och tre av färgerna
    i ljust läge ligger under 3:1 mot ytan, vilket gör den obligatorisk."""
    cols = panels + [OTHER_LABEL, "Totalt"]
    out = wide[panels].copy()
    out[OTHER_LABEL] = wide.drop(columns=panels).sum(axis=1)
    out["Totalt"] = wide.sum(axis=1)
    head = "".join(f"<th scope='col'>{c}</th>" for c in cols)
    body = "".join(
        f"<tr><th scope='row'>{y}</th>"
        + "".join(f"<td>{fmt(out.loc[y, c])}</td>" for c in cols)
        + "</tr>"
        for y in out.index)
    return (f"<table><caption>Invandrade per år och födelseland</caption>"
            f"<thead><tr><th scope='col'>År</th>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table>")


def figures(df: pd.DataFrame, cont: pd.DataFrame, cty: pd.DataFrame, br: pd.DataFrame):
    wide = df.pivot_table(index="year", columns="country", values="value", aggfunc="sum").fillna(0)

    area_series = select(wide, AREA_N)
    panels = select(wide, PANEL_N, ALWAYS_INCLUDE)
    panels = wide[panels].sum().sort_values(ascending=False).index.tolist()

    stacked = wide[area_series].copy()
    stacked[OTHER_LABEL] = wide.drop(columns=area_series).sum(axis=1)
    order = stacked.sum().sort_values(ascending=False).index.drop(OTHER_LABEL).tolist()
    stacked = stacked[order + [OTHER_LABEL]]

    hues = {c: T["series"][i % len(T["series"])] for i, c in enumerate(order)}

    cw = cont.pivot_table(index="year", columns="region", values="value", aggfunc="sum").fillna(0)
    cw = cw[cw.sum().sort_values(ascending=False).index]
    yw = cty.pivot_table(index="year", columns="county", values="value", aggfunc="sum").fillna(0)
    yw = yw[yw.sum().sort_values(ascending=False).index]

    figs = dict(
        area=area_figure(stacked, T),
        rank=rank_figure(wide, panels, hues, T),
        panels=panels_figure(wide, panels, "Varje land för sig, med egen skala", T),
        continents=panels_figure(cw, list(cw.columns),
                                 "Varje världsdel för sig, med egen skala", T),
        counties=panels_figure(yw, list(yw.columns),
                               "Varje län för sig, med egen skala", T),
        regionbar=region_bar_figure(br, T),
    )
    tables = dict(
        countries=table_html(wide, panels),
        continents=simple_table(cw, list(cw.columns), "Invandrade per år och världsdel"),
        counties=simple_table(yw, list(yw.columns), "Invandrade per år och län"),
    )
    return figs, tables, stacked, panels


PAGE = """<!doctype html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Invandring till Sverige efter födelseland</title>
<meta name="description" content="Invandrade till Sverige efter födelseland {y0}–{y1}, enligt SCB:s statistikdatabas.">
<style>
  :root {{
    color-scheme: light;
    --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --second:#52514e;
    --muted:#898781; --rule:#e1e0d9; --axis:#c3c2b7;
  }}
  html {{ background: var(--page); }}
  body {{ margin:0 auto; padding:2.5rem 16px 4rem; max-width:1140px; background:var(--page);
         color:var(--ink); font:16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  h1 {{ font-size:1.75rem; line-height:1.25; margin:0 0 .5rem; letter-spacing:-.01em; }}
  h2 {{ font-size:1.25rem; margin:3rem 0 .4rem; padding-top:1.25rem;
       border-top:1px solid var(--rule); letter-spacing:-.01em; }}
  p.lead {{ color:var(--second); margin:0 0 2.25rem; max-width:68ch; }}
  p.note {{ color:var(--second); margin:0 0 1.25rem; max-width:68ch; font-size:.92rem; }}
  nav {{ margin:0 0 2.5rem; font-size:.92rem; }}
  nav a {{ color:var(--second); margin-right:1.25rem; }}
  .chart {{ background:var(--surface); border:1px solid var(--rule); border-radius:10px;
           padding:.9rem .75rem; margin:0 0 1.75rem; }}
  .chart h3 {{ font-size:1rem; font-weight:600; margin:.1rem 0 .6rem; }}
  /* Rangordningen och småmultiplarna är breda av naturen. Hellre svepa i sidled
     än att klippa namnen på en telefon. */
  .chart.wide > .inner {{ overflow-x:auto; }}
  .chart.wide .plot {{ min-width:720px; }}
  details {{ margin:0 0 2.5rem; }}
  summary {{ cursor:pointer; color:var(--second); padding:.5rem 0; }}
  .scroll {{ overflow-x:auto; }}
  table {{ border-collapse:collapse; font-size:.8rem; font-variant-numeric:tabular-nums;
          margin-top:.75rem; }}
  caption {{ text-align:left; color:var(--second); padding-bottom:.5rem; font-size:.85rem; }}
  th, td {{ padding:.3rem .55rem; border-bottom:1px solid var(--rule); white-space:nowrap; }}
  td {{ text-align:right; color:var(--second); }}
  thead th {{ text-align:right; color:var(--ink); font-weight:600; position:sticky; top:0;
             background:var(--page); }}
  thead th:first-child, tbody th {{ text-align:left; }}
  tbody th {{ color:var(--ink); font-weight:600; }}
  footer {{ border-top:1px solid var(--rule); padding-top:1rem; color:var(--second);
           font-size:.85rem; max-width:80ch; }}
  footer a {{ color:inherit; }}
</style>
</head>
<body>
<h1>Invandring till Sverige efter födelseland, {y0}–{y1}</h1>
<p class="lead">Antal invandrade per år och födelseland enligt SCB:s statistikdatabas.
Det övre diagrammet växlar mellan antal och andel. De {n_area} största serierna har egen
färg där och resten summeras. I rangordningen står landsnamnen i kanterna, så den går att
läsa utan färg, och längre ned finns varje land som en egen panel. Sidan visar samma
invandring på fyra sätt: efter födelseland, efter världsdel, efter SCB:s egen
regionindelning och efter län. Alla värden finns som tabeller.</p>
<nav>
<a href="#land">Födelseland</a><a href="#varldsdel">Världsdel</a>
<a href="#scb">SCB:s födelseregioner</a><a href="#lan">Län</a>
</nav>

<h2 id="land">Efter födelseland</h2>
<div class="chart wide"><h3>Alla invandrade per år, uppdelade på födelseland</h3>
<div class="inner"><div class="plot">{area}</div></div></div>
<div class="chart wide"><h3>Plats på topplistan, år för år</h3><div class="inner"><div class="plot">{rank}</div></div></div>
<div class="chart wide"><h3>Varje land för sig, med egen skala</h3><div class="inner"><div class="plot">{panels}</div></div></div>
<details>
<summary>Visa alla värden som tabell</summary>
<div class="scroll">{table}</div>
</details>

<h2 id="varldsdel">Efter världsdel</h2>
<p class="note">SCB:s tabell innehåller bara enskilda länder, inga regionaggregat. Länderna
är därför grupperade till världsdel efter sin landkod, med mappningen i
<a href="https://github.com/{repo}/blob/main/data/regions.csv">data/regions.csv</a>.
Historiska stater är placerade där merparten av området ligger; Sovjetunionen räknas som
Europa. Det här är inte samma indelning som SCB:s egen längre ned.</p>
<div class="chart wide"><h3>Varje världsdel för sig, med egen skala</h3><div class="inner"><div class="plot">{continents}</div></div></div>
<details>
<summary>Visa alla värden som tabell</summary>
<div class="scroll">{table_continents}</div>
</details>

<h2 id="scb">SCB:s egen indelning i födelseregioner</h2>
<p class="note">SCB publicerar en egen regionindelning, men bara i tabellen ImmiCKM som
hittills bara omfattar {region_year}. Det blir därför en ögonblicksbild och ingen tidsserie.
Indelningen skiljer sig från världsdelarna ovan: Turkiet räknas här till Europa utom EU och
Norden, och Sovjetunionen ligger i samma grupp som Nord- och Sydamerika och Oceanien.</p>
<div class="chart"><h3>SCB:s egen indelning i födelseregioner, {region_year}</h3>{regionbar}</div>

<h2 id="lan">Efter län</h2>
<p class="note">Var de invandrade folkbokfördes, för alla {n_counties} län. Det här är en
annan dimension än resten av sidan och kommer ur en annan tabell: FlyttFodReg redovisar
utrikes inflyttningar per län men delar inte upp dem på födelseland, så serierna kan inte
kombineras med länderna ovan. Årssummorna stämmer ändå exakt mot födelselandstabellen till
och med 2024.</p>
<div class="chart wide"><h3>Varje län för sig, med egen skala</h3><div class="inner"><div class="plot">{counties}</div></div></div>
<details>
<summary>Visa alla värden som tabell</summary>
<div class="scroll">{table_counties}</div>
</details>

<footer>
<p>Källa: SCB, Statistikdatabasen: ImmiEmiFod och ImmiEmiFodCKM (födelseland),
FlyttFodReg och ImmiCKM (län och födelseregioner). Uppgifterna för {y1} redovisas med kontrollerad
slumpmässig avrundning, vilket gör att små tal är något osäkra och att delarna inte summerar
exakt till totalen. EU avser dagens 27 medlemsländer utom Sverige, tillämpat på alla år;
Storbritannien redovisas separat för hela perioden. Serien Sverige är återinvandrade personer
födda i Sverige.</p>
<p>Diagrammen genereras av <a href="https://github.com/{repo}">{repo}</a>.</p>
</footer>
</body>
</html>
"""

REPO = "oluies/se-immigration"


CONF = {"displaylogo": False, "responsive": True}


def build(df: pd.DataFrame, cont: pd.DataFrame, cty: pd.DataFrame,
          br: pd.DataFrame) -> None:
    figs, tables, stacked, panels = figures(df, cont, cty, br)
    y0, y1 = int(stacked.index.min()), int(stacked.index.max())

    html = {k: f.to_html(full_html=False, config=CONF,
                         include_plotlyjs="cdn" if k == "area" else False)
            for k, f in figs.items()}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(PAGE.format(
        y0=y0, y1=y1, n_area=AREA_N, repo=REPO,
        n_counties=len(cty["county"].unique()), region_year=int(br["year"].iloc[0]),
        table=tables["countries"], table_continents=tables["continents"],
        table_counties=tables["counties"], **html,
    ), encoding="utf-8")
    print(f"Skrev {OUT} ({y1 - y0 + 1} år, {len(panels)} länder, "
          f"{len(cont['region'].unique())} världsdelar, {len(cty['county'].unique())} län)")


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
        build(load(con), load_continents(con), load_counties(con), load_birth_regions(con))


if __name__ == "__main__":
    main()
