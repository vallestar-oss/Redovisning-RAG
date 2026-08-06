"""Koordinatbaserad extraktion av finansiella tabellrader.

Bakgrund (se docs/DECISIONS.md): pdfplumber.extract_tables() gav otillräcklig
och inkonsekvent kvalitet över våra tre bolags rapporter - hopslagna
multi-kolumnvärden i en och samma cell (Volvo), helt saknade radetiketter
(SkiStar), fungerande men inkonsekvent tabelldetektering (Hexatronic).

Den här modulen bygger istället tabellrader direkt från ordens
positionsdata (page.extract_words()):

1. Hitta rubrikrader - rader som innehåller minst två årtalsliknande tokens
   (t.ex. "2023" "2022", eller hela datum som "2023-12-31") och/eller en
   valutaenhet (Mkr/MSEK/TSEK/...).
2. En rubrikrad kan innehålla FLERA tabeller sida vid sida (t.ex. SkiStars
   "Koncernens rapport över totalresultat" och "Övrigt totalresultat" på
   samma rad, med separata Not/år-kolumner). Årtalstokens klustras efter
   x-position till separata "regioner" - varje region får sin egen
   vänstergräns (utesluter sidopaneler/löptext till vänster om den, t.ex.
   SkiStars vertikala innehållsnavigering i marginalen) och sina egna
   kolumncentra. Övriga ord på rubrikraden (tabelltitlar) tilldelas den
   region vars kolumner ligger närmast till höger om ordet - en titel står
   alltid till vänster om sina egna värdekolumner, aldrig till höger om dem.
3. För varje rad efter rubriken (till nästa rubrik eller sidans slut)
   behandlas varje region OBEROENDE av varandra, med orden på raden
   uppdelade efter vilken regions x-intervall de faller inom. Detta
   förhindrar att etiketter/värden från två tabeller som råkar dela samma
   y-position (rad) blandas ihop.
4. Inom en region: ordtokens som tillsammans bildar ett tal slås ihop
   (tusentalsavgränsare som mellanslag delar annars upp ett tal i flera
   ord, t.ex. "3" "101" "291" -> "3 101 291"; ett fristående minustecken
   framför en siffra slås ihop till ett negativt tal).
5. Varje sammanslaget tal tilldelas till regionens kolumner utifrån
   x-position (tal för långt från alla kolumncentra räknas inte som ett
   tabellvärde - filtrerar bort tal inbäddade i löptext/fotnoter under
   tabellen). Kvarvarande ord bildar radetiketten. Rader utan tal antas
   vara rubriker/mellanrubriker och läggs som prefix till nästa värderad
   rad. Ett ovanligt stort radavstånd (efter att minst en rad redan
   extraherats i regionen) markerar att tabellen är slut.
"""

import re
from dataclasses import dataclass, field

import pdfplumber.page

CURRENCY_UNITS = {"mkr", "msek", "tsek", "tkr", "ksek", "sek"}
# Kolumnrubriker är inte alltid rena årtal ("2023") - ofta hela datum
# ("2023-12-31", "-2024-08-31" för brutet räkenskapsår). Vi matchar därför
# ett årtal SOM DELSTRÄNG i en kort token, inte hela token.
_YEAR_TOKEN_RE = re.compile(r"(19|20)\d{2}")
_MAX_YEAR_TOKEN_LEN = 14  # "-2024-08-31" = 11 tecken; ger marginal utan att fånga löptext
_SIGN_RE = re.compile(r"^[−\-–]$")
_SIGNED_NUMBER_RE = re.compile(r"^[−\-–]?\d[\d.,]*$")
_THOUSANDS_GROUP_RE = re.compile(r"^\d{3}$")

_X_GAP_TOL = 6.0  # max mellanrum (pt) mellan ordtokens för att slås ihop till ett tal
_LEFT_BOUNDARY_SLACK = 5.0  # pt marginal runt en tabellregions vänsterkant
_LINE_Y_TOL = 2.0  # pt tolerans för att räkna två ord till samma rad
_MAX_COLUMN_DISTANCE = 25.0  # pt - tal längre än så från alla kolumncentra räknas inte som tabellvärden
_MAX_LABEL_LENGTH = 120  # tecken - längre "etiketter" är nästan alltid löptext (fotnoter), inte en tabellrad
_MIN_LABEL_LENGTH = 2  # en äkta radpost saknar aldrig etikett - tomma/nästan tomma etiketter är trasiga rader
# Radavstånd MELLAN PÅFÖLJANDE RADER i en tabellregion (inklusive
# underrubriker utan värden) håller sig konsekvent under ~35pt i våra
# dokument, även vid sektionsbyten med extra luft (t.ex. Hexatronics
# rubriker mellan delsummor). Ett större hopp markerar övergången till
# löptext (fotnoter/kommentarer) och avslutar den regionen. Tillämpas bara
# EFTER att minst en riktig rad redan extraherats i regionen - avståndet
# mellan rubrikraden och första dataraden kan legitimt vara stort
# (mellanliggande sektionsrubriker), och ska inte tolkas som slutet.
_MAX_LINE_GAP_AFTER_FIRST_ROW = 36.0
# Gap (i årtalscentras x-position) som skiljer TVÅ OLIKA tabeller sida vid
# sida från kolumner inom SAMMA tabell. Störst observerat avstånd mellan
# kolumner i en enskild tabell hos oss är ~80pt (Volvos 8-kolumnstabeller);
# minsta observerade avstånd mellan två skilda tabeller sida vid sida är
# ~335pt (SkiStars "Rapport över totalresultat" / "Övrigt totalresultat").
_REGION_GAP_THRESHOLD = 150.0
# Två rubrikrader vars y-position skiljer sig med högst detta värde slås
# ihop innan regionerna beräknas. Nödvändigt eftersom två titlar som visuellt
# står på SAMMA rad (t.ex. "TILLGÅNGAR, TSEK ..." och "EGET KAPITAL OCH
# SKULDER, TSEK ...") ibland har en sub-pixel-baslinjeskillnad (~2pt) som gör
# att _group_lines annars delar upp dem i två separata rader - utan denna
# sammanslagning skulle den andra rubrikraden helt ERSÄTTA (inte komplettera)
# regionerna från den första, och en hel tabell tappas bort.
_HEADER_LINE_MERGE_GAP = 5.0
# Ord som ligger längre än så till vänster om sitt tilldelade ankare räknas
# inte som en tabelltitel utan utesluts helt. Riktiga titlar i våra dokument
# ligger ~250-300pt från sin kolumn; SkiStars återkommande vänstermarginal-
# navigering (INLEDNING, STRATEGI, AKTIEN, RISK, ...) ligger ~500pt bort -
# annars kan ett sådant ord som råkar hamna på samma rad som en rubrik dra in
# hela sidopanelen i regionens vänstergräns för efterföljande datarader.
_MAX_TITLE_TO_ANCHOR_DISTANCE = 400.0
# Hur långt ovanför årsraden vi letar efter en segmentrubrik. Volvos
# segmentrad ligger ~19pt över årsraden med en "31 dec"-rad emellan.
_SEGMENT_LOOKBACK_LINES = 4
_SEGMENT_LOOKBACK_PT = 45.0


@dataclass
class FinancialRow:
    label: str
    # POSITIONELLT KOPPLADE till `columns`: values[i] hör till columns[i].
    # None betyder att kolumnen saknar värde på den raden (i PDF:en oftast
    # ett tankstreck). Att bevara luckorna är avgörande - se
    # docs/DECISIONS_FAS3.md om varför en kompakt lista utan luckor gav
    # direkt felaktig år-till-värde-koppling i flerårsöversikter.
    values: list[str | None]
    columns: list[str]  # kolumnrubriker, t.ex. ["2023", "2022", ..., "2013"]
    page: int
    source: str = "layout"

    @property
    def present_values(self) -> list[str]:
        """Endast de värden som faktiskt finns, utan kolumnkoppling.
        Använd bara där kolumntillhörigheten är irrelevant."""
        return [v for v in self.values if v is not None]

    def by_column(self) -> list[tuple[str, str]]:
        """(kolumnrubrik, värde) för de kolumner som har ett värde."""
        return [(c, v) for c, v in zip(self.columns, self.values) if v is not None]


@dataclass
class _Line:
    top: float
    words: list[dict] = field(default_factory=list)


@dataclass
class _Region:
    left_boundary: float
    right_boundary: float  # exklusiv övre gräns; float("inf") för sista regionen
    column_centers: list[float]
    column_labels: list[str] = field(default_factory=list)  # samma ordning som column_centers
    pending_label_parts: list[str] = field(default_factory=list)
    last_line_top: float | None = None
    has_emitted_row: bool = False
    active: bool = True


def _group_lines(words: list[dict]) -> list[_Line]:
    lines: list[_Line] = []
    for w in sorted(words, key=lambda w: w["top"]):
        if lines and abs(lines[-1].top - w["top"]) <= _LINE_Y_TOL:
            lines[-1].words.append(w)
        else:
            lines.append(_Line(top=w["top"], words=[w]))
    for line in lines:
        line.words.sort(key=lambda w: w["x0"])
    return lines


def _merge_split_header_lines(lines: list[_Line]) -> list[_Line]:
    """Slår ihop på varandra följande rader som båda ser ut som delar av
    samma rubrikrad (innehåller årtalsliknande tokens) men hamnat i olika
    _Line-objekt på grund av en sub-pixel y-skillnad. Se _HEADER_LINE_MERGE_GAP."""
    merged: list[_Line] = []
    for line in lines:
        if merged:
            prev = merged[-1]
            gap = line.top - prev.top
            prev_has_year = any(_is_year_like(w["text"]) for w in prev.words)
            cur_has_year = any(_is_year_like(w["text"]) for w in line.words)
            if 0 < gap <= _HEADER_LINE_MERGE_GAP and prev_has_year and cur_has_year:
                prev.words = sorted(prev.words + line.words, key=lambda w: w["x0"])
                continue
        merged.append(_Line(top=line.top, words=list(line.words)))
    return merged


def _is_year_like(text: str) -> bool:
    return len(text) <= _MAX_YEAR_TOKEN_LEN and bool(_YEAR_TOKEN_RE.search(text))


def _cluster_year_words(year_words: list[dict]) -> list[list[dict]]:
    ordered = sorted(year_words, key=lambda w: (w["x0"] + w["x1"]) / 2)
    clusters: list[list[dict]] = []
    for w in ordered:
        center = (w["x0"] + w["x1"]) / 2
        if clusters:
            prev_center = (clusters[-1][-1]["x0"] + clusters[-1][-1]["x1"]) / 2
            if center - prev_center <= _REGION_GAP_THRESHOLD:
                clusters[-1].append(w)
                continue
        clusters.append([w])
    return clusters


def _group_into_phrases(words: list[dict], max_gap: float = 8.0) -> list[dict]:
    """Slår ihop ord som står tätt intill varandra till en fras
    ("Financial" + "Services" -> "Financial Services")."""
    phrases: list[dict] = []
    for w in sorted(words, key=lambda w: w["x0"]):
        if phrases and w["x0"] - phrases[-1]["x1"] <= max_gap:
            phrases[-1]["text"] += " " + w["text"]
            phrases[-1]["x1"] = w["x1"]
        else:
            phrases.append({"text": w["text"], "x0": w["x0"], "x1": w["x1"]})
    return phrases


def _find_segment_labels(
    preceding_lines: list[_Line], column_centers: list[float]
) -> list[str] | None:
    """Letar efter en segmentrubrikrad ovanför årsraden och returnerar ett
    segmentnamn per kolumn.

    Volvos huvudräkningar delar upp samma årtal på fyra segment
    (Industriverksamheten / Financial Services / Elimineringar /
    Volvokoncernen), var och en med kolumnerna 2023 och 2022. Utan
    segmentnamnet blir kolumnetiketterna tvetydiga ("2023" fyra gånger)
    och en läsare kan inte se vilket tal som är koncernens totalsiffra.

    Segmentraden identifieras som den närmaste raden ovanför vars fraser
    (a) innehåller bokstäver, (b) är färre än antalet kolumner, och
    (c) delar kolumnantalet jämnt. Det utesluter t.ex. Volvos
    mellanliggande "31 dec"-rad, som ger exakt lika många fraser som
    kolumner.
    """
    if len(column_centers) < 2:
        return None
    for line in reversed(preceding_lines):
        phrases = _group_into_phrases([w for w in line.words if not _is_year_like(w["text"])])
        phrases = [p for p in phrases if any(c.isalpha() for c in p["text"])]
        if len(phrases) < 2 or len(phrases) >= len(column_centers):
            continue
        if len(column_centers) % len(phrases) != 0:
            continue
        labels = []
        for center in column_centers:
            nearest = min(phrases, key=lambda p: abs(center - (p["x0"] + p["x1"]) / 2))
            labels.append(nearest["text"])
        # Varje segment ska täcka lika många kolumner - annars har vi
        # sannolikt matchat fel rad.
        per_segment = len(column_centers) // len(phrases)
        if any(labels.count(p["text"]) != per_segment for p in phrases):
            continue
        return labels
    return None


def _find_header_regions(line: _Line, preceding_lines: list[_Line] | None = None) -> list[_Region]:
    year_words = [w for w in line.words if _is_year_like(w["text"])]
    has_currency = any(w["text"].lower().strip(":") in CURRENCY_UNITS for w in line.words)
    is_header = len(year_words) >= 2 or (has_currency and len(year_words) >= 1)
    if not is_header:
        return []

    clusters = _cluster_year_words(year_words) if year_words else [[]]
    # anchor = regionens egen vänstraste kolumn - inget ord i rubrikraden
    # kan tillhöra en region och ligga till höger om regionens egna kolumner.
    anchors = sorted(min(w["x0"] for w in cluster) for cluster in clusters) if year_words \
        else [min(w["x0"] for w in line.words)]

    # Årtalstoken tillhör trivialt sin egen region (de definierade klustren).
    # OBS: de kan INTE tilldelas via ankarregeln nedan - ett årtalsords egen
    # x1 ligger per definition till höger om sitt eget ankare (ankaret ÄR
    # ordets x0), vilket skulle knuffa det till nästa region.
    assigned_words: list[list[dict]] = [list(cluster) for cluster in (clusters if year_words else [[]])]
    year_word_ids = {id(w) for cluster in (clusters if year_words else []) for w in cluster}

    # Övriga ord (titlar, "Not", valutaenhet) tilldelas närmaste ankare SOM
    # LIGGER TILL HÖGER OM (eller vid) ordets slutposition - en titel
    # föregår alltid sina egna kolumner, den ligger aldrig till höger om dem.
    # Vid FLERA regioner utesluts ord som hamnar orimligt långt från sitt
    # ankare (sidopanelstext som annars felaktigt skulle knytas till fel
    # regions vänstergräns). Vid EN region tillämpas inte gränsen: där finns
    # ingen "fel region" att skydda mot, och rubrikraden kan sakna egen
    # titeltext helt (titeln kan stå på en helt annan rad högre upp) - då
    # ska den enda regionens vänstergräns vara så tillåtande som möjligt,
    # inte snävas in av att sidopanelen råkar vara det enda ordet kvar.
    for w in line.words:
        if id(w) in year_word_ids:
            continue
        target = len(anchors) - 1
        for i, a in enumerate(anchors):
            if a >= w["x1"]:
                target = i
                break
        if len(anchors) > 1 and anchors[target] - w["x1"] > _MAX_TITLE_TO_ANCHOR_DISTANCE:
            continue
        assigned_words[target].append(w)

    left_boundaries = [min(w["x0"] for w in words) if words else anchors[i] for i, words in enumerate(assigned_words)]

    regions = []
    for i, cluster in enumerate(clusters if year_words else [[]]):
        # Sortera etikett och center TILLSAMMANS så att column_labels[j]
        # alltid hör ihop med column_centers[j].
        by_x = sorted(cluster, key=lambda w: (w["x0"] + w["x1"]) / 2)
        column_centers = [(w["x0"] + w["x1"]) / 2 for w in by_x]
        column_labels = [w["text"].strip() for w in by_x]
        # Om samma årtal förekommer flera gånger är etiketterna tvetydiga -
        # då (och endast då) letar vi efter en segmentrubrik ovanför för att
        # skilja kolumnerna åt, t.ex. "Industriverksamheten 2023" vs
        # "Volvokoncernen 2023".
        if preceding_lines and len(set(column_labels)) < len(column_labels):
            segments = _find_segment_labels(preceding_lines, column_centers)
            if segments:
                column_labels = [f"{seg} {yr}" for seg, yr in zip(segments, column_labels)]
        left_boundary = left_boundaries[i]
        right_boundary = left_boundaries[i + 1] if i + 1 < len(left_boundaries) else float("inf")
        regions.append(
            _Region(
                left_boundary=left_boundary,
                right_boundary=right_boundary,
                column_centers=column_centers,
                column_labels=column_labels,
            )
        )
    return regions


def _nearest_column_distance(x_center: float, column_centers: list[float]) -> float:
    if not column_centers:
        return 0.0
    return min(abs(x_center - c) for c in column_centers)


def _merge_number_tokens(words: list[dict]) -> list[dict]:
    merged = []
    i, n = 0, len(words)
    while i < n:
        w = words[i]
        text = w["text"]

        if _SIGN_RE.match(text) and i + 1 < n and re.fullmatch(r"\d+", words[i + 1]["text"]) \
                and (words[i + 1]["x0"] - w["x1"]) <= _X_GAP_TOL:
            current = {"text": text + words[i + 1]["text"], "x0": w["x0"], "x1": words[i + 1]["x1"], "is_number": True}
            i += 2
        elif _SIGNED_NUMBER_RE.match(text):
            current = {"text": text, "x0": w["x0"], "x1": w["x1"], "is_number": True}
            i += 1
        elif _SIGN_RE.match(text):
            merged.append({"text": text, "x0": w["x0"], "x1": w["x1"], "is_dash": True})
            i += 1
            continue
        else:
            merged.append(w)
            i += 1
            continue

        while i < n and _THOUSANDS_GROUP_RE.match(words[i]["text"]) \
                and (words[i]["x0"] - current["x1"]) <= _X_GAP_TOL:
            current["text"] += " " + words[i]["text"]
            current["x1"] = words[i]["x1"]
            i += 1
        merged.append(current)
    return merged


def _is_number_token(token: dict) -> bool:
    return token.get("is_number", False) or token.get("is_dash", False)


def _assign_to_columns(number_tokens: list[dict], column_centers: list[float]) -> list[str | None]:
    """Placerar varje tal på den kolumn vars centrum ligger närmast talets
    egen mittpunkt, och returnerar en lista som är POSITIONELLT KOPPLAD
    till kolumnerna (None där kolumnen saknar värde).

    Tidigare returnerades bara talen i x-ordning, komprimerat utan luckor.
    Det gav fel år-till-värde-koppling så snart en rad saknade värden för
    något år: raden "Skulder som innehas för försäljning" i Volvos
    elvaårsöversikt (8.157 - - 6.638 ...) komprimerades till
    [8.157, 6.638, ...], vilket positionellt lästes som att 2022 var 6.638
    när det värdet i själva verket hör till 2020. Se docs/DECISIONS_FAS3.md.

    Vid krock (två tal närmast samma kolumn - ska inte hända i en
    välformad tabell) vinner det tal som ligger närmast kolumncentrum;
    det andra placeras på närmaste lediga kolumn åt det håll det ligger,
    så att inget extraherat värde tyst försvinner.
    """
    if not column_centers:
        return []

    assigned: list[str | None] = [None] * len(column_centers)
    distances: list[float] = [float("inf")] * len(column_centers)

    def place(text: str, x_center: float) -> None:
        idx = min(range(len(column_centers)), key=lambda i: abs(x_center - column_centers[i]))
        dist = abs(x_center - column_centers[idx])
        if assigned[idx] is None:
            assigned[idx], distances[idx] = text, dist
            return
        # Krock: behåll det närmaste, knuffa ut det andra till närmaste
        # lediga kolumn i den riktning det hör hemma.
        if dist < distances[idx]:
            displaced, assigned[idx], distances[idx] = assigned[idx], text, dist
            text = displaced
        direction = 1 if x_center >= column_centers[idx] else -1
        j = idx + direction
        while 0 <= j < len(assigned):
            if assigned[j] is None:
                assigned[j] = text
                return
            j += direction
        # Ingen ledig kolumn åt det hållet - prova motsatt riktning.
        j = idx - direction
        while 0 <= j < len(assigned):
            if assigned[j] is None:
                assigned[j] = text
                return
            j -= direction

    for t in sorted(number_tokens, key=lambda t: (t["x0"] + t["x1"]) / 2):
        place(t["text"], (t["x0"] + t["x1"]) / 2)

    return assigned


def _process_region_line(region: _Region, line: _Line, page_number: int) -> FinancialRow | None:
    # I en finansiell tabell står radetiketten alltid TILL VÄNSTER om
    # värdekolumnerna. Ingenting till höger om den sista kolumnen hör till
    # raden - där ligger istället sidoinnehåll, t.ex. det inbäddade
    # stapeldiagrammet på SkiStars kassaflödessida vars axeletiketter
    # ("MSEK", "1 500", "19/2020/2121/...") annars klistras in i
    # radetiketten. Se docs/DECISIONS_FAS3.md.
    right_cutoff = region.right_boundary
    if region.column_centers:
        right_cutoff = min(right_cutoff, max(region.column_centers) + _MAX_COLUMN_DISTANCE)

    in_scope_words = [
        w for w in line.words
        if w["x0"] >= region.left_boundary - _LEFT_BOUNDARY_SLACK and w["x0"] < right_cutoff
    ]
    if not in_scope_words:
        return None

    if region.has_emitted_row and region.last_line_top is not None \
            and (line.top - region.last_line_top) > _MAX_LINE_GAP_AFTER_FIRST_ROW:
        region.active = False
        return None

    region.last_line_top = line.top

    merged = _merge_number_tokens(in_scope_words)

    # Ett tal räknas bara som ett tabellvärde om det ligger nära en känd
    # kolumnposition. Löptext under tabellen (fotnoter, kommentarer) har
    # ofta inbäddade tal, men de faller sällan på exakt kolumnposition -
    # detta filter skiljer tabellvärden från sådan text.
    number_tokens = []
    label_words = []
    for t in merged:
        if _is_number_token(t) and not t.get("is_dash"):
            x_center = (t["x0"] + t["x1"]) / 2
            if _nearest_column_distance(x_center, region.column_centers) <= _MAX_COLUMN_DISTANCE:
                number_tokens.append(t)
            else:
                label_words.append(t["text"])
        elif not _is_number_token(t):
            label_words.append(t["text"])

    if not number_tokens:
        if label_words:
            region.pending_label_parts.append(" ".join(label_words))
        return None

    label = " ".join(region.pending_label_parts + [" ".join(label_words)]).strip()
    region.pending_label_parts = []

    if len(label) > _MAX_LABEL_LENGTH or len(label) < _MIN_LABEL_LENGTH:
        return None  # sannolikt löptext/fotnot eller trasig rad, inte en äkta tabellrad

    values = _assign_to_columns(number_tokens, region.column_centers)
    region.has_emitted_row = True
    return FinancialRow(
        label=label,
        values=values,
        columns=list(region.column_labels),
        page=page_number,
    )


def extract_financial_rows(page: pdfplumber.page.Page) -> list[FinancialRow]:
    words = page.extract_words()
    lines = _merge_split_header_lines(_group_lines(words))

    rows: list[FinancialRow] = []
    regions: list[_Region] = []

    for idx, line in enumerate(lines):
        # Segmentrubriken (t.ex. "Industriverksamheten") står några rader
        # ovanför årsraden - skicka med den närmaste kontexten.
        preceding = [ln for ln in lines[max(0, idx - _SEGMENT_LOOKBACK_LINES):idx]
                     if 0 < line.top - ln.top <= _SEGMENT_LOOKBACK_PT]
        header_regions = _find_header_regions(line, preceding)
        if header_regions:
            regions = header_regions
            continue

        for region in regions:
            if not region.active:
                continue
            row = _process_region_line(region, line, page.page_number)
            if row is not None:
                rows.append(row)

    return rows
