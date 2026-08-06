# Beslutslogg — Fas 2 (chunking)

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
