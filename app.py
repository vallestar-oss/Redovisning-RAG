"""Streamlit-gränssnitt för RAG-systemet (Fas 6).

Minimalt men rent: dokumentväljare (informativ, retrieval hittar rätt
dokument automatiskt ur frågan - se src/hybrid_search.py), fritextfråga,
klickbara exempel ur facit-setet, och svar med källhänvisning alltid
synlig direkt vid svaret.
"""

import json
import os
from pathlib import Path

import streamlit as st
from openai import APIConnectionError, APITimeoutError
from streamlit.errors import StreamlitSecretNotFoundError

# Streamlit Clouds "Secrets"-panel populerar st.secrets, inte os.environ.
# Resten av koden (src/llm.py, src/migrate_to_cloud.py) läser nycklar via
# os.environ/python-dotenv, precis som lokalt - så vi bryggar över dem här,
# INNAN något annat i appen importeras eller körs. Lokalt finns ingen
# .streamlit/secrets.toml alls (se .gitignore), och st.secrets.items() kastar
# StreamlitSecretNotFoundError i det läget snarare än att bara vara tom -
# därför try/except istället för en tystare koll. .env läses som vanligt.
try:
    for _key, _value in st.secrets.items():
        os.environ.setdefault(_key, str(_value))
except StreamlitSecretNotFoundError:
    pass

import chromadb  # noqa: E402 - måste komma efter secrets-bryggan ovan

from src.answer import answer_question  # noqa: E402
from src.chunking import _display_company  # noqa: E402
from src.evaluation import EVAL_SET  # noqa: E402
from src.hybrid_search import HybridSearcher  # noqa: E402
from src.llm import get_provider  # noqa: E402

ROOT = Path(__file__).resolve().parent
_MAX_QUESTION_LENGTH = 300
# Kostnadsskydd (Fas 7): varje fråga kostar ett DeepSeek-anrop. Sessionsbaserad
# gräns istället för IP-baserad - se main() för motiveringen.
_MAX_QUESTIONS_PER_SESSION = 10

st.set_page_config(page_title="Årsredovisnings-RAG", page_icon="📊", layout="wide")


@st.cache_resource(show_spinner="Laddar sök- och språkmodell...")
def _load_pipeline():
    # I produktion (Streamlit Cloud) finns CHROMA_API_KEY och vi pratar mot
    # Chroma Cloud (se docs/DECISIONS_FAS7.md). Lokalt är den tom och
    # HybridSearcher faller tillbaka på den lokala data/chroma-mappen -
    # samma explicita mönster som src/migrate_to_cloud.py.
    client = None
    if os.environ.get("CHROMA_API_KEY"):
        client = chromadb.CloudClient(
            api_key=os.environ["CHROMA_API_KEY"],
            tenant=os.environ.get("CHROMA_TENANT"),
            database=os.environ.get("CHROMA_DATABASE"),
        )
    searcher = HybridSearcher(ROOT / "data" / "chroma", ROOT / "data" / "chunks", client=client)
    provider = get_provider("deepseek")
    return searcher, provider


@st.cache_data
def _load_document_overview() -> list[dict]:
    """Bolag/år/sidantal för varje indexerat dokument - visas i sidopanelen
    så att man ser vad systemet faktiskt kan svara på."""
    overview = []
    for path in sorted((ROOT / "data" / "processed").glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        overview.append({
            "company": doc["company"],
            "fiscal_year": doc["fiscal_year"],
            "pages": len(doc["pages"]),
        })
    return overview


def _format_source(source) -> str:
    pages = ", ".join(str(p) for p in source.pages)
    section = f" · {source.section}" if source.section else ""
    return f"**{source.document}**, s. {pages}{section}"


def _render_answer(question: str, searcher: HybridSearcher, provider) -> None:
    with st.spinner("Söker i årsredovisningarna och formulerar svar..."):
        try:
            answer = answer_question(question, searcher, provider)
        except APITimeoutError:
            st.error(
                "⏱️ DeepSeek svarade inte inom rimlig tid. Prova igen om en "
                "liten stund - inget svar genererades."
            )
            return
        except APIConnectionError:
            st.error(
                "🔌 Kunde inte nå DeepSeek. Kontrollera internetuppkopplingen "
                "och att `DEEPSEEK_API_KEY` är giltig, och försök igen."
            )
            return
        except Exception as exc:  # noqa: BLE001 - UI:t ska aldrig krascha på ett API-fel
            st.error(f"❌ Något gick fel när svaret skulle genereras: {exc}")
            return

    if answer.is_no_answer:
        st.warning(answer.text)
    else:
        st.markdown(f"### Svar\n{answer.text}")

    if answer.sources:
        st.markdown("**Källor:**")
        for s in answer.sources:
            st.markdown(f"- {_format_source(s)}")
    elif not answer.is_no_answer:
        st.caption("Inga källor hittades för det här svaret.")


def main() -> None:
    # Sessionsbaserad rate limiting, inte IP-baserad. Streamlit Cloud körs
    # bakom en proxy och exponerar ingen tillförlitlig klient-IP i det
    # publika API:t (headers kan förfalskas eller saknas, och delad IP bakom
    # NAT/VPN skulle straffa oskyldiga besökare på samma nätverk). Målet här
    # är att skydda mot okontrollerad DeepSeek-kostnad vid oavsiktliga loopar
    # eller sladdrig användning av en portföljdemo - inte att stoppa en
    # målmedveten aktör som öppnar nya sessioner, vilket varken IP- eller
    # sessionsbaserad spärr klarar av utan betydligt mer infrastruktur
    # (t.ex. en delad backend-databas för att räkna över sessioner). Se
    # docs/DECISIONS_FAS7.md.
    st.session_state.setdefault("questions_asked", 0)

    st.title("📊 Årsredovisnings-RAG")
    st.caption(
        "Ställ en fråga om Hexatronic, SkiStar eller Volvo. Svaren bygger "
        "uteslutande på innehållet i de indexerade årsredovisningarna, med "
        "källhänvisning till dokument och sida."
    )

    with st.sidebar:
        st.header("Tillgängliga årsredovisningar")
        st.caption(
            "Systemet läser bolag/år direkt ur din fråga - du behöver inte "
            "välja dokument manuellt, men det är bra att se vad som finns."
        )
        overview = _load_document_overview()
        by_company: dict[str, list[dict]] = {}
        for row in overview:
            by_company.setdefault(row["company"], []).append(row)
        for company, rows in sorted(by_company.items()):
            years = ", ".join(r["fiscal_year"] for r in rows)
            st.markdown(f"**{_display_company(company)}**  \n{years}")

        st.divider()
        st.caption(
            "Utanför scope: aktiekursberoende nyckeltal (P/E m.fl.), "
            "jämförelser mellan olika bolag, samt djupa notberäkningar. "
            "Se docs/SCOPE.md."
        )

    if "question_input" not in st.session_state:
        st.session_state.question_input = ""

    with st.expander("💡 Exempelfrågor (klicka för att prova)", expanded=False):
        by_type = {}
        for case in EVAL_SET:
            by_type.setdefault(case.question_type, []).append(case)
        type_labels = {
            "enårsuppslag": "Enårsuppslag",
            "yoy": "Flerårstrend / YoY",
            "nyckeltal": "Nyckeltalsberäkning",
            "kvalitativ": "Kvalitativa frågor",
        }
        cols = st.columns(len(type_labels))
        for col, (qtype, label) in zip(cols, type_labels.items()):
            with col:
                st.markdown(f"**{label}**")
                for case in by_type.get(qtype, []):
                    if st.button(case.question, key=f"example_{case.id}", use_container_width=True):
                        st.session_state.question_input = case.question

    question = st.text_input(
        "Din fråga",
        key="question_input",
        placeholder="T.ex. Vad var Volvos nettoomsättning 2024?",
    )
    # Läses in EFTER att frågan (om någon) räknats, inte innan - annars visar
    # texten antalet kvarvarande frågor från FÖRE den här körningen, eftersom
    # Streamlit kör skriptet top-to-bottom i ett svep och ökningen av
    # questions_asked sker längre ned. Knappens disabled-status ska däremot
    # avgöras av läget INNAN klicket, så den delen läses av här.
    remaining = _MAX_QUESTIONS_PER_SESSION - st.session_state.questions_asked
    ask = st.button("Fråga", type="primary", disabled=remaining <= 0)
    caption_placeholder = st.empty()

    if remaining <= 0:
        st.warning(
            f"Du har nått gränsen på {_MAX_QUESTIONS_PER_SESSION} frågor för den här "
            "sessionen (kostnadsskydd för demot). Ladda om sidan för en ny session."
        )
    elif ask:
        stripped = question.strip()
        if not stripped:
            st.warning("Skriv en fråga innan du trycker på Fråga.")
        elif len(stripped) > _MAX_QUESTION_LENGTH:
            st.warning(
                f"Frågan är {len(stripped)} tecken - försök hålla dig under "
                f"{_MAX_QUESTION_LENGTH} tecken så blir svaret mer träffsäkert."
            )
        else:
            st.session_state.questions_asked += 1
            searcher, provider = _load_pipeline()
            _render_answer(stripped, searcher, provider)

    remaining_after = _MAX_QUESTIONS_PER_SESSION - st.session_state.questions_asked
    caption_placeholder.caption(
        f"{max(remaining_after, 0)} av {_MAX_QUESTIONS_PER_SESSION} frågor kvar i den här sessionen."
    )


if __name__ == "__main__":
    main()
