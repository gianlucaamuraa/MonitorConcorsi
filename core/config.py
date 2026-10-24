"""
core/config.py — Lettura di config.yaml e fonti.yaml

Tutti i percorsi sono relativi alla cartella del progetto, così il programma
funziona da qualsiasi cartella venga lanciato.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
FONTI_PATH = ROOT / "fonti.yaml"

_cache: dict | None = None


def carica_config() -> dict:
    """Restituisce il contenuto di config.yaml (letto una sola volta)."""
    global _cache
    if _cache is None:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            _cache = yaml.safe_load(f) or {}
    return _cache


def carica_fonti() -> list[dict]:
    """Restituisce l'elenco delle fonti definite in fonti.yaml."""
    if not FONTI_PATH.exists():
        return []
    with open(FONTI_PATH, encoding="utf-8") as f:
        dati = yaml.safe_load(f) or {}
    return dati.get("fonti", [])


def percorso(chiave: str, default: str) -> Path:
    """Percorso assoluto di un file dati (sezione 'dati' di config.yaml)."""
    valore = carica_config().get("dati", {}).get(chiave, default)
    p = Path(valore)
    return p if p.is_absolute() else ROOT / p
