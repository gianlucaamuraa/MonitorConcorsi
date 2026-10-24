"""
core/dedup.py — Deduplicazione dei bandi

Logica:
1. ID deterministico (hash ente+titolo+scadenza) per corrispondenze esatte
2. Fuzzy matching (Levenshtein) per varianti del titolo con stesso ente

Se lo stesso bando è trovato su più fonti → una sola scheda con tutte le fonti elencate.
Se è su una fonte ufficiale, quella prevale sulla secondaria.
"""

import json

try:
    from rapidfuzz import fuzz          # consigliato: veloce e senza compilazione
except ImportError:                     # compatibilità con l'installazione precedente
    from fuzzywuzzy import fuzz


# Soglia di similarità per considerare due bandi duplicati (0-100)
SOGLIA_FUZZY = 85

# Fonti considerate ufficiali (in ordine di priorità)
FONTI_UFFICIALI = [
    "inpa", "gazzetta_ufficiale", "regione_sardegna", "comune_sassari",
    "citta_metropolitana_sassari", "uniss", "aspal_concorsi", "aspal_avvisi",
    "agenzia_regionale_lavoro", "aspal_art16", "aspal_art16_svolgimento",
    "ares_sardegna", "areus", "asl1_sassari", "aou_sassari",
    "abbanoa_selezioni", "arst", "atp_sassari", "ase_entrate",
    "sardegnait_avvisi", "sardegnait_selezioni", "area_sardegna",
    "ader_selezioni", "ader_aperte", "agenzia_entrate", "agenzia_entrate_ns",
    "dogane_bandi", "dogane_svolgimento", "ministero_interno",
    "inps", "inail", "inl", "aci", "mit_bandi", "mit_attivi",
    "vigili_fuoco"
]

FONTI_SECONDARIE = ["ossian", "concorsipubblici", "concorso_vinto"]


def registra_fonte(nome: str, ufficiale: bool):
    """Aggiunge una fonte definita in fonti.yaml all'elenco ufficiali/secondarie."""
    elenco = FONTI_UFFICIALI if ufficiale else FONTI_SECONDARIE
    if nome not in elenco:
        elenco.append(nome)


def normalizza_testo(testo: str) -> str:
    """Normalizza il testo per il confronto fuzzy."""
    if not testo:
        return ""
    return " ".join(testo.lower().strip().split())


def is_duplicato_fuzzy(bando1: dict, bando2: dict) -> bool:
    """
    Verifica se due bandi sono probabilmente lo stesso usando fuzzy matching.
    Considera duplicati se: stesso ente E titoli molto simili.
    """
    ente1 = normalizza_testo(bando1.get("ente", ""))
    ente2 = normalizza_testo(bando2.get("ente", ""))
    
    # Enti devono essere almeno parzialmente simili
    if fuzz.token_set_ratio(ente1, ente2) < 70:
        return False
    
    titolo1 = normalizza_testo(bando1.get("titolo", ""))
    titolo2 = normalizza_testo(bando2.get("titolo", ""))
    
    return fuzz.token_set_ratio(titolo1, titolo2) >= SOGLIA_FUZZY


def fonti_ordinate(fonti: list) -> list:
    """
    Ordina le fonti mettendo quelle ufficiali per prime.
    """
    ufficiali = [f for f in fonti if f in FONTI_UFFICIALI]
    secondarie = [f for f in fonti if f in FONTI_SECONDARIE]
    altre = [f for f in fonti if f not in FONTI_UFFICIALI and f not in FONTI_SECONDARIE]
    return ufficiali + altre + secondarie


def unisci_bandi(primario: dict, secondario: dict) -> dict:
    """
    Unisce i dati di due bandi duplicati in un'unica scheda.
    I dati del bando su fonte ufficiale hanno la precedenza.
    """
    # Determina quale fonte è più autorevole
    fonti_primarie = json.loads(primario.get("fonti") or "[]")
    fonti_secondarie_b = json.loads(secondario.get("fonti") or "[]")
    
    # Se il primario è su fonte ufficiale, mantiene i suoi dati
    # Aggiunge solo le fonti aggiuntive
    tutte_fonti = list(set(fonti_primarie + fonti_secondarie_b))
    
    primario_aggiornato = dict(primario)
    primario_aggiornato["fonti"] = json.dumps(fonti_ordinate(tutte_fonti))
    
    # Se il link ufficiale non è presente nel primario, prendilo dal secondario
    if not primario_aggiornato.get("link_ufficiale") and secondario.get("link_ufficiale"):
        primario_aggiornato["link_ufficiale"] = secondario["link_ufficiale"]
    
    # Solo fonte secondaria: aggiorna il flag
    ha_fonte_ufficiale = any(f in FONTI_UFFICIALI for f in tutte_fonti)
    primario_aggiornato["solo_fonte_secondaria"] = 0 if ha_fonte_ufficiale else 1
    
    return primario_aggiornato
