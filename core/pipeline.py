"""
core/pipeline.py — Una scansione completa

1. Per ogni fonte attiva in fonti.yaml: scarica i bandi (siti diversi in parallelo)
2. Scarta i bandi scaduti
3. Calcola il punteggio e salva (con deduplica tra fonti)
4. Registra l'esito di ogni fonte nel log
5. Archivia i bandi scaduti già presenti e ricalcola i punteggi
   (l'urgenza della scadenza cambia ogni giorno)
6. Genera report HTML, calendario .ics e data/bandi.json
7. Invia la notifica Telegram con i nuovi bandi (se attiva)
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from collectors.registry import crea_collettore, fonti_attive
from core.config import carica_config, percorso
from core.dates import is_scaduto
from core.db import (aggiorna_punteggio, get_bandi, init_db, log_scansione,
                     scarta_scaduti, upsert_bando)
from core.scorer import calcola_punteggio

log = logging.getLogger("pipeline")


def _esito_errore(tipo: str) -> str:
    return "bloccato" if tipo in ("robots", "accesso_negato") else "errore"


def ricalcola_punteggi(profilo: dict) -> int:
    """Ricalcola punteggio e sezione di tutti i bandi attivi."""
    bandi = get_bandi(solo_attivi=True)
    for b in bandi:
        punteggio, motivo, sezione = calcola_punteggio(b, profilo)
        aggiorna_punteggio(b["id"], punteggio, motivo, sezione)
    return len(bandi)


LETTURE_IN_PARALLELO = 8   # siti diversi letti contemporaneamente


def _leggi_fonti(fonti: list[dict]):
    """
    Legge le fonti in parallelo e restituisce, nell'ordine di fonti.yaml,
    (fonte, collettore, bandi, eccezione).

    Le fonti sullo stesso sito (es. cinque comuni su egov.halleysardegna.com)
    restano in fila, con le loro pause, per non sovraccaricare quel server.
    Il database si aggiorna dopo, nel thread principale.
    """
    gruppi: dict[str, list[tuple[int, dict]]] = {}
    for i, f in enumerate(fonti):
        sito = urlparse(f.get("url") or "").netloc.lower() or f.get("tipo", "?")
        gruppi.setdefault(sito, []).append((i, f))

    def leggi_gruppo(elenco):
        risultati = []
        for i, fonte in elenco:
            log.info(f"Scansione fonte: {fonte.get('nome', '?')}")
            collettore = None
            try:
                collettore = crea_collettore(fonte)
                risultati.append((i, fonte, collettore, collettore.esegui(), None))
            except Exception as e:
                log.exception(f"Errore nella fonte {fonte.get('nome', '?')}")
                risultati.append((i, fonte, collettore, [], e))
        return risultati

    tutti = []
    with ThreadPoolExecutor(max_workers=LETTURE_IN_PARALLELO) as ex:
        for risultati in ex.map(leggi_gruppo, gruppi.values()):
            tutti.extend(risultati)
    tutti.sort(key=lambda r: r[0])
    return [r[1:] for r in tutti]


def esegui_scansione(solo: list[str] | None = None, notifica: bool = True) -> dict:
    """Esegue la scansione e restituisce un riepilogo."""
    init_db()
    config = carica_config()
    profilo = config.get("profilo", {})
    inizio = time.time()

    riepilogo = {"fonti": [], "nuovi": [], "scartati_scaduti": 0}

    for fonte, collettore, bandi, eccezione in _leggi_fonti(fonti_attive(solo)):
        nome = fonte.get("nome", "?")
        url = fonte.get("url", "") or (collettore.URL_BASE if collettore else "")
        if eccezione is not None:
            log.error(f"Errore nella fonte {nome}: {eccezione}")
            log_scansione(nome, url, "errore", errore=str(eccezione)[:500])
            riepilogo["fonti"].append({"fonte": nome, "esito": "errore", "n_bandi": 0, "n_nuovi": 0})
            continue

        n_nuovi = n_scaduti = 0
        for b in bandi:
            if is_scaduto(b.get("scadenza")):
                n_scaduti += 1
                continue
            b["fonti"] = [nome]
            b["solo_fonte_secondaria"] = 0 if fonte.get("ufficiale", True) else 1
            b["punteggio"], b["motivo_punteggio"], b["sezione"] = calcola_punteggio(b, profilo)
            if upsert_bando(b):
                n_nuovi += 1
                riepilogo["nuovi"].append(b)

        if bandi:
            esito, errore = "ok", None
        elif collettore.ultimo_errore:
            tipo, errore = collettore.ultimo_errore
            esito = _esito_errore(tipo)
        else:
            esito, errore = "vuoto", "Nessun bando aperto trovato nella pagina"

        log_scansione(nome, url, esito, len(bandi) - n_scaduti, n_nuovi, errore)
        riepilogo["fonti"].append({"fonte": nome, "esito": esito,
                                   "n_bandi": len(bandi) - n_scaduti, "n_nuovi": n_nuovi})
        riepilogo["scartati_scaduti"] += n_scaduti
        log.info(f"  {nome}: {esito}, {len(bandi) - n_scaduti} bandi, {n_nuovi} nuovi"
                 + (f", {n_scaduti} scaduti scartati" if n_scaduti else ""))

    # Bandi già nel database che nel frattempo sono scaduti
    riepilogo["archiviati"] = scarta_scaduti()
    ricalcola_punteggi(profilo)

    genera_export()

    if notifica:
        from notify.telegram import notifica_nuovi
        notifica_nuovi(riepilogo["nuovi"])

    riepilogo["durata_sec"] = round(time.time() - inizio)
    log.info(
        f"Scansione completata in {riepilogo['durata_sec']}s: "
        f"{len(riepilogo['nuovi'])} nuovi, {riepilogo['archiviati']} archiviati perché scaduti"
    )
    return riepilogo


def genera_export():
    """Rigenera report HTML, calendario .ics e JSON per l'interfaccia web."""
    from export.html_report import genera_report
    from export.ics_export import genera_ics
    from export.json_export import genera_json

    genera_report(percorso("report_path", "data/report_giornaliero.html"))
    genera_ics(percorso("ics_path", "data/scadenze.ics"))
    genera_json(percorso("json_path", "data/bandi.json"))
