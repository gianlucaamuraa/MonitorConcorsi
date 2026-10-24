-- MonitorConcorsi: stato e note personali dei bandi, sincronizzati tra dispositivi.
-- Da eseguire una volta in Supabase: SQL Editor → New query → incolla → Run.
--
-- Ogni utente vede e modifica solo le proprie righe (Row Level Security).
-- La chiave "anon" usata nella pagina è pubblica per natura: senza login
-- non permette di leggere né scrivere nulla in questa tabella.

create table if not exists public.stati (
    user_id    uuid        not null default auth.uid() references auth.users (id) on delete cascade,
    bando_id   text        not null check (char_length(bando_id) <= 64),
    stato      text        not null default 'da_vedere'
                           check (stato in ('da_vedere', 'interessante', 'candidato', 'scartato')),
    note       text        check (char_length(note) <= 2000),
    aggiornato timestamptz not null default now(),
    primary key (user_id, bando_id)
);

alter table public.stati enable row level security;

drop policy if exists "leggo i miei stati" on public.stati;
drop policy if exists "inserisco i miei stati" on public.stati;
drop policy if exists "modifico i miei stati" on public.stati;
drop policy if exists "cancello i miei stati" on public.stati;

create policy "leggo i miei stati" on public.stati
    for select to authenticated using ((select auth.uid()) = user_id);
create policy "inserisco i miei stati" on public.stati
    for insert to authenticated with check ((select auth.uid()) = user_id);
create policy "modifico i miei stati" on public.stati
    for update to authenticated using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);
create policy "cancello i miei stati" on public.stati
    for delete to authenticated using ((select auth.uid()) = user_id);
