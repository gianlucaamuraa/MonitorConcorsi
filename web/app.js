/* ============================================================
   MonitorConcorsi — Interfaccia web

   Dati: prima prova il server locale (python main.py serve → /api/bandi),
   altrimenti legge ../data/bandi.json (versione online su GitHub Pages).

   Stato e note:
   - con il server locale → database del PC
   - online con accesso a Supabase → tabella "stati", uguale su tutti i dispositivi
   - altrimenti → solo in questo browser
   ============================================================ */

"use strict";

const CONFIG = window.MONITOR_CONFIG || {};

const STATO = {
    bandi: [],
    errori: [],
    ultimaScansione: null,
    conServer: false,
    sb: null,          // client Supabase (se configurato)
    utente: null,      // utente Supabase collegato
};

function supabaseAttivo() {
    return Boolean(CONFIG.supabaseUrl && CONFIG.supabaseAnonKey && window.supabase);
}

function initSupabase() {
    if (!supabaseAttivo() || STATO.sb) return;
    STATO.sb = window.supabase.createClient(CONFIG.supabaseUrl, CONFIG.supabaseAnonKey);
    STATO.sb.auth.onAuthStateChange((evento, sessione) => {
        if (evento === "INITIAL_SESSION") return;   // gestito all'avvio
        const prima = STATO.utente && STATO.utente.id;
        STATO.utente = sessione ? sessione.user : null;
        aggiornaPulsanteAccesso();
        if ((STATO.utente && STATO.utente.id) !== prima) caricaDati();
    });
}

async function statiRemoti() {
    if (!STATO.sb || !STATO.utente) return {};
    const { data, error } = await STATO.sb.from("stati").select("bando_id, stato, note");
    if (error) { mostraToast("Impossibile leggere stato e note: " + error.message); return {}; }
    const mappa = {};
    for (const r of data) mappa[r.bando_id] = { stato: r.stato, note: r.note };
    return mappa;
}

const ETICHETTE_SEZIONE = {
    compatibile: ["Compatibile", "badge-sezione-compatibile"],
    da_verificare: ["Da verificare", "badge-sezione-verificare"],
    archivio: ["Archivio", "badge-sezione-archivio"],
};

const ETICHETTE_STATO = {
    da_vedere: "Da vedere",
    interessante: "Interessante",
    candidato: "Candidato",
    scartato: "Scartato",
};

const SEDI_SASSARI = /sassari|alghero|porto torres|sorso|sennori|ossi|usini|ittiri|ozieri|castelsardo|thiesi|stintino/i;
const SEDI_SARDEGNA = /sardegna|cagliari|nuoro|oristano|olbia|tempio|iglesias|carbonia|ogliastra|gallura/i;
const CHIAVE_LOCALE = "monitorconcorsi.stati";

/* ---------------------------------------------------------- utilità */

const $ = (id) => document.getElementById(id);

function esc(testo) {
    return String(testo ?? "").replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
}

function linkSicuro(url) {
    return /^https?:\/\//i.test(url || "") ? url : "#";
}

function oggi() {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d;
}

function dataDa(iso) {
    if (!iso) return null;
    const [a, m, g] = iso.slice(0, 10).split("-").map(Number);
    return a ? new Date(a, m - 1, g) : null;
}

function giorniRimasti(iso) {
    const d = dataDa(iso);
    return d ? Math.round((d - oggi()) / 86400000) : null;
}

function formatoData(iso) {
    const d = dataDa(iso);
    return d ? d.toLocaleDateString("it-IT") : "n/d";
}

// Data di pubblicazione; se la fonte non la indica uso il giorno in cui il
// programma ha trovato il bando (è al massimo qualche giorno dopo)
function pubblicazioneDi(b) {
    if (b.data_pubblicazione) return { iso: b.data_pubblicazione, stimata: false };
    const rilevato = (b.prima_rilevazione || "").slice(0, 10);
    return { iso: rilevato || null, stimata: true };
}

function giorniDallaPubblicazione(b) {
    const g = giorniRimasti(pubblicazioneDi(b).iso);
    return g === null ? null : -g;
}

function scaduto(b) {
    const g = giorniRimasti(b.scadenza);
    return g !== null && g < 0;
}

function leggiLocale() {
    try { return JSON.parse(localStorage.getItem(CHIAVE_LOCALE) || "{}"); }
    catch { return {}; }
}

function scriviLocale(dati) {
    try { localStorage.setItem(CHIAVE_LOCALE, JSON.stringify(dati)); } catch { /* storage non disponibile */ }
}

/* ---------------------------------------------------------- caricamento */

async function caricaDati() {
    let dati = null;
    try {
        const r = await fetch("/api/bandi", { cache: "no-store" });
        if (r.ok) { dati = await r.json(); STATO.conServer = true; }
    } catch { /* nessun server */ }

    if (!dati) {
        try {
            const r = await fetch("../data/bandi.json", { cache: "no-store" });
            if (r.ok) dati = await r.json();
        } catch { /* nessun file */ }
        STATO.conServer = false;
    }

    if (!dati) {
        $("lista-bandi").innerHTML = `
            <div class="card avviso">
                <p><strong>Nessun dato disponibile.</strong></p>
                <p>Esegui una scansione con <code>python main.py scan</code> e apri l'interfaccia
                con <code>python main.py serve</code>.</p>
            </div>`;
        return;
    }

    // Stato e note: dal server locale (già nei dati), da Supabase o dal browser
    const personali = STATO.conServer ? {} : (STATO.utente ? await statiRemoti() : leggiLocale());

    // Sicurezza in più: i bandi scaduti non vengono mai mostrati
    STATO.bandi = (dati.bandi || []).filter((b) => !scaduto(b)).map((b) => {
        if (personali[b.id]) Object.assign(b, personali[b.id]);
        return b;
    });
    STATO.errori = dati.errori || [];
    STATO.ultimaScansione = dati.ultima_scansione;
    STATO.entiMonitorati = (dati.enti_monitorati || []).map((e) => e.ente);

    aggiornaIntestazione();
    preparaFiltroEnte();
    mostraErrori();
    aggiornaStatistiche();
    filtra();
}

function aggiornaIntestazione() {
    const info = $("info-scansione");
    if (!info) return;
    const quando = STATO.ultimaScansione
        ? new Date(STATO.ultimaScansione).toLocaleString("it-IT", { dateStyle: "short", timeStyle: "short" })
        : "mai";
    let dove = "";
    if (!STATO.conServer) {
        dove = STATO.utente ? " · stato e note sincronizzati"
            : (supabaseAttivo() ? " · accedi per sincronizzare stato e note"
                : " · stato e note salvati solo in questo browser");
    }
    info.textContent = `Ultima scansione: ${quando}${dove}`;
    $("btn-aggiorna-testo").textContent =
        STATO.conServer || CONFIG.githubRepo ? "Avvia scansione" : "Ricarica dati";
    aggiornaPulsanteAccesso();
}

/* ---------------------------------------------------------- scansione */

async function aggiornaDati() {
    if (!STATO.conServer) {
        // Online la scansione gira su GitHub: apro la pagina da cui avviarla
        if (CONFIG.githubRepo) {
            window.open(`https://github.com/${CONFIG.githubRepo}/actions/workflows/scansione.yml`, "_blank", "noopener");
            mostraToast("Premi \"Run workflow\" su GitHub: in pochi minuti i dati si aggiornano");
            return;
        }
        return caricaDati();
    }

    const btn = $("btn-aggiorna");
    btn.disabled = true;
    $("btn-aggiorna-testo").textContent = "Scansione in corso…";
    try {
        await fetch("/api/scansione", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}",
        });
        // Controlla ogni 5 secondi finché la scansione non finisce
        for (;;) {
            await new Promise((ok) => setTimeout(ok, 5000));
            const stato = await (await fetch("/api/scansione", { cache: "no-store" })).json();
            if (!stato.in_corso) {
                if (stato.errore) alert("Scansione non riuscita: " + stato.errore);
                else if (stato.ultimo_esito) mostraToast(`Scansione completata: ${stato.ultimo_esito.nuovi} nuovi bandi`);
                break;
            }
        }
    } catch (e) {
        alert("Errore di comunicazione con il server: " + e.message);
    } finally {
        btn.disabled = false;
        await caricaDati();
    }
}

function mostraToast(testo) {
    const t = document.createElement("div");
    t.className = "toast";
    t.textContent = testo;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 4000);
}

/* ---------------------------------------------------------- pannelli */

function mostraErrori() {
    const pannello = $("pannello-errori");
    if (!STATO.errori.length) { pannello.classList.add("hidden"); return; }
    pannello.classList.remove("hidden");
    $("lista-errori").innerHTML = STATO.errori.map((e) => `
        <div class="errore-riga">
            <strong>${esc(e.fonte)}</strong>
            <span class="badge badge-urgente">${esc(e.tipo_errore || "errore")}</span>
            <span class="meta">${esc((e.motivo || "").slice(0, 160))}</span>
        </div>`).join("");
}

function aggiornaStatistiche() {
    const visibili = STATO.bandi.filter((b) => b.sezione !== "archivio" && b.stato !== "scartato");
    $("stat-totali").textContent = visibili.length;
    $("stat-scadenza").textContent = visibili.filter((b) => {
        const g = giorniRimasti(b.scadenza);
        return g !== null && g <= 7;
    }).length;
    $("stat-compatibili").textContent = visibili.filter((b) => b.sezione === "compatibile").length;
    $("stat-verificare").textContent = visibili.filter((b) => b.sezione === "da_verificare").length;
}

/* ---------------------------------------------------------- filtri */

function eNuovo(b) {
    if (!b.prima_rilevazione) return false;
    const rif = STATO.ultimaScansione ? new Date(STATO.ultimaScansione) : new Date();
    return new Date(b.prima_rilevazione) >= new Date(rif - 86400000);
}

function zonaDi(b) {
    const testo = `${b.sede || ""} ${b.titolo || ""} ${b.ente || ""}`;
    if (SEDI_SASSARI.test(testo)) return "sassari";
    if (SEDI_SARDEGNA.test(testo)) return "sardegna";
    return "nazionale";
}

/* ---------------------------------------------------------- filtro ente */

// "CITTA' METROPOLITANA DI SASSARI" e "Città Metropolitana di Sassari" → stessa chiave
function chiaveEnte(nome) {
    return (nome || "").normalize("NFD").replace(/[̀-ͯ]/g, "")
        .toLowerCase().replace(/[’'`´"]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
}

// Nomi generici degli aggregatori: l'ente vero si cerca nel titolo
// (es. "Comune di Budoni, 1 funzionario tecnico")
const ENTI_GENERICI = new Set(["ente della sardegna"]);

function enteCorrisponde(b, chiave) {
    const propria = chiaveEnte(b.ente);
    if (propria === chiave) return true;
    if (!ENTI_GENERICI.has(propria)) return false;
    return ` ${chiaveEnte(b.titolo)} `.includes(` ${chiave} `);
}

function gruppoEnte(nome) {
    const k = chiaveEnte(nome);
    if (k.startsWith("comune di ")) return "Comuni";
    if (k.startsWith("unione ")) return "Unioni di comuni";
    return "Altri enti";
}

// Etichetta corta nel menu: "Comune di Usini" → "Usini"
function etichettaEnte(nome) {
    return nome.replace(/^comune di\s+/i, "").replace(/^unione dei comuni\s+/i, "Unione ");
}

function preparaFiltroEnte() {
    const select = $("filtro-ente");
    if (!select) return;
    const scelto = select.value;

    // Tutti gli enti scansionati (anche senza bandi) + gli enti dei bandi trovati
    const enti = new Map();   // chiave → nome da mostrare
    for (const nome of STATO.entiMonitorati || []) enti.set(chiaveEnte(nome), nome);
    for (const b of STATO.bandi) {
        const k = chiaveEnte(b.ente);
        if (k && !ENTI_GENERICI.has(k) && !enti.has(k)) enti.set(k, b.ente);
    }

    // Conteggio dei bandi visibili normalmente (esclusi archivio e scartati)
    const visibili = STATO.bandi.filter((b) => b.sezione !== "archivio" && b.stato !== "scartato");
    const gruppi = { "Comuni": [], "Unioni di comuni": [], "Altri enti": [] };
    for (const [k, nome] of enti) {
        const n = visibili.filter((b) => enteCorrisponde(b, k)).length;
        gruppi[gruppoEnte(nome)].push({ k, nome, n });
    }

    let html = `<option value="">Tutti gli enti (${enti.size})</option>`;
    for (const [titolo, voci] of Object.entries(gruppi)) {
        if (!voci.length) continue;
        voci.sort((a, b) => etichettaEnte(a.nome).localeCompare(etichettaEnte(b.nome), "it"));
        html += `<optgroup label="${esc(titolo)} (${voci.length})">`;
        for (const v of voci) {
            html += `<option value="${esc(v.k)}">${esc(etichettaEnte(v.nome))} (${v.n})</option>`;
        }
        html += `</optgroup>`;
    }
    select.innerHTML = html;
    if (scelto && enti.has(scelto)) select.value = scelto;
}

function filtra() {
    const sezione = $("filtro-sezione").value;
    const stato = $("filtro-stato").value;
    const zona = $("filtro-zona").value;
    const ente = $("filtro-ente").value;
    const testo = $("filtro-testo").value.trim().toLowerCase();
    const minimo = Number($("filtro-punteggio").value);
    const entro = $("filtro-scadenza").value;
    const pubblicatiDa = $("filtro-pubblicazione").value;

    const risultati = STATO.bandi.filter((b) => {
        // Archivio e scartati si vedono solo se richiesti esplicitamente
        if (sezione === "archivio") { if (b.sezione !== "archivio") return false; }
        else if (b.sezione === "archivio") return false;
        if (stato) { if (b.stato !== stato) return false; }
        else if (b.stato === "scartato") return false;

        if (sezione === "nuovi" && !eNuovo(b)) return false;
        if (sezione && !["nuovi", "archivio"].includes(sezione) && b.sezione !== sezione) return false;
        if (zona && zonaDi(b) !== zona) return false;
        if (ente && !enteCorrisponde(b, ente)) return false;
        if (testo && !`${b.titolo} ${b.ente} ${b.profilo || ""}`.toLowerCase().includes(testo)) return false;
        if ((b.punteggio || 0) < minimo) return false;
        if (pubblicatiDa) {
            const g = giorniDallaPubblicazione(b);
            if (g === null || g > Number(pubblicatiDa)) return false;
        }
        if (entro) {
            const g = giorniRimasti(b.scadenza);
            if (g === null || g > Number(entro)) return false;
        }
        return true;
    });

    // Ordine: punteggio, poi scadenza più vicina
    risultati.sort((a, b) => (b.punteggio || 0) - (a.punteggio || 0)
        || (a.scadenza || "9999").localeCompare(b.scadenza || "9999"));

    disegnaLista(risultati);
    $("contatore-risultati").textContent = `${risultati.length} bandi mostrati`;
}

function aggiornaSliderpunteggio(valore) {
    $("label-punteggio").textContent = `≥ ${valore}`;
    filtra();
}

function resettaFiltri() {
    ["filtro-sezione", "filtro-stato", "filtro-zona", "filtro-ente", "filtro-scadenza", "filtro-pubblicazione"].forEach((id) => { $(id).value = ""; });
    $("filtro-testo").value = "";
    $("filtro-punteggio").value = 0;
    aggiornaSliderpunteggio(0);
}

/* ---------------------------------------------------------- lista */

function badgeScadenza(b) {
    const g = giorniRimasti(b.scadenza);
    if (g === null) return `<span class="badge badge-scadenza">Scadenza n/d</span>`;
    if (g === 0) return `<span class="badge badge-urgente">Scade oggi</span>`;
    if (g <= 7) return `<span class="badge badge-urgente">Scade tra ${g} gg (${formatoData(b.scadenza)})</span>`;
    return `<span class="badge badge-scadenza">Scade il ${formatoData(b.scadenza)}</span>`;
}

function metaPubblicazione(b) {
    const p = pubblicazioneDi(b);
    if (!p.iso) return "";
    const etichetta = p.stimata ? "Rilevato il" : "Pubblicato il";
    const aiuto = p.stimata ? ' title="La fonte non indica la data di pubblicazione: è il giorno in cui il bando è stato trovato"' : "";
    return `<span${aiuto}><span class="etichetta">${etichetta}</span> ${formatoData(p.iso)}</span>`;
}

function disegnaLista(bandi) {
    const lista = $("lista-bandi");
    $("nessun-risultato").classList.toggle("hidden", bandi.length > 0);

    lista.innerHTML = bandi.map((b) => {
        const g = giorniRimasti(b.scadenza);
        const cls = g !== null && g <= 7 ? "urgente"
            : (b.punteggio >= 60 ? "alto" : (b.punteggio < 30 ? "basso" : ""));
        const [etichetta, clsSezione] = ETICHETTE_SEZIONE[b.sezione] || ["", ""];
        const fonti = (b.fonti || []).join(", ");
        const opzioni = Object.entries(ETICHETTE_STATO).map(([v, t]) =>
            `<option value="${v}" ${b.stato === v ? "selected" : ""}>${t}</option>`).join("");

        return `
        <article class="card bando-card ${cls}">
            <div>
                <div class="bando-titolo" onclick="apriDettaglio('${esc(b.id)}')">${esc(b.titolo)}</div>
                <div class="bando-meta">
                    <span><span class="etichetta">Ente</span> ${esc(b.ente)}</span>
                    ${b.sede ? `<span><span class="etichetta">Sede</span> ${esc(b.sede)}</span>` : ""}
                    ${b.n_posti ? `<span><span class="etichetta">Posti</span> ${esc(b.n_posti)}</span>` : ""}
                    ${metaPubblicazione(b)}
                </div>
                <div class="bando-meta bando-badge">
                    <span class="badge badge-punteggio" title="${esc(b.motivo_punteggio)}">${b.punteggio || 0}/100</span>
                    ${etichetta ? `<span class="badge ${clsSezione}">${etichetta}</span>` : ""}
                    ${badgeScadenza(b)}
                    ${eNuovo(b) ? `<span class="badge badge-nuovo">Nuovo</span>` : ""}
                    ${b.solo_fonte_secondaria ? `<span class="badge badge-secondaria">Solo fonte secondaria</span>` : ""}
                </div>
                ${b.note ? `<div class="bando-nota"><span class="etichetta">Nota:</span> ${esc(b.note)}</div>` : ""}
                <div class="bando-fonte">Fonti: ${esc(fonti)}</div>
            </div>
            <div class="bando-azioni">
                <select class="stato-select" onchange="salvaStato('${esc(b.id)}', this.value)" aria-label="Stato personale">${opzioni}</select>
                <a class="btn btn-primary" href="${esc(linkSicuro(b.link_ufficiale))}" target="_blank" rel="noopener">Apri bando</a>
                <button class="btn btn-contorno" onclick="apriDettaglio('${esc(b.id)}')">Dettagli</button>
            </div>
        </article>`;
    }).join("");
}

/* ---------------------------------------------------------- dettaglio */

function apriDettaglio(id) {
    const b = STATO.bandi.find((x) => x.id === id);
    if (!b) return;
    const motivi = (b.motivo_punteggio || "").split(" | ").filter(Boolean)
        .map((m) => `<li>${esc(m)}</li>`).join("");

    $("modal-body").innerHTML = `
        <h2 class="modal-titolo titolo-sfumato">${esc(b.titolo)}</h2>
        <dl class="dettaglio">
            <dt>Ente</dt><dd>${esc(b.ente)}</dd>
            <dt>Sede</dt><dd>${esc(b.sede || "n/d")}</dd>
            <dt>Pubblicazione</dt><dd>${b.data_pubblicazione ? formatoData(b.data_pubblicazione) : "non indicata dalla fonte"}</dd>
            <dt>Scadenza</dt><dd>${formatoData(b.scadenza)}</dd>
            <dt>Posti</dt><dd>${esc(b.n_posti || "n/d")}</dd>
            ${b.profilo ? `<dt>Profilo</dt><dd>${esc(b.profilo)}</dd>` : ""}
            ${b.livello ? `<dt>Tipo</dt><dd>${esc(b.livello)}</dd>` : ""}
            <dt>Titolo di studio</dt><dd>${esc(b.titolo_studio || "non indicato dalla fonte")}</dd>
            <dt>Fonti</dt><dd>${esc((b.fonti || []).join(", "))}</dd>
            <dt>Prima rilevazione</dt><dd>${esc((b.prima_rilevazione || "").slice(0, 10))}</dd>
        </dl>
        <h3>Punteggio ${b.punteggio || 0}/100</h3>
        <ul class="motivi">${motivi}</ul>
        <h3>Note personali</h3>
        <textarea id="nota-bando" rows="4" placeholder="es. requisiti da controllare, documenti da preparare…">${esc(b.note || "")}</textarea>
        <div class="modal-azioni">
            <button class="btn btn-primary" onclick="salvaNota('${esc(b.id)}')">Salva nota</button>
            <a class="btn btn-contorno" href="${esc(linkSicuro(b.link_ufficiale))}" target="_blank" rel="noopener">Apri bando ufficiale</a>
        </div>
        <p class="meta">Controlla sempre requisiti e scadenza sul bando ufficiale.</p>`;
    $("modal").classList.remove("hidden");
}

function chiudiModal() {
    $("modal").classList.add("hidden");
}

/* ---------------------------------------------------------- salvataggio */

async function salva(id, modifiche) {
    const b = STATO.bandi.find((x) => x.id === id);
    if (!b) return;

    if (STATO.conServer) {
        try {
            const r = await fetch(`/api/bandi/${encodeURIComponent(id)}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(modifiche),
            });
            if (!r.ok) throw new Error((await r.json()).errore || r.status);
        } catch (e) {
            alert("Salvataggio non riuscito: " + e.message);
            return;
        }
    } else if (STATO.utente) {
        const riga = {
            user_id: STATO.utente.id,
            bando_id: id,
            stato: modifiche.stato ?? b.stato ?? "da_vedere",
            note: (modifiche.note ?? b.note ?? null) || null,
            aggiornato: new Date().toISOString(),
        };
        const { error } = await STATO.sb.from("stati").upsert(riga, { onConflict: "user_id,bando_id" });
        if (error) { alert("Salvataggio non riuscito: " + error.message); return; }
    } else {
        const locali = leggiLocale();
        locali[id] = { ...(locali[id] || {}), ...modifiche };
        scriviLocale(locali);
    }
    Object.assign(b, modifiche);
    aggiornaStatistiche();
    filtra();
}

function salvaStato(id, stato) {
    salva(id, { stato });
}

function salvaNota(id) {
    const b = STATO.bandi.find((x) => x.id === id);
    salva(id, { stato: b ? b.stato : "da_vedere", note: $("nota-bando").value });
    chiudiModal();
    mostraToast("Nota salvata");
}

/* ---------------------------------------------------------- accesso (Supabase) */

function aggiornaPulsanteAccesso() {
    const btn = $("btn-accesso");
    if (!btn) return;
    btn.classList.toggle("hidden", STATO.conServer || !supabaseAttivo());
    $("btn-accesso-testo").textContent = STATO.utente ? "Esci" : "Accedi";
    btn.title = STATO.utente ? `Collegato come ${STATO.utente.email}` : "Accedi per sincronizzare stato e note";
}

async function gestisciAccesso() {
    if (STATO.utente) {
        await STATO.sb.auth.signOut();
        mostraToast("Disconnesso");
        return;
    }
    $("modal-body").innerHTML = `
        <h2 class="modal-titolo titolo-sfumato">Accedi</h2>
        <p class="meta">Con l'accesso, stato e note dei bandi sono gli stessi su telefono e PC.</p>
        <form id="form-accesso" class="form-accesso" onsubmit="return accedi(event)">
            <label for="acc-email">Email</label>
            <input type="email" id="acc-email" autocomplete="username" required>
            <label for="acc-password">Password</label>
            <input type="password" id="acc-password" autocomplete="current-password" required minlength="6">
            <p id="acc-errore" class="errore-testo hidden"></p>
            <div class="modal-azioni">
                <button type="submit" class="btn btn-primary">Accedi</button>
            </div>
        </form>`;
    $("modal").classList.remove("hidden");
    $("acc-email").focus();
}

async function accedi(evento) {
    evento.preventDefault();
    const errore = $("acc-errore");
    errore.classList.add("hidden");
    const { error } = await STATO.sb.auth.signInWithPassword({
        email: $("acc-email").value.trim(),
        password: $("acc-password").value,
    });
    if (error) {
        errore.textContent = error.message === "Invalid login credentials"
            ? "Email o password non corrette" : error.message;
        errore.classList.remove("hidden");
        return false;
    }
    chiudiModal();
    mostraToast("Accesso effettuato");
    return false;
}

/* ---------------------------------------------------------- avvio */

document.addEventListener("keydown", (e) => { if (e.key === "Escape") chiudiModal(); });
$("modal").addEventListener("click", (e) => { if (e.target.id === "modal") chiudiModal(); });

(async () => {
    initSupabase();
    if (STATO.sb) {
        const { data } = await STATO.sb.auth.getSession();
        STATO.utente = data.session ? data.session.user : null;
    }
    caricaDati();
})();
