# se-immigration

Invandring till Sverige efter födelseland och år, hämtad från SCB:s statistikdatabas
och publicerad som en interaktiv sida.

**[Se diagrammen →](https://oluies.github.io/se-immigration/)**

## Vad som visas

Tre vyer över perioden 2005–2025:

- **Staplad yta** med alla invandrade per år, uppdelade på födelseland. En knapp växlar
  mellan antal och andel, vilket gör förskjutningarna tydliga: Irak i mitten av 00-talet,
  Syrien 2015–2016 och Ukraina 2024. Sju serier har egen färg, resten summeras som "Övriga".
- **Småmultiplar**, ett litet linjediagram per land med egen y-skala, så att Syrien 2016
  inte trycker ihop allt annat. Toppvärdet skrivs ut i varje panel.
- **Tabellvy** med alla värden, utfällbar under diagrammen.

## Färg och färgseende

Ytdiagrammet bär sju färger. Fler färgklasser än så går inte att skilja åt med nedsatt
färgseende, oavsett palett, så de övriga länderna får varsin panel i småmultiplarna i
stället, där alla paneler har samma färg och färg därmed inte behöver särskilja någonting.

Paletten är hämtad ur dataviz-riktlinjerna och kontrollerad med deras validator, separat
för ljus och mörk yta:

```bash
node scripts/validate_palette.js \
  "#2a78d6,#eb6834,#1baf7a,#eda100,#e87ba4,#008300,#4a3aa7" \
  --mode light --surface "#fcfcfb"
```

Båda lägena passerar ljushetsband, kromafloor, CVD-separation och normalseendefloor. I
ljust läge ligger tre av färgerna under 3:1 mot ytan, vilket gör tabellvyn obligatorisk
snarare än valfri. Banden skiljs dessutom åt av en 2 px lucka i ytans egen färg, som andra
kanal utöver färgen. Stegen för mörkt läge är egna val för den mörka ytan, inte uträknade
ur de ljusa.

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
| `AREA_N` | `7` | Antal färgade ytor i det staplade diagrammet |
| `PANEL_N` | `13` | Antal länder som får en egen panel i småmultiplarna |
| `RANK_BY` | `"peak"` | `"peak"` = största andel ett enskilt år, `"total"` = summa över perioden |
| `ALWAYS_INCLUDE` | `("USA", "Storbritannien", "Ryssland")` | Får alltid en egen panel |
| `INCLUDE_SWEDEN` | `True` | Ta med återinvandrade födda i Sverige |
| `GROUP_EU` | `True` | Slå ihop EU-länderna till en serie |

`RANK_BY` spelar roll för länder med en kort men kraftig topp. På summan över hela perioden
hamnar Ukraina först på plats 11 trots 28 065 invandrade under 2024, och hade då hamnat i
"Övriga". På `"peak"` hamnar Ukraina på plats 2.

Höj inte `AREA_N` över sju utan att köra om validatorn.

## Licens

Koden är MIT-licensierad. Statistiken kommer från SCB och omfattas av deras villkor.
