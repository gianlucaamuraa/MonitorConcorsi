"""
core/db.py — Gestione del database SQLite per MonitorConcorsi

Contiene:
- Creazione dello schema (tabelle bandi, scan_log, fonti_errore)
- Funzioni CRUD per inserire, aggiornare e leggere i bandi
- Funzioni per il registro delle scansioni
"""

import sqlite3
import json
import hashlib
from datetime import datetime

from core.config import percorso
from core.dates import oggi


def get_connection() -> sqlite3.Connection:
    """Restituisce una connessione al database SQLite con row_factory abilitata."""
    db_path = percorso("db_path", "data/concorsi.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Abilita le chiavi esterne
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Crea le tabelle se non esistono. Da chiamare all'avvio del programma."""
    conn = get_connection()
    with conn:
        conn.executescript("""
            -- ----------------------------------------------------------------
            -- Tabella principale: bandi normalizzati e deduplicati
            -- ----------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS bandi (
                id TEXT PRIMARY KEY,           -- hash deterministico (ente+titolo+scadenza)
                ente TEXT NOT NULL,            -- nome dell'ente che ha pubblicato
                titolo TEXT NOT NULL,          -- titolo del bando/selezione
                profilo TEXT,                  -- profilo professionale cercato
                livello TEXT,                  -- livello contrattuale (es. B, C, D, ...)
                n_posti INTEGER,               -- numero di posti disponibili
                titolo_studio TEXT,            -- titolo di studio richiesto (testo libero)
                sede TEXT,                     -- sede di lavoro
                scadenza TEXT,                 -- data scadenza (ISO 8601: YYYY-MM-DD)
                link_ufficiale TEXT,           -- URL al bando ufficiale
                fonti TEXT DEFAULT '[]',       -- JSON array: lista delle fonti in cui compare
                solo_fonte_secondaria INTEGER DEFAULT 0,  -- 1 = trovato solo su fonte secondaria
                prima_rilevazione TEXT,        -- data/ora della prima rilevazione
                ultima_verifica TEXT,          -- data/ora dell'ultima scansione che lo ha trovato
                punteggio INTEGER DEFAULT 0,   -- punteggio rilevanza 0-100
                motivo_punteggio TEXT,         -- spiegazione in una riga del punteggio
                sezione TEXT DEFAULT 'da_verificare',  -- compatibile | da_verificare | archivio
                stato TEXT DEFAULT 'da_vedere', -- da_vedere | interessante | candidato | scartato
                note TEXT,                     -- note personali dell'utente
                attivo INTEGER DEFAULT 1       -- 1 = bando ancora rilevato, 0 = sparito dalle fonti
            );

            -- ----------------------------------------------------------------
            -- Registro di ogni scansione per ogni fonte
            -- ----------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS scan_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fonte TEXT NOT NULL,           -- nome identificativo della fonte
                url TEXT,                      -- URL scansionato
                timestamp TEXT NOT NULL,       -- data/ora della scansione (ISO 8601)
                esito TEXT NOT NULL,           -- ok | errore | bloccato | vuoto
                n_bandi INTEGER DEFAULT 0,     -- bandi trovati in questa scansione
                n_nuovi INTEGER DEFAULT 0,     -- bandi nuovi (non presenti nel DB)
                dettaglio_errore TEXT          -- messaggio di errore se esito != ok
            );

            -- ----------------------------------------------------------------
            -- Fonti con errore: pannello dedicato nell'interfaccia
            -- ----------------------------------------------------------------
            CREATE TABLE IF NOT EXISTS fonti_errore (
                fonte TEXT PRIMARY KEY,        -- nome della fonte
                url TEXT,
                ultimo_errore TEXT,            -- data/ora dell'ultimo errore
                motivo TEXT,                   -- descrizione del problema
                tipo_errore TEXT               -- http_error | timeout | robots | struttura_cambiata
            );

            -- ----------------------------------------------------------------
            -- Indici per le query più frequenti
            -- ----------------------------------------------------------------
            CREATE INDEX IF NOT EXISTS idx_bandi_scadenza ON bandi(scadenza);
            CREATE INDEX IF NOT EXISTS idx_bandi_sezione ON bandi(sezione);
            CREATE INDEX IF NOT EXISTS idx_bandi_punteggio ON bandi(punteggio DESC);
            CREATE INDEX IF NOT EXISTS idx_scan_log_timestamp ON scan_log(timestamp DESC);
        """)
    conn.close()


def genera_id_bando(ente: str, titolo: str, scadenza: str) -> str:
    """
    Genera un ID deterministico per un bando basato su ente, titolo e scadenza.
    Serve per identificare duplicati dello stesso bando su fonti diverse.
    """
    stringa = f"{ente.lower().strip()}|{titolo.lower().strip()}|{scadenza}"
    return hashlib.sha256(stringa.encode("utf-8")).hexdigest()[:16]


def _trova_duplicato_fuzzy(conn: sqlite3.Connection, bando: dict):
    """Cerca tra i bandi attivi uno molto simile (stesso ente, titolo quasi uguale)."""
    from core.dedup import is_duplicato_fuzzy

    candidati = conn.execute(
        "SELECT id, ente, titolo, scadenza, fonti, link_ufficiale, attivo FROM bandi WHERE attivo = 1"
    ).fetchall()
    for c in candidati:
        # Due scadenze diverse e note → bandi diversi (es. stesso profilo, anni diversi)
        if c["scadenza"] and bando.get("scadenza") and c["scadenza"] != bando["scadenza"]:
            continue
        if is_duplicato_fuzzy(dict(c), bando):
            return c
    return None


def upsert_bando(bando: dict) -> bool:
    """
    Inserisce un nuovo bando o aggiorna quello esistente (deduplicazione).
    Restituisce True se il bando è NUOVO, False se era già presente.
    """
    from core.dedup import fonti_ordinate, FONTI_UFFICIALI

    ora = datetime.now().isoformat(timespec="seconds")

    # Genera ID deterministico se non fornito
    if not bando.get("id"):
        bando["id"] = genera_id_bando(
            bando.get("ente", ""),
            bando.get("titolo", ""),
            bando.get("scadenza") or ""
        )

    conn = get_connection()
    try:
        with conn:
            esistente = conn.execute(
                "SELECT id, fonti, scadenza, link_ufficiale, attivo FROM bandi WHERE id = ?",
                (bando["id"],)
            ).fetchone() or _trova_duplicato_fuzzy(conn, bando)

            if esistente and not esistente["attivo"]:
                # Già scartato (scaduto): resta in archivio anche se la fonte lo mostra ancora
                return False

            if esistente:
                # Stesso bando già noto: unisce le fonti e completa i campi mancanti
                fonti_esistenti = json.loads(esistente["fonti"] or "[]")
                fonti_aggiornate = fonti_ordinate(
                    list(dict.fromkeys(fonti_esistenti + bando.get("fonti", [])))
                )
                solo_secondaria = 0 if any(f in FONTI_UFFICIALI for f in fonti_aggiornate) else 1

                conn.execute("""
                    UPDATE bandi SET
                        fonti = ?,
                        solo_fonte_secondaria = ?,
                        scadenza = COALESCE(scadenza, ?),
                        link_ufficiale = COALESCE(NULLIF(link_ufficiale, ''), ?),
                        ultima_verifica = ?,
                        attivo = 1
                    WHERE id = ?
                """, (json.dumps(fonti_aggiornate), solo_secondaria,
                      bando.get("scadenza"), bando.get("link_ufficiale"),
                      ora, esistente["id"]))
                bando["id"] = esistente["id"]
                return False  # Non è nuovo

            # Inserimento nuovo bando
            bando.setdefault("prima_rilevazione", ora)
            bando.setdefault("ultima_verifica", ora)
            fonti_json = json.dumps(bando.get("fonti", []))

            conn.execute("""
                INSERT INTO bandi (
                    id, ente, titolo, profilo, livello, n_posti,
                    titolo_studio, sede, scadenza, link_ufficiale,
                    fonti, solo_fonte_secondaria, prima_rilevazione,
                    ultima_verifica, punteggio, motivo_punteggio,
                    sezione, stato, note, attivo
                ) VALUES (
                    :id, :ente, :titolo, :profilo, :livello, :n_posti,
                    :titolo_studio, :sede, :scadenza, :link_ufficiale,
                    :fonti, :solo_fonte_secondaria, :prima_rilevazione,
                    :ultima_verifica, :punteggio, :motivo_punteggio,
                    :sezione, :stato, :note, :attivo
                )
            """, {
                "id": bando["id"],
                "ente": bando.get("ente", ""),
                "titolo": bando.get("titolo", ""),
                "profilo": bando.get("profilo"),
                "livello": bando.get("livello"),
                "n_posti": bando.get("n_posti"),
                "titolo_studio": bando.get("titolo_studio"),
                "sede": bando.get("sede"),
                "scadenza": bando.get("scadenza"),
                "link_ufficiale": bando.get("link_ufficiale"),
                "fonti": fonti_json,
                "solo_fonte_secondaria": bando.get("solo_fonte_secondaria", 0),
                "prima_rilevazione": bando["prima_rilevazione"],
                "ultima_verifica": bando["ultima_verifica"],
                "punteggio": bando.get("punteggio", 0),
                "motivo_punteggio": bando.get("motivo_punteggio"),
                "sezione": bando.get("sezione", "da_verificare"),
                "stato": bando.get("stato", "da_vedere"),
                "note": bando.get("note"),
                "attivo": 1,
            })
            return True  # È nuovo
    finally:
        conn.close()


def get_bando(bando_id: str) -> dict | None:
    """Restituisce un singolo bando (o None)."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM bandi WHERE id = ?", (bando_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def scarta_scaduti(giorni_senza_scadenza: int = 120) -> int:
    """
    Toglie dalla vista i bandi scaduti (attivo=0, sezione archivio):
    - con scadenza già passata;
    - senza scadenza, rilevati per la prima volta più di N giorni fa.
    Non li cancella, così stato e note personali restano nel database.
    Restituisce quanti bandi sono stati scartati.
    """
    oggi_iso = oggi().isoformat()
    conn = get_connection()
    with conn:
        cur = conn.execute("""
            UPDATE bandi SET attivo = 0, sezione = 'archivio',
                   motivo_punteggio = 'Scaduto'
            WHERE attivo = 1 AND (
                  (scadenza IS NOT NULL AND scadenza != '' AND date(scadenza) < date(?))
               OR ((scadenza IS NULL OR scadenza = '')
                   AND date(prima_rilevazione) < date(?, ? || ' days'))
            )
        """, (oggi_iso, oggi_iso, f"-{giorni_senza_scadenza}"))
        n = cur.rowcount
    conn.close()
    return n


def aggiorna_stato_bando(bando_id: str, stato: str, note: str = None):
    """Aggiorna lo stato e le note di un bando (impostate dall'utente)."""
    conn = get_connection()
    with conn:
        if note is not None:
            conn.execute(
                "UPDATE bandi SET stato=?, note=? WHERE id=?",
                (stato, note, bando_id)
            )
        else:
            conn.execute(
                "UPDATE bandi SET stato=? WHERE id=?",
                (stato, bando_id)
            )
    conn.close()


def aggiorna_punteggio(bando_id: str, punteggio: int, motivo: str, sezione: str):
    """Aggiorna il punteggio di rilevanza calcolato dallo scorer."""
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE bandi SET punteggio=?, motivo_punteggio=?, sezione=? WHERE id=?",
            (punteggio, motivo, sezione, bando_id)
        )
    conn.close()


def get_bandi(sezione: str = None, stato: str = None, solo_attivi: bool = True) -> list:
    """
    Recupera i bandi dal database con filtri opzionali.
    Restituisce una lista di dizionari.
    """
    conn = get_connection()
    query = "SELECT * FROM bandi WHERE 1=1"
    params = []
    
    if solo_attivi:
        query += " AND attivo = 1"
    if sezione:
        query += " AND sezione = ?"
        params.append(sezione)
    if stato:
        query += " AND stato = ?"
        params.append(stato)
    
    query += " ORDER BY punteggio DESC, scadenza ASC"
    
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def log_scansione(fonte: str, url: str, esito: str, n_bandi: int = 0,
                  n_nuovi: int = 0, errore: str = None):
    """Registra il risultato di una scansione nel log."""
    conn = get_connection()
    with conn:
        conn.execute("""
            INSERT INTO scan_log (fonte, url, timestamp, esito, n_bandi, n_nuovi, dettaglio_errore)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (fonte, url, datetime.now().isoformat(), esito, n_bandi, n_nuovi, errore))
        
        if esito == "vuoto":
            # Pagina raggiunta ma senza bandi aperti: non è un errore
            conn.execute("DELETE FROM fonti_errore WHERE fonte = ?", (fonte,))
        elif esito != "ok":
            conn.execute("""
                INSERT OR REPLACE INTO fonti_errore (fonte, url, ultimo_errore, motivo, tipo_errore)
                VALUES (?, ?, ?, ?, ?)
            """, (fonte, url, datetime.now().isoformat(), errore, esito))
        else:
            # Rimuove dalla lista errori se ora funziona
            conn.execute("DELETE FROM fonti_errore WHERE fonte = ?", (fonte,))
    conn.close()


def get_fonti_in_errore() -> list:
    """Restituisce le fonti che hanno avuto errori nell'ultima scansione."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM fonti_errore ORDER BY ultimo_errore DESC").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_bandi_in_scadenza(giorni: int = 7) -> list:
    """Restituisce i bandi in scadenza entro N giorni (esclusi quelli in archivio)."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM bandi
        WHERE attivo = 1
          AND sezione != 'archivio'
          AND stato != 'scartato'
          AND scadenza IS NOT NULL
          AND scadenza != ''
          AND date(scadenza) BETWEEN date(?) AND date(?, ? || ' days')
        ORDER BY scadenza ASC
    """, (oggi().isoformat(), oggi().isoformat(), f"+{giorni}")).fetchall()
    conn.close()
    return [dict(row) for row in rows]
