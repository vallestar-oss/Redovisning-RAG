# Beslutslogg — Fas 3 (embeddings och vektordatabas)

## 2026-08-06 — Lokala embeddings under utveckling

**Beslut:** kör embeddings lokalt med `sentence-transformers`
(`all-MiniLM-L6-v2`, 384 dimensioner) istället för ett moln-API i det här
läget.

**Varför:**
- **Kostnad** - vi itererar mycket under utveckling (chunkstorlek,
  retrieval-parametrar, ev. omindexering av alla chunkar). Ett API som
  DeepSeek/OpenAI skulle debitera per körning; en lokal modell kostar bara
  CPU-tid.
- **Inget nätverksberoende under utveckling** - snabbare iteration, och vi
  slipper att retrieval-testerna (steg 4-5 i den här fasen) blir beroende
  av ett externt API:s tillgänglighet/rate limits.
- **Tillräckligt bra för svenska** - `all-MiniLM-L6-v2` är flerspråkigt
  tränad och används brett för just den här typen av retrieval-uppgift;
  räcker gott för att verifiera att pipelinen fungerar innan vi eventuellt
  byter till en kraftfullare modell.

Modellen laddas ned första gången den används (cachas lokalt av
`sentence-transformers`/`huggingface_hub`) och ger 384-dimensionella
embeddings per chunk.

## 2026-08-06 (forts.) — Indexering i Chroma

`src/vectorstore.py` bygger ett lokalt, persistent Chroma-index
(`data/chroma/`, gitignorad) av samtliga chunkar från Fas 2. Varje chunks
metadata (dokument, bolag, år, sida, sektion, text/tabell) sparas
tillsammans med embeddingen så att en sökträff alltid går att källhänvisa.

**Anpassning:** Chroma tillåter bara str/int/float/bool som
metadatavärden - inte listor och inte `None`. Fas 2:s `pages`-fält (en
lista, för att stödja flersidiga tabellchunkar som Volvos delade
balansräkning) lagras därför som en kommaseparerad sträng ("87,88").
`section` (som är `None` för textchunkar) lagras som tom sträng.

Indexet byggs om från grunden vid varje körning (`delete_collection` följt
av `create_collection`) för att undvika dubbletter vid omindexering efter
ändringar i chunkningen.

**Resultat:** 5 619 chunkar indexerade från alla 9 dokument.

## 2026-08-06 (forts.) — Sökfunktion och retrieval-verifiering: svag träffsäkerhet, två grundorsaker hittade och åtgärdade

`src/search.py` bygger en enkel `Searcher.search(query, top_k)` ovanpå
Chroma-indexet. Manuell verifiering med 8 kända frågor kopplade till
nyckeltalen (t.ex. "Vad var Hexatronics nettoomsättning 2023?") visade att
**ingen enda fråga hittade rätt tabellchunk i topp 5** - för en fråga låg
den korrekta chunken inte ens bland topp 100 av 5619. Detta bekräftade
forskningens varning om att retrieval ofta är flaskhalsen. Flaggat
explicit för användaren istället för att gissa vidare, enligt
arbetsprincipen.

**Grundorsak 1 (fixad, men otillräcklig ensam):** `client.create_collection()`
i `src/vectorstore.py` angav inget avståndsmått, så Chroma föll tillbaka
på L2 (euklidiskt avstånd) istället för cosinuslikhet - fel mått för
sentence-transformers embeddings. Fixat med
`metadata={"hnsw:space": "cosine"}`. Rankningen var dock i praktiken
oförändrad efter denna fix ensam, vilket visade att ett djupare problem
fanns kvar.

**Grundorsak 2 (huvudorsaken):** tabellchunkarnas text var formaterad som
tätt "etikett: värde1 / värde2" (t.ex. `"Nettoomsättning 5, 6, 15: 8 150 /
6 574"`). Ett kontrollerat experiment jämförde avståndet mellan frågan och
samma sakuppgift i två format:

| Format | Avstånd till frågan |
|---|---|
| `"Nettoomsättning 5, 6, 15: 8 150 / 6 574"` | 0,335 |
| `"Hexatronics nettoomsättning år 2023 var 8 150 MSEK..."` | 0,180 |

Nästan en halvering - `all-MiniLM-L6-v2` är tränad på löpande språk och
matchar dåligt mot tätt tabellformat, oavsett avståndsmått.

**Bidragande faktor:** en hel huvudräkning (10-20 rader) som EN chunk (Fas
2:s princip: dela aldrig en tabell) gör att embeddingen blir ett
genomsnitt av många poster, vilket späder ut relevansen för en fråga om en
enskild post.

**Åtgärd (två kombinerade fixar, `src/chunking.py`):**

1. **Naturligt formulerad tabelltext.** Hela tabellchunkens text renderas
   nu som en löpande text av naturligt formulerade meningar - en per rad
   (t.ex. `"Hexatronic resultaträkning 2023: Nettoomsättning ... var 8 150
   (föregående period: 6 574)."`) - istället för det täta
   etikett/värde-formatet. Chunkens STRUKTUR (en chunk per huvudräkning,
   aldrig delad) är oförändrad - bara texten som embeddas är
   omformulerad.
2. **Fakta-chunkar per rad.** Utöver helhetschunken skapas nu en extra,
   granulär "fakta"-chunk (`chunk_type="fact"`) per tabellrad, med samma
   naturliga meningsformulering. Dessa ger precision för frågor om en
   specifik post, medan helhetschunken behålls för frågor som kräver hela
   räkningen i sammanhang.

**Resultat efter fix:** samma 8 frågor omkörda - 6 av 6 kvantitativa
frågor hittar nu rätt fakta-chunk inom topp 5 (flera på rank 1-3), mot 0
av 6 innan. Index: 6821 chunkar (5619 helhets-/textchunkar + ~1200
fakta-chunkar).

**Testsviten justerad:** `tests/test_chunking.py`s
dubbett-kontroll begränsades till `chunk_type == "text"` - table/fact-
chunkar kan legitimt upprepa samma etikett/värde (t.ex. en rad som
förekommer likadant i både huvudräkningen och Volvos elvaårsöversikt,
eller en post som anges både som delsumma och slutsumma i samma
räkning) - det är verklig, upprepad data i källdokumentet, inte ett
chunkningsfel.

## 2026-08-06 (forts.) — Kvalitetslyft: kolumnkoppling och SkiStars koncernräkningar

Två kvarvarande svagheter åtgärdade efter genomgång av vad som fortfarande
inte höll högsta kvalitet.

### 1. Värden var inte kopplade till rätt kolumn (gav FEL SVAR)

`_assign_to_columns` returnerade tidigare bara talen i x-ordning,
komprimerade utan luckor. Så snart en rad saknade värden för någon period
förskjöts allt som följde. Konkret exempel från Volvos elvaårsöversikt,
raden "Skulder som innehas för försäljning":

| | 2023 | 2022 | 2021 | 2020 | 2019 | 2018 | 2017 | 2016 |
|---|---|---|---|---|---|---|---|---|
| I PDF:en | 8.157 | – | – | 6.638 | 5.927 | – | – | 148 |
| Lagrades som | 8.157 | 6.638 | 5.927 | 148 | ... | | | |

En läsare som tolkade listan positionellt fick alltså 2022 = 6.638, när
det värdet i själva verket hör till 2020. Det här är den enda av de
kvarvarande bristerna som kunde ge ett **direkt felaktigt svar** på en
fråga inom scope (flerårstrend är prioritet 2 i docs/SCOPE.md).

**Åtgärd:** `FinancialRow` bär nu `columns` (kolumnrubrikerna) och en
`values`-lista som är POSITIONELLT KOPPLAD till dem, med `None` där
kolumnen saknar värde. Hjälparna `present_values` och `by_column()` ger
åtkomst utan respektive med kolumnkoppling. Ett nytt test
(`test_values_are_positionally_aligned_with_columns`) kräver att antalet
värdeplatser alltid är lika med antalet kolumner.

**Följdförbättring - segmentnamn.** Volvos huvudräkningar delar samma
årtal på fyra segment, så kolumnrubrikerna blev tvetydiga ("2023" fyra
gånger). `_find_segment_labels` letar nu efter segmentrubrikraden ovanför
årsraden och bygger sammansatta rubriker. "Summa tillgångar" blir därmed
otvetydig:

```
Industriverksamheten 2023 = 439.807
Financial Services 2023   = 270.307
Elimineringar 2023        = –36.046
Volvokoncernen 2023       = 674.068   <- koncernens totalsiffra
```

Segmentraden identifieras robust genom att dess fraser ska vara färre än
antalet kolumner OCH dela kolumnantalet jämnt - det utesluter Volvos
mellanliggande "31 dec"-rad, som ger exakt lika många fraser som kolumner.

### 2. SkiStar kördes på moderbolagsnivå (täckningslucka)

SkiStar extraherades tidigare från moderbolagets räkningar eftersom
koncernsidorna gav fel resultat. Det innebar att SkiStars **koncern**-
nyckeltal helt saknades i systemet. Båda underliggande orsakerna är nu
åtgärdade:

- **Tabeller sida vid sida** - löstes redan av regionsstödet som byggdes
  tidigare i Fas 2/3; koncernsidornas två regioner (t.ex. "Koncernens
  rapport över totalresultat" / "Övrigt totalresultat") separeras korrekt.
- **Inbäddat stapeldiagram på kassaflödessidan** - diagrammets
  axeletiketter ("MSEK", "1 500", "19/2020/2121/...") låg till höger om
  tabellens sista kolumn och klistrades in i radetiketterna. Åtgärdat med
  en generell regel: i en finansiell tabell står radetiketten alltid till
  VÄNSTER om värdekolumnerna, så allt till höger om sista kolumnen
  (+ tolerans) hör inte till raden.

**Verifierat:** alla tre SkiStar-år balanserar nu på koncernnivå
(tillgångar = eget kapital + skulder: 8 760 992 / 8 681 892 / 8 762 467).
`_LEVEL_BY_COMPANY_PREFIX` är därmed tömd - samtliga bolag körs på
koncernnivå. Testsviten importerar nu nivåvalet från `src/pipeline.py`
istället för att duplicera det, så att test och produktionskod inte kan
glida isär.

## 2026-08-06 (forts.) — Byte av embeddingmodell efter mätning

Vid verifieringen av ovanstående fixar syntes att retrieval-precisionen var
svagare än den sett ut vid det tidigare stickprovet. Ett kontrollerat test
visade att `all-MiniLM-L6-v2` knappt skiljer på svenska facktermer:

| Fråga | Avstånd rätt svar | Avstånd fel svar |
|---|---|---|
| "Hexatronics nettoomsättning 2023" | 0,2819 | 0,2887 (*nettoinvesteringar*) |
| "SkiStars summa tillgångar" | 0,2548 | **0,2514** (*summa långfristiga skulder*) |

Marginalerna är ~0,005 - i praktiken slumpmässigt. Modellen är
engelskcentrerad och saknar tillräcklig svensk semantik.

**Mätning istället för gissning.** Ett facit byggdes med 8 svenska
nyckeltalsfrågor och kända korrekta chunk-id:n, och fyra modeller kördes
mot HELA korpusen (6855 chunkar):

| Modell | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---|---|---|---|
| all-MiniLM-L6-v2 (tidigare) | 25 % | 62 % | 75 % | 0,476 |
| paraphrase-multilingual-MiniLM-L12-v2 | 0 % | 12 % | 38 % | 0,108 |
| KBLab/sentence-bert-swedish-cased | 25 % | 25 % | 25 % | 0,295 |
| **intfloat/multilingual-e5-base** | **38 %** | 62 % | 75 % | **0,527** |

**Beslut:** byt till `intfloat/multilingual-e5-base`. Den vinner på
Recall@1 och MRR och är oförändrad på Recall@3/@5.

Två noterbara resultat: den uppenbara kandidaten
(`paraphrase-multilingual-MiniLM-L12-v2`) var *dramatiskt sämre* än
baslinjen, och den svenskspecifika KBLab-modellen presterade också sämre.
Utan mätningen hade ett "rimligt" modellval med god sannolikhet försämrat
systemet.

**Implementationsdetalj:** E5-modeller är tränade med asymmetriska prefix
och tappar mätbart utan dem - dokument ska embeddas som `passage: ...` och
frågor som `query: ...`. Prefixen definieras därför tillsammans med
modellnamnet i `src/vectorstore.py` (`embed_passages` / `embed_query`), och
`src/search.py` importerar dem, så att indexering och sökning inte kan
använda olika konventioner.

**Kvarstående begränsning:** även den bästa modellen hade enskilda
katastroffall (SkiStars "summa tillgångar" på rank 42). Åtgärdat med
hybridsökning, se nästa avsnitt.

## 2026-08-06 (forts.) — Hybridsökning: metadatafilter + vektor + BM25

Frågorna i vårt scope bär tre signaler som ren vektorsökning inte
utnyttjar:

    "Vad var SkiStars nettoomsättning 2023/24?"
         |            |               |
      bolag        nyckeltal      räkenskapsår

`src/hybrid_search.py` använder alla tre:

1. **Metadatafilter** - bolag och räkenskapsår tolkas ur frågan och används
   som hårt filter mot Chromas metadata. Sätts bara när tolkningen är
   entydig OCH matchar värden som finns i indexet; annars ofiltrerat.
   Automatisk återgång till ofiltrerad sökning om filtret gav för få
   träffar.
2. **Vektorsökning** - semantisk likhet (multilingual-e5-base).
3. **BM25** - lexikalisk matchning, som fångar exakta termer där
   embeddingen är osäker.

Vektor- och BM25-listorna slås ihop med Reciprocal Rank Fusion. RRF valdes
för att den bara använder RANGORDNING, inte poäng - de två systemens
poängskalor är inte jämförbara och skulle annars kräva normalisering med
godtyckligt valda vikter.

**Årstolkning för brutet räkenskapsår.** SkiStars räkenskapsår heter
"2023-24" i indexet, men en användare skriver naturligt "2023/24" eller
bara "2024". Årskandidater valideras därför mot DET IDENTIFIERADE BOLAGETS
år, inte mot alla år i indexet: "SkiStars omsättning 2024" blir "2023-24"
(året som slutar i augusti 2024), medan samma årtal för Volvo blir "2024".
Utan bolagsspecifik validering hade filtret gett noll träffar.

**Resultat (samma facit som modelljämförelsen):**

| | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---|---|---|---|
| Vektorsökning (e5-base) | 38 % | 62 % | 75 % | 0,527 |
| **Hybrid** | 62 % | **100 %** | **100 %** | 0,771 |

Ingen fråga blev sämre. Det tidigare katastroffallet (rank 42) ligger nu
inom topp 3.

**Val av RRF-konstant - en avvägning värd att förstå.** Vid felsökning av
den svåraste frågan visade det sig att BM25 rankade rätt svar som #1 medan
vektorsökningen rankade det som #15 (modellen skiljer genuint inte på
"summa tillgångar", "summa skulder" och "summa eget kapital"). Med
standardvärdet k=60 blir rank 1 och rank 15 nästan likvärdiga i RRF, så
vektorns självsäkra men felaktiga förstaplats röstade ner BM25:s korrekta.

Uppmätt sveptest:

| k | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---|---|---|---|
| 1-3 | 62 % | 88 % | 100 % | 0,78 |
| **5** | 62 % | **100 %** | **100 %** | 0,771 |
| 10-20 | 62 % | 88 % | 100 % | 0,78 |
| 40-60 | **75 %** | 88 % | 88 % | **0,828** |

Höga k ger bäst Recall@1 och MRR - men missar en fråga helt (rank 8). I ett
RAG-system läser språkmodellen ALLA topp-k chunkar, så att svaret
överhuvudtaget finns i kontexten (Recall@5) väger tyngre än att det ligger
exakt först. **Valt k=5.** Hela bandet k=1..20 ger Recall@5=100 %, så valet
är inte känsligt för det exakta värdet.

**Facit är nu en del av testsviten** (`tests/test_hybrid_search.py`), inte
bara ett engångsexperiment: retrieval är systemets vanligaste flaskhals och
en tyst försämring vid framtida ändringar (ny modell, ändrad chunkning,
annan RRF-konstant) vore annars svår att upptäcka. Testet
`test_recall_at_5_is_complete` failar om något facit-svar hamnar utanför
topp 5.

**Kvarstående begränsning:** facit omfattar 8 frågor, alla kvantitativa och
välformulerade. Det säger inget om vagare frågor ("hur har det gått för
bolaget?"), frågor utan bolagsnamn, eller kvalitativa risk-/utsiktsfrågor.
Facit bör utökas när fler frågetyper testas i senare faser.
