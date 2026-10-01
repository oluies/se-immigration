# se-immigration

Invandring till Sverige efter födelseland och år, hämtad från SCB:s statistikdatabas
och publicerad som en interaktiv sida.

**[Se diagrammen →](https://oluies.github.io/se-immigration/)**

## Vad som visas

Tre vyer över perioden 2005–2025:

- **Staplad yta** med alla invandrade per år, uppdelade på födelseland. En knapp växlar
  mellan antal och andel, vilket gör förskjutningarna tydliga: Irak i mitten av 00-talet,
  Syrien 2015–2016 och Ukraina 2024. Åtta serier har egen färg, resten summeras som "Övriga".
- **Rangordning per år** bland samtliga födelseländer, för samma urval som panelerna. Landsnamnen står utsatta i båda
  kanterna i stället för i en färgförklaring, så diagrammet går att läsa utan färg.
  Skalan går ned till plats 35, annars försvinner Ukrainas väg från 35 till 1 under axeln.
- **Småmultiplar**, ett litet linjediagram per land med egen y-skala, så att Syrien 2016
  inte trycker ihop allt annat. Toppvärdet skrivs ut i varje panel.
- **Tabellvy** med alla värden, utfällbar under diagrammen.

Samma invandring visas sedan på tre andra sätt:

- **Efter världsdel**, en panel per världsdel över hela perioden.
- **Efter SCB:s egen regionindelning**, som liggande staplar. Den finns bara för 2025.
- **Efter län**, en panel för vart och ett av de 21 länen — var de invandrade folkbokfördes.

## Färg och färgseende

Ytdiagrammet bär åtta färger, vilket är hela den validerade paletten. Fler färgklasser än
så går inte att skilja åt med nedsatt färgseende, oavsett palett. De övriga länderna får
därför varsin panel i småmultiplarna, där alla paneler har samma färg, och i rangordningen
bärs identiteten av utsatta namn i stället för av färg.

Ett land har alltid samma färg på hela sidan. De åtta som har egen färg i ytdiagrammet
behåller den i rangordningen; de övriga åtta ritas neutralt grå. Grått betyder alltså
"finns inte som egen yta i det övre diagrammet", inte "saknar betydelse".

`PANEL_N` kan höjas fritt, eftersom småmultiplarna inte använder färg för att särskilja
något. Priset tas ut i rangordningen, som visar samma urval: där blir de grå linjerna
fler och svårare att följa mellan kanterna. Vid 16 serier är åtta grå.

Paletten är hämtad ur dataviz-riktlinjerna och kontrollerad med deras validator:

```bash
node scripts/validate_palette.js \
  "#2a78d6,#eb6834,#1baf7a,#eda100,#e87ba4,#008300,#4a3aa7,#e34948" \
  --mode light --surface "#fcfcfb"
```

Den passerar ljushetsband, kromafloor, CVD-separation och normalseendefloor. Sämsta
grannpar är guld mot akvamarin, ΔE 9,1 för protanopi mot målet 8, och rosa mot guld,
ΔE 19,6 för normalseende mot golvet 15. Tre av färgerna ligger under 3:1 mot ytan, vilket
gör tabellvyn obligatorisk snarare än valfri. Banden skiljs dessutom åt av en 2 px lucka i
ytans egen färg, som andra kanal utöver färgen.

Restposten "Övriga" är neutralt grå och är med avsikt inte en kategorifärg: den faller på
validatorns ljushets- och kromatest. Mot grannbandet röd mäter den ΔE 22,9 för CVD och
29,5 för normalseende, alltså klart över golven.

## Varför sidan alltid är ljus

Sidan ritas mot ljus yta oavsett systemets inställning. Ett mörkt läge kräver egna,
separat validerade steg för varje färg och rätt bläck i varje enskild ruta. Plotlys
hover-ruta har vit botten som inte följer med ett tema, så en global ljus textfärg ger
vit text på vitt — vilket också var det som fick mörkt läge att tas bort här.

Textfärg och hover-ruta sätts därför uttryckligen i varje diagram i stället för att ärvas,
och landsnamnet i hover-rutan står i textbläck, inte i seriens färg, eftersom flera av
färgerna ligger under 3:1 mot vitt.

## Datakällor

| Tabell | År | Används till |
|---|---|---|
| [`ImmiEmiFod`](https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/START__BE__BE0101__BE0101J/ImmiEmiFod/) | 2000–2024 | Födelseland |
| [`ImmiEmiFodCKM`](https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/START__BE__BE0101__BE0101J/ImmiEmiFodCKM/) | 2025 | Födelseland, med kontrollerad slumpmässig avrundning |
| [`FlyttFodReg`](https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/START__BE__BE0101__BE0101J/FlyttFodReg/) | 2002–2024 | Län, via tabellinnehållet "Utrikes inflyttningar" |
| [`ImmiCKM`](https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/START__BE__BE0101__BE0101J/ImmiCKM/) | 2025 | Län, och SCB:s egna födelseregioner |

Två saker är värda att känna till om underlaget:

- SCB redovisar 2025 och framåt med **kontrollerad slumpmässig avrundning**. Små tal är
  därför något osäkra, och delarna summerar inte exakt till totalen: för 2025 är tabellens
  egen total 89 434 medan summan av länderna blir 89 439.
- CKM-tabellen har ett **förräknat totalvärde i könsdimensionen** (`TotSa`) som den äldre
  tabellen saknar. Den som summerar över alla könsvärden dubbelräknar 2025. Skriptet väljer
  totalvärdet när det finns.

## De tre regionindelningarna

De skiljer sig åt, och det är avsiktligt redovisat på sidan.

**Världsdel** är vår egen gruppering. SCB:s födelselandstabell innehåller bara enskilda
länder, inga regionaggregat, så de 208 länderna är mappade efter sin landkod — som följer
ISO 3166-1 alpha-2 — till mappningen i [`data/regions.csv`](data/regions.csv). Åtta koder
saknas i ISO eller avser historiska stater och är satta för hand: Gaza-området och Östtimor
till Asien, Vatikanen, Jugoslavien, Serbien och Montenegro samt Tjeckoslovakien till Europa,
okänt födelseland till en egen grupp. Sovjetunionen spände över två världsdelar och räknas
här som Europa; det gäller 1 397 personer av 2,27 miljoner över hela perioden.

**SCB:s egen födelseregion** finns bara i `ImmiCKM`, som hittills bara omfattar 2025. Den
är inte samma indelning: Turkiet räknas dit till Europa utom EU och Norden medan vår
mappning lägger det i Asien, och Sovjetunionen ligger i samma grupp som Nord- och
Sydamerika och Oceanien. Av SCB:s åtta grupper används alla utom *okänt födelseland*, som
är samma personer som *okänd födelseregion* — tas båda med dubbelräknas nio personer.

**Län** är en annan dimension än resten av sidan. `FlyttFodReg` redovisar utrikes
inflyttningar per län men delar bara upp dem på född i Sverige eller utrikes född, inte på
födelseland, så serierna kan inte kombineras med länderna. Att de ändå mäter samma sak
syns på att årssummorna stämmer exakt mot födelselandstabellen till och med 2024
(163 005 år 2016, 82 518 år 2020, 116 197 år 2024). För 2025 skiljer de sig med 8 personer,
vilket är CKM-avrundningen.

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

Databasen har fyra tabeller:

| Tabell | Innehåll |
|---|---|
| `immigration` | år, landkod, födelseland, antal, källtabell |
| `county` | år, län, antal, källtabell |
| `birth_region` | SCB:s födelseregioner för 2025 |
| `region_map` | världsdelsmappningen, inläst från `data/regions.csv` |

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
| `AREA_N` | `8` | Antal färgade ytor i det staplade diagrammet |
| `PANEL_N` | `16` | Antal länder som får en egen panel i småmultiplarna |
| `PANEL_COLS` | `4` | Antal kolumner i småmultiplarna |
| `RANK_BY` | `"peak"` | `"peak"` = största andel ett enskilt år, `"total"` = summa över perioden |
| `ALWAYS_INCLUDE` | `("USA", "Storbritannien", "Ryssland")` | Får alltid en egen panel |
| `INCLUDE_SWEDEN` | `True` | Ta med återinvandrade födda i Sverige |
| `GROUP_EU` | `True` | Slå ihop EU-länderna till en serie |

`RANK_BY` spelar roll för länder med en kort men kraftig topp. På summan över hela perioden
hamnar Ukraina först på plats 11 trots 28 065 invandrade under 2024, och hade då hamnat i
"Övriga". På `"peak"` hamnar Ukraina på plats 2.

`AREA_N` är satt till paletten hela längd. Att höja den kräver fler kategorifärger, och
åtta är vad paletten rymmer — kör om validatorn på en ny uppsättning innan du gör det.

Gränsen för att hamna i "Övriga" följer av `AREA_N` och är ingen fast nivå. Med nuvarande
data går snittet mellan Afghanistan, som nådde 6,43 procent av invandringen 2017, och
Eritrea, som som mest nådde 5,09 procent 2015. Observera att kriteriet är andel och inte
antal: Afghanistans 9 297 under 2017 är fler än Indiens 7 480 under 2023, men 2017 var ett
större invandringsår, så Indien ligger högre på andelen.

## Licens

Koden är MIT-licensierad. Statistiken kommer från SCB och omfattas av deras villkor.
