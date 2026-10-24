"""
collectors/generico.py — Collettori configurabili da fonti.yaml

Due tipi, per aggiungere siti di enti senza scrivere codice:

  tipo: html  → scarica una pagina e prende i link il cui testo sembra un bando
                (concorso, selezione, avviso, posti...).
                - selettore: CSS per limitare la ricerca a una parte della pagina
                - blocco:    CSS di un riquadro che contiene UN bando (titolo,
                             date, ente). Ogni riquadro diventa un bando e la
                             scadenza si legge dal testo del riquadro.
  tipo: rss   → legge un feed RSS/Atom.

Bandi scaduti: se nel testo c'è una scadenza già passata la voce viene scartata.
Se la scadenza non c'è, la voce viene scartata quando cita solo anni passati
(es. un PDF "..._20240730.pdf" o "Concorso 2023"), perché quasi certamente
è un bando vecchio rimasto in pagina.
"""

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from collectors.base import CollettoreBase
from core.dates import a_iso, anno_piu_recente, estrai_scadenza, is_scaduto, oggi

FILTRO_DEFAULT = r"concors|selezion|avviso|bando|mobilit|reclutament|assunzion|\bpost[oi]\b"
ESCLUSI_DEFAULT = (
    r"graduatori|esito|esiti|diario|calendario|commission|tracce|ammess|"
    r"verbale|rettifica|revoca|archivio|scadut|gara|appalt|affidament|"
    r"contribut|finanziament|manifestazione di interesse per (?:la fornitura|il servizio)|"
    r"continua a leggere|leggi tutto|visita la pagina|regolamento|tabella riepilogativa|"
    r"espletat|presa d.atto|erasmus|borsa di studio|tirocini|in corso di svolgimento|"
    r"il portale dei concorsi|area geografica|^selezioni per altre pubbliche amministrazioni|"
    r"^categoria\b"
)
LUNGHEZZA_MIN_TITOLO = 30   # i testi più corti sono voci di menu ("Bandi di concorso")


def _vecchio(*testi: str) -> bool:
    """True se i testi citano solo anni passati (bando quasi certamente chiuso)."""
    anno = anno_piu_recente(*testi)
    if anno is None:
        return False
    oggi_ = oggi()
    # A gennaio/febbraio tengo anche i bandi dell'anno prima (scadenze a cavallo d'anno)
    limite = oggi_.year - 1 if oggi_.month <= 2 else oggi_.year
    return anno < limite


def _spazi(testo: str) -> str:
    return " ".join((testo or "").split())


class CollettoreGenerico(CollettoreBase):
    """Collettore costruito da una voce di fonti.yaml."""

    def __init__(self, nome: str, url: str, tipo: str = "html", ente: str = "",
                 sede: str = None, selettore: str = None, blocco: str = None,
                 ente_dal_contesto: bool = False,
                 filtro: str = None, escludi: str = None, escludi_link: str = None, **_):
        self.NOME_FONTE = nome
        self.URL_BASE = url
        super().__init__()
        self.url = url
        self.tipo = tipo
        self.ente = ente or nome
        self.sede = sede
        self.selettore = selettore
        self.blocco = blocco
        self.ente_dal_contesto = ente_dal_contesto
        self.re_filtro = re.compile(filtro or FILTRO_DEFAULT, re.IGNORECASE)
        self.re_escludi = re.compile(escludi or ESCLUSI_DEFAULT, re.IGNORECASE)
        self.re_escludi_link = re.compile(escludi_link, re.IGNORECASE) if escludi_link else None

    # ------------------------------------------------------------------
    def esegui(self) -> list[dict]:
        if not self.rispetta_robots(self.url):
            self._registra_errore(self.url, "robots.txt non permette la lettura", "robots")
            return []
        if self.tipo == "rss":
            voci = self._voci_rss()
        elif self.blocco:
            voci = self._voci_blocchi()
        else:
            voci = self._voci_html()
        if voci is None:
            return []

        bandi, visti = [], set()
        for v in voci:
            titolo, link, extra = _spazi(v["titolo"]), v["link"], _spazi(v.get("extra", ""))
            # Link con testo corto ("Avviso del 22 ottobre 2026"): uso la riga intera,
            # ma solo se sembra un bando (non "Home", non un menu senza numeri)
            if (len(titolo) < LUNGHEZZA_MIN_TITOLO and self._candidato_testo(titolo)
                    and extra.startswith(titolo) and len(extra) <= 300 and re.search(r"\d", extra)):
                titolo = extra
            if len(titolo) < LUNGHEZZA_MIN_TITOLO or (link, titolo) in visti:
                continue
            if not self.re_filtro.search(titolo) or self.re_escludi.search(titolo):
                continue
            if self.re_escludi_link and self.re_escludi_link.search(link):
                continue
            # Link alla pagina stessa o alla home: voci di menu, non bandi
            if link.rstrip("/") == self.url.rstrip("/") or urlparse(link).path in ("", "/"):
                continue
            visti.add((link, titolo))

            scadenza = estrai_scadenza(f"{titolo} {extra}")
            if scadenza and is_scaduto(scadenza):
                continue
            if scadenza is None and _vecchio(titolo, link, extra):
                continue

            bandi.append(self._crea_bando(
                titolo=titolo[:300],
                ente=v.get("ente") or self.ente,
                sede=self.sede,
                scadenza=a_iso(scadenza),
                link_ufficiale=link,
            ))

        self.logger.info(f"[{self.NOME_FONTE}] {len(bandi)} bandi trovati")
        return bandi

    # ------------------------------------------------------------------
    def _pagina(self):
        r = self.get(self.url)
        if r is None:
            return None
        zuppa = BeautifulSoup(r.content, "lxml")
        if not self.selettore:
            return zuppa
        aree = zuppa.select(self.selettore)
        if not aree:
            self._registra_errore(self.url, f"selettore '{self.selettore}' non trovato",
                                  "struttura_cambiata")
            return None
        # Più aree → le unisco in un unico contenitore
        contenitore = BeautifulSoup("<div></div>", "lxml").div
        for a in aree:
            contenitore.append(a)
        return contenitore

    def _voci_html(self) -> list[dict] | None:
        area = self._pagina()
        if area is None:
            return None
        voci = []
        for a in area.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("#", "mailto:", "javascript:")):
                continue
            titolo = a.get_text(" ", strip=True) or a.get("title", "")
            voci.append({"titolo": titolo, "link": urljoin(self.url, href),
                         "extra": self._contesto(a)})
        return voci

    def _candidato_testo(self, t: str) -> bool:
        """Testo di link che potrebbe essere un bando (anche corto, es. 'Avviso del 6 ottobre')."""
        return len(t) >= 12 and bool(self.re_filtro.search(t))

    def _candidato(self, a) -> bool:
        return self._candidato_testo(a.get_text(" ", strip=True))

    def _contesto(self, a) -> str:
        """
        Testo del riquadro che contiene SOLO questo bando (titolo, date, ente).
        Risale dal link finché il contenitore non include un altro bando: così
        la scadenza letta è quella giusta e non quella di un bando vicino.
        """
        mio = a["href"]
        nodo = a
        while nodo.parent is not None and nodo.parent.name not in ("body", "html", "[document]"):
            padre = nodo.parent
            if len(padre.get_text(" ", strip=True)) > 1500:
                break
            altri = {x["href"] for x in padre.find_all("a", href=True)
                     if x["href"] != mio and self._candidato(x)}
            if altri:
                break
            nodo = padre
        return nodo.get_text(" ", strip=True) if nodo is not a else ""

    def _voci_blocchi(self) -> list[dict] | None:
        """Un bando per ogni riquadro 'blocco' (es. div.mb-5)."""
        area = self._pagina()
        if area is None:
            return None
        riquadri = area.select(self.blocco)
        if not riquadri:
            self._registra_errore(self.url, f"blocco '{self.blocco}' non trovato", "struttura_cambiata")
            return None

        voci = []
        for b in riquadri:
            link_tag = b.find("a", href=True)
            if not link_tag:
                continue
            href = link_tag["href"]
            # Il titolo può essere spezzato su più link con lo stesso indirizzo
            titolo = " ".join(a.get_text(" ", strip=True)
                              for a in b.find_all("a", href=True) if a["href"] == href)
            testo = b.get_text(" ", strip=True)
            # Ente: il testo del contenitore che precede il riquadro (es. "Comune di Bulzi")
            ente = ""
            genitore = b.parent
            if self.ente_dal_contesto and genitore is not None:
                prima = _spazi(genitore.get_text(" ", strip=True).split(testo[:40])[0])
                prima = re.sub(r"^(?:concorso|avviso|selezione)\s+", "", prima, flags=re.I)
                if 3 <= len(prima) <= 120:
                    ente = prima
            voci.append({"titolo": titolo, "link": urljoin(self.url, href),
                         "extra": testo[:1500], "ente": ente})
        return voci

    def _voci_rss(self) -> list[dict] | None:
        import feedparser

        r = self.get(self.url)
        if r is None:
            return None
        feed = feedparser.parse(r.content)
        if feed.bozo and not feed.entries:
            self._registra_errore(self.url, "feed non leggibile", "struttura_cambiata")
            return None
        voci = []
        for e in feed.entries:
            sommario = BeautifulSoup(e.get("summary", ""), "lxml").get_text(" ", strip=True)
            voci.append({"titolo": e.get("title", ""), "link": e.get("link", ""),
                         "extra": sommario[:500]})
        return voci
