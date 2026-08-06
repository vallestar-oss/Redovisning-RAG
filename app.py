"""Streamlit-gränssnitt för RAG-systemet (Fas 6).

Minimalt men rent: dokumentväljare (informativ, retrieval hittar rätt
dokument automatiskt ur frågan - se src/hybrid_search.py), fritextfråga,
klickbara exempel ur facit-setet, och svar med källhänvisning alltid
synlig direkt vid svaret.
"""

import json
from pathlib import Path

import streamlit as st
from openai import APIConnectionError, APITimeoutError

from src.answer import answer_question
from src.chunking import _display_company
from src.evaluation import EVAL_SET
from src.hybrid_search import HybridSearcher
from src.llm import get_provider

ROOT = Path(__file__).resolve().parent
_MAX_QUESTION_LENGTH = 300

st.set_page_config(page_title="Årsredovisnings-RAG", page_icon="📊", layout="wide")


@st.cache_resource(show_spinner="Laddar sök- och språkmodell...")
def _load_pipeline():
    searcher = HybridSearcher(ROOT / "data" / "chroma", ROOT / "data" / "chunks")
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
    ask = st.button("Fråga", type="primary")

    if ask:
        stripped = question.strip()
        if not stripped:
            st.warning("Skriv en fråga innan du trycker på Fråga.")
        elif len(stripped) > _MAX_QUESTION_LENGTH:
            st.warning(
                f"Frågan är {len(stripped)} tecken - försök hålla dig under "
                f"{_MAX_QUESTION_LENGTH} tecken så blir svaret mer träffsäkert."
            )
        else:
            searcher, provider = _load_pipeline()
            _render_answer(stripped, searcher, provider)


if __name__ == "__main__":
    main()
