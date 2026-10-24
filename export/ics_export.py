"""
export/ics_export.py — Generazione del calendario .ics con le scadenze dei bandi

Il file .ics è importabile in Google Calendar, Outlook, Apple Calendar ecc.
Contiene un evento per ogni bando attivo con scadenza, con:
- Titolo: nome del bando
- Descrizione: ente, link, punteggio, motivo
- Data: giorno della scadenza (evento "tutto il giorno")
- Allarme: 7 giorni prima e 1 giorno prima

Esclusi: bandi in archivio (scaduti o con laurea richiesta) e bandi scartati.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib

from core.dates import parse_data


def _escape(testo: str) -> str:
    """Escape dei caratteri speciali secondo RFC 5545."""
    return (str(testo).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r", "").replace("\n", "\\n"))


def _piega(riga: str) -> list[str]:
    """Spezza le righe oltre 75 byte (obbligatorio per RFC 5545)."""
    out, corrente = [], ""
    for ch in riga:
        if len((corrente + ch).encode("utf-8")) > 74:
            out.append(corrente)
            corrente = " " + ch
        else:
            corrente += ch
    out.append(corrente)
    return out


def genera_ics(output_path: str = "data/scadenze.ics"):
    """Genera il file .ics con tutte le scadenze dei bandi attivi."""
    from core.db import get_bandi

    bandi = [
        b for b in get_bandi(solo_attivi=True)
        if parse_data(b.get("scadenza"))
        and b.get("sezione") != "archivio"
        and b.get("stato") != "scartato"
    ]

    linee = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//MonitorConcorsi//IT",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Scadenze Concorsi",
        "X-WR-TIMEZONE:Europe/Rome",
        "X-WR-CALDESC:Scadenze bandi concorsi pubblici - MonitorConcorsi",
    ]

    ora_generazione = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for bando in bandi:
        giorno = parse_data(bando["scadenza"])
        inizio = giorno.strftime("%Y%m%d")
        fine = (giorno + timedelta(days=1)).strftime("%Y%m%d")  # DTEND è escluso
        uid = hashlib.md5(f"{bando['id']}{inizio}".encode()).hexdigest()

        desc_parti = [
            f"Ente: {bando.get('ente') or 'N/D'}",
            f"Punteggio: {bando.get('punteggio', 0)}/100",
            f"Sezione: {bando.get('sezione') or 'N/D'}",
        ]
        if bando.get("link_ufficiale"):
            desc_parti.append(f"Link: {bando['link_ufficiale']}")
        if bando.get("motivo_punteggio"):
            desc_parti.append(f"Note: {bando['motivo_punteggio']}")

        evento = [
            "BEGIN:VEVENT",
            f"UID:{uid}@monitorconcorsi",
            f"DTSTAMP:{ora_generazione}",
            f"DTSTART;VALUE=DATE:{inizio}",
            f"DTEND;VALUE=DATE:{fine}",
            f"SUMMARY:{_escape('SCADENZA: ' + (bando.get('titolo') or 'Bando')[:60])}",
            f"DESCRIPTION:{_escape(chr(10).join(desc_parti))}",
        ]
        if bando.get("link_ufficiale"):
            evento.append(f"URL:{bando['link_ufficiale']}")
        evento += [
            "BEGIN:VALARM", "TRIGGER:-P7D", "ACTION:DISPLAY",
            "DESCRIPTION:Scadenza tra 7 giorni", "END:VALARM",
            "BEGIN:VALARM", "TRIGGER:-P1D", "ACTION:DISPLAY",
            "DESCRIPTION:Scadenza domani!", "END:VALARM",
            "END:VEVENT",
        ]
        for riga in evento:
            linee.extend(_piega(riga))

    linee.append("END:VCALENDAR")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\r\n".join(linee) + "\r\n", encoding="utf-8", newline="")
    print(f"Calendario generato: {output} ({len(bandi)} eventi)")
