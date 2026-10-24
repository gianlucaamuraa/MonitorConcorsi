"""
server.py — Server locale per l'interfaccia web

Avvio:  python main.py serve      → http://127.0.0.1:8765

Serve i file del progetto (web/, data/) e una piccola API:
  GET  /api/bandi              → bandi attivi, fonti in errore, ultima scansione
  POST /api/bandi/<id>         → {"stato": "...", "note": "..."} salva stato e note
  GET  /api/scansione          → stato della scansione in corso
  POST /api/scansione          → avvia una scansione in background

Ascolta solo su 127.0.0.1: non è raggiungibile da altri computer.
"""

import json
import logging
import posixpath
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from core.config import ROOT

log = logging.getLogger("server")

STATI_VALIDI = {"da_vedere", "interessante", "candidato", "scartato"}

_scansione = {"in_corso": False, "ultimo_esito": None, "errore": None}
_lock = threading.Lock()


def _avvia_scansione_background() -> bool:
    with _lock:
        if _scansione["in_corso"]:
            return False
        _scansione.update(in_corso=True, errore=None)

    def lavoro():
        from core.pipeline import esegui_scansione
        try:
            r = esegui_scansione()
            _scansione["ultimo_esito"] = {
                "nuovi": len(r["nuovi"]), "archiviati": r["archiviati"],
                "durata_sec": r["durata_sec"],
            }
        except Exception as e:
            log.exception("Scansione fallita")
            _scansione["errore"] = str(e)
        finally:
            _scansione["in_corso"] = False

    threading.Thread(target=lavoro, daemon=True).start()
    return True


class Gestore(SimpleHTTPRequestHandler):
    # Dichiaro UTF-8, altrimenti accenti e simboli possono apparire storpiati
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".ics": "text/calendar; charset=utf-8",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, formato, *args):
        log.debug(formato % args)

    # -- risposte ---------------------------------------------------------
    def _json(self, dati, codice=HTTPStatus.OK):
        corpo = json.dumps(dati, ensure_ascii=False).encode("utf-8")
        self.send_response(codice)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _leggi_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n or n > 100_000:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # -- GET ----------------------------------------------------------------
    def do_GET(self):
        percorso = urlparse(self.path).path
        if percorso in ("/", "/index.html"):
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/web/index.html")
            self.end_headers()
            return
        if percorso == "/api/bandi":
            from export.json_export import dati_interfaccia
            return self._json(dati_interfaccia())
        if percorso == "/api/scansione":
            return self._json(_scansione)
        # File statici: solo web/ e data/ (niente config.yaml con i token!).
        # normpath risolve "/web/../config.yaml" prima del controllo.
        pulito = posixpath.normpath(unquote(percorso).replace("\\", "/"))
        if not (pulito.startswith("/web/") or pulito.startswith("/data/")) \
                or pulito.endswith((".db", ".db-journal")):
            return self.send_error(HTTPStatus.NOT_FOUND)
        return super().do_GET()

    # -- POST ---------------------------------------------------------------
    def do_POST(self):
        percorso = urlparse(self.path).path
        # Richiedere JSON impedisce ad altri siti aperti nel browser di
        # inviare richieste a questo server (il browser le blocca senza CORS)
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._json({"errore": "Content-Type deve essere application/json"},
                              HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        if percorso == "/api/scansione":
            avviata = _avvia_scansione_background()
            return self._json({"avviata": avviata, **_scansione})

        if percorso.startswith("/api/bandi/"):
            from core.db import aggiorna_stato_bando, get_bando
            bando_id = percorso.rsplit("/", 1)[-1]
            dati = self._leggi_json()
            bando = get_bando(bando_id)
            if bando is None:
                return self._json({"errore": "bando non trovato"}, HTTPStatus.NOT_FOUND)
            stato = dati.get("stato", bando["stato"])
            if stato not in STATI_VALIDI:
                return self._json({"errore": "stato non valido"}, HTTPStatus.BAD_REQUEST)
            note = dati.get("note")
            aggiorna_stato_bando(bando_id, stato, note[:2000] if isinstance(note, str) else None)
            return self._json(get_bando(bando_id))

        self.send_error(HTTPStatus.NOT_FOUND)


def avvia_server(porta: int = 8765, apri_browser: bool = True):
    from core.db import init_db
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", porta), Gestore)
    url = f"http://127.0.0.1:{porta}/web/index.html"
    print(f"Interfaccia disponibile su {url}  (Ctrl+C per chiudere)")
    if apri_browser:
        import webbrowser
        threading.Timer(1.0, webbrowser.open, args=[url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer chiuso.")
    finally:
        server.server_close()
