"""
core/nuove_fonti.py — Aggiunta di una fonte proposta dall'utente

Il link viene controllato prima di finire in fonti.yaml:
1. indirizzo valido (http/https) e non già presente tra le fonti;
2. robots.txt del sito che ne permette la lettura;
3. pagina raggiungibile e in formato HTML;
4. pagina che parla di concorsi, bandi, selezioni o avvisi.

Se tutto va bene la fonte viene aggiunta in fondo all'elenco con tipo "html".
Il numero di bandi aperti trovati è solo informativo: una pagina giusta può
non avere bandi aperti in questo momento.
"""

import json
import re
from datetime import date
from urllib.parse import urlparse

import yaml
from bs4 import BeautifulSoup

from collectors.generico import CollettoreGenerico
from core.config import FONTI_PATH, carica_fonti

PAROLE_CONCORSI = re.compile(
    r"concors|bando|bandi|selezion|avvis|reclutament|assunzion|lavora con noi",
    re.IGNORECASE)

# Le fonti aggiunte vanno prima del riepilogo finale (commenti) di fonti.yaml
SEGNO_FINE_ELENCO = "# ============================================================\n# Fonti tolte"


def _normalizza(url: str) -> str:
    p = urlparse(url.strip())
    return f"{p.netloc.lower().removeprefix('www.')}{p.path.rstrip('/')}?{p.query}"


def _nome_libero(base: str, esistenti: set[str]) -> str:
    nome = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")[:40] or "fonte"
    if not nome[0].isalpha():
        nome = f"fonte_{nome}"
    candidato, n = nome, 2
    while candidato in esistenti:
        candidato, n = f"{nome}_{n}", n + 1
    return candidato


def verifica_link(url: str, ente: str = "", sede: str = "") -> dict:
    """
    Controlla un link senza modificare nulla.
    Restituisce {"ok": bool, "motivo": str, "fonte": dict, "n_bandi": int, "esempi": [...]}
    """
    url = (url or "").strip()
    esito = {"ok": False, "motivo": "", "fonte": None, "n_bandi": 0, "esempi": []}

    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc or len(url) > 1000:
        esito["motivo"] = "Indirizzo non valido: deve iniziare con http:// o https://"
        return esito

    fonti = carica_fonti()
    for f in fonti:
        if f.get("url") and _normalizza(f["url"]) == _normalizza(url):
            esito["motivo"] = f"Link già presente tra le fonti (fonte '{f['nome']}')"
            return esito

    dominio = p.netloc.lower().removeprefix("www.")
    nome = _nome_libero(ente or dominio, {f.get("nome") for f in fonti})
    coll = CollettoreGenerico(nome=nome, url=url, ente=ente or dominio, sede=sede or None)

    if not coll.rispetta_robots(url):
        esito["motivo"] = "Il robots.txt del sito non permette la lettura automatica di questa pagina"
        return esito

    r = coll.get(url)
    if r is None:
        causa = coll.ultimo_errore[1] if coll.ultimo_errore else "nessuna risposta"
        esito["motivo"] = f"Pagina non raggiungibile: {causa}"
        return esito
    if "html" not in r.headers.get("content-type", "html").lower():
        esito["motivo"] = (f"Non è una pagina web ({r.headers.get('content-type')}): "
                           "serve la pagina con l'elenco dei bandi, non un file")
        return esito

    zuppa = BeautifulSoup(r.content, "lxml")
    titolo_pagina = zuppa.title.get_text(" ", strip=True) if zuppa.title else ""
    for tag in zuppa(["script", "style"]):
        tag.decompose()
    if not PAROLE_CONCORSI.search(zuppa.get_text(" ")):
        esito["motivo"] = "La pagina non parla di concorsi, bandi, selezioni o avvisi"
        return esito

    if not ente and titolo_pagina:
        ente = titolo_pagina[:80]
        coll.ente = ente

    bandi = coll.esegui()
    fonte = {"nome": nome, "tipo": "html", "url": url, "ente": ente or dominio,
             "sede": sede or "Sardegna", "aggiunta": date.today().isoformat()}
    esito.update(
        ok=True,
        fonte=fonte,
        n_bandi=len(bandi),
        esempi=[b["titolo"][:120] for b in bandi[:5]],
        motivo=(f"Pagina raggiungibile: {len(bandi)} bandi aperti trovati" if bandi else
                "Pagina raggiungibile, ma ora non si trovano bandi aperti con scadenza "
                "leggibile: verrà controllata a ogni scansione"),
    )
    return esito


def _blocco_yaml(fonte: dict) -> str:
    # json.dumps produce stringhe tra virgolette valide anche in YAML
    righe = [f"  # Aggiunta dall'utente il {fonte['aggiunta']}",
             f"  - nome: {json.dumps(fonte['nome'])}",
             "    tipo: html",
             f"    url: {json.dumps(fonte['url'], ensure_ascii=False)}",
             f"    ente: {json.dumps(fonte['ente'], ensure_ascii=False)}",
             f"    sede: {json.dumps(fonte['sede'], ensure_ascii=False)}"]
    return "\n".join(righe) + "\n\n"


def aggiungi_fonte(url: str, ente: str = "", sede: str = "") -> dict:
    """Verifica il link e, se funziona, lo aggiunge a fonti.yaml."""
    esito = verifica_link(url, ente, sede)
    if not esito["ok"]:
        return esito

    testo = FONTI_PATH.read_text(encoding="utf-8")
    blocco = _blocco_yaml(esito["fonte"])
    pos = testo.find(SEGNO_FINE_ELENCO)
    nuovo = (testo[:pos] + blocco + testo[pos:]) if pos >= 0 else testo.rstrip("\n") + "\n\n" + blocco

    # Controllo di sicurezza: il file deve restare leggibile e contenere la fonte
    dati = yaml.safe_load(nuovo) or {}
    if not any(f.get("nome") == esito["fonte"]["nome"] for f in dati.get("fonti", [])):
        esito.update(ok=False, motivo="Errore interno: fonti.yaml non è stato modificato")
        return esito

    FONTI_PATH.write_text(nuovo, encoding="utf-8")
    esito["motivo"] += f". Aggiunta come fonte '{esito['fonte']['nome']}'"
    return esito
