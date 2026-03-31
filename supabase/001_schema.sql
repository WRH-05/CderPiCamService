-- 001_schema.sql
-- Core relational schema for EL degradation tracking.

begin;

create table if not exists assets (
  id bigint generated always as identity primary key,
  panel_id text not null,
  pad_id text not null,
  location text,
  install_date date,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (panel_id, pad_id)
);

create table if not exists thresholds (
  id bigint generated always as identity primary key,
  panel_id text,
  pad_id text,
  warn_threshold numeric(6,4) not null default 0.55,
  critical_threshold numeric(6,4) not null default 0.70,
  recovery_threshold numeric(6,4) not null default 0.60,
  slope_threshold numeric(12,10) not null default 0.0000015,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (warn_threshold < critical_threshold),
  check (recovery_threshold < critical_threshold)
);

create table if not exists inspections (
  id bigint generated always as identity primary key,
  captured_at timestamptz not null,
  robot_id text not null,
  panel_id text not null,
  pad_id text not null,
  severity_score numeric(6,4) not null,
  status text not null,
  image_path text,
  model_version text,
  raw_payload jsonb not null default '{}'::jsonb,
  inserted_at timestamptz not null default now(),
  check (severity_score >= 0 and severity_score <= 1)
);

create index if not exists inspections_panel_pad_time_idx
  on inspections (panel_id, pad_id, captured_at desc);

create index if not exists inspections_inserted_at_idx
  on inspections (inserted_at desc);

create table if not exists alerts (
  id bigint generated always as identity primary key,
  panel_id text not null,
  pad_id text not null,
  state text not null check (state in ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')),
  reason text not null,
  opened_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  resolved_at timestamptz,
  last_severity numeric(6,4),
  slope_4w numeric(14,10),
  active boolean not null default true,
  acknowledged_by text,
  acknowledged_at timestamptz
);

-- Enforce only one active alert per pad while preserving alert history.
create unique index if not exists alerts_one_active_per_pad_idx
  on alerts (panel_id, pad_id)
  where active = true;

create index if not exists alerts_active_idx
  on alerts (active, panel_id, pad_id, updated_at desc);

create table if not exists maintenance_events (
  id bigint generated always as identity primary key,
  panel_id text not null,
  pad_id text not null,
  replaced_at timestamptz not null,
  engineer text,
  notes text,
  created_at timestamptz not null default now()
);

-- Default fallback threshold row (global policy).
insert into thresholds (
  panel_id,
  pad_id,
  warn_threshold,
  critical_threshold,
  recovery_threshold,
  slope_threshold
)
select null, null, 0.55, 0.70, 0.60, 0.0000015
where not exists (
  select 1
  from thresholds t
  where t.panel_id is null and t.pad_id is null
);

commit;
