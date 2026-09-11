"""Efterhandskontroll: segment vs. koncern (Fas 6, fixar Y2 i docs/evaluation.md).

Volvo redovisar samma nyckeltal (rörelseresultat, nettoomsättning, m.fl.)
på FLERA nivåer i samma rapport - Volvokoncernen (totalen) och de tre
affärsområdena Industriverksamheten/Financial Services/Elimineringar.
Prompt-regel 7 (src/prompts.py) instruerar redan modellen att använda
koncernkolumnen för frågor om "bolaget" som helhet - men i Y2 hittade
retrieval ALDRIG en koncernnivå-chunk för den efterfrågade posten (den
korrekta chunken rankades utanför top-k), så modellen svarade med
Industriverksamheten-siffror och påstod ändå att det var koncernen. Det är
alltså inte ett promptfel modellen kunde ha undvikit givet sitt underlag -
det är ett retrieval-fel som ingen promptregel kan skydda mot i efterhand.
Den här kontrollen körs därför EFTER generering och läser de FAKTISKA
källorna, inte prompten.

Två tillförlitlighetsnivåer används avsiktligt:

- **Strukturerade chunkar** (chunk_type "fact"/"table") har alltid en
  tillförlitlig "(Segment år)"-tagg per värde, se _row_sentence/
  _format_values i src/chunking.py, t.ex. "66.784 (Volvokoncernen 2023)".
  Dessa litar vi på för att BEKRÄFTA koncernnivå.
- **"text"-chunkar** (oformaterad löptext från sidor tabellextraktionen
  inte strukturerade, t.ex. Volvos nyckeltalssidor 208-210) saknar den
  tillförlitliga taggningen och kan innehålla sidhuvud-boilerplate
  ("VOLVOKONCERNEN 2024\\nÖVRIG INFORMATION" upprepas överst på nästan
  varje sida i den här rapportsektionen) som INTE säger något om vilken
  nivå en enskild siffra längre ner på sidan faktiskt avser - att lita på
  den hade gett falsk trygghet. De används bara för den LÖSA kontrollen
  (nämner källan ett segment alls, vilket avgör om frågan rör ett
  segmenterat bolag) - det är precis den kanalen som fångar Y2:s faktiska
  källa (volvo_2024.pdf::p210::1, en text-chunk som nämner
  "Industriverksamheten" men aldrig "Volvokoncernen").
"""

import re

from .search import SearchResult

_KNOWN_SEGMENTS = ("Industriverksamheten", "Financial Services", "Elimineringar", "Volvokoncernen")
_GROUP_LEVEL_SEGMENT = "Volvokoncernen"
_NON_GROUP_SEGMENTS = tuple(s for s in _KNOWN_SEGMENTS if s != _GROUP_LEVEL_SEGMENT)
_STRUCTURED_CHUNK_TYPES = {"fact", "table"}

# Matchar EXAKT den taggade formen "(Segment år)" som _format_values
# (src/chunking.py) alltid producerar för strukturerade chunkar, t.ex.
# "(Volvokoncernen 2023)" eller "(Industriverksamheten 2022)".
_TAGGED_SEGMENT_RE = re.compile(
    r"\((" + "|".join(re.escape(s) for s in _KNOWN_SEGMENTS) + r")\s+\d"
)

SEGMENT_WARNING = (
    "OBS (automatisk efterhandskontroll): underlaget för det här svaret berör "
    "ett bolag som redovisar per segment (Industriverksamheten/Financial "
    "Services/Elimineringar/Volvokoncernen), men ingen av källorna har en "
    "tillförlitligt märkt Volvokoncernen-siffra för den här posten. Svaret "
    "ovan kan därför avse ett enskilt segment istället för hela koncernen - "
    "kontrollera källhänvisningen innan du litar på nivån."
)


def _mentions_any_segment(text: str) -> bool:
    """Bred/lös kontroll: nämner källan NÅGON känd segmentetikett
    överhuvudtaget - avgör om frågan rör ett segmenterat bolag alls.
    Körs mot ALLA chunk-typer (se modulkommentaren för varför)."""
    return any(segment in text for segment in _KNOWN_SEGMENTS)


def _has_confirmed_group_level_value(result: SearchResult) -> bool:
    """Strikt kontroll: har källan en TILLFÖRLITLIGT taggad koncernnivå-
    siffra? Bara strukturerade chunkar (fact/table) litar vi på här."""
    if result.chunk_type not in _STRUCTURED_CHUNK_TYPES:
        return False
    return any(
        match.group(1) == _GROUP_LEVEL_SEGMENT for match in _TAGGED_SEGMENT_RE.finditer(result.text)
    )


def check_segment_consistency(
    question: str, answer_text: str, sources: list[SearchResult]
) -> str | None:
    """Returnerar en varningstext att bifoga svaret, eller None om inget
    behöver flaggas.

    Ingen varning när:
    - frågan själv uttryckligen efterfrågar ett specifikt segment (då
      förväntas ingen koncernsiffra),
    - inget av källorna nämner ett segment alls (inte en segmenterad-
      rapport-situation, t.ex. Hexatronic/SkiStar),
    - en källa har en tillförlitligt taggad Volvokoncernen-siffra (svaret
      går alltså att bekräfta mot en riktig koncernrad), eller
    - svaret redan själv namnger ett enskilt segment UTAN att också påstå
      "Volvokoncernen"/"koncernen" - det har då redan gjort precis vad
      promptregel 7 kräver (transparent om nivån), inget mer att flagga.
    """
    lowered_question = question.lower()
    if any(seg.lower() in lowered_question for seg in _NON_GROUP_SEGMENTS):
        return None

    if not any(_mentions_any_segment(r.text) for r in sources):
        return None

    if any(_has_confirmed_group_level_value(r) for r in sources):
        return None

    lowered_answer = answer_text.lower()
    claims_group_level = _GROUP_LEVEL_SEGMENT.lower() in lowered_answer or "koncernen" in lowered_answer
    names_a_segment = any(seg.lower() in lowered_answer for seg in _NON_GROUP_SEGMENTS)
    if names_a_segment and not claims_group_level:
        return None

    return SEGMENT_WARNING
