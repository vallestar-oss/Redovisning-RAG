"""Hittar sidorna för koncernens huvudräkningar (resultat-, balans- och
kassaflödesräkning) i en årsredovisnings-PDF.

Bakgrund: en enkel sökning efter orden "resultaträkning"/"balansräkning"
någonstans på sidan fångar även notsidor (femårsöversikter, förfallo-
scheman) och förvaltningsberättelsens kommentartext - se docs/DECISIONS.md.
Dessa har helt andra tabellformer och är dessutom explicit utanför scope
(docs/SCOPE.md: "Djupa notberäkningar... ska inte räknas om av systemet").

Bolagen namnger dessutom huvudräkningarna olika:

- Volvo:      "KONCERNENS RESULTATRÄKNING" / "KONCERNENS BALANSRÄKNING" /
              "KONCERNENS KASSAFLÖDESANALYS"
- Hexatronic: "Koncernens rapport över rörelseresultat" / "Koncernens
              balansräkning" / "Koncernens rapport över kassaflöden"
- SkiStar:    "Koncernens rapport över totalresultat" / "Rapport över
              finansiell ställning för koncernen" / "Rapport över
              kassaflöden för koncernen"

Denna modul matchar mot samtliga kända varianter. Matchningen kräver att
frasen inleder en KORT rad (rubrik), inte en mening i löptexten som råkar
innehålla samma ord (t.ex. "Koncernens resultat- och balansräkningar
kommer att föreläggas årsstämman... " är en hel mening, inte en rubrik).

Nivå ("koncern" vs "moderbolag"): SkiStars koncernsidor (Rapport över
totalresultat/finansiell ställning/kassaflöden för koncernen) har en
väsentligt rörigare layout - tabeller sida vid sida med avvikande
underrubriker (t.ex. Resultat per aktie) och, på kassaflödessidan, ett
inbäddat stapeldiagram vars axelvärden blandas in i tabelldata. Detta gav
felaktig extraktion som INTE fångades av det automatiska "misstänkta
rader"-filtret (etikett och värden såg var för sig rimliga ut, bara
kombinationen var fel) - se docs/DECISIONS.md. Beslut: använd
moderbolagets räkningar för SkiStar istället, vilka är helt verifierat
rena. `locate_statement_pages` stödjer därför en `level`-parameter.
"""

from dataclasses import dataclass

import pdfplumber

_MAX_HEADING_LINE_LENGTH = 55

KONCERN_INCOME_STATEMENT_PHRASES = [
    "koncernens resultaträkning",
    "koncernens rapport över rörelseresultat",
    "koncernens rapport över totalresultat",
]
KONCERN_BALANCE_SHEET_PHRASES = [
    "koncernens balansräkning",
    "rapport över finansiell ställning för koncernen",
]
KONCERN_CASH_FLOW_PHRASES = [
    "koncernens kassaflödesanalys",
    "koncernens rapport över kassaflöden",
    "rapport över kassaflöden för koncernen",
]

MODERBOLAG_INCOME_STATEMENT_PHRASES = [
    "resultaträkning för moderbolaget",
    "moderbolagets resultaträkning",
    "moderföretagets resultaträkning",
]
MODERBOLAG_BALANCE_SHEET_PHRASES = [
    "balansräkning för moderbolaget",
    "moderbolagets balansräkning",
    "moderföretagets balansräkning",
]
MODERBOLAG_CASH_FLOW_PHRASES = [
    "kassaflödesanalys för moderbolaget",
    "moderbolagets kassaflödesanalys",
    "moderföretagets kassaflödesanalys",
    "moderbolagets rapport över kassaflöden",
    "moderföretagets rapport över kassaflöden",
]

_PHRASES_BY_LEVEL = {
    "koncern": {
        "resultaträkning": KONCERN_INCOME_STATEMENT_PHRASES,
        "balansräkning": KONCERN_BALANCE_SHEET_PHRASES,
        "kassaflödesanalys": KONCERN_CASH_FLOW_PHRASES,
    },
    "moderbolag": {
        "resultaträkning": MODERBOLAG_INCOME_STATEMENT_PHRASES,
        "balansräkning": MODERBOLAG_BALANCE_SHEET_PHRASES,
        "kassaflödesanalys": MODERBOLAG_CASH_FLOW_PHRASES,
    },
}


@dataclass
class StatementPages:
    resultaträkning: list[int]
    balansräkning: list[int]
    kassaflödesanalys: list[int]


def locate_statement_pages(pdf_path, level: str = "koncern") -> StatementPages:
    if level not in _PHRASES_BY_LEVEL:
        raise ValueError(f"okänd nivå {level!r}, förväntade 'koncern' eller 'moderbolag'")
    statement_phrases = _PHRASES_BY_LEVEL[level]
    found: dict[str, list[int]] = {k: [] for k in statement_phrases}

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            for line in text.split("\n"):
                stripped = line.strip()
                if len(stripped) > _MAX_HEADING_LINE_LENGTH:
                    continue
                lower = stripped.lower()
                for statement, phrases in statement_phrases.items():
                    if any(lower.startswith(p) for p in phrases):
                        found[statement].append(page_num)
                        break

    return StatementPages(
        resultaträkning=found["resultaträkning"],
        balansräkning=found["balansräkning"],
        kassaflödesanalys=found["kassaflödesanalys"],
    )
