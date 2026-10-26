"""
collectors/registry.py — Crea i collettori a partire da fonti.yaml
"""

import os

from collectors.base import CollettoreBase
from collectors.generico import CollettoreGenerico
from collectors.gazzetta import CollettoreGazzetta
from collectors.inpa import CollettoreInPA
from core.config import carica_fonti
from core.dedup import registra_fonte

# tipo in fonti.yaml → classe del collettore
TIPI = {
    "inpa": CollettoreInPA,
    "gazzetta": CollettoreGazzetta,
    "html": CollettoreGenerico,
    "rss": CollettoreGenerico,
}


def fonti_attive(solo: list[str] | None = None) -> list[dict]:
    """Fonti con attivo: true (o quelle indicate in 'solo', anche se disattivate)."""
    fonti = carica_fonti()
    if solo:
        return [f for f in fonti if f.get("nome") in solo]
    return [f for f in fonti if f.get("attivo", True)]


def crea_collettore(fonte: dict) -> CollettoreBase:
    """Istanzia il collettore giusto per una voce di fonti.yaml."""
    tipo = fonte.get("tipo", "html")
    if tipo not in TIPI:
        raise ValueError(f"Tipo di fonte sconosciuto '{tipo}' per '{fonte.get('nome')}'")

    registra_fonte(fonte["nome"], fonte.get("ufficiale", True))
    parametri = {k: v for k, v in fonte.items()
                 if k not in ("attivo", "ufficiale", "note", "via_italia")}

    classe = TIPI[tipo]
    if classe is CollettoreGenerico:
        collettore = CollettoreGenerico(**parametri)
    else:
        collettore = classe(**{k: v for k, v in parametri.items() if k not in ("nome", "tipo")})
        collettore.NOME_FONTE = fonte["nome"]

    # Siti che rifiutano i collegamenti dall'estero: su GitHub passano dal
    # proxy Tailscale (uscita dal cellulare in Italia). Sul PC PROXY_ITALIA
    # non c'è e il collegamento resta diretto.
    proxy = os.environ.get("PROXY_ITALIA")
    if fonte.get("via_italia") and proxy:
        collettore.session.proxies = {"http": proxy, "https": proxy}
        collettore.logger.info(f"[{fonte['nome']}] collegamento tramite proxy Italia")
    return collettore
