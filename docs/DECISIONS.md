# Beslutslogg

## 2026-08-06 — Tabellextraktion: koordinatbaserad rekonstruktion istället för pdfplumber.extract_tables()

**Problem:** `pdfplumber.extract_tables()` testades på balans-/resultaträkningssidor i
alla tre bolag och gav olika brister:

- **Volvo:** flerkolumnstabeller (Industriverksamheten / Financial Services /
  Elimineringar / Volvokoncernen × 2023/2022) fick flera tal hopslagna i en och
  samma cell, t.ex. `'42.378 41.471 135 73 – –'`.
- **SkiStar:** tabelldetekteringen gick sönder helt - rader innehöll enbart
  lösryckta tal utan radetiketter.
- **Hexatronic:** fungerade bäst men inkonsekvent mellan sidor i samma dokument
  (en tabell på sida 87 fångade hela balansräkningen som en enda cell).

Ett första försök med ren radbaserad textparsning (`extract_text()` + regex)
löste inte Volvo (kolumner blandas ihop i textflödet vid många kolumner) och
avslöjade dessutom ett nytt problem hos SkiStar: en vertikal innehålls-
navigering i vänstermarginalen (t.ex. "AKTIEN", "RISK", "BOLAGSSTYRNING")
blandades in i radetiketterna eftersom den ligger i samma lästextflöde.

**Beslut:** Byggde om extraktionen (`src/tables.py`) till att arbeta direkt med
ordens positionsdata (`page.extract_words()`) istället för `extract_tables()`
eller ren löptextparsning:

1. **Rubrikrad hittas** genom att leta rader med minst två årtalsliknande
   tokens (matchar årtal som delsträng, t.ex. `"2023-12-31"`,
   `"-2024-08-31"` för brutna räkenskapsår - inte bara rena `"2023"`) eller en
   valutaenhet (Mkr/MSEK/TSEK/...) plus minst ett årtal.
2. **Vänstergräns för tabellen** = min x-position bland rubrikradens ord. Ord
   till vänster om detta i efterföljande rader utesluts - detta är exakt vad
   som filtrerar bort SkiStars sidopanel-navigering utan bolagsspecifik kod.
3. **Kolumncentra** = x-mittpunkter för årtalstokens i rubrikraden.
4. **Sammanslagning av flerordstal:** tal med mellanslag som tusentals-
   avgränsare delas annars upp i flera separata "ord" av pdfplumber (t.ex.
   `"3" "101" "291"`). Ord som ligger tätt intill varandra (< 6pt mellanrum)
   och följer mönstret för tusentalsgrupper slås ihop till ett tal. Ett
   fristående minustecken följt av en siffra slås också ihop.
5. **Tilldelning till kolumn** görs genom avstånd till närmaste kolumncentrum
   (max 25pt) - tal som inte ligger nära någon känd kolumn (t.ex. tal
   inbäddade i löptext/fotnoter under tabellen) räknas inte som tabellvärden.
6. **Rader utan tal** (rubriker/underrubriker) läggs som prefix till nästa
   radetikett med värden, för att hantera flerradiga poster.
7. **Orimligt långa etiketter** (>120 tecken) filtreras bort - ett tydligt
   tecken på att det är löptext, inte en tabellrad.

**Verifierat mot:** balans-/resultaträkningssidor i Volvo (`volvo_2023.pdf`
s. 62-63), SkiStar (`skistar_2023-24.pdf` s. 108-109) och Hexatronic
(`hexatronic_2023.pdf` s. 86-88). Extraherade totalsummor (t.ex. Hexatronics
"SUMMA TILLGÅNGAR" → 8 733 / 7 388, SkiStars "Rörelseresultat" → 385 258 /
364 898) stämmer mot den råa tabelldumpen som gjordes innan omskrivningen.

**Uppföljning 2026-08-06 - noll fel krävdes, inte "good enough":** användaren
bad explicit om att inget steg ska gå vidare förrän det är helt felfritt,
inte "tillräckligt bra". Detta drev tre ytterligare fixar (se nedan) och en
bredare verifieringsmetod: en sweep över SAMTLIGA nio dokument (inte bara
stickprov), med ett automatiskt filter som flaggar rader med tom/orimligt
kort etikett, saknade värden, eller en etikett som bara innehåller siffror.

1. **Radavstånd mättes mot fel referenspunkt.** Ursprungligen jämfördes
   mot senast EXTRAHERADE radens y-position, vilket gjorde att legitima
   mellanrubriker utan värden (vanligt hos Hexatronic, t.ex. en rubrik
   mellan två delsummor) fick hela tabellen att avslutas för tidigt -
   Hexatronics sidor tappade 90 % av raderna. Fix: mät mot senast
   BEHANDLADE radens position (oavsett om den gav upphov till en rad eller
   ej), med en gräns på 36pt kalibrerad mot det största legitima
   radavståndet vi observerat (~32pt hos Hexatronic) och det minsta
   observerade genuina "tabellen är slut"-hoppet (~40pt hos Volvo).
2. **Sidsökningen var för bred.** Att bara leta efter orden
   "resultaträkning"/"balansräkning" var som helst på en sida fångade även
   notsidor (femårsöversikter, förfalloscheman - explicit utanför scope)
   och förvaltningsberättelsens kommentartext, vilka har helt andra
   tabellformer. Löst med `src/locate_statements.py`: matchar mot kända
   rubrikfraser per bolag och nivå (koncern/moderbolag) - Volvo:
   "KONCERNENS RESULTATRÄKNING"/"...BALANSRÄKNING"/"...KASSAFLÖDESANALYS";
   Hexatronic: "Koncernens rapport över rörelseresultat"/"...balansräkning"/
   "...rapport över kassaflöden"; SkiStar: "Koncernens rapport över
   totalresultat"/"Rapport över finansiell ställning för koncernen"/
   "Rapport över kassaflöden för koncernen" (koncern), samt
   "Resultaträkning/Balansräkning/Kassaflödesanalys för Moderbolaget"
   (moderbolag). Matchningen kräver att frasen inleder en KORT rad (≤55
   tecken) för att inte fånga hela meningar i löptext som råkar innehålla
   samma ord.
3. **Flera tabeller sida vid sida på samma rad hanterades inte.** SkiStars
   rapporter lägger ibland två separata tabeller bredvid varandra på
   samma y-position (t.ex. "Koncernens rapport över totalresultat" och
   "Övrigt totalresultat", eller TILLGÅNGAR och EGET KAPITAL OCH SKULDER
   på en och samma balansräkningssida). Den ursprungliga algoritmen antog
   en tabell per rubrikrad och blandade ihop etiketter/värden från båda.
   Löst genom att `src/tables.py` nu klustrar årtalstokens på rubrikraden
   efter x-position till separata "regioner" (tröskel 150pt - långt över
   största observerade kolumnavstånd inom en tabell, ~80pt, och långt under
   minsta observerade avstånd mellan två skilda tabeller, ~335pt), och
   behandlar varje region oberoende med egen vänster-/högergräns,
   kolumncentra och radbokföring. En efterföljande bugg upptäcktes här:
   ett ords EGEN vänsterkant (x0) användes som "ankare" för att avgöra
   vilken region andra ord hör till, men algoritmen jämförde sedan
   ordets HÖGERKANT (x1) mot detta ankare - ett ord är alltid bredare än
   noll, så x1 > x0 alltid, vilket knuffade varje årtalstoken till NÄSTA
   region. Fixat genom att tilldela årtalstokens direkt till sin egen
   kluster-region (de definierar klustren) istället för att köra dem
   genom ankarregeln avsedd för övriga ord (titlar, "Not", valutaenhet).

**Kvarstående, medvetet ej löst (se uppdatering nedan):** SkiStars
KONCERN-sidor (motsvarande Volvo/Hexatronics primära räkningar) har utöver
sida-vid-sida-tabeller även underrubriker med avvikande kolumnpositioner
(t.ex. "Resultat per aktie") och, på kassaflödessidan, ett inbäddat
stapeldiagram vars axelvärden blandas in i tabelldata. Detta gav fortsatt
fel extraktion även efter region-stödet, och användaren beslutade
(2026-08-06) att använda SkiStars MODERBOLAG-räkningar istället, vilka är
helt verifierat rena. `locate_statement_pages(pdf_path, level=...)`
stödjer båda nivåerna; SkiStar körs med `level="moderbolag"`, övriga bolag
med `level="koncern"` (standard).

**Uppdatering (Fas 3, se docs/DECISIONS_FAS3.md):** båda underliggande
orsakerna åtgärdades senare (regionstöd för tabeller sida vid sida,
filtrering av diagramaxelvärden). SkiStar körs sedan dess på "koncern"-
nivå precis som övriga bolag - `level="moderbolag"` används inte längre i
produktion, se `src/pipeline.py::_LEVEL_BY_COMPANY_PREFIX`. Beslutet ovan
var alltså inte slutgiltigt trots formuleringen "kvarstående, medvetet ej
löst" - lämnat orört här som historik, inte som aktuell status.

**Slutgiltig verifiering (2026-08-06):** automatisk sweep över samtliga
34 lokaliserade sidor i alla 9 dokument gav noll flaggade rader. Manuell
kontroll av kända kontrollsummor (t.ex. Volvos "Summa tillgångar" och
"Summa eget kapital och skulder" ska vara lika; Hexatronics "SUMMA
TILLGÅNGAR" ska matcha "SUMMA EGET KAPITAL OCH SKULDER") bekräftade
korrekta värden på samtliga testade sidor.

## 2026-08-06 (forts.) — Fas 1, steg 4: testsviten hittade två nya buggar

`tests/test_tables_extraction.py` byggdes för att köra `extract_financial_rows`
på SAMTLIGA (inte bara stickprovs-) huvudräkningssidor i alla 9 dokument,
med fyra kontroller per sida: att varje lokaliserad sida ger minst en rad,
att inga rader har tom/kort/sifferbara etiketter eller saknade värden, att
balansräkningens tillgångar = eget kapital + skulder (en verklig
korrekthetskontroll, inte bara en heuristik), och att alla värden går att
tolka som tal. Detta avslöjade två ytterligare buggar som det manuella
stickprovet i steg 2 inte hade träffat på:

1. **SkiStars balansräkningssida för moderbolaget (2023/24) tappade hela
   tillgångssidan.** Rubrikerna "TILLGÅNGAR, TSEK ..." och "EGET KAPITAL
   OCH SKULDER, TSEK ..." låg visuellt på samma rad men hade en
   sub-pixel-baslinjeskillnad (2,1pt) som gjorde att `_group_lines` delade
   upp dem i två separata rader. Eftersom huvudloopen ERSATTE (inte
   kompletterade) de aktiva regionerna vid varje ny rubrikrad, raderade
   den andra rubrikraden helt bort den första regionen. Fixat med
   `_merge_split_header_lines()`: rader vars y-position skiljer sig med
   högst 5pt OCH som båda innehåller årtalsliknande tokens slås ihop
   innan regionerna beräknas.
2. **Regressions-fix av föregående fix.** För att lösa (1) lades även ett
   avståndsfilter till (ord som ligger >400pt från sitt tilldelade ankare
   räknas som sidopanelstext och utesluts) - detta löste sidopanel-
   läckaget på multi-region-sidor, men skapade en regression på
   SkiStars ENKEL-region-resultaträkningssidor: när rubrikraden bara
   innehåller "TSEK Not <år> <år>" (den riktiga titeln "Resultaträkning
   för Moderbolaget" står på en helt annan rad långt ovanför), fanns
   inget annat ord på rubrikraden att sätta vänstergränsen efter - och
   avståndsfiltret tog bort just det ord (sidopanelstext) som
   TIDIGARE, av en slump, gav en tillräckligt tillåtande vänstergräns.
   Resultatet blev att riktiga radetiketter som "Övriga externa
   kostnader" klipptes bort, vilket lämnade kvar bara notreferens-
   siffror som etikett (t.ex. `'6, 7' -> ['−1 090 311', '−1 041 525']`).
   Fixat genom att bara tillämpa avståndsfiltret när det finns FLERA
   regioner att skydda mot - med en enda region finns ingen "fel
   region" att läcka till, och gränsen ska då vara så tillåtande som
   möjligt.

**Efter båda fixarna: 36/36 tester gröna över alla 9 dokument**, inklusive
balansräkningskontrollen (tillgångar = eget kapital + skulder) på varje
enskilt dokument. Detta bekräftar värdet av att testa mot ALLA dokument
istället för att lita på manuell stickprovskontroll - båda buggarna var
osynliga i de sidor som granskades manuellt i steg 2.
