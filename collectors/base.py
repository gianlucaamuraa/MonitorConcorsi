"""
collectors/base.py — Classe base per tutti i collettori MonitorConcorsi

Fornisce funzionalità comuni:
- self.get(url)           → esegue una richiesta HTTP con retry e pausa
- self._pausa()           → pausa casuale tra le richieste
- self._crea_bando()      → costruisce il dizionario standardizzato del bando
- self._registra_errore() → registra un errore strutturato
- self.rispetta_robots()  → verifica il robots.txt prima di fare richieste
- USER_AGENT              → stringa User-Agent condivisa
- self.logger             → logger con nome del collettore
- self.session            → sessione requests condivisa
"""

import logging
import random
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests


# User-Agent realistico (aggiornare periodicamente)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Timeout HTTP di default in secondi
TIMEOUT_HTTP = 30

# Numero massimo di tentativi per richiesta
MAX_TENTATIVI = 3


class CollettoreBase:
    """
    Classe base astratta per tutti i collettori di bandi.
    Ogni collettore concreto deve:
    1. Definire NOME_FONTE (stringa identificativa)
    2. Implementare il metodo esegui() → list[dict]
    """

    # Attributi di classe da sovrascrivere nei collettori concreti
    NOME_FONTE: str = "base"
    URL_BASE: str = ""

    # Intervallo di pausa tra richieste (secondi)
    PAUSA_MIN: int = 3
    PAUSA_MAX: int = 7

    def __init__(self):
        # Configura il logger con il nome del collettore
        self.logger = logging.getLogger(f"collector.{self.NOME_FONTE}")

        # Valori da config.yaml (sezione "scansione"), se presenti
        try:
            from core.config import carica_config
            conf = carica_config().get("scansione", {})
        except Exception:
            conf = {}
        self.timeout = conf.get("timeout_sec", TIMEOUT_HTTP)
        self.max_tentativi = conf.get("max_retry", MAX_TENTATIVI)
        self.PAUSA_MIN = conf.get("pausa_tra_richieste_sec", self.PAUSA_MIN)
        self.PAUSA_MAX = max(conf.get("pausa_max_sec", self.PAUSA_MAX), self.PAUSA_MIN)

        # Ultimo errore rilevato: (tipo, messaggio). Letto dalla pipeline.
        self.ultimo_errore: tuple[str, str] | None = None

        # Sessione HTTP condivisa con header realistici
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
            # niente "br": requests non decodifica Brotli senza pacchetti extra
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "DNT": "1",
        })

        # Cache per robots.txt già scaricati (URL base → RobotFileParser)
        self._cache_robots: dict[str, RobotFileParser] = {}

    def esegui(self) -> list[dict]:
        """
        Metodo principale da implementare in ogni collettore.
        Deve restituire una lista di dizionari bando.
        """
        raise NotImplementedError(
            f"Il collettore '{self.NOME_FONTE}' deve implementare esegui()"
        )

    # -------------------------------------------------------------------------
    # Metodi HTTP
    # -------------------------------------------------------------------------

    def get(self, url: str, params: dict = None, **kwargs) -> requests.Response | None:
        """
        Esegue una richiesta GET con retry automatico e pausa tra tentativi.

        Args:
            url: URL da scaricare
            params: parametri query string (opzionale)
            **kwargs: argomenti aggiuntivi per requests.get()

        Returns:
            Response se la richiesta ha successo, None in caso di errore definitivo
        """
        return self._richiesta("GET", url, params=params, **kwargs)

    def post(self, url: str, json: dict = None, **kwargs) -> requests.Response | None:
        """Come get(), ma con metodo POST e corpo JSON (usato dalle API, es. InPA)."""
        return self._richiesta("POST", url, json=json, **kwargs)

    def _richiesta(self, metodo: str, url: str, **kwargs) -> requests.Response | None:
        """Richiesta HTTP con retry, backoff e registrazione degli errori."""
        MAX_TENTATIVI = self.max_tentativi
        causa = "nessuna risposta"   # ultimo problema, per spiegare l'errore finale
        for tentativo in range(1, MAX_TENTATIVI + 1):
            try:
                self.logger.debug(f"{metodo} {url} (tentativo {tentativo}/{MAX_TENTATIVI})")

                risposta = self.session.request(
                    metodo,
                    url,
                    timeout=self.timeout,
                    **kwargs
                )

                # Gestione codici di errore HTTP
                if risposta.status_code == 200:
                    return risposta

                elif risposta.status_code in (403, 401):
                    # Accesso negato: non ritentare
                    self.logger.warning(
                        f"[{self.NOME_FONTE}] HTTP {risposta.status_code} su {url} — accesso negato"
                    )
                    self._registra_errore(url, f"HTTP {risposta.status_code}", "accesso_negato")
                    return None

                elif risposta.status_code == 429:
                    # Troppi tentativi: attendi più a lungo
                    attesa = 60 * tentativo
                    causa = "HTTP 429 (troppe richieste)"
                    self.logger.warning(
                        f"[{self.NOME_FONTE}] Rate limit (429) su {url}. Attendo {attesa}s"
                    )
                    time.sleep(attesa)

                elif risposta.status_code >= 500:
                    # Errore server: ritenta con backoff
                    attesa = 10 * tentativo
                    causa = f"HTTP {risposta.status_code} (errore del sito)"
                    self.logger.warning(
                        f"[{self.NOME_FONTE}] Errore server {risposta.status_code} su {url}. "
                        f"Ritento in {attesa}s"
                    )
                    time.sleep(attesa)

                else:
                    self.logger.warning(
                        f"[{self.NOME_FONTE}] HTTP {risposta.status_code} su {url}"
                    )
                    self._registra_errore(url, f"HTTP {risposta.status_code}", "http_error")
                    return None

            except requests.exceptions.ConnectionError as e:
                self.logger.warning(f"[{self.NOME_FONTE}] Connessione fallita ({url}): {e}")
                causa = "connessione rifiutata o interrotta"
                time.sleep(5 * tentativo)

            except requests.exceptions.Timeout:
                self.logger.warning(f"[{self.NOME_FONTE}] Timeout su {url} (tentativo {tentativo})")
                causa = f"il sito non risponde entro {self.timeout} secondi"
                time.sleep(5 * tentativo)

            except Exception as e:
                self.logger.error(f"[{self.NOME_FONTE}] Errore imprevisto su {url}: {e}")
                self._registra_errore(url, str(e), "errore")
                return None

        # Tutti i tentativi esauriti
        self.logger.error(f"[{self.NOME_FONTE}] Impossibile raggiungere {url} dopo {MAX_TENTATIVI} tentativi")
        self._registra_errore(url, f"Tutti i tentativi esauriti: {causa}", "errore_rete")
        return None

    def _pausa(self):
        """Pausa casuale tra PAUSA_MIN e PAUSA_MAX secondi per non sovraccaricare il server."""
        attesa = random.uniform(self.PAUSA_MIN, self.PAUSA_MAX)
        self.logger.debug(f"[{self.NOME_FONTE}] Pausa {attesa:.1f}s")
        time.sleep(attesa)

    # -------------------------------------------------------------------------
    # Costruzione bando standardizzato
    # -------------------------------------------------------------------------

    def _crea_bando(
        self,
        titolo: str = "",
        ente: str = "",
        sede: str = None,
        scadenza: str = None,
        n_posti: int = None,
        titolo_studio: str = None,
        link_ufficiale: str = "",
        **extra
    ) -> dict:
        """
        Costruisce il dizionario standardizzato per un bando.

        Args:
            titolo:        Titolo/descrizione del bando
            ente:          Ente/amministrazione che pubblica il bando
            sede:          Sede di lavoro (es. "Sassari", "Sardegna")
            scadenza:      Data di scadenza in formato ISO (YYYY-MM-DD) o None
            n_posti:       Numero di posti disponibili o None
            titolo_studio: Titolo di studio richiesto o None
            link_ufficiale: URL del bando ufficiale
            **extra:       Campi aggiuntivi specifici del collettore

        Returns:
            Dizionario con campi standardizzati
        """
        bando = {
            "fonte": self.NOME_FONTE,
            "ente": ente.strip() if ente else "",
            "titolo": titolo.strip() if titolo else "",
            "sede": sede,
            "scadenza": scadenza,
            "n_posti": n_posti,
            "titolo_studio": titolo_studio,
            "link_ufficiale": link_ufficiale.strip() if link_ufficiale else "",
        }

        # Aggiungi eventuali campi extra
        bando.update(extra)

        return bando

    # -------------------------------------------------------------------------
    # Gestione errori
    # -------------------------------------------------------------------------

    def _registra_errore(self, url: str, messaggio: str, tipo: str = "errore"):
        """
        Registra un errore strutturato nel log.

        Args:
            url:       URL che ha causato l'errore
            messaggio: Descrizione dell'errore
            tipo:      Categoria errore (es. "errore", "robots", "accesso_negato")
        """
        self.logger.error(
            f"[{self.NOME_FONTE}] ERRORE [{tipo}] su {url}: {messaggio}"
        )
        self.ultimo_errore = (tipo, f"{messaggio} ({url})")

    # -------------------------------------------------------------------------
    # Rispetto robots.txt
    # -------------------------------------------------------------------------

    def rispetta_robots(self, url: str) -> bool:
        """
        Verifica se lo scraping dell'URL è permesso dal robots.txt del sito.

        Args:
            url: URL da verificare

        Returns:
            True se permesso (o se il robots.txt non è accessibile), False altrimenti
        """
        try:
            parsed = urlparse(url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            url_robots = f"{base}/robots.txt"

            # Usa la cache per non riscaricare robots.txt ad ogni richiesta
            if base not in self._cache_robots:
                rp = RobotFileParser()
                rp.set_url(url_robots)
                try:
                    # Scarico con la stessa sessione (stesso User-Agent): rp.read()
                    # usa urllib, che alcuni siti bloccano, e in quel caso
                    # considererebbe vietato tutto il sito.
                    r = self.session.get(url_robots, timeout=self.timeout)
                    if r.status_code != 200:
                        # robots.txt assente o non leggibile: lettura permessa
                        return True
                    rp.parse(r.text.splitlines())
                    self._cache_robots[base] = rp
                except Exception:
                    # Se non riesce a leggere robots.txt, assume che sia permesso
                    self.logger.debug(f"[{self.NOME_FONTE}] Impossibile leggere {url_robots}")
                    return True

            rp = self._cache_robots[base]
            permesso = rp.can_fetch(USER_AGENT, url)

            if not permesso:
                self.logger.info(f"[{self.NOME_FONTE}] robots.txt vieta: {url}")

            return permesso

        except Exception as e:
            self.logger.debug(f"[{self.NOME_FONTE}] Errore verifica robots.txt: {e}")
            return True  # In caso di errore, procedi comunque
