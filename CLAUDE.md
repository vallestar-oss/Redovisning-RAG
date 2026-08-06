# RAG-system för årsredovisningar — projektkontext

Portföljprojekt: RAG-system (retrieval-augmented generation) som svarar på frågor om
årsredovisningar med källhänvisning (sida/dokument). Ska hålla produktionsmässig kvalitet,
inte en tutorial-demo.

Full scope finns i [docs/SCOPE.md](docs/SCOPE.md) — läs den för nyckeltal, frågetyper och
out-of-scope-gränser.

## Tech-stack

- **PDF-extraktion:** pdfplumber (eller unstructured), särskild hantering av tabeller
- **Chunking:** semantisk (per stycke/sektion), inte fast teckenlängd; bevara metadata
  (dokument, sida, sektion)
- **Embeddings:** sentence-transformers (lokalt, t.ex. all-MiniLM-L6-v2)
- **Vektordatabas:** Chroma (lokalt under utveckling, Chroma Cloud vid deploy)
- **LLM:** DeepSeek API (OpenAI-kompatibelt format), modulärt så providern går att byta
- **UI:** Streamlit
- **Deploy:** Streamlit Cloud

## Arbetsprinciper — VIKTIGT

- Ett steg i taget. Gör INTE flera faser i följd utan att stanna.
- En commit per avslutad deluppgift, aldrig en stor commit som blandar flera faser.
- Efter varje deluppgift: rapportera vad som gjorts, visa hur det testats/verifierats,
  och VÄNTA på godkännande innan nästa steg.
- Kvalitet och metodik prioriteras över hastighet. Om något känns osäkert (t.ex.
  tabellextraktion ger konstiga resultat), stanna och flagga det istället för att
  gissa och gå vidare.
- Dokumentera beslut löpande i `/docs` medan de fattas.

## Krav på varje svar (systemet, inte utvecklingsprocessen)

- Källhänvisning till sida/dokument på varje svar.
- Om svaret inte finns i underlaget: säg det tydligt, gissa aldrig.

## Repostruktur

- `/data` — årsredovisningar (PDF) och genererad data (embeddings, index)
- `/src` — applikationskod
- `/tests` — tester
- `/docs` — beslut och scope, dokumenteras löpande
