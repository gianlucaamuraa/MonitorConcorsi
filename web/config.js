/* Configurazione della versione online.
   supabaseUrl / supabaseAnonKey: da Supabase → Project Settings → API.
   La chiave "anon" è pensata per stare in una pagina pubblica: i dati
   sono protetti dalle regole della tabella (vedi supabase/schema.sql).
   githubRepo: "utente/MonitorConcorsi", per il pulsante "Avvia scansione". */
window.MONITOR_CONFIG = {
    supabaseUrl: "",
    supabaseAnonKey: "",
    githubRepo: "gianlucaamuraa/MonitorConcorsi",
};
