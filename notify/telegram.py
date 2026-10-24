"""
notify/telegram.py — Notifiche Telegram dopo ogni scansione

Configurazione in config.yaml → telegram (bot_token, chat_id, attivo: true).
In alternativa (es. GitHub Actions) le variabili d'ambiente
TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID hanno la precedenza.

Invia un messaggio con:
- i nuovi bandi compatibili o da verificare (esclusi quelli in archivio)
- i bandi che scadono nei prossimi 3 giorni non ancora scartati
"""

import logging
import os
from datetime import datetime
from html import escape

import requests

from core.config import carica_config

log = logging.getLogger("telegram")

API = "https://api.telegram.org/bot{token}/sendMessage"
LIMITE_CARATTERI = 3800       # Telegram accetta fino a 4096 caratteri per messaggio
SOGLIA_PUNTEGGIO = 30         # sotto questa soglia i nuovi bandi non vengono notificati


def _credenziali() -> tuple[str, str] | None:
    conf = carica_config().get("telegram", {})
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or conf.get("bot_token")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or conf.get("chat_id")
    attivo = conf.get("attivo") or bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
    if not (attivo and token and chat_id):
        return None
    return token, str(chat_id)


def invia_messaggio(testo: str) -> bool:
    """Invia un messaggio (HTML), spezzandolo se troppo lungo."""
    cred = _credenziali()
    if cred is None:
        log.info("Telegram non configurato: notifica saltata")
        return False
    token, chat_id = cred

    blocchi, corrente = [], ""
    for riga in testo.split("\n"):
        if len(corrente) + len(riga) + 1 > LIMITE_CARATTERI:
            blocchi.append(corrente)
            corrente = ""
        corrente += riga + "\n"
    if corrente.strip():
        blocchi.append(corrente)

    ok = True
    for blocco in blocchi:
        try:
            r = requests.post(API.format(token=token), timeout=20, data={
                "chat_id": chat_id,
                "text": blocco,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            })
            if r.status_code != 200:
                log.warning(f"Telegram ha risposto {r.status_code}: {r.text[:200]}")
                ok = False
        except requests.RequestException as e:
            log.warning(f"Invio Telegram fallito: {e}")
            ok = False
    return ok


def _riga_bando(b: dict) -> str:
    scad = b.get("scadenza")
    if scad:
        try:
            scad = datetime.strptime(scad, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            pass
    titolo = escape((b.get("titolo") or "Bando")[:140])
    link = b.get("link_ufficiale")
    titolo_html = f'<a href="{escape(link)}">{titolo}</a>' if link else titolo
    return (f"• <b>{b.get('punteggio', 0)}</b>/100 — {titolo_html}\n"
            f"   {escape(b.get('ente') or '')} · scad. {escape(scad or 'n/d')}")


def notifica_nuovi(nuovi: list[dict]):
    """Notifica i nuovi bandi rilevanti e quelli in scadenza a breve."""
    if _credenziali() is None:
        return

    from core.db import get_bandi_in_scadenza

    rilevanti = sorted(
        (b for b in nuovi
         if b.get("sezione") != "archivio" and b.get("punteggio", 0) >= SOGLIA_PUNTEGGIO),
        key=lambda b: b.get("punteggio", 0), reverse=True,
    )
    in_scadenza = [b for b in get_bandi_in_scadenza(giorni=3)
                   if b.get("stato") in ("da_vedere", "interessante")]

    if not rilevanti and not in_scadenza:
        log.info("Nessuna novità da notificare")
        return

    parti = ["<b>MonitorConcorsi</b>"]
    if rilevanti:
        parti.append(f"\n<b>{len(rilevanti)} nuovi bandi</b>")
        parti += [_riga_bando(b) for b in rilevanti[:25]]
        if len(rilevanti) > 25:
            parti.append(f"…e altri {len(rilevanti) - 25} nell'interfaccia web")
    if in_scadenza:
        parti.append(f"\n<b>In scadenza entro 3 giorni ({len(in_scadenza)})</b>")
        parti += [_riga_bando(b) for b in in_scadenza[:15]]

    invia_messaggio("\n".join(parti))
