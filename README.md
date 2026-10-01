# se-immigration

Invandring till Sverige efter födelseland och år, hämtad från SCB:s statistikdatabas
och publicerad som en interaktiv sida.

**[Se diagrammen →](https://oluies.github.io/se-immigration/)**

## Vad som visas

Två diagram över perioden 2005–2025:

- **Staplad yta** med alla invandrade per år, uppdelade på födelseland. En knapp växlar
  mellan antal och andel, vilket gör förskjutningarna tydliga: Irak i mitten av 00-talet,
  Syrien 2015–2016 och Ukraina 2024.
- **Rangordning per år** som följer hur serierna byter plats på topplistan.

De 13 största serierna redovisas separat och resten summeras som "Övriga".

## Datakällor

| Tabell | År | Kommentar |
|---|---|---|
| [`ImmiEmiFod`](https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/START__BE__BE0101__BE0101J/ImmiEmiFod/) | 2000–2024 | |
| [`ImmiEmiFodCKM`](https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/START__BE__BE0101__BE0101J/ImmiEmiFodCKM/) | 2025 | Kontrollerad slumpmässig avrundning |

Två saker är värda att känna till om underlaget:

- SCB redovisar 2025 och framåt med **kontrollerad slumpmässig avrundning**. Små tal är
  därför något osäkra, och delarna summerar inte exakt till totalen: för 2025 är tabellens
  egen total 89 434 medan summan av länderna blir 89 439.
- CKM-tabellen har ett **förräknat totalvärde i könsdimensionen** (`TotSa`) som den äldre
  tabellen saknar. Den som summerar över alla könsvärden dubbelräknar 2025. Skriptet väljer
  totalvärdet när det finns.

## Grupperingar

EU avser dagens 27 medlemsländer utom Sverige, tillämpat på alla år. Storbritannien ingår
inte i gruppen utan redovisas separat för hela perioden, så att utträdet 2020 inte bryter
serien. Serien Sverige är återinvandrade personer födda i Sverige.

## Köra själv

```bash
uv run invandring_fodelseland.py --refresh   # hämtar om från SCB
uv run invandring_fodelseland.py             # bygger om sidan från DuckDB-filen
```

Data lagras i `data/immigration.duckdb` och sidan skrivs till `docs/index.html`.
Utan `--refresh` går skriptet aldrig ut på nätet.

Databasen kan frågas direkt:

```bash
duckdb data/immigration.duckdb \
  "SELECT year, value FROM immigration WHERE country = 'Ukraina' ORDER BY year DESC LIMIT 3"
```

```
┌───────┬───────┐
│ year  │ value │
├───────┼───────┤
│  2025 │  6588 │
│  2024 │ 28065 │
│  2023 │   606 │
└───────┴───────┘
```

Hoppet 2024 ser ut att spegla när personer med tillfälligt skydd folkbokfördes
snarare än när de kom till Sverige. Det är inte verifierat mot SCB:s beskrivning av
tabellen, så kontrollera innan du drar slutsatser av just det året.

## Inställningar

Överst i `invandring_fodelseland.py`:

| Inställning | Standard | Betydelse |
|---|---|---|
| `START_YEAR` | `2005` | Första år som hämtas |
| `TOP_N` | `13` | Antal serier som redovisas separat |
| `RANK_BY` | `"peak"` | `"peak"` = största andel ett enskilt år, `"total"` = summa över perioden |
| `ALWAYS_INCLUDE` | `("USA", "Storbritannien", "Ryssland")` | Serier som alltid visas separat |
| `INCLUDE_SWEDEN` | `True` | Ta med återinvandrade födda i Sverige |
| `GROUP_EU` | `True` | Slå ihop EU-länderna till en serie |

`RANK_BY` spelar roll för länder med en kort men kraftig topp. På summan över hela perioden
hamnar Ukraina först på plats 11 trots 28 065 invandrade under 2024, och hade då hamnat i
"Övriga". På `"peak"` hamnar Ukraina på plats 2.

## Licens

Koden är MIT-licensierad. Statistiken kommer från SCB och omfattas av deras villkor.
