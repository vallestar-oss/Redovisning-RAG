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

---

# Omkörning — 2026-09-11

**Gren:** `claude/festive-tesla-teyts0`
**Facit:** samma `EVAL_SET` (18 frågor) som baseline ovan - oförändrat i
`src/evaluation.py`, alltså samma facit-siffror och samma källhänvisningar.
**Metod:** identisk med baseline - `python -m src.evaluation` kördes exakt en
gång (`temperature=0`), resultatet loggades till `data/evaluation_run.json`
och bedömdes manuellt mot samma facit (RÄTT/DELVIS/FEL). För de fyra
avvikande fallen i baseline (Y2, Y3, N3, N5) verifierades dessutom det
faktiska retrieval-underlaget genom att anropa `HybridSearcher.search()`
direkt med exakt samma frågetext, för att se vilka chunkar modellen faktiskt
fick i sin kontext - inte bara gissa utifrån svarstexten.

**Vad som ändrats i koden sedan baseline:** `src/hybrid_search.py` innehåller
nu en bolagsmedveten `_COMPANY_NET_RESULT_TERMS`-mappning
(`volvo: "periodens resultat"`, `hexatronic`/`skistar: "årets resultat"`) som
löser upp en platshållare i `_RATIO_TERM_EXPANSIONS` per bolag - exakt
åtgärd #1 från baseline-förslagen ovan, redan implementerad.

## Sammanfattning

**15 av 18 rätt (83 %),** upp från 14/18 (78 %) i baseline - men
sammansättningen har ändrats mer än totalsumman antyder: två tidigare
avvikande fall är fixade (Y3, N3), ett tidigare korrekt fall har brutits
(N4), och det fjärde (N5) är fortfarande fel men av en **annan, ny orsak**.

| Frågetyp | Rätt | Delvis | Fel | Andel rätt | Baseline |
|---|---|---|---|---|---|
| Enårsuppslag | 6 | 0 | 0 | 100 % | 100 % (oförändrat) |
| Flerårstrend/YoY | 4 | 1 | 0 | 80 % | 60 % |
| Nyckeltalsberäkning | 3 | 0 | 2 | 60 % | 60 % (oförändrad andel, men N3 fixad / N4 ny regression) |
| Kvalitativ | 2 | 0 | 0 | 100 % | 100 % (oförändrat) |
| **Totalt** | **15** | **1** | **2** | **83 %** | **78 %** |

## Detaljerat resultat

| ID | Typ | Bedömning | Baseline | Ändring |
|---|---|---|---|---|
| E1–E6 | Enårsuppslag | RÄTT (alla 6) | RÄTT | oförändrat |
| Y1 | YoY | RÄTT | RÄTT | oförändrat |
| Y2 | YoY | **DELVIS** | FEL | **förbättrat** |
| Y3 | YoY | **RÄTT** | DELVIS | **fixat** |
| Y4 | YoY | RÄTT | RÄTT | oförändrat |
| Y5 | YoY | RÄTT | RÄTT | oförändrat |
| N1 | Nyckeltal | RÄTT | RÄTT | oförändrat |
| N2 | Nyckeltal | RÄTT | RÄTT | oförändrat |
| N3 | Nyckeltal | **RÄTT** | DELVIS | **fixat och verifierat** |
| N4 | Nyckeltal | **FEL** | RÄTT | **ny regression** |
| N5 | Nyckeltal | FEL | FEL | oförändrad bedömning, **men ny grundorsak** |
| K1, K2 | Kvalitativ | RÄTT (båda) | RÄTT | oförändrat |

## De fyra jämförda fallen - vad som faktiskt hände

### Y2 (FEL → DELVIS) — Volvos rörelseresultat 2023→2024

**Nytt svar:** "Rörelseresultatet för Volvokoncernen uppgick till 66,6 (2024)
jämfört med 66,8 (2023), enligt kassaflödesanalysen (volvo_2024.pdf, s. 213)."
- Detta **matchar facit** (66.611 / 66.784 Mkr, avrundat till Mdr med en
  decimal - `volvo_2024.pdf::kassaflödesanalys::rad38`, en tioårig
  sammandragsrad, verifierad manuellt: "Rörelseresultat var 66,6 (2024),
  66,8 (2023)..."). Till skillnad från baseline citerar svaret nu korrekt
  Volvokoncernen-nivån, inte ett segment som om det vore koncernen -
  promptregel 7-brottet från baseline är borta.

**Men** svaret fortsätter: "Observera att olika källutdrag anger olika
rörelseresultat för samma år" och listar sedan Industriverksamheten
(62.198/63.063) och "Lastbilsverksamheten rapporterat rörelseresultat"
(45.966/48.893) som om de vore konkurrerande tolkningar, och avslutar med
"Vilket mått som avses framgår inte av frågan." Det är fel: frågan gäller
uttryckligen "Volvos rörelseresultat" (dvs. koncernen), vilket svaret redan
korrekt hade identifierat i första meningen. Att därefter så tvivel om ett
redan korrekt besvarat mått är en **självunderminerande efterhängsel** -
inte en felaktig siffra, men en presentation som riskerar att få en
läsare att inte lita på den rätta siffran den precis fått.

**Bedömning: DELVIS**, inte RÄTT - kärnsvaret är korrekt och rätt
attribuerat, men den onödiga tvekan om vilket mått som avses (trots att
frågan är entydig) är en kvalitetsbrist värd att åtgärda.

### Y3 (DELVIS → RÄTT) — SkiStars årets resultat 2023/24→2024/25

**Nytt svar:** "Årets resultat uppgick till 552 019 (2025-08-31) i 2024/25,
jämfört med 472 887 (2024-08-31) i 2023/24 – en ökning (skistar_2024-25.pdf,
s. 119)."

Matchar facit exakt på båda årens siffror, korrekt post ("Årets resultat",
inte "Årets totalresultat" som i baseline), korrekt riktning och korrekt
sida. Baseline-problemet (retrieval hämtade fel men snarlik radetikett för
2024/25) är löst - antingen genom förbättrad retrieval eller för att
frågeformuleringen denna gång lyfte fram rätt term. **Bedömning: RÄTT**,
en ren förbättring.

### N3 (DELVIS → RÄTT, verifierad) — SkiStars soliditet 2023/24

**Nytt svar:** "SkiStars soliditet 2023/24 var 42 % inklusive IFRS 16 och
56 % exklusive IFRS 16 (skistar_2023-24.pdf, s. 144)."

Baseline lämnade det här öppet ("kan inte utan vidare granskning avgöra om
s. 144 verkligen innehåller '42 %' som SkiStars egen redovisade siffra").
Jag har nu läst chunken för s. 144 direkt (`data/chunks/skistar_2023-24.json`,
chunk `skistar_2023-24.pdf::p144::1`) och den innehåller ordagrant SkiStars
egen tabell "Avstämning av alternativa nyckeltal":

```
Soliditet inklusive IFRS 16
Eget kapital 3 656 803    Balansomslutning 8 681 892    Soliditet, % 42
Soliditet exklusive IFRS 16
Eget kapital 3 727 113    Balansomslutning 6 651 080    Soliditet, % 56
```

Eget kapital (3 656 803) och Balansomslutning (8 681 892) är exakt samma
tal facit räknade fram sin 42,1 % ur - s. 144 är alltså bolagets EGEN
avrundade version (42 %) av precis samma kvot, inte en förväxlad sida. Enligt
promptens princip att föredra bolagets egen redovisade siffra framför en
egen beräkning är s. 144 dessutom ett **bättre** källval än facits s. 105.
"Exklusive IFRS 16"-tillägget (56 %) är korrekt återgivet bonusinformation
från samma tabell, inte en felaktig siffra. **Bedömning: RÄTT**, och
baseline-osäkerheten är nu upplöst i systemets favör.

### N5 (FEL → FEL, men NY grundorsak) — Volvos vinstmarginal 2023

**Nytt svar:** "Jag hittar inte svaret i underlaget. Vinstmarginal
(periodens resultat dividerat med nettoomsättning) redovisas inte som
nyckeltal i utdragen, och någon definition eller färdigberäknad siffra för
vinstmarginal finns inte med."

Baseline-buggen (query-expansionen letade efter "årets resultat" istället
för Volvos "Periodens resultat") är **fixad** - koden har nu
`_COMPANY_NET_RESULT_TERMS = {"volvo": ["periodens resultat"], ...}`
(`src/hybrid_search.py:134`). Jag körde om exakt samma fråga direkt mot
`HybridSearcher.search()` för att se vad fixen faktiskt hämtar:

```
volvo_2023.pdf::resultaträkning::rad20 | [59]  | fact  (Periodens resultat: 49.932 / 32.969)
volvo_2023.pdf::resultaträkning::rad25 | [59]  | fact  (Periodens övriga totalresultat - fel rad)
volvo_2023.pdf::resultaträkning::rad15 | [59]  | fact  (Periodens resultat, segmentsnedbrytning: 49.932 Volvokoncernen)
volvo_2023.pdf::p220::0                | [220] | text
volvo_2023.pdf::resultaträkning::rad44 | [223] | fact  (Periodens resultat, 11-årshistorik: 49.932...)
volvo_2023.pdf::p221::0                | [221] | text
volvo_2023.pdf::p220::1                | [220] | text
volvo_2023.pdf::p157::0                | [157] | text
```

Täljaren (49.932, "Periodens resultat", Volvokoncernen 2023) finns nu med
**tre gånger** - i tre nästan identiska varianter av samma rad
(segmentnedbrytning, kortversion, 11-årshistorik). Men **ingen enda chunk
med Nettoomsättning (552.764) kom med i top-8**, trots att "nettoomsättning"
uttryckligen är en av expansionstermerna. De tre näst-intill-dubblettraderna
för täljaren konkurrerade ut nämnaren ur kandidatdjupet. Modellens svar är
alltså **ärligt givet sin faktiska kontext** - den hade verkligen inte
nettoomsättningen framför sig - men resultatet är samma FEL-betyg som
baseline, av en helt annan orsak:

- **Baseline:** fel term i expansionen → hittade fel nyckeltal, svarade
  självsäkert men fel (rörelsemarginal istället för vinstmarginal).
- **Nu:** träffsäker term i expansionen → hittade rätt post tre gånger, men
  trängde ut den andra posten den behövde. Ärlig "hittar inte", men
  fortfarande obesvarad trots att svaret gick att räkna fram ur data som
  faktiskt finns i indexet.

**Bedömning: FEL** (ingen användbar siffra levererades), men detta är en
**ny bugg**, inte en kvarleva av den gamla. Åtgärdsförslag #1 från baseline
(bolagsmedveten terminologi) är alltså genomförd och gör sitt jobb för
täljaren - men avslöjar ett närliggande problem: dedupliceringen/rankningen
hanterar inte flera snarlika rader om SAMMA post bra, på bekostnad av den
andra postens utrymme i top-k.

## Ny observation utanför de fyra jämförda fallen: N4 har brutits (RÄTT → FEL)

Inte efterfrågad i uppdraget, men för viktig för att utelämna: **N4
(Hexatronics vinstmarginal 2024) var RÄTT i baseline och är FEL nu.**

**Nytt svar:** "Vinstmarginal definieras inte i underlaget, och någon post
med den benämningen finns inte. Däremot redovisas EBITA-marginalen för
2024: 10,6 procent."

Jag körde samma verifiering som för N5: `HybridSearcher.search()` med exakt
frågetexten. Båda ingredienserna finns i topp-8 och är entydigt märkta:

```
hexatronic_2024.pdf::resultaträkning::rad0  | [100] | Nettoomsättning: 7 581 (2024)
hexatronic_2024.pdf::resultaträkning::rad16 | [100] | Årets resultat: 344 (2024)
hexatronic_2024.pdf::resultaträkning::rad19 | [100] | Årets resultat: 344 (2024) (dublett)
```

344 / 7 581 = 4,5 %, exakt facit. Detta är alltså **ingen retrieval-bugg**
- båda talen låg i modellens kontext, precis som i N1 (Hexatronics
rörelsemarginal 2023) där modellen FRAMGÅNGSRIKT räknade ut kvoten från två
separata fakta-rader. Här vägrade den istället att räkna, och bytte i
stället ut till ett helt annat nyckeltal (EBITA-marginal) som den hittade
färdigredovisat i löptext.

Detta pekar mot samma mönster som N5 fast i genereringssteget snarare än
retrieval: modellen är **inkonsekvent** i om den är villig att beräkna ett
nyckeltal från två råa rader eller kräver en färdig siffra. N1 lyckades,
N4 och N5 (delvis) misslyckades med i grunden samma uppgift. Detta är
värt en egen utredning - trolig kandidat är prompten/systeminstruktionen
för svarsgenerering (`src/answer.py` eller `src/llm.py`), inte
retrieval-lagret som de föreslagna åtgärderna i baseline riktade in sig på.

## Sammanfattande bedömning

Åtgärd #1 från baseline (bolagsmedveten terminologi) är genomförd och
**löser sitt avsedda problem** (N5:s ursprungliga fel-metod är borta, Y3 är
fixad). Men totalsiffran 15/18 (83 %) mot 14/18 (78 %) döljer att
förbättringen inte är entydig:

1. **Två äkta förbättringar**, verifierade mot källdata: Y3 (rätt post,
   rätt sida) och N3 (bekräftat att s. 144 är en legitim, till och med
   bättre, källa än facits egen).
2. **En kvarstående brist med ny grundorsak**: N5 är inte längre en
   "fel metod"-bugg utan en "flera dubbletter av samma rad tränger ut den
   andra raden"-bugg i retrieval-rankningen.
3. **En ny regression**: N4, som var RÄTT i baseline, är nu FEL - inte på
   grund av retrieval (data fanns i kontext) utan för att modellen vägrade
   beräkna en kvot den tidigare (N1) visat sig kunna beräkna.
4. **En kvalitetsbrist utan sifferfel**: Y2 har nu rätt tal och rätt
   attribution, men häver sin egen korrekta slutsats med en onödig
   "vilket mått avses"-brasklapp.

**Förslag på åtgärder** (inte genomförda i denna omgång - flaggas för
nästa steg, i linje med projektets princip att stanna vid osäkerhet snarare
än att gissa vidare):
1. Undersök varför modellen (N4, N5) ibland vägrar beräkna ett nyckeltal
   från två närvarande råa poster trots att den bevisligen kan (N1) - detta
   är sannolikt en prompt-/instruktionsfråga i svarsgenereringen, inte
   retrieval.
2. Inför en avdubblingsregel i retrieval som slår samman/väljer EN
   representant när flera hämtade chunkar uppenbart beskriver samma
   post-och-år (N5:s tre "Periodens resultat"-rader), så att kandidatdjupet
   inte slösas på dubbletter av samma fakta på bekostnad av en annan
   efterfrågad post.
3. En regel som dämpar överflödig hedging när modellen redan gett ett
   entydigt, korrekt attribuerat svar (Y2) - onödig osäkerhet om en redan
   besvarad fråga är i sig en kvalitetsbrist, även när ingen siffra är fel.

---

# Uppföljning — 2026-09-12: prompt-fix för åtgärd #1 (N4/N5-beräkningsvägran)

**Ändring:** `src/prompts.py`, regel 8 och 9 (BERÄKNINGAR). Rotorsaken från
föregående avsnitts åtgärdsförslag #1 var att modellen (N4, N5) ibland
vägrade beräkna ett efterfrågat standardnyckeltal (t.ex. vinstmarginal) trots
att båda ingående posterna fanns i kontexten, och i N4:s fall bytte ut det
mot ett annat, färdigredovisat närliggande mått (EBITA-marginal) istället
för att räkna. Hypotesen: regel 9 ("räkna aldrig om alternativa nyckeltal...
återge bolagets egen siffra istället") övergeneraliserades av modellen till
att gälla ALLA nyckeltal med en näraliggande publicerad siffra, inte bara
de uttryckliga "alternativa" måtten (justerat EBITDA, organisk tillväxt)
regeln avsåg.

**Fix:** skärpte regel 8 med en explicit mening om att avsaknad av en
FÄRDIG siffra inte är samma sak som att svaret saknas för standardnyckeltal,
och skärpte regel 9 med ett uttryckligt förbud mot att byta ut det
efterfrågade nyckeltalet mot ett annat närliggande mått bara för att det
råkar finnas färdigredovisat.

**Verifiering:**
1. `pytest tests/` - 155/156 gröna. Den enda röda
   (`test_hybrid_search.py::test_multi_year_query_covers_all_mentioned_years`)
   är en retrieval-nivå-test, opåverkad av prompt-ändringen (rör exakt
   åtgärdsförslag #2 ovan, inte #1) - fanns redan innan denna ändring.
2. `python -m src.evaluation` kördes om i sin helhet. Resultat för de två
   berörda frågorna:

   - **N4** (Hexatronics vinstmarginal 2024): **FEL → RÄTT.** Modellen
     räknar nu ut 344 / 7 581 = ca 4,5 % med formel och båda
     källhänvisningarna utskrivna, exakt facit.
   - **N5** (Volvos vinstmarginal 2023): **FEL → RÄTT.** Modellen räknar nu
     ut 49.932 / 552.764 = ca 9,0 % (Volvokoncernen), med båda posterna
     korrekt källhänvisade (s. 59 för Periodens resultat, s. 220 för
     Nettoomsättning). Detta löste alltså även N5, trots att den tidigare
     diagnosen (retrieval trängde ut Nettoomsättning-raden) inte är
     patchad - i den här körningen fanns båda posterna i kontexten, och
     med den skärpta regeln användes de.

   Övriga 16 frågor kontrollerades om oförändrade: alla fortsatt RÄTT,
   inklusive N1/N2/N3 (ingen regression). Y2:s tidigare hedging
   ("vilket mått som avses framgår inte av frågan") var borta i den här
   körningen också - svaret gav den korrekta Volvokoncernen-siffran direkt
   och listade segmentsiffrorna som ren tilläggsinformation utan att så
   tvivel om huvudsvaret. Detta ingick inte i den här ändringen och kan
   vara körning-till-körning-variation snarare än en effekt av
   prompt-fixen; bekräftas först vid en framtida omkörning.

**Nytt resultat: 18 av 18 rätt (100 %)** i denna körning, upp från 15/18
(83 %) i föregående avsnitt.

**Kvarstående, inte åtgärdat i detta steg:**
- Åtgärdsförslag #2 (avdubblingsregel i retrieval) är fortfarande relevant
  - `test_multi_year_query_covers_all_mentioned_years` visar att samma
  klass av crowding-problem som orsakade N5:s ursprungliga fel fortfarande
  finns kvar i retrieval-lagret, det råkade bara inte slå igenom i den här
  evaluerings-körningen. En enda lyckad körning är inte bevis på att buggen
  är borta - bara att den inte alltid triggas.
- 100 % på 18 frågor vid en enda körning (temperature=0, men LLM-API:er är
  inte garanterat deterministiska) är inte samma sak som ett bevisat
  stabilt system. Rekommenderar att detta facit körs om ytterligare någon
  gång innan det räknas som en bekräftad baseline.

## Stabilitetskontroll — andra omkörningen, 2026-09-12

Körde `python -m src.evaluation` en tredje gång totalt (andra gången efter
prompt-fixen), utan någon kodändring emellan, för att skilja en riktig fix
från en enstaka tur.

**N4 och N5 höll: RÄTT igen, båda gångerna.** Samma formel, samma
källhänvisningar, samma resultat (344/7 581 → 4,5 %; 49.932/552.764 →
9,0 %). Två av två gånger efter fixen räknar modellen nu ut nyckeltalet
istället för att neka eller byta ut det - ett rimligt stöd för att
prompt-fixen faktiskt sitter, inte bara råkade träffa rätt en gång.

**Y2 var INTE stabil.** Kärnsvaret var återigen korrekt (66,6 vs 66,8,
minskning med 0,2), men den självunderminerande hedgen var tillbaka:
"Vilken siffra som avses beror på vilken del av verksamheten frågan
gäller." - trots att frågan otvetydigt gäller Volvokoncernen som helhet
och modellen redan givit rätt svar i första meningen. Föregående körning
(samma prompt, samma kod) saknade den hedgen helt. Detta är alltså
**genuin körning-till-körning-variation** i modellen, inte något min
ändring av regel 8/9 påverkade (Y2 rör inte beräkningsreglerna) - och
bekräftar att åtgärdsförslag #3 (dämpa överflödig hedging) fortfarande är
obehandlat och kvarstår som öppen brist, oavsett vilken körning man råkar
titta på.

**Sammanfattning av de tre körningarna för de fyra ursprungligt avvikande
frågorna:**

| Fråga | Körning 1 (baseline) | Körning 2 (efter retrieval-fixar) | Körning 3 (efter prompt-fix) | Körning 4 (stabilitetskontroll) |
|---|---|---|---|---|
| Y2 | FEL | DELVIS (hedge) | RÄTT (ingen hedge) | DELVIS (hedge tillbaka) |
| Y3 | DELVIS | RÄTT | RÄTT | RÄTT |
| N3 | DELVIS | RÄTT | RÄTT | RÄTT |
| N4 | RÄTT | FEL (regression) | RÄTT | RÄTT |
| N5 | FEL | FEL (annan orsak) | RÄTT | RÄTT |

Y3 och N3 är stabilt fixade (retrieval-lagret). N4 och N5 är nu stabilt
fixade över två körningar (prompt-lagret). Y2 är den enda kvarstående
instabila punkten - rätt tal varje gång, men presentationen växlar mellan
en ren och en självunderminerande version beroende på körning.

## Försökt och backad åtgärd — 2026-09-12: avdubblingsregel i retrieval (åtgärdsförslag #2)

**Försök:** `_diversify_by_fiscal_year()` i `src/hybrid_search.py` - vid en
flerårsfråga (t.ex. "...från 2023 till 2024?") varvades den fuserade
kandidatlistan strikt per räkenskapsår innan topp-k valdes ut, så att inte
ett enda dokument kunde ta alla platserna. Detta fick den sedan tidigare
röda `test_multi_year_query_covers_all_mentioned_years`
(`tests/test_hybrid_search.py`) att bli grön, och alla 156 tester passerade.

**Varför den backades:** verifiering mot den faktiska produktionsinställningen
(`answer_question()` använder `top_k=8`, inte testets `top_k=10`) visade att
fixen gjorde Y2 SÄMRE, inte bättre - `python -m src.evaluation` gav Y2 som
rent FEL (bara Lastbilsverksamhetens segmentsiffror, inget
Volvokoncernen-tal alls), en regression jämfört med de två föregående
körningarna.

**Grundorsak till varför fixen slog fel:** kollade var
`volvo_2023.pdf::resultaträkning::rad9` (den "saknade" 2023-raden testet
efterlyste) faktiskt rankas i den ofiltrerade fusionslistan: **plats 103 av
185** kandidater. Den är alltså inte en korrekt rad som trängs undan av
dubbletter - den är genuint lexikalt olik frågan, eftersom volvo_2023.pdf:s
rad bara nämner "2023"/"2022" medan frågan nämner "2023"+"2024" (volvo_2024.pdf:s
rader vinner ärligt genom att nämna BÅDA årtalen). Att tvinga in en
2023-kandidat i topp-k innebär därför att tvinga in en irrelevant rad -
inte att avslöja en dold korrekt rad - på bekostnad av
`volvo_2024.pdf::kassaflödesanalys::rad38` (den tioåriga sammandragsraden
som redan innehåller korrekta 2023/2024-siffror och som gav Y2 rätt svar i
de två föregående körningarna).

**Slutsats:** testets underliggande oro är fortfarande legitim (skydda mot
omräknade jämförelsetal mellan årsrapporter, exakt det Y2-analysen ovan
identifierade som en risk), men en generell "tvinga fram dokumentspridning"-
lösning är fel verktyg här - det botar symptomet (testets mätvärde) på
bekostnad av den faktiska svarskvaliteten. Koden i `src/hybrid_search.py` är
återställd till sitt tidigare, verifierade läge (18/18 i föregående
avsnitt). `test_multi_year_query_covers_all_mentioned_years` är alltså
KVAR röd - ett medvetet, dokumenterat beslut, inte en förbisedd
regression. En framtida lösning behöver sannolikt vara mer riktad (t.ex.
kräva minst en kandidat av samma SEKTION/rad-typ som redan finns i topp-k,
inte bara samma år, eller acceptera testets nuvarande begränsning och
skriva om det för att spegla att en enda välvald sammandragsrad kan vara
en giltig, fullständig källa för en flerårsfråga).
