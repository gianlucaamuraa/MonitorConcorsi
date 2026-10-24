"""
core/scorer.py — Calcolo del punteggio di rilevanza per ogni bando

Il punteggio va da 0 a 100 e considera:
- Titolo di studio richiesto (30 punti)
- Distanza geografica da Sassari (25 punti)
- Ambito professionale (25 punti)
- Urgenza della scadenza (20 punti)

Sezioni assegnate:
- "compatibile"   : diploma sufficiente (esplicito o dedotto dal profilo)
- "da_verificare" : titolo non chiaro o solo fonte secondaria
- "archivio"      : laurea richiesta, oppure bando scaduto

Il titolo di studio raramente è scritto nel titolo del bando, quindi viene
dedotto anche dall'inquadramento (CCNL Funzioni Locali / Funzioni Centrali):
  Operatori / Operatori esperti / categoria A-B  → basta la scuola dell'obbligo
  Istruttori / Assistenti / categoria C          → diploma
  Funzionari / Dirigenti / categoria D           → laurea
"""

import re

from core.dates import parse_data, oggi


# --- Parole chiave di default (integrate con config.yaml → profilo.ambiti_preferiti) ---
AMBITI_PREFERITI = [
    "informatica", "informatico", "informatici", "it", "ict", "sistemi informativi",
    "assistenza tecnica", "help desk", "supporto informatico", "assistente informatico",
    "tributi", "tributario", "tributaria", "accertamento", "riscossione", "entrate",
    "gestione dati", "data entry", "digitalizzazione", "transizione digitale",
    "excel", "applicativi", "software", "hardware", "reti",
    "amministrativo", "amministrativa", "amministrativi", "contabile", "contabilità",
    "segreteria",
]

SEDI_SASSARI_AREA = [
    "sassari", "alghero", "porto torres", "sorso", "sennori", "ossi", "usini",
    "ittiri", "tissi", "uri", "olmedo", "stintino", "valledoria", "codrongianos",
    "muros", "cargeghe", "florinas", "ploaghe", "osilo", "tergu", "nulvi",
    "ozieri", "castelsardo", "thiesi", "bonorva",
]

SEDI_SARDEGNA = [
    "sardegna", "cagliari", "nuoro", "oristano", "olbia", "tempio", "iglesias",
    "carbonia", "quartu", "selargius", "assemini", "capoterra", "macomer",
    "lanusei", "tortolì", "villacidro", "sanluri", "siniscola", "arzachena",
    "la maddalena", "sud sardegna", "gallura", "ogliastra",
]

PAROLE_DIPLOMA_OK = [
    "diploma", "scuola secondaria superiore", "scuola secondaria di secondo grado",
    "maturità", "istruzione secondaria", "titolo di studio secondario",
    "perito", "ragioniere", "geometra", "scuola dell'obbligo", "licenza media",
]

PAROLE_LAUREA_RICHIESTA = [
    "laurea", "laurea triennale", "laurea magistrale", "laurea specialistica",
    "corso di laurea", "facoltà", "abilitazione professionale",
    "diploma di laurea", "dottorato",
]

# Profili che implicano il diploma (o meno) come requisito
PROFILI_DIPLOMA = [
    "istruttore", "istruttori", "area degli istruttori", "assistente", "assistenti",
    "area degli assistenti", "categoria c", "cat. c", "cat c", "operatore",
    "operatori", "operatore esperto", "operatori esperti", "categoria b",
    "cat. b", "esecutore", "collaboratore amministrativo", "coadiutore",
    "agente di polizia locale", "agenti di polizia locale",
]

# Profili che implicano la laurea
PROFILI_LAUREA = [
    "funzionario", "funzionari", "area dei funzionari", "elevata qualificazione",
    "dirigente", "dirigenti", "direttore", "incarico quinquennale", "primario", "categoria d", "cat. d", "cat d", "medico", "medici",
    "docente", "docenti", "ricercatore", "ricercatori", "professore", "tecnologo",
    "borsa di ricerca", "assegno di ricerca", "specialista", "avvocato", "ingegnere",
    "psicologo", "farmacista", "biologo", "veterinario", "infermiere", "ostetrica",
    "fisioterapista", "tecnico sanitario", "assistente sociale", "educatore professionale",
    "dottore", "collaboratore professionale sanitario",
]


def _regex(parole: list[str]) -> re.Pattern:
    """Regex che trova le parole solo intere (così 'it' non trova 'comunità')."""
    parti = sorted({re.escape(p.lower()) for p in parole}, key=len, reverse=True)
    return re.compile(r"(?<![\wàèéìòù])(?:" + "|".join(parti) + r")(?![\wàèéìòù])")


def _trova(pattern: re.Pattern, testo: str) -> list[str]:
    return list(dict.fromkeys(pattern.findall(testo)))


_RE_DIPLOMA = _regex(PAROLE_DIPLOMA_OK)
_RE_LAUREA = _regex(PAROLE_LAUREA_RICHIESTA)
_RE_PROF_DIPLOMA = _regex(PROFILI_DIPLOMA)
_RE_PROF_LAUREA = _regex(PROFILI_LAUREA)
_RE_SASSARI = _regex(SEDI_SASSARI_AREA)
_RE_SARDEGNA = _regex(SEDI_SARDEGNA)
_RE_REMOTO = _regex(["remoto", "telelavoro", "smart working", "lavoro agile"])
_RE_RISERVATI = re.compile(
    r"mobilit[aà]['’]?\s*(?:esterna|volontaria|tra enti|compartimentale|intercompartimentale)?"
    r"|art\.?\s*30\s*(?:del)?\s*d\.?\s*lgs|progression[ei] vertical[ei]|riservat[oa] al personale (?:interno|dipendente)"
    r"|procedura (?:selettiva )?interna",
    re.IGNORECASE,
)
_cache_ambiti: dict[tuple, re.Pattern] = {}


def _regex_ambiti(profilo: dict | None) -> re.Pattern:
    extra = tuple((profilo or {}).get("ambiti_preferiti") or [])
    if extra not in _cache_ambiti:
        _cache_ambiti[extra] = _regex(AMBITI_PREFERITI + list(extra))
    return _cache_ambiti[extra]


def _valuta_titolo_studio(titolo_studio: str, testo_profilo: str) -> tuple[int, str, str]:
    """Restituisce (punti, motivo, sezione) per il criterio titolo di studio."""
    laurea = _trova(_RE_LAUREA, titolo_studio)
    # "diploma di laurea", "diploma accademico", "diploma di specializzazione"
    # non sono diplomi di scuola superiore: li tolgo prima di cercare "diploma"
    senza_falsi = re.sub(
        r"diplom[ai] (?:di laurea|universitari[oa]|accademic[oi]|di specializzazione)",
        " ", titolo_studio,
    )
    diploma = _trova(_RE_DIPLOMA, senza_falsi)

    if diploma:
        return 30, "diploma ammesso dai requisiti", "compatibile"
    if laurea:
        return 0, "laurea richiesta (non compatibile)", "archivio"

    prof_diploma = _trova(_RE_PROF_DIPLOMA, testo_profilo)
    prof_laurea = _trova(_RE_PROF_LAUREA, testo_profilo)

    if prof_diploma and not prof_laurea:
        return 26, f"profilo '{prof_diploma[0]}': diploma sufficiente", "compatibile"
    if prof_laurea and not prof_diploma:
        return 0, f"profilo '{prof_laurea[0]}': di norma richiede laurea", "archivio"
    if prof_diploma and prof_laurea:
        return 12, "profili misti (diploma e laurea): verificare il bando", "da_verificare"
    if titolo_studio.strip() in ("", "non specificato", "n/d", "—", "-"):
        return 15, "titolo di studio non specificato", "da_verificare"
    return 12, f"titolo di studio non chiaro: '{titolo_studio[:40]}'", "da_verificare"


def calcola_punteggio(bando: dict, profilo: dict = None) -> tuple[int, str, str]:
    """
    Calcola il punteggio di rilevanza di un bando per il profilo utente.

    Args:
        bando: dizionario con i dati del bando
        profilo: sezione 'profilo' di config.yaml (opzionale)

    Returns:
        (punteggio: int, motivo: str, sezione: str)
    """
    titolo_studio = (bando.get("titolo_studio") or "").lower()
    sede = (bando.get("sede") or "").lower()
    titolo = (bando.get("titolo") or "").lower()
    profilo_bando = (bando.get("profilo") or "").lower()
    livello = (bando.get("livello") or "").lower()

    # ----------------------------------------------------------------
    # 0. BANDO SCADUTO → scartato subito
    # ----------------------------------------------------------------
    scadenza = parse_data(bando.get("scadenza"))
    if scadenza and scadenza < oggi():
        return 0, f"Scaduto il {scadenza.strftime('%d/%m/%Y')}", "archivio"

    # Mobilità e procedure interne sono riservate a chi è già dipendente pubblico
    if not (profilo or {}).get("dipendente_pubblico", False):
        m = _RE_RISERVATI.search(f"{titolo} {livello}")
        if m:
            return 0, f"Riservato a dipendenti pubblici ('{m.group(0)}')", "archivio"

    motivi = []
    punteggio = 0

    # ----------------------------------------------------------------
    # 1. TITOLO DI STUDIO (30 punti)
    # ----------------------------------------------------------------
    ts_score, ts_motivo, sezione = _valuta_titolo_studio(
        titolo_studio, f"{profilo_bando} {livello} {titolo}"
    )
    punteggio += ts_score
    motivi.append(f"Titolo studio: {ts_score}/30 ({ts_motivo})")

    # Se la laurea è richiesta, fermiamo qui: il profilo utente non è compatibile
    if sezione == "archivio":
        return 0, " | ".join(motivi), "archivio"

    # ----------------------------------------------------------------
    # 2. DISTANZA / ZONA (25 punti)
    # ----------------------------------------------------------------
    sede_full = f"{sede} {titolo} {(bando.get('ente') or '').lower()}"
    zona_prioritaria = ((profilo or {}).get("zona_prioritaria") or "sassari").lower()

    if zona_prioritaria in sede_full or _RE_SASSARI.search(sede_full):
        zona_score, zona_motivo = 25, "Sassari o area vicina"
    elif _RE_SARDEGNA.search(sede_full):
        zona_score, zona_motivo = 15, "altra sede in Sardegna"
    elif _RE_REMOTO.search(sede_full):
        zona_score, zona_motivo = 8, "lavoro remoto"
    elif sede.strip() in ("", "italia", "nazionale", "tutta italia", "—", "-"):
        zona_score, zona_motivo = 5, "sede non specificata o nazionale"
    else:
        zona_score, zona_motivo = 0, f"sede fuori Sardegna ({sede[:30]})"

    punteggio += zona_score
    motivi.append(f"Zona: {zona_score}/25 ({zona_motivo})")

    # ----------------------------------------------------------------
    # 3. AMBITO PROFESSIONALE (25 punti)
    # ----------------------------------------------------------------
    ambiti_trovati = _trova(_regex_ambiti(profilo), f"{titolo} {profilo_bando}")

    if len(ambiti_trovati) >= 2:
        ambito_score = 25
        ambito_motivo = f"ambito molto pertinente ({', '.join(ambiti_trovati[:3])})"
    elif len(ambiti_trovati) == 1:
        ambito_score = 18
        ambito_motivo = f"ambito pertinente ({ambiti_trovati[0]})"
    else:
        ambito_score = 3
        ambito_motivo = "ambito non specificato o non pertinente"

    punteggio += ambito_score
    motivi.append(f"Ambito: {ambito_score}/25 ({ambito_motivo})")

    # ----------------------------------------------------------------
    # 4. SCADENZA / URGENZA (20 punti)
    # ----------------------------------------------------------------
    if scadenza:
        giorni_rimasti = (scadenza - oggi()).days
        if giorni_rimasti <= 7:
            scad_score = 8
            scad_motivo = f"scade tra {giorni_rimasti} giorni — URGENTE"
        elif giorni_rimasti <= 30:
            scad_score = 15
            scad_motivo = f"scade tra {giorni_rimasti} giorni"
        else:
            scad_score = 20
            scad_motivo = f"scade tra {giorni_rimasti} giorni (ampio margine)"
    elif bando.get("scadenza"):
        scad_score, scad_motivo = 10, f"data non leggibile: '{bando['scadenza']}'"
    else:
        scad_score, scad_motivo = 10, "scadenza non specificata"

    punteggio += scad_score
    motivi.append(f"Scadenza: {scad_score}/20 ({scad_motivo})")

    # ----------------------------------------------------------------
    # Sezione finale
    # ----------------------------------------------------------------
    if bando.get("solo_fonte_secondaria"):
        sezione = "da_verificare"
    elif sezione == "compatibile" and punteggio < 40:
        sezione = "da_verificare"

    return min(punteggio, 100), " | ".join(motivi), sezione
