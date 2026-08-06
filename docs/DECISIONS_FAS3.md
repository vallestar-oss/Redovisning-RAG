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
