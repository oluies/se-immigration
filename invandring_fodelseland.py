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
import json
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import requests
from plotly.subplots import make_subplots

BASE = "https://api.scb.se/OV0104/v1/doris/sv/ssd/BE/BE0101/BE0101J"
TABLES = ["ImmiEmiFod", "ImmiEmiFodCKM"]   # 2000-2024 respektive 2025 (CKM)
START_YEAR = 2005

# Ytdiagrammet bär sju färger. Fler färgklasser än så går inte att skilja åt med
# nedsatt färgseende, oavsett palett, så resten av länderna får varsin panel i
# småmultiplarna i stället, där färg inte behöver särskilja någonting.
AREA_N = 7                  # antal färgade ytor, resten summeras som "Övriga"
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
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"]
OTHER_LIGHT, OTHER_DARK = "#cfcec6", "#3c3c38"

# Diagrammens ytor och text, ur samma riktlinjer.
THEME = {
    "light": dict(surface="#fcfcfb", page="#f9f9f7", ink="#0b0b0b", second="#52514e",
                  muted="#898781", grid="#e1e0d9", axis="#c3c2b7",
                  series=SERIES_LIGHT, other=OTHER_LIGHT),
    "dark": dict(surface="#1a1a19", page="#0d0d0d", ink="#ffffff", second="#c3c2b7",
                 muted="#898781", grid="#2c2c2a", axis="#383835",
                 series=SERIES_DARK, other=OTHER_DARK),
}

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
        title=dict(text="Alla invandrade per år, uppdelade på födelseland",
                   x=0, xanchor="left", y=0.97, yanchor="top", font=dict(size=17)),
        yaxis_title="Antal invandrade", hovermode="x unified", template="plotly_white",
        height=560, margin=dict(t=120, l=72, r=24, b=56),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(traceorder="reversed", font=dict(size=12)),
        # Knapparna behåller fasta färger, annars blir texten oläslig när sidan
        # växlar till mörkt läge och den globala textfärgen blir ljus.
        updatemenus=[dict(type="buttons", direction="right", x=0, xanchor="left",
                          y=1.07, yanchor="bottom", bgcolor="#ffffff",
                          bordercolor="#c3c2b7", font=dict(color="#0b0b0b", size=12), buttons=[
            dict(label="Antal", method="update",
                 args=[{"groupnorm": ""}, {"yaxis.title.text": "Antal invandrade"}]),
            dict(label="Andel (%)", method="update",
                 args=[{"groupnorm": "percent"}, {"yaxis.title.text": "Andel av invandrade, %"}]),
        ])])
    fig.update_xaxes(gridcolor=t["grid"], linecolor=t["axis"], tickfont=dict(color=t["muted"]))
    fig.update_yaxes(gridcolor=t["grid"], linecolor=t["axis"], tickfont=dict(color=t["muted"]))
    return fig


def panels_figure(wide: pd.DataFrame, panels: list[str], t: dict) -> go.Figure:
    """Ett litet linjediagram per land, alla i samma färg.

    Varje panel har egen y-skala, annars dränker Syrien 2016 allt annat. Toppens
    värde skrivs ut i panelen, så att skalorna går att jämföra ändå.
    """
    rows = -(-len(panels) // PANEL_COLS)
    fig = make_subplots(rows=rows, cols=PANEL_COLS, subplot_titles=panels,
                        vertical_spacing=0.10, horizontal_spacing=0.055)
    hue = t["series"][0]
    for i, c in enumerate(panels):
        r, col = divmod(i, PANEL_COLS)
        r, col = r + 1, col + 1
        y = wide[c]
        fig.add_trace(go.Scatter(
            x=y.index, y=y, name=c, mode="lines", line=dict(width=2, color=hue),
            fill="tozeroy", fillcolor=rgba(hue, 0.10), showlegend=False,
            hovertemplate="%{x}: %{y:,.0f}<extra>" + c + "</extra>"), row=r, col=col)
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
        title=dict(text="Varje land för sig, med egen skala",
                   x=0, xanchor="left", font=dict(size=17)),
        height=170 * rows + 90, margin=dict(t=90, l=24, r=24, b=24),
        template="plotly_white", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", hovermode="x")
    return fig


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


def figures(df: pd.DataFrame):
    wide = df.pivot_table(index="year", columns="country", values="value", aggfunc="sum").fillna(0)

    area_series = select(wide, AREA_N)
    panels = select(wide, PANEL_N, ALWAYS_INCLUDE)
    panels = wide[panels].sum().sort_values(ascending=False).index.tolist()

    stacked = wide[area_series].copy()
    stacked[OTHER_LABEL] = wide.drop(columns=area_series).sum(axis=1)
    order = stacked.sum().sort_values(ascending=False).index.drop(OTHER_LABEL).tolist()
    stacked = stacked[order + [OTHER_LABEL]]

    # Sidan ritas i ljusa färger och byter själv till de mörka stegen vid behov.
    area = area_figure(stacked, THEME["light"])
    panel = panels_figure(wide, panels, THEME["light"])
    return area, panel, table_html(wide, panels), stacked, panels


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
  @media (prefers-color-scheme: dark) {{
    :root {{
      color-scheme: dark;
      --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --second:#c3c2b7;
      --muted:#898781; --rule:#2c2c2a; --axis:#383835;
    }}
  }}
  html {{ background: var(--page); }}
  body {{ margin:0 auto; padding:2.5rem 16px 4rem; max-width:1140px; background:var(--page);
         color:var(--ink); font:16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  h1 {{ font-size:1.75rem; line-height:1.25; margin:0 0 .5rem; letter-spacing:-.01em; }}
  p.lead {{ color:var(--second); margin:0 0 2.25rem; max-width:68ch; }}
  .chart {{ background:var(--surface); border:1px solid var(--rule); border-radius:10px;
           padding:.75rem .5rem; margin:0 0 1.75rem; }}
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
färg där och resten summeras; varje enskilt land finns i stället som en egen panel längre
ned, där färg inte behöver skilja något åt. Alla värden finns i tabellen.</p>
<div class="chart">{area}</div>
<div class="chart">{panels}</div>
<details>
<summary>Visa alla värden som tabell</summary>
<div class="scroll">{table}</div>
</details>
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
// genomskinlig bakgrund och får text-, rutnäts- och seriefärger härifrån i stället.
(function () {{
  var SWAP = {swap};
  var LIGHT = {light};
  var DARK = {dark};
  var mq = window.matchMedia("(prefers-color-scheme: dark)");

  function apply() {{
    var t = mq.matches ? DARK : LIGHT;
    var plots = document.querySelectorAll(".js-plotly-plot");
    plots.forEach(function (gd, i) {{
      Plotly.relayout(gd, {{
        "font.color": t.ink, "title.font.color": t.ink, "legend.font.color": t.ink,
        "xaxis.title.font.color": t.second, "yaxis.title.font.color": t.second
      }});
      var ax = {{}};
      Object.keys(gd.layout).forEach(function (k) {{
        if (/^[xy]axis\\d*$/.test(k)) {{
          ax[k + ".gridcolor"] = t.grid;
          ax[k + ".linecolor"] = t.axis;
          ax[k + ".zerolinecolor"] = t.axis;
          ax[k + ".tickfont.color"] = t.muted;
        }}
      }});
      Plotly.relayout(gd, ax);
      if (i === 0) {{
        Plotly.restyle(gd, {{
          "fillcolor": (mq.matches ? SWAP.area.dark : SWAP.area.light),
          "line.color": t.surface
        }});
      }} else {{
        Plotly.restyle(gd, {{
          "line.color": (mq.matches ? SWAP.panels.dark.line : SWAP.panels.light.line),
          "fillcolor": (mq.matches ? SWAP.panels.dark.fill : SWAP.panels.light.fill)
        }});
      }}
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
    area, panel, table, stacked, panels = figures(df)
    y0, y1 = int(stacked.index.min()), int(stacked.index.max())
    light, dark = THEME["light"], THEME["dark"]

    def fills(t: dict) -> list[str]:
        return [rgba(t["other"] if c == OTHER_LABEL else t["series"][i % len(t["series"])], 0.95)
                for i, c in enumerate(stacked.columns)]

    swap = {
        "area": {"light": fills(light), "dark": fills(dark)},
        "panels": {
            "light": {"line": light["series"][0], "fill": rgba(light["series"][0], 0.10)},
            "dark": {"line": dark["series"][0], "fill": rgba(dark["series"][0], 0.10)},
        },
    }
    tokens = {m: {k: THEME[m][k] for k in ("surface", "ink", "second", "muted", "grid", "axis")}
              for m in ("light", "dark")}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(PAGE.format(
        y0=y0, y1=y1, n_area=AREA_N, repo=REPO, table=table,
        swap=json.dumps(swap, ensure_ascii=False),
        light=json.dumps(tokens["light"]), dark=json.dumps(tokens["dark"]),
        area=area.to_html(full_html=False, include_plotlyjs="cdn",
                          config={"displaylogo": False, "responsive": True}),
        panels=panel.to_html(full_html=False, include_plotlyjs=False,
                             config={"displaylogo": False, "responsive": True}),
    ), encoding="utf-8")
    print(f"Skrev {OUT} ({y1 - y0 + 1} år, {AREA_N} färgade ytor, {len(panels)} paneler)")


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
