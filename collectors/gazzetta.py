"""
collectors/gazzetta.py — Gazzetta Ufficiale, 4ª Serie Speciale "Concorsi ed esami"

Legge il feed RSS dell'ultimo numero: https://www.gazzettaufficiale.it/rss/S4
Ogni voce ha un titolo tipo:
  "COMUNE DI SASSARI - CONCORSO (scad. 12 novembre 2026)"
e nel testo il dettaglio:
  "Concorso pubblico, per esami, per la copertura di due posti di istruttore..."

Tiene solo concorsi con scadenza ancora valida e che riguardano la Sardegna
oppure enti nazionali (ministeri, agenzie, istituti nazionali...).
La Gazzetta esce due volte a settimana: con una scansione al giorno non si
perde nessun numero.
"""

import html
import re
import xml.etree.ElementTree as ET

from collectors.base import CollettoreBase
from core.dates import estrai_scadenza, a_iso, is_scaduto
from core.scorer import _RE_SARDEGNA, _RE_SASSARI

URL_RSS = "https://www.gazzettaufficiale.it/rss/S4"
NS_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"

# Tipologie di atto da ignorare (non sono bandi a cui candidarsi)
ESCLUSI = re.compile(
    r"graduatori|esito|diario|rinvio|annullament|revoca|cancellazion|nomina|"
    r"riapertura dei termini.*esito|borsa di (?:studio|ricerca)|assegn[oi] di ricerca|"
    r"dottorato|ricercator|professore|tecnologo|dirigente medico",
    re.IGNORECASE,
)

# Enti nazionali (sedi in tutta Italia, spesso anche in Sardegna)
ENTI_NAZIONALI = re.compile(
    r"^(?:MINISTERO|PRESIDENZA DEL CONSIGLIO|AGENZIA|ISTITUTO NAZIONALE|INPS|INAIL|"
    r"AUTORIT[AÀ]|CORTE|CONSIGLIO DI STATO|AVVOCATURA|ARMA DEI CARABINIERI|"
    r"CORPO NAZIONALE|GUARDIA DI FINANZA|POLIZIA DI STATO|BANCA D.ITALIA|"
    r"ISTAT|ISTITUTO SUPERIORE|AUTOMOBILE CLUB D.ITALIA|ENTE NAZIONALE|"
    r"DIPARTIMENTO|COMMISSIONE INTERMINISTERIALE)",
    re.IGNORECASE,
)


def _data_dal_link(link: str) -> str | None:
    """Data del numero della Gazzetta, scritta nell'indirizzo: .../eli/id/2026/10/23/..."""
    m = re.search(r"/(20\d{2})/(\d{2})/(\d{2})/", link or "")
    return f"{m[1]}-{m[2]}-{m[3]}" if m else None


class CollettoreGazzetta(CollettoreBase):
    NOME_FONTE = "gazzetta_ufficiale"
    URL_BASE = "https://www.gazzettaufficiale.it"

    def __init__(self, includi_nazionali: bool = True, **_):
        super().__init__()
        self.includi_nazionali = includi_nazionali

    def esegui(self) -> list[dict]:
        r = self.get(URL_RSS)
        if r is None:
            return []

        try:
            radice = ET.fromstring(r.content)
        except ET.ParseError as e:
            self._registra_errore(URL_RSS, f"RSS non leggibile: {e}", "struttura_cambiata")
            return []

        bandi = []
        voci = radice.findall("./channel/item")
        for voce in voci:
            titolo_gu = html.unescape((voce.findtext("title") or "").strip())
            testo = html.unescape((voce.findtext(NS_CONTENT) or "").strip())
            testo = re.sub(r"\(\d{2}[A-Z]\d{5}\)\s*$", "", " ".join(testo.split())).strip()
            link = (voce.findtext("link") or "").strip().replace("http://", "https://", 1)

            # Solo concorsi/avvisi con scadenza ancora valida
            if not re.search(r"CONCORS|AVVISO|SELEZIONE", titolo_gu, re.IGNORECASE):
                continue
            if ESCLUSI.search(f"{titolo_gu} {testo}"):
                continue
            scadenza = a_iso(estrai_scadenza(titolo_gu) or estrai_scadenza(testo))
            if not scadenza or is_scaduto(scadenza):
                continue

            # "COMUNE DI SASSARI - CONCORSO (scad...)" → ente = "COMUNE DI SASSARI"
            ente = re.split(r"\s+-\s+(?:CONCORS|AVVISO|SELEZIONE|GRADUATORIA)", titolo_gu,
                            maxsplit=1, flags=re.IGNORECASE)[0].strip()
            ente_testo = f"{ente} {testo}".lower()

            if _RE_SASSARI.search(ente_testo) or _RE_SARDEGNA.search(ente_testo):
                sede = "Sardegna"
                if _RE_SASSARI.search(ente_testo):
                    sede = "Sassari e provincia"
            elif (self.includi_nazionali and ENTI_NAZIONALI.search(ente)
                  and not re.search(r"REGIONAL|PROVINCIAL|TERRITORIAL|LOCALE", ente, re.I)):
                sede = "Nazionale"
            else:
                continue

            m_posti = re.search(r"\b(\d+)\s+posti?\b", testo)
            bandi.append(self._crea_bando(
                titolo=testo[:300] or titolo_gu,
                ente=ente.title(),
                sede=sede,
                scadenza=scadenza,
                n_posti=int(m_posti.group(1)) if m_posti else None,
                link_ufficiale=link,
                data_pubblicazione=_data_dal_link(link),
            ))

        self.logger.info(f"[gazzetta] {len(voci)} voci nel numero, {len(bandi)} pertinenti")
        return bandi
