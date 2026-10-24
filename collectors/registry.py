"""
collectors/registry.py — Crea i collettori a partire da fonti.yaml
"""

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
    parametri = {k: v for k, v in fonte.items() if k not in ("attivo", "ufficiale", "note")}

    classe = TIPI[tipo]
    if classe is CollettoreGenerico:
        return CollettoreGenerico(**parametri)
    collettore = classe(**{k: v for k, v in parametri.items() if k not in ("nome", "tipo")})
    collettore.NOME_FONTE = fonte["nome"]
    return collettore
