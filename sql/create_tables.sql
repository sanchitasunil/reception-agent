-- Arogya Clinic reception-agent: create all required Supabase tables.
-- Run once in the Supabase SQL editor: https://supabase.com/dashboard/project/_/sql

-- Patient memory: persists across calls for known callers.
create table if not exists patients (
    phone              text primary key,
    name               text,
    preferred_doctor   text,
    last_booking_id    text,
    last_appointment_date text,
    last_appointment_time text,
    call_count         integer not null default 0,
    last_seen          timestamptz,
    created_at         timestamptz not null default now()
);

-- Appointment log: one row per booking made through the agent.
create table if not exists appointments (
    id          text primary key,
    phone       text not null,
    doctor      text,
    date        text,
    time        text,
    reason      text,
    booking_id  text,
    created_at  timestamptz not null default now()
);

-- Call transcripts: one row per completed call.
create table if not exists call_logs (
    id               bigserial primary key,
    phone            text,
    started_at       timestamptz,
    ended_at         timestamptz,
    duration_seconds integer,
    transcript       jsonb,
    booking_id       text,
    intent           text,
    call_outcome     text,
    created_at       timestamptz not null default now()
);
