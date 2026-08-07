# Årsredovisnings-RAG

Ett RAG-system (retrieval-augmented generation) som svarar på frågor om svenska
årsredovisningar — med källhänvisning till exakt dokument och sida på varje svar,
och en tydlig markering när svaret inte finns i underlaget istället för att gissa.

**🔗 Live demo:** https://redovisning-rag-nzmmjgrcgnz5xcyougctdw.streamlit.app/
**📦 Repo:** https://github.com/vallestar-oss/Redovisning-RAG

Byggt mot tre bolags årsredovisningar (Volvo, Hexatronic, SkiStar) över tre
räkenskapsår vardera — 9 dokument, ~6 900 indexerade textbitar.

---

## Problem

Årsredovisningar är hundratals sidor långa PDF:er med tabeller som spänner
över flera sidor, brutna räkenskapsår och bolagsspecifik terminologi. Att
manuellt leta upp ett enskilt nyckeltal, eller jämföra det mellan år, är
tidskrävande — och ett vanligt LLM utan grounding gissar hellre än erkänner
att det inte vet. Det här systemet löser båda: snabb sökning i underlaget,
och ett svar som alltid går att verifiera mot källan.

## Arkitektur

```
PDF (pdfplumber)
   │  koordinatbaserad extraktion: löptext + tabeller (se docs/DECISIONS.md)
   ▼
Chunkning (src/chunking.py)
   │  hela huvudräkningar = en chunk (aldrig delade mitt i en tabell),
   │  löptext grupperas meningsvis; varje chunk bär dokument/bolag/år/sida
   ▼
Embeddings (sentence-transformers, intfloat/multilingual-e5-base)
   │  indexeras i Chroma (lokalt under utveckling, Chroma Cloud i produktion)
   ▼
Hybridsökning (src/hybrid_search.py)
   │  metadatafilter (bolag/år ur frågan) + vektorsökning + BM25,
   │  slås ihop med Reciprocal Rank Fusion
   ▼
DeepSeek (src/prompts.py + src/answer.py)
   │  svarar ENDAST utifrån hämtad kontext, citerar sida/dokument,
   │  säger tydligt ifrån när svaret saknas i underlaget
   ▼
Streamlit-UI — svar + källhänvisning, alltid synliga tillsammans
```

Varje steg i kedjan finns dokumenterat med konkreta problem-lösning-verifiering
i `/docs` — se särskilt [docs/case-study.md](docs/case-study.md) för de
tekniskt svåraste besluten.

## Tech-stack

- **PDF-extraktion:** pdfplumber, koordinatbaserad tabellrekonstruktion (egen kod, inte `extract_tables()`)
- **Chunkning:** semantisk — hela tabeller eller meningsgrupper, aldrig fast teckenlängd
- **Embeddings:** sentence-transformers, `intfloat/multilingual-e5-base` (vald efter mätning, se case study)
- **Vektordatabas:** Chroma (lokalt under utveckling → Chroma Cloud i produktion)
- **Sökning:** hybrid — metadatafilter + vektor + BM25 (`rank-bm25`), fusion via RRF
- **LLM:** DeepSeek API (OpenAI-kompatibelt), modulärt providerlager
- **UI:** Streamlit, deployat på Streamlit Cloud
- **Tester:** pytest, 134 tester (extraktion, chunkning, retrieval, utvärdering)

## Hur man kör lokalt

```bash
git clone https://github.com/vallestar-oss/Redovisning-RAG.git
cd Redovisning-RAG
python -m venv venv
venv\Scripts\activate          # Windows; source venv/bin/activate på macOS/Linux
pip install -r requirements.txt

cp .env.example .env           # fyll i DEEPSEEK_API_KEY
```

Egna PDF:er läggs i `data/raw/` (namnges `bolag_år.pdf`), sedan:

```bash
python -m src.pipeline          # extraktion + tabelltolkning
python -m src.chunking          # chunkning
python -m src.vectorstore       # bygger lokalt Chroma-index
streamlit run app.py
```

Kör testsviten med `pytest tests/ -v`.

## Utvärdering

18 verifierade facit-frågor över de fyra prioriterade frågetyperna (se
[docs/SCOPE.md](docs/SCOPE.md)), körda genom hela pipelinen en gång vardera
och manuellt bedömda mot facit. Full genomgång inklusive rotorsaksanalys av
varje avvikande fall i [docs/evaluation.md](docs/evaluation.md).

**14 av 18 rätt (78 %).**

| Frågetyp | Andel rätt |
|---|---|
| Enårsuppslag | 100 % (6/6) |
| Flerårstrend/YoY | 60 % (3/5) |
| Nyckeltalsberäkning | 60 % (3/5) |
| Kvalitativ | 100 % (2/2) |

Systemet är starkast på enkla enårsfrågor (en fråga, en rad, en chunk) och
svagast när retrieval måste hitta flera specifika poster samtidigt (två år,
eller täljare+nämnare för ett nyckeltal) — då konkurrerar lexikalt
närliggande rader om samma platser i topp-k. Ingen hallucination observerades
i något av de fyra avvikande fallen: systemet gav antingen en verklig men fel
siffra från en verklig men fel rad, eller flaggade korrekt att det saknade
underlag.

## Future work

Metadata-designen (varje chunk bär dokument, bolag, räkenskapsår och sida)
gör det tekniskt enkelt att lägga till fler bolag eller år — samma
extraktions-/chunkningspipeline återanvänds rakt av. Multi-bolagsjämförelser
("hur skiljer sig Volvos och Hexatronics marginaler åt?") hölls dock medvetet
utanför v1-scope (se [docs/SCOPE.md](docs/SCOPE.md)): det kräver en annan
frågetolkning och riskerar att uppmuntra jämförelser mellan bolag i olika
branscher/storlekar utan relevant kontext, vilket inte prioriterades för den
här versionen.

## Om utvecklingsprocessen

Byggt med [Claude Code](https://claude.com/claude-code) i en strikt
fas-för-fas-process — en deluppgift i taget, med commit och manuell
godkännande mellan varje steg. Beslutsloggen i `/docs` skrevs löpande under
utvecklingen, inte i efterhand.

**Varför DeepSeek:** kostnadseffektiv jämfört med GPT-4-klassens modeller,
och API:t är OpenAI-kompatibelt — providerlagret (`src/llm.py`) är en tunn
wrapper som gör det enkelt att byta LLM-leverantör utan att röra resten av
pipelinen.
