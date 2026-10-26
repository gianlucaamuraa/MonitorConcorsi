"""
collectors/inpa.py — Portale del Reclutamento InPA (inpa.gov.it)

Dal 2023 le pubbliche amministrazioni pubblicano qui i propri concorsi, quindi
è la fonte principale. Usa l'API pubblica del portale (la stessa del sito):

  POST /concorsi-smart/api/concorso-public-area/search-better?page=N&size=100
       corpo: {"text": "", "status": ["OPEN"]}
  GET  /concorsi-smart/api/concorso-public-area/{id}   → dettaglio (requisiti)

Tiene solo i bandi aperti con sede in Sardegna o validi su tutto il territorio
nazionale (molte regioni oppure nessuna sede indicata). I bandi scaduti
vengono scartati.
"""

import html
import re

from collectors.base import CollettoreBase
from core.dates import parse_data, a_iso, is_scaduto
from core.db import get_bando, genera_id_bando

API = "https://portale.inpa.gov.it/concorsi-smart/api/concorso-public-area"
LINK_PUBBLICO = "https://www.inpa.gov.it/bandi-e-avvisi/dettaglio-bando-avviso/?concorso_id={id}"

DIMENSIONE_PAGINA = 100
MAX_PAGINE = 40                 # limite di sicurezza (~4000 bandi)
REGIONI_PER_NAZIONALE = 5       # sedi in almeno N regioni → bando "nazionale"

REGIONI = {
    "abruzzo", "basilicata", "calabria", "campania", "emilia romagna", "emilia-romagna",
    "friuli venezia giulia", "friuli-venezia giulia", "lazio", "liguria", "lombardia",
    "marche", "molise", "piemonte", "puglia", "sardegna", "sicilia", "toscana",
    "trentino alto adige", "trentino-alto adige", "umbria", "valle d'aosta", "veneto",
}


def _testo_pulito(valore: str | None) -> str:
    """Toglie i tag HTML e gli spazi superflui."""
    if not valore:
        return ""
    testo = re.sub(r"<[^>]+>", " ", valore)
    return " ".join(html.unescape(testo).split())


class CollettoreInPA(CollettoreBase):
    NOME_FONTE = "inpa"
    URL_BASE = "https://www.inpa.gov.it"
    PAUSA_MIN = 1
    PAUSA_MAX = 3

    def __init__(self, includi_nazionali: bool = True, dettagli: bool = True, **_):
        super().__init__()
        self.includi_nazionali = includi_nazionali
        self.dettagli = dettagli
        # L'API risponde in JSON
        self.session.headers["Accept"] = "application/json, text/plain, */*"

    # ------------------------------------------------------------------
    def _in_zona(self, sedi: list[str]) -> tuple[bool, str]:
        """Decide se un bando interessa e restituisce la sede da mostrare."""
        sedi_l = [s.lower().strip() for s in sedi or []]
        if "sardegna" in sedi_l:
            # Mostra le province sarde se indicate (es. "Sardegna, Sassari")
            return True, ", ".join(sedi)
        if not self.includi_nazionali:
            return False, ""
        n_regioni = sum(1 for s in sedi_l if s in REGIONI)
        if not sedi_l or n_regioni >= REGIONI_PER_NAZIONALE or "italia" in sedi_l:
            return True, "Nazionale" + (f" ({n_regioni} regioni)" if n_regioni else "")
        return False, ""

    def _scarica_elenco(self) -> list[dict]:
        risultati = []
        for pagina in range(MAX_PAGINE):
            r = self.post(
                f"{API}/search-better?page={pagina}&size={DIMENSIONE_PAGINA}",
                json={"text": "", "status": ["OPEN"]},
            )
            if r is None:
                break
            dati = r.json()
            risultati.extend(dati.get("content", []))
            if dati.get("last", True) or pagina + 1 >= dati.get("totalPages", 0):
                break
            self._pausa()
        self.logger.info(f"[inpa] {len(risultati)} bandi aperti in Italia")
        return risultati

    def _dettaglio(self, bando_id: str) -> dict:
        r = self.get(f"{API}/{bando_id}")
        return r.json() if r is not None else {}

    # ------------------------------------------------------------------
    def esegui(self) -> list[dict]:
        bandi = []
        for c in self._scarica_elenco():
            interessa, sede = self._in_zona(c.get("sedi"))
            if not interessa:
                continue

            scadenza = a_iso(parse_data(c.get("dataScadenza")))
            if is_scaduto(scadenza):
                continue

            enti = c.get("entiRiferimento") or []
            ente = ", ".join(enti) or "Ente non indicato"
            titolo = _testo_pulito(c.get("titolo"))

            titolo_studio = None
            link = LINK_PUBBLICO.format(id=c["id"])
            # Il dettaglio serve solo per i bandi mai visti: se è già nel DB lo salto
            gia_noto = get_bando(genera_id_bando(ente, titolo, scadenza or "")) is not None
            if self.dettagli and not gia_noto:
                d = self._dettaglio(c["id"])
                titolo_studio = _testo_pulito(d.get("requisitiSpecifici")) or None
                # Se i requisiti non ci sono, cerco "diploma/laurea" nella descrizione
                if not titolo_studio:
                    desc = _testo_pulito(d.get("descrizione"))
                    m = re.search(r"[^.]*\b(diploma|laurea|scuola dell.obbligo)\b[^.]*\.", desc, re.I)
                    titolo_studio = m.group(0).strip() if m else None
                self._pausa()

            categorie = ", ".join(c.get("categorie") or [])
            bandi.append(self._crea_bando(
                titolo=titolo,
                ente=ente,
                sede=sede,
                scadenza=scadenza,
                n_posti=c.get("numPosti"),
                titolo_studio=titolo_studio[:500] if titolo_studio else None,
                link_ufficiale=link,
                profilo=c.get("figuraRicercata"),
                livello=categorie or None,
                data_pubblicazione=a_iso(parse_data(c.get("dataPubblicazione"))),
            ))
        self.logger.info(f"[inpa] {len(bandi)} bandi in Sardegna/nazionali")
        return bandi
