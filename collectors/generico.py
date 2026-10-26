"""
collectors/generico.py — Collettori configurabili da fonti.yaml

Due tipi, per aggiungere siti di enti senza scrivere codice:

  tipo: html  → scarica una pagina e prende i link il cui testo sembra un bando
                (concorso, selezione, avviso, posti...).
                - selettore: CSS per limitare la ricerca a una parte della pagina
                - max_giorni_pubblicazione: per le pagine di notizie senza scadenza,
                             scarta le voci pubblicate da più di N giorni
                             (data letta dal riquadro, es. "Novità 12 ott 2026")
                - dettagli:  true per gli elenchi senza date: per le voci senza
                             scadenza apre la pagina del bando (al massimo
                             max_dettagli, default 10) e la scarta se è scaduta o
                             se la data più recente citata è più vecchia di
                             max_giorni_pubblicazione (default 60)
                - verifica_ssl: false per i siti con certificato intestato male
                - sottomenu_di: parte dell'indirizzo di un link di menu; si leggono
                             solo le voci del suo sottomenu (per i menu che
                             cambiano classi e id a ogni visita)
                - blocco:    CSS di un riquadro che contiene UN bando (titolo,
                             date, ente). Ogni riquadro diventa un bando e la
                             scadenza si legge dal testo del riquadro.
  tipo: rss   → legge un feed RSS/Atom.

Nell'indirizzo si può scrivere {anno}: diventa l'anno in corso.

Bandi scaduti: se nel testo c'è una scadenza già passata la voce viene scartata.
Se la scadenza non c'è, la voce viene scartata quando cita solo anni passati
(es. un PDF "..._20240730.pdf" o "Concorso 2023"), perché quasi certamente
è un bando vecchio rimasto in pagina.
"""

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from collectors.base import CollettoreBase
from core.dates import (a_iso, anno_piu_recente, data_piu_recente, estrai_pubblicazione,
                        estrai_scadenza, is_scaduto, oggi, parse_data)

FILTRO_DEFAULT = (r"concors|selezion|avviso|bando|mobilit|reclutament|assunzion|\bpost[oi]\b|"
                  r"interpello|\bistruttor[ei]\b|\bfunzionari[oa]?\b")
ESCLUSI_DEFAULT = (
    r"graduatori|esito|esiti|diario|calendario|commission|tracce|ammess|"
    r"verbale|rettifica|revoca|archivio|scadut|gara|appalt|affidament|"
    r"contribut|finanziament|manifestazione di interesse per (?:la fornitura|il servizio)|"
    r"continua a leggere|leggi tutto|visita la pagina|regolamento|tabella riepilogativa|"
    r"espletat|presa d.atto|erasmus|borsa di studio|tirocini|in corso di svolgimento|"
    r"il portale dei concorsi|area geografica|^selezioni per altre pubbliche amministrazioni|"
    r"^categoria\b|scrutator|seggi[oi] elettoral|convocazion|ammission|"
    r"prova (?:preselettiva|scritta|orale|pratica)|rinvio (?:delle |della )?prov|"
    r"riciclon|presepi|concorso (?:fotografic|di idee|letterari|di poesia|artistic|canor|musical)"
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
                 sottomenu_di: str = None, max_giorni_pubblicazione: int = None,
                 verifica_ssl: bool = True, dettagli: bool = False, max_dettagli: int = 10,
                 ente_dal_contesto: bool = False,
                 filtro: str = None, escludi: str = None, escludi_link: str = None, **_):
        # {anno} nell'indirizzo = anno in corso (pagine divise per anno, es. .../2026)
        url = url.replace("{anno}", str(oggi().year))
        self.NOME_FONTE = nome
        self.URL_BASE = url
        super().__init__()
        # Siti comunali: chi non risponde in 20 secondi di solito non risponde
        # affatto (spesso bloccano i server esteri, come quelli di GitHub)
        self.timeout = min(self.timeout, 20)
        self.max_tentativi = min(self.max_tentativi, 2)
        self.url = url
        self.tipo = tipo
        self.ente = ente or nome
        self.sede = sede
        self.selettore = selettore
        self.sottomenu_di = sottomenu_di
        self.max_giorni_pubblicazione = max_giorni_pubblicazione
        self.dettagli = dettagli
        self.max_dettagli = max_dettagli
        if not verifica_ssl:
            # Alcuni siti comunali hanno un certificato intestato a un altro nome:
            # si legge solo una pagina pubblica, quindi si accetta lo stesso.
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            self.session.verify = False
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
        aperti = 0   # pagine di dettaglio già aperte (opzione "dettagli")
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
            pubblicazione = estrai_pubblicazione(f"{titolo} {extra}")
            if scadenza and is_scaduto(scadenza):
                continue
            if scadenza is None and _vecchio(titolo, link, extra):
                continue
            if scadenza is None and self.max_giorni_pubblicazione:
                # Pagine di notizie: la prima data del riquadro è quella di pubblicazione
                pubblicato = pubblicazione or parse_data(extra) or parse_data(titolo)
                pubblicazione = pubblicato
                if pubblicato and (oggi() - pubblicato).days > self.max_giorni_pubblicazione:
                    continue
            if scadenza is None and self.dettagli:
                # Elenco senza date: apro la pagina del bando (solo le prime, le più recenti)
                if aperti >= self.max_dettagli:
                    continue
                aperti += 1
                scadenza, recente, pubbl_dettaglio = self._date_dal_dettaglio(link)
                pubblicazione = pubblicazione or pubbl_dettaglio
                if scadenza and is_scaduto(scadenza):
                    continue
                if scadenza is None:
                    limite = self.max_giorni_pubblicazione or 60
                    if recente is None or (oggi() - recente).days > limite:
                        continue

            bandi.append(self._crea_bando(
                titolo=titolo[:300],
                ente=v.get("ente") or self.ente,
                sede=self.sede,
                scadenza=a_iso(scadenza),
                link_ufficiale=link,
                data_pubblicazione=a_iso(pubblicazione),
            ))

        self.logger.info(f"[{self.NOME_FONTE}] {len(bandi)} bandi trovati")
        return bandi

    def _date_dal_dettaglio(self, link: str):
        """(scadenza, data più recente, data di pubblicazione) lette dalla pagina del
        bando; tutte None se la pagina non è HTML (es. un PDF)."""
        self._pausa()
        r = self.get(link)
        if r is None or "html" not in r.headers.get("content-type", "html"):
            return None, None, None
        zuppa = BeautifulSoup(r.content, "lxml")
        for tag in zuppa(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        testo = _spazi(zuppa.get_text(" "))
        return estrai_scadenza(testo), data_piu_recente(testo), estrai_pubblicazione(testo)

    # ------------------------------------------------------------------
    def _pagina(self):
        r = self.get(self.url)
        if r is None:
            return None
        zuppa = BeautifulSoup(r.content, "lxml")
        if not self.selettore and not self.sottomenu_di:
            return zuppa
        if self.sottomenu_di:
            aree = []
            for a in zuppa.find_all("a", href=re.compile(re.escape(self.sottomenu_di))):
                voce = a.find_parent("li")
                sotto = voce.find("ul") if voce else None
                if sotto is not None and sotto not in aree:
                    aree.append(sotto)
            cosa = f"sottomenu di '{self.sottomenu_di}'"
        else:
            aree = zuppa.select(self.selettore)
            cosa = f"selettore '{self.selettore}'"
        if not aree:
            # Dico anche cosa è arrivato: spesso è una pagina di blocco o di errore
            titolo = _spazi(zuppa.title.get_text()) if zuppa.title else "senza titolo"
            self._registra_errore(
                self.url,
                f"{cosa} non trovato (ricevuta pagina '{titolo[:80]}', {len(r.content)} byte)",
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
