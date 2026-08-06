# Utvärdering — baseline (Fas 5)

**Datum:** 2026-08-06
**Facit:** 18 frågor, fördelade över de fyra prioriterade frågetyperna i
`docs/SCOPE.md`. Facit-siffrorna är hämtade ur den extraherade datan
(`data/chunks/`) och därefter korsverifierade oberoende mot rå PDF-text med
`pdfplumber.extract_text()` (dvs. inte via vår egen extraktionspipeline) för
att utesluta att ett fel i vår egen kod skulle smitta facit. Se
`src/evaluation.py` för den fullständiga listan.

**Metod:** varje fråga kördes genom hela pipelinen (`answer_question()` -
hybridsökning + DeepSeek) exakt en gång, med `temperature=0`. Resultatet
bedömdes manuellt mot facit (RÄTT / DELVIS / FEL) - en sträng- eller
talmatchning hade inte kunnat skilja "rätt tal, fel post" (N5) eller "rätt
tal, fel sida" (N3) från ett genuint rätt svar.

## Sammanfattning

**14 av 18 rätt (78 %).**

| Frågetyp | Rätt | Delvis | Fel | Andel rätt |
|---|---|---|---|---|
| Enårsuppslag | 6 | 0 | 0 | 100 % |
| Flerårstrend/YoY | 3 | 1 | 1 | 60 % |
| Nyckeltalsberäkning | 3 | 1 | 1 | 60 % |
| Kvalitativ | 2 | 0 | 0 | 100 % |
| **Totalt** | **14** | **2** | **2** | **78 %** |

**Systemet är starkast på enårsuppslag** - en fråga, en post, en chunk. Det
är också där facit-siffrorna dessutom var lättast att verifiera oberoende.

**Systemet är svagast på flerårstrend och nyckeltalsberäkning** - båda
kräver att retrieval hittar FLERA specifika poster samtidigt (två år, eller
täljare+nämnare), och det är just där vi ser båda felen och båda
delvis-fallen. Det är inte en slump: samma klass av problem (retrieval
hittar en lexikalt närliggande men FEL rad) återkommer i tre av de fyra
avvikande fallen.

## Detaljerat resultat

| ID | Typ | Fråga | Bedömning |
|---|---|---|---|
| E1 | Enårsuppslag | Volvos nettoomsättning 2024 | RÄTT |
| E2 | Enårsuppslag | Volvos summa tillgångar 2023 | RÄTT |
| E3 | Enårsuppslag | Hexatronics nettoomsättning 2023 | RÄTT |
| E4 | Enårsuppslag | Hexatronics summa tillgångar 2025 | RÄTT |
| E5 | Enårsuppslag | SkiStars nettoomsättning 2023/24 | RÄTT |
| E6 | Enårsuppslag | SkiStars summa tillgångar 2024/25 | RÄTT |
| Y1 | YoY | Hexatronics nettoomsättning 2023-2025 | RÄTT |
| Y2 | YoY | Volvos rörelseresultat 2023→2024 | **FEL** |
| Y3 | YoY | SkiStars årets resultat 2023/24→2024/25 | **DELVIS** |
| Y4 | YoY | Volvos eget kapital 2023→2024 | RÄTT |
| Y5 | YoY | Hexatronics årets resultat 2023→2024 | RÄTT |
| N1 | Nyckeltal | Hexatronics rörelsemarginal 2023 | RÄTT |
| N2 | Nyckeltal | Volvos soliditet 2023 | RÄTT |
| N3 | Nyckeltal | SkiStars soliditet 2023/24 | **DELVIS** |
| N4 | Nyckeltal | Hexatronics vinstmarginal 2024 | RÄTT |
| N5 | Nyckeltal | Volvos vinstmarginal 2023 | **FEL** |
| K1 | Kvalitativ | Hexatronics riskkategorier | RÄTT |
| K2 | Kvalitativ | Volvos risker/osäkerhetsfaktorer | RÄTT |

## De fyra avvikande fallen - konkreta exempel och grundorsak

### Y2 (FEL) — Volvos rörelseresultat 2023→2024

**Fråga:** "Hur har Volvos rörelseresultat förändrats från 2023 till 2024?"
**Facit:** 66.784 (2023) → 66.611 (2024) Mkr, Volvokoncernen, i det närmaste
oförändrat.
**Systemets svar:** "Volvokoncernens rapporterade rörelseresultat uppgick
till 62.198 Mkr (2024) jämfört med 63.063 Mkr (2023)" - siffror hämtade från
en tabell på s. 209 som visar **Industriverksamheten**, inte koncernen,
trots att svaret påstår "Volvokoncernen".

**Grundorsak: retrieval, inte modellen.** De korrekta fakta-chunkarna
(`volvo_2023.pdf::resultaträkning::rad9`,
`volvo_2024.pdf::resultaträkning::rad9` - "Rörelseresultat" med
Volvokoncernen-kolumnen) rankades **17 och 34** i vektorsökningen, men
matchade **inte alls** i BM25 inom kandidatdjupet. Ingen av de åtta
källorna som faktiskt gick till modellen innehöll koncernens
rörelseresultat från primärräkningen - modellen gjorde så gott den kunde
med fel underlag, och citerade dessutom fel segment som om det vore
koncernen (ett brott mot promptregel 7, sannolikt för att inget bättre
alternativ fanns i kontexten). En flerårsfråga fördubblar konkurrensen om
platserna i topp-k (båda årens chunkar tävlar samtidigt), vilket gör att
en post som annars skulle rankats bra för en enårsfråga trängs ut.

### Y3 (DELVIS) — SkiStars årets resultat 2023/24→2024/25

**Fråga:** "Hur har SkiStars årets resultat utvecklats från 2023/24 till
2024/25?"
**Facit:** 472 887 (2023/24) → 552 019 (2024/25) TSEK, ökande.
**Systemets svar:** nekade att svara för 2024/25 och angav istället
"Årets totalresultat" (525 754) som den enda tillgängliga siffran.

**Grundorsak: lexikal förväxling mellan två snarlika radetiketter.**
SkiStars resultaträkning för 2024/25 innehåller BÅDA raderna på samma sida
(s. 119): "Årets resultat" (552 019/472 887 - det efterfrågade) och "Årets
totalresultat" (525 754/407 437 - resultat inklusive övrigt totalresultat,
en annan post). Retrieval hämtade "Årets totalresultat"-chunken men inte
"Årets resultat"-chunken för 2024/25. Samma mönster som "nettoomsättning"
vs. "nettoinvesteringar" i Fas 3 (docs/DECISIONS_FAS3.md) - två rader med
nästan identisk text ligger nära varandra i både BM25- och
embeddingrymden. **Positivt:** modellen hittade INTE på ett svar när den
saknade rätt siffra - den flaggade det tydligt istället, precis enligt
promptregel 2. Bedöms som DELVIS snarare än FEL eftersom halva svaret
(2023/24-värdet) var korrekt och resten korrekt flaggades som osäkert,
istället för att gissa.

### N3 (DELVIS) — SkiStars soliditet 2023/24

**Fråga:** "Vad var SkiStars soliditet 2023/24?"
**Facit:** ca 42,1 % (3 656 803 / 8 681 892, beräknat).
**Systemets svar:** "42 %" - talet är korrekt (avrundat), men källan anges
som s. 144, en sida vi inte identifierat vad den innehåller (troligen en
egen nyckeltalstabell SkiStar publicerar, snarare än en beräkning ur
balansräkningens poster på s. 105 som facit förutsatte).

**Grundorsak: oklart, kräver line-nivågranskning.** Talet är rätt, men vi
kan inte utan vidare granskning avgöra om s. 144 verkligen innehåller
"42 %" som SkiStars egen redovisade siffra (i så fall är svaret FULLGOTT
och till och med bättre än en beräkning, enligt promptens princip att
föredra bolagets egen siffra), eller om modellen råkat citera fel sida för
ett tal den ändå beräknat korrekt. Bedöms DELVIS tills källan är
verifierad - talet självt underkänns inte.

### N5 (FEL) — Volvos vinstmarginal 2023

**Fråga:** "Vad var Volvos vinstmarginal 2023?"
**Facit:** ca 9,0 % (49.932 / 552.764 - Periodens resultat / Nettoomsättning).
**Systemets svar:** "Volvokoncernens **rörelsemarginal** 2023 var 12,1 %" -
fel nyckeltal helt, inte bara fel tal.

**Grundorsak: bugg i query-expansionen (`src/hybrid_search.py`).**
`_RATIO_TERM_EXPANSIONS["vinstmarginal"]` innehåller `["nettoresultat",
"årets resultat", "nettoomsättning"]`. Volvo kallar dock sin nettoresultat-
rad **"Periodens resultat"**, inte "Årets resultat" (bekräftat vid
facit-verifiering, se `src/evaluation.py`). Expansionen letade alltså efter
en term som inte finns i Volvos rapporter, hittade ingen bra träff för
täljaren, och sökningen föll tillbaka på en annan, redan tillgänglig
marginal-siffra (rörelsemarginal) som råkade ligga nära i sökrymden.
Samma klass av bugg som identifierades och fixades för
"nettoomsättning 2019" i Fas 4 (docs/DECISIONS_FAS4.md) - ordvalet skiljer
sig mellan bolagen, och vår expansionslista var inte bolagsmedveten.

## Sammanfattande bedömning

Ett tydligt, återkommande mönster: **tre av fyra avvikande fall orsakas av
att två radetiketter med snarlik betydelse konkurrerar om samma
sökträffar** (Industriverksamheten vs. Volvokoncernen; Årets resultat vs.
Årets totalresultat; Volvos "Periodens resultat" vs. vår generiska
"Årets resultat"-term). Det är inte slumpmässiga fel - det är samma
grundproblem (retrieval kan inte alltid skilja mellan lexikalt närliggande
poster) som redan flaggats en gång i Fas 3/4, men som fortsatt orsakar fel
i just de frågetyper (YoY, nyckeltalsberäkning) som kräver flera samtidiga
träffar.

**Ingen hallucination observerades.** I samtliga fyra avvikande fall är
felet antingen (a) en verklig men FEL siffra hämtad från en verklig men
FEL rad i dokumentet, eller (b) en korrekt flaggad avsaknad av data. Inte
i något fall hittade modellen på ett tal som inte fanns i något
källutdrag.

**Förslag på åtgärder** (inte genomförda i denna fas - dokumenteras som
baseline, se steg 5 i Fas 5-prompten):
1. Bolagsspecifik terminologi i `_RATIO_TERM_EXPANSIONS` (t.ex. Volvos
   "Periodens resultat") istället för en enda generisk lista.
2. En strängare regel eller ett efterhandskontroll-steg som varnar när
   modellen citerar ett segment (Industriverksamheten/Financial Services)
   som om det vore koncernen.
3. Högre `top_k` eller `_CANDIDATE_DEPTH` specifikt för flerårsfrågor,
   där två periodens chunkar konkurrerar om samma platser.
