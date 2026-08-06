# Beslutslogg — Fas 2 (chunking)

## 2026-08-06 — Chunkningsstrategi

**Två helt olika strategier, en per innehållstyp** (`src/chunking.py`):

- **Tabellchunkar:** en huvudräkning (resultat-/balans-/kassaflödesräkning)
  blir alltid EN chunk, oavsett hur många sidor den spänner över (Volvo
  delar t.ex. balansräkningen TILLGÅNGAR / EGET KAPITAL OCH SKULDER på två
  sidor - dessa hör ihop och får aldrig delas). Att aldrig klippa mitt i en
  tabell var den tydligaste lärdomen från Fas 1: en halv rad utan sina
  kolumnrubriker är meningslös vid retrieval.
- **Textchunkar:** grupperar hela MENINGAR upp till en målstorlek (~1000
  tecken, hårt tak 1400) och klipper aldrig mitt i en mening. Vald framför
  klassisk stycke-baserad chunkning eftersom `pdfplumber`s textextraktion
  inte ger tillförlitliga styckesmarkörer i denna typ av flerkolumniga
  PDF-layouter (ingen blankrad mellan stycken i den extraherade texten) -
  meningen är den minsta semantiska enhet vi kan lita på givet det
  underlaget. Sidor utan meningsskiljande punktuering (leveransstatistik,
  ESRS-indextabeller) faller tillbaka på radbaserad delning istället för
  att bli en enda överdimensionerad chunk.

**Metadata per chunk:** `document`, `company`, `fiscal_year`, `chunk_type`
("text"/"table"), `section` (huvudräkningstyp för tabellchunkar, annars
`None`), `pages` (lista - stödjer flersidiga tabellchunkar). Detta är vad
som gör källhänvisning möjlig i Fas 3+.

**Rättelse 2026-08-06 (upptäckt i Fas 4): fakta-chunkar ärvde fel sidor.**
Varje fakta-chunk fick hela räkningens sidlista istället för radens egen
sida. För Volvo spänner "balansräkning" över två helt olika tabeller - den
segmenterade huvudräkningen (s. 62-63) och elvaårsöversikten (s. 224) - så
en siffra hämtad från elvaårsöversikten hänvisades även till s. 62-63, där
den tabellen inte finns. Källhänvisningen gick alltså inte att slå upp,
vilket underminerar hela poängen med Fas 4. Åtgärdat genom att behålla
radens ursprungssida genom chunkningen; fakta-chunkar har nu exakt ett
sidnummer. Två tester (`test_fact_chunks_cite_exactly_one_page`,
`test_fact_chunk_pages_exist_in_source_document`) skyddar mot återfall.

**Testverifiering (steg 3) hittade två buggar** som fixades: dels
överdimensionerade chunkar på sidor utan meningsskiljande punktuering
(löst med radbaserad fallback-delning), dels triviala dubbletter av
återkommande sidfötter/rubriker samt upprepad text från en redan känt
problematisk sida (löst med dokumentnivå-deduplicering av identiskt
textinnehåll). 45/45 tester gröna efter fix.

**Stickprov (steg 4) bekräftade:** kärninnehållet (löptext i
förvaltningsberättelsen, samtliga huvudräkningar) är genomgående rent och
sammanhängande. Kvarvarande svagheter (notdisclosure-tabeller utan
kolumnrubriker, infografik-tunga hållbarhetssidor, personprofilrutnät för
styrelse/ledning) ligger uteslutande i innehåll som redan är utanför scope
enligt docs/SCOPE.md.

## 2026-08-06 — Rättelse i Fas 1: spaltmedveten textextraktion

**Upptäckt under Fas 2, steg 1.** Vid stickprov av de första textchunkarna
visade det sig att löptext från flerspaltiga sidor (vanligt i
förvaltningsberättelsens narrativa avsnitt) var obegriplig - meningar från
vänster och höger spalt var sammanvävda rad för rad, t.ex.:

> "2023 var ett år av ytterligheter. Första halvåret redo- Fortsatt
> diversifiering genom visade vi de starkaste försäljningssiffrorna
> någonsin, expansion i nya strategiska..."

**Rotorsak:** `page.extract_text()` (använt i Fas 1:s `src/extraction.py`)
läser ord i y-position-ordning över hela sidans bredd. På en tvåspaltig
sida ligger höger spalts första rad på samma höjd som vänster spalts
första rad, så de blandas ihop som om de vore en enda löpande text. Detta
missades i Fas 1 eftersom testerna där bara verifierade siffror i
tabeller - aldrig läsbarheten i den vanliga löptexten.

**Beslut:** användaren valde att gå tillbaka och fixa detta i Fas 1:s
extraktionsmodul (`src/extraction.py`) hellre än att bygga vidare på en
trasig textgrund i Fas 2, eftersom flera scope-frågor (kvalitativa frågor
om risker/utsikter) är beroende av begriplig löptext.

**Lösning:** `_extract_page_text()` upptäcker en eventuell tvåspaltsgata -
det bredaste sammanhängande vertikala tomrummet i sidans mittparti (30-70%
av bredden) som inget ord korsar. Om en tydlig gata (≥10pt) hittas läses
vänster spalt i sin helhet, sedan höger spalt i sin helhet, istället för
att väva ihop dem. Sidor utan en tydlig gata (enkolumniga sidor,
försättsblad, sidor utan text) faller tillbaka på standardbeteendet
(`page.extract_text()`).

**Bugg 1 (fixad under utveckling):** rubriker/underrubriker har betydligt
större radhöjd än brödtext (33pt respektive 18pt mot brödtextens ~10pt i
våra dokument) och spänner medvetet över hela sidbredden som ett
designelement. Sådana ord blockerade annars gatan över hela sidan, så att
ingen gata alls hittades trots att kroppstexten under var tydligt
tvåspaltig. Fixat genom att utesluta ord med radhöjd > 14pt från
gat-sökningen (`_MAX_BODY_TEXT_HEIGHT`).

**Bugg 2 (fixad under utveckling):** en rubrik som korsar den funna gatan
(eftersom den designmässigt spänner över båda spalterna) hamnade i
varken vänster- eller högerbucket när tilldelningen gjordes via
`x1 <= gata` / `x0 >= gata` - ordet försvann tyst ur texten. Fixat genom
att tilldela efter ordets MITTPUNKT istället, så att varje ord alltid
hamnar i exakt en bucket.

**Verifierat:** Hexatronic sida 4 (VD-ordet) går från fullständigt
osammanhängande till en helt läsbar, sammanhängande vänsterspalt följt av
högerspalten. Stickprov på Volvo och SkiStar bekräftade rimligt resultat.
Fullständig testsvit (36 tester, alla 9 dokument) kördes om efter ändringen
- alla gröna, ingen regression i tabellextraktionen (som är oberoende av
`extraction.py` och bygger direkt på `tables.py`).

**Känd kvarvarande begränsning:** sidor som blandar en riktig tvåspaltig
brödtext MED en separat sidopanel-navigering (t.ex. SkiStars
innehållsförteckningssida) kan fortfarande ge en delvis ihopblandad
läsordning, eftersom det då egentligen finns tre "spalter" att skilja åt,
inte två. Detta bedöms som lägre prioritet eftersom sådana sidor
(innehållsförteckningar, navigering) sällan är källan till substantiellt
sakinnehåll för de frågetyper som är i scope.
