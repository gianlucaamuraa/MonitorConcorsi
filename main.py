"""
MonitorConcorsi — punto di avvio

Comandi:
  python main.py scan                 scansione di tutte le fonti attive
  python main.py scan --fonte inpa    scansione di una sola fonte (anche se disattivata)
  python main.py serve                interfaccia web su http://127.0.0.1:8765
  python main.py schedule             scansione automatica ogni giorno (config.yaml)
  python main.py report               rigenera report, calendario e JSON senza scansionare
  python main.py fonti                elenca le fonti configurate in fonti.yaml
  python main.py sito                 prepara la cartella sito/ da pubblicare online
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Lavora sempre dalla cartella del progetto, ovunque venga lanciato
os.chdir(Path(__file__).resolve().parent)
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Su Windows la console non sempre accetta emoji/accenti
for flusso in (sys.stdout, sys.stderr):
    try:
        flusso.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def configura_log(verboso: bool = False):
    from core.config import percorso
    file_log = percorso("log_path", "data/scan_log.json").with_name("monitor.log")
    file_log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verboso else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(file_log, encoding="utf-8"),
        ],
    )
    # Le librerie HTTP sono troppo loquaci in modalità normale
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def cmd_scan(args):
    from core.pipeline import esegui_scansione
    r = esegui_scansione(solo=args.fonte, notifica=not args.no_notifica)
    print("\n=== Riepilogo ===")
    for f in r["fonti"]:
        print(f"  {f['fonte']:<28} {f['esito']:<9} {f['n_bandi']:>4} bandi  {f['n_nuovi']:>3} nuovi")
    print(f"  Nuovi bandi: {len(r['nuovi'])} · archiviati perché scaduti: {r['archiviati']}"
          f" · durata: {r['durata_sec']}s")


def cmd_serve(args):
    from server import avvia_server
    avvia_server(porta=args.porta, apri_browser=not args.no_browser)


def cmd_schedule(args):
    from apscheduler.schedulers.blocking import BlockingScheduler
    from core.config import carica_config
    from core.pipeline import esegui_scansione

    orario = carica_config().get("scansione", {}).get("orario_giornaliero", "07:00")
    ore, minuti = (int(x) for x in orario.split(":"))
    scheduler = BlockingScheduler(timezone="Europe/Rome")
    scheduler.add_job(esegui_scansione, "cron", hour=ore, minute=minuti,
                      misfire_grace_time=3600, coalesce=True)
    print(f"Scansione automatica ogni giorno alle {orario} (Ctrl+C per fermare)")
    if args.subito:
        esegui_scansione()
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\nPianificazione fermata.")


def cmd_report(args):
    from core.config import carica_config
    from core.db import init_db, scarta_scaduti
    from core.pipeline import genera_export, ricalcola_punteggi
    init_db()
    n = scarta_scaduti()
    ricalcola_punteggi(carica_config().get("profilo", {}))
    genera_export()
    if n:
        print(f"{n} bandi archiviati perché scaduti")


def cmd_sito(args):
    """Prepara la cartella 'sito/' da pubblicare: pagina web + file pubblici (niente database)."""
    import shutil
    from core.config import ROOT, percorso

    sito = ROOT / "sito"
    if sito.exists():
        shutil.rmtree(sito)
    shutil.copytree(ROOT / "web", sito / "web")
    (sito / "data").mkdir(parents=True)
    for chiave, default in (("json_path", "data/bandi.json"),
                            ("report_path", "data/report_giornaliero.html"),
                            ("ics_path", "data/scadenze.ics")):
        f = percorso(chiave, default)
        if f.exists():
            shutil.copy2(f, sito / "data" / f.name)
    # La radice del sito porta direttamente alla pagina
    (sito / "index.html").write_text(
        '<!DOCTYPE html><meta charset="utf-8"><title>MonitorConcorsi</title>'
        '<meta http-equiv="refresh" content="0; url=web/index.html">'
        '<a href="web/index.html">MonitorConcorsi</a>', encoding="utf-8")
    (sito / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Cartella del sito pronta: {sito}")


def cmd_fonti(args):
    from core.config import carica_fonti
    for f in carica_fonti():
        stato = "attiva" if f.get("attivo", True) else "disattivata"
        print(f"  {f['nome']:<28} {f.get('tipo', 'html'):<9} {stato:<12} {f.get('url', '')}")


def main():
    parser = argparse.ArgumentParser(description="MonitorConcorsi — concorsi pubblici per il tuo profilo")
    parser.add_argument("-v", "--verboso", action="store_true", help="log dettagliato")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("scan", help="esegue una scansione")
    p.add_argument("--fonte", action="append", help="solo questa fonte (ripetibile)")
    p.add_argument("--no-notifica", action="store_true", help="non inviare Telegram")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("serve", help="interfaccia web locale")
    p.add_argument("--porta", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="non aprire il browser")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("schedule", help="scansione automatica giornaliera")
    p.add_argument("--subito", action="store_true", help="esegue anche una scansione ora")
    p.set_defaults(func=cmd_schedule)

    sub.add_parser("report", help="rigenera report/calendario/JSON").set_defaults(func=cmd_report)
    sub.add_parser("fonti", help="elenca le fonti").set_defaults(func=cmd_fonti)
    sub.add_parser("sito", help="prepara la cartella sito/ da pubblicare").set_defaults(func=cmd_sito)

    args = parser.parse_args()
    configura_log(args.verboso)
    args.func(args)


if __name__ == "__main__":
    main()
