# MonitorConcorsi

Raccoglie ogni giorno i concorsi pubblici aperti, scarta quelli scaduti, assegna
a ciascuno un punteggio di compatibilità con il tuo profilo (diploma, zona di
Sassari, ambiti informatica/amministrativo/tributi) e li mostra in una pagina web.

## Installazione (una volta sola)

Serve **Python 3.12 o 3.13**. Python 3.15 è troppo recente: alcune librerie non
hanno ancora pacchetti pronti per Windows e l'installazione fallisce.

```powershell
cd "C:\Users\Gian Luca Mura\Desktop\MonitorConcorsi"
uv venv --python 3.12          # oppure: py -3.12 -m venv .venv
uv pip install -r requirements.txt
```

## Uso

| Comando | Cosa fa |
|---|---|
| `.venv\Scripts\python main.py scan` | scansiona tutte le fonti attive |
| `.venv\Scripts\python main.py scan --fonte inpa` | scansiona una sola fonte |
| `.venv\Scripts\python main.py serve` | apre l'interfaccia su http://127.0.0.1:8765 |
| `.venv\Scripts\python main.py schedule` | resta acceso e scansiona ogni giorno all'orario di `config.yaml` |
| `.venv\Scripts\python main.py report` | rigenera report, calendario e dati senza scansionare |
| `.venv\Scripts\python main.py fonti` | elenca le fonti configurate |

In alternativa fai doppio clic su `avvia.bat`: apre l'interfaccia web.
Dalla pagina il pulsante **Avvia scansione** lancia una scansione.

La prima scansione dura circa 10-15 minuti (scarica i requisiti di ogni bando
InPA, con pause tra le richieste per non sovraccaricare il portale). Le successive
sono molto più rapide perché i dettagli dei bandi già noti non vengono riscaricati.

## Fonti

Configurate in `fonti.yaml`:

- **InPA** (inpa.gov.it): dal 2023 le PA vi pubblicano tutti i concorsi. Si tengono
  quelli con sede in Sardegna e quelli nazionali (sedi in 5 o più regioni).
- **Gazzetta Ufficiale, 4ª Serie Speciale Concorsi**: feed RSS dell'ultimo numero.
- **Siti di enti** (tipo `html` o `rss`): basta indicare l'indirizzo della pagina dei
  bandi. Comune di Sassari, Regione e Uniss sono già predisposti ma disattivati,
  perché va inserito l'indirizzo esatto della loro pagina concorsi.

## Bandi scaduti

I bandi scaduti non compaiono mai:

1. i collettori li scartano prima di salvarli;
2. a ogni scansione i bandi già salvati con scadenza passata vanno in archivio
   (restano nel database con le tue note, ma non si vedono più);
3. i bandi senza scadenza vanno in archivio dopo 120 giorni dalla prima rilevazione;
4. dai siti generici si scartano le voci che citano solo anni passati
   (es. `bando_2023.pdf`).

## Punteggio (0-100)

| Criterio | Punti | Come |
|---|---|---|
| Titolo di studio | 30 | requisiti del bando; se mancano, dall'inquadramento: Istruttore / Assistente / cat. C = diploma, Funzionario / Dirigente / cat. D = laurea |
| Zona | 25 | Sassari e dintorni 25, resto Sardegna 15, remoto 8, nazionale 5 |
| Ambito | 25 | parole chiave del profilo (`config.yaml` → `ambiti_preferiti`) |
| Scadenza | 20 | più tempo c'è, più punti |

Vanno in **archivio** (filtro "Archivio" per vederli): bandi che richiedono la laurea,
avvisi di mobilità e procedure interne riservate ai dipendenti pubblici
(`dipendente_pubblico: true` in `config.yaml` per mostrarli).

## Notifiche Telegram

In `config.yaml` → `telegram`: inserisci `bot_token` (da @BotFather) e `chat_id`
(da @userinfobot) e metti `attivo: true`. Dopo ogni scansione arriva un messaggio
con i nuovi bandi rilevanti e quelli che scadono entro 3 giorni.

## File generati (cartella `data/`)

- `concorsi.db`: database con bandi, stato e note
- `bandi.json`: dati per l'interfaccia web
- `report_giornaliero.html`: report dell'ultima scansione
- `scadenze.ics`: calendario da importare in Google Calendar / Outlook
- `monitor.log`: registro delle operazioni
