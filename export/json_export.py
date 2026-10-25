"""
export/json_export.py — data/bandi.json per l'interfaccia web

La pagina web/index.html legge i dati dal server locale (python main.py serve).
Se il server non c'è (es. pagina pubblicata con GitHub Pages) usa questo file.
"""

import json
import os
from datetime import datetime
from pathlib import Path


def enti_monitorati() -> list[dict]:
    """Enti con una fonte propria in fonti.yaml, per il filtro "Ente" della pagina.
    Restano fuori gli aggregatori e i portali nazionali (InPA, Gazzetta): i loro
    enti compaiono nel filtro solo quando hanno bandi."""
    from core.config import carica_fonti

    enti = {}
    for f in carica_fonti():
        ente = (f.get("ente") or "").strip()
        if (not f.get("attivo", True) or f.get("ufficiale") is False or not ente
                or f.get("tipo") not in ("html", "rss") or ente.lower().startswith("ente della")):
            continue
        enti.setdefault(ente, []).append(f["nome"])
    return [{"ente": e, "fonti": n} for e, n in sorted(enti.items())]


def dati_interfaccia() -> dict:
    """Bandi attivi, fonti in errore e info sull'ultima scansione."""
    from core.db import get_bandi, get_connection, get_fonti_in_errore

    bandi = get_bandi(solo_attivi=True)
    # Versione online: stato e note personali non vanno nei file pubblici
    # (online si salvano su Supabase, dietro login)
    pubblico = os.environ.get("MONITOR_PUBBLICO") == "1"
    for b in bandi:
        b["fonti"] = json.loads(b.get("fonti") or "[]")
        if pubblico:
            b["stato"], b["note"] = "da_vedere", None

    conn = get_connection()
    ultima = conn.execute("SELECT MAX(timestamp) AS t FROM scan_log").fetchone()["t"]
    conn.close()

    return {
        "generato": datetime.now().isoformat(timespec="seconds"),
        "ultima_scansione": ultima,
        "bandi": bandi,
        "errori": get_fonti_in_errore(),
        "enti_monitorati": enti_monitorati(),
    }


def genera_json(output_path: str | Path = "data/bandi.json"):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dati_interfaccia(), ensure_ascii=False, indent=1),
                      encoding="utf-8")
    print(f"Dati per l'interfaccia: {output}")
