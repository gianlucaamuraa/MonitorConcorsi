"""
core/dates.py — Lettura delle date nei formati usati dai bandi italiani

Esempi gestiti:
    "2026-11-13", "2026-11-13T22:59:00Z", "13/11/2026", "13-11-2026",
    "13.11.2026", "13 novembre 2026", "scad. 7 novembre 2026"
"""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

FUSO_ITALIA = ZoneInfo("Europe/Rome")

MESI = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
    "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10,
    "novembre": 11, "dicembre": 12,
}

_RE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})")
_RE_NUM = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b")
_RE_TESTO = re.compile(
    r"\b(\d{1,2})(?:°|º)?\s+(" + "|".join(MESI) + r")\s+(\d{4})\b", re.IGNORECASE
)


def _crea(anno: int, mese: int, giorno: int) -> date | None:
    try:
        return date(anno, mese, giorno)
    except ValueError:
        return None


def parse_data(testo: str | None) -> date | None:
    """Trova la prima data nel testo e la restituisce come date (o None)."""
    if not testo:
        return None
    testo = str(testo).strip()

    # Timestamp ISO con orario in UTC (es. InPA): converto nell'ora italiana,
    # così "2026-11-13T22:59:00Z" diventa il 13/11 e non il 14/11.
    if "T" in testo and _RE_ISO.match(testo):
        try:
            dt = datetime.fromisoformat(testo.replace("Z", "+00:00"))
            if dt.tzinfo:
                dt = dt.astimezone(FUSO_ITALIA)
            return dt.date()
        except ValueError:
            pass

    m = _RE_ISO.search(testo)
    if m:
        return _crea(int(m[1]), int(m[2]), int(m[3]))
    m = _RE_NUM.search(testo)
    if m:
        return _crea(int(m[3]), int(m[2]), int(m[1]))
    m = _RE_TESTO.search(testo)
    if m:
        return _crea(int(m[3]), MESI[m[2].lower()], int(m[1]))
    return None


# Parole che introducono la scadenza: prima quelle certe, poi quelle generiche
_RE_PAROLA_SCAD_FORTE = re.compile(r"(?<!non )\b(?:scadenza|scade|scadr[àa]|scad\.|scaduto il)", re.IGNORECASE)
_RE_PAROLA_SCAD_DEBOLE = re.compile(r"\b(?:entro|termine)\b", re.IGNORECASE)
# Una data dopo "pubblicazione"/"pubblicato" è la data di pubblicazione, non la scadenza
_RE_STOP_FINESTRA = re.compile(r"pubblica(?:zion|t)", re.IGNORECASE)
# "15 (quindici) giorni", "30° giorno": termine relativo, non una data
_RE_RELATIVO = re.compile(r"\d{1,3}\s*(?:°|º)?\s*(?:\([^)]{0,20}\)\s*)?giorn[oi]", re.IGNORECASE)
_RE_REL_PUBBL = re.compile(
    r"(\d{1,3})\s*(?:°|º)?\s*(?:\([^)]{0,20}\)\s*)?giorn[oi][^.]{0,100}?pubblicazion", re.IGNORECASE
)
_RE_PUBBLICATO = re.compile(r"pubblicat\w*[^.]{0,60}", re.IGNORECASE)


def estrai_scadenza(testo: str | None) -> date | None:
    """
    Cerca la scadenza in un testo libero. Esempi:
      "scad. 7 novembre 2026", "Scadenza 13/11/2026", "scade 26/10/2026",
      "entro le ore 23:59 del 19/10/2026", "termine: 28.10.2026",
      "entro 30 giorni dalla pubblicazione" + "Pubblicato il 29 settembre 2026"
    """
    if not testo:
        return None
    for parole in (_RE_PAROLA_SCAD_FORTE, _RE_PAROLA_SCAD_DEBOLE):
        for m in parole.finditer(testo):
            finestra = testo[m.end():m.end() + 90]
            # "entro 15 giorni dalla pubblicazione": la data che segue non è la scadenza
            for stop in (_RE_RELATIVO, _RE_STOP_FINESTRA):
                s = stop.search(finestra)
                if s:
                    finestra = finestra[:s.start()]
            d = parse_data(finestra)
            if d:
                return d

    # Termine relativo alla data di pubblicazione
    m = _RE_REL_PUBBL.search(testo)
    if m:
        p = _RE_PUBBLICATO.search(testo)
        pubblicazione = parse_data(p.group(0)) if p else None
        if pubblicazione:
            return pubblicazione + timedelta(days=int(m.group(1)))
    return None


def a_iso(d: date | None) -> str | None:
    """date → 'YYYY-MM-DD' (o None)."""
    return d.isoformat() if d else None


def oggi() -> date:
    """Data odierna nel fuso orario italiano."""
    return datetime.now(FUSO_ITALIA).date()


def is_scaduto(scadenza: str | date | None) -> bool:
    """True se la scadenza è già passata. Senza scadenza leggibile → False."""
    d = scadenza if isinstance(scadenza, date) else parse_data(scadenza)
    return d is not None and d < oggi()


# "2024" isolato, oppure all'inizio di una data compatta "20240730..." (nomi di file)
_RE_ANNO = re.compile(r"(?<!\d)(20\d{2})(?:(?!\d)|(?=(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])))")


def anno_piu_recente(*testi: str | None) -> int | None:
    """Anno più recente (20xx) citato nei testi, utile per riconoscere bandi vecchi."""
    anni = [int(a) for t in testi if t for a in _RE_ANNO.findall(t)]
    anni = [a for a in anni if 2000 <= a <= oggi().year + 2]
    return max(anni) if anni else None
