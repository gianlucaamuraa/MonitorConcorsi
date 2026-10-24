"""
export/html_report.py — Generazione del report HTML giornaliero

Produce un file HTML leggibile con:
- Riepilogo della scansione (fonte per fonte)
- Bandi nuovi dall'ultima scansione
- Bandi in scadenza nei prossimi 7 giorni
- Fonti con errore
"""

from pathlib import Path
from datetime import datetime, timedelta
from html import escape
import json


def _e(valore, default: str = "N/D") -> str:
    """Testo sicuro per l'HTML (i dati arrivano da siti esterni)."""
    return escape(str(valore)) if valore not in (None, "") else default


TEMPLATE_HTML = """<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MonitorConcorsi — Report {data}</title>
    <style>
        /* Palette di stepservizi.net/new */
        :root {{ --fg: 11 12 14; --bg: 240 242 245; --surface: 255 255 255; --primary: 1 53 101;
                 --primary-600: 2 105 202; --muted: 82 102 122; --danger: oklch(.586 .253 17.585);
                 --success: oklch(.596 .145 163.225); color-scheme: light; }}
        @media (prefers-color-scheme: dark) {{
            :root {{ --fg: 204 204 204; --bg: 14 21 37; --surface: 20 29 50; --primary: 138 206 255;
                     --primary-600: 51 170 255; --muted: 109 134 197; --danger: oklch(.712 .194 13.428);
                     --success: oklch(.765 .177 163.223); color-scheme: dark; }}
        }}
        * {{ box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                max-width: 64rem; margin: 0 auto; padding: 2rem 1rem; background: rgb(var(--bg)); color: rgb(var(--fg)); line-height: 1.5; }}
        h1, h2 {{ font-weight: 700; background: linear-gradient(to right, rgb(var(--primary)), rgb(var(--primary-600)));
                  -webkit-background-clip: text; background-clip: text; color: transparent; }}
        h1 {{ font-size: 2rem; margin: 0 0 .25rem; }}
        h2 {{ font-size: 1.35rem; margin: 2.25rem 0 .75rem; padding-bottom: .4rem; border-bottom: 3px solid rgb(var(--primary) / .25); }}
        .bando {{ background: rgb(var(--surface) / .8); border: 1px solid rgb(var(--fg) / .1); border-left: 3px solid rgb(var(--primary) / .5);
                  border-radius: .75rem; padding: .85rem 1rem; margin: .6rem 0; }}
        .bando strong {{ color: rgb(var(--primary)); }}
        .bando.urgente {{ border-left-color: var(--danger); }}
        .bando.alto {{ border-left-color: rgb(var(--primary-600)); }}
        .punteggio {{ display: inline-block; background: rgb(var(--primary)); color: rgb(var(--bg)); padding: .05rem .55rem;
                      border-radius: 9999px; font-size: .75rem; font-weight: 600; margin-left: .35rem; }}
        .fonte {{ font-size: .75rem; color: rgb(var(--muted)); }}
        .meta {{ color: rgb(var(--muted)); font-size: .875rem; }}
        a {{ color: rgb(var(--primary-600)); text-decoration: none; font-weight: 500; }}
        a:hover {{ text-decoration: underline; }}
        .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: .75rem; margin-top: 1.25rem; }}
        .stat {{ background: rgb(var(--surface) / .8); border: 1px solid rgb(var(--fg) / .1); border-radius: .75rem; padding: .85rem 1rem; font-size: .875rem; color: rgb(var(--muted)); }}
        .stat strong {{ display: block; font-size: 1.75rem; color: rgb(var(--primary)); }}
        .tabella {{ overflow-x: auto; border: 1px solid rgb(var(--fg) / .1); border-radius: .75rem; background: rgb(var(--surface) / .8); }}
        table {{ width: 100%; border-collapse: collapse; font-size: .875rem; }}
        th {{ text-align: left; padding: .55rem .75rem; color: rgb(var(--muted)); font-size: .75rem; text-transform: uppercase; letter-spacing: .05em;
              border-bottom: 1px solid rgb(var(--fg) / .1); }}
        td {{ padding: .55rem .75rem; border-bottom: 1px solid rgb(var(--fg) / .06); }}
        .ok {{ color: var(--success); font-weight: 600; }}
        .errore-fonte {{ color: var(--danger); font-weight: 600; }}
    </style>
</head>
<body>
    <h1>MonitorConcorsi — Report del {data}</h1>
    <p class="meta">Generato il {ora} | {n_totale} bandi attivi nel database</p>
    
    <div class="stats">
        <div class="stat"><strong>{n_nuovi}</strong>Nuovi oggi</div>
        <div class="stat"><strong>{n_scadenza}</strong>In scadenza (7gg)</div>
        <div class="stat"><strong>{n_compatibili}</strong>Compatibili</div>
        <div class="stat"><strong>{n_errori}</strong>Fonti con errore</div>
    </div>

    {sezione_scadenza}
    {sezione_nuovi}
    {sezione_errori}
    {sezione_log}
    
    <p class="meta" style="margin-top: 40px; text-align: center;">
        MonitorConcorsi — Progetto personale, uso non commerciale
    </p>
</body>
</html>"""


def genera_report(output_path: str = "data/report_giornaliero.html"):
    """Genera il report HTML giornaliero."""
    from core.db import get_bandi, get_bandi_in_scadenza, get_fonti_in_errore, get_connection
    
    oggi = datetime.now()
    bandi_tutti = get_bandi(solo_attivi=True)
    bandi_scadenza = get_bandi_in_scadenza(giorni=7)
    fonti_errore = get_fonti_in_errore()
    
    # Bandi nuovi nelle ultime 24 ore (esclusi quelli non compatibili)
    ieri = (oggi - timedelta(days=1)).isoformat()
    bandi_nuovi = [
        b for b in bandi_tutti
        if (b.get("prima_rilevazione") or "") >= ieri and b.get("sezione") != "archivio"
    ]
    bandi_compatibili = [b for b in bandi_tutti if b.get("sezione") == "compatibile"]
    
    # Sezione bandi in scadenza
    sezione_scadenza = _genera_sezione_bandi(
        "In scadenza nei prossimi 7 giorni",
        bandi_scadenza,
        urgente=True
    )
    
    # Sezione bandi nuovi
    sezione_nuovi = _genera_sezione_bandi(
        "Nuovi dall'ultima scansione",
        bandi_nuovi[:20]  # Max 20 nel report
    )
    
    # Sezione errori fonti
    sezione_errori = ""
    if fonti_errore:
        righe = ""
        for f in fonti_errore:
            righe += f"""
            <tr>
                <td><strong>{_e(f['fonte'])}</strong></td>
                <td class="errore-fonte">{_e(f.get('tipo_errore'), 'errore')}</td>
                <td>{_e((f.get('motivo') or '')[:150])}</td>
                <td class="meta">{_e((f.get('ultimo_errore') or '')[:16].replace('T', ' '), '')}</td>
            </tr>"""
        sezione_errori = f"""
        <h2>Fonti con errore</h2>
        <div class="tabella"><table>
            <tr><th>Fonte</th><th>Tipo</th><th>Motivo</th><th>Data</th></tr>
            {righe}
        </table></div>"""
    
    # Log ultima scansione
    conn = get_connection()
    log_rows = conn.execute(
        "SELECT * FROM scan_log ORDER BY timestamp DESC LIMIT 50"
    ).fetchall()
    conn.close()
    
    righe_log = ""
    for row in log_rows:
        cls = "ok" if row["esito"] == "ok" else "errore-fonte"
        righe_log += f"""
        <tr>
            <td><strong>{_e(row['fonte'])}</strong></td>
            <td class="{cls}">{_e(row['esito'])}</td>
            <td>{row['n_bandi']}</td>
            <td>{row['n_nuovi']}</td>
            <td class="meta">{_e(row['timestamp'][:16].replace('T', ' '))}</td>
        </tr>"""
    
    sezione_log = f"""
    <h2>Registro delle scansioni</h2>
    <div class="tabella"><table>
        <tr><th>Fonte</th><th>Esito</th><th>Bandi trovati</th><th>Nuovi</th><th>Data/Ora</th></tr>
        {righe_log}
    </table></div>""" if righe_log else ""
    
    pagina = TEMPLATE_HTML.format(
        data=oggi.strftime("%d/%m/%Y"),
        ora=oggi.strftime("%H:%M"),
        n_totale=len(bandi_tutti),
        n_nuovi=len(bandi_nuovi),
        n_scadenza=len(bandi_scadenza),
        n_compatibili=len(bandi_compatibili),
        n_errori=len(fonti_errore),
        sezione_scadenza=sezione_scadenza,
        sezione_nuovi=sezione_nuovi,
        sezione_errori=sezione_errori,
        sezione_log=sezione_log,
    )
    
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(pagina, encoding="utf-8")
    print(f"Report generato: {output}")


def _genera_sezione_bandi(titolo: str, bandi: list, urgente: bool = False) -> str:
    """Genera l'HTML per una sezione di bandi."""
    if not bandi:
        return f"<h2>{titolo}</h2><p>Nessun bando in questa sezione.</p>"
    
    cards = ""
    for b in bandi:
        punteggio = b.get("punteggio", 0)
        cls = "urgente" if urgente else ("alto" if punteggio >= 60 else "")
        fonti = json.loads(b.get("fonti") or "[]")
        
        scadenza = b.get("scadenza")
        if scadenza:
            try:
                scadenza = datetime.strptime(scadenza, "%Y-%m-%d").strftime("%d/%m/%Y")
            except ValueError:
                pass

        cards += f"""
        <div class="bando {cls}">
            <strong>{_e(b.get('titolo'))}</strong>
            <span class="punteggio">{punteggio}/100</span><br>
            <span class="meta">
                {_e(b.get('ente'))} |
                Scadenza: {_e(scadenza)} |
                Sede: {_e(b.get('sede'))}
            </span><br>
            <span class="fonte">Fonti: {_e(', '.join(fonti))}</span><br>
            <a href="{_e(b.get('link_ufficiale'), '#')}" target="_blank" rel="noopener">Apri bando ufficiale</a>
        </div>"""
    
    return f"<h2>{titolo} ({len(bandi)})</h2>{cards}"
