-- 003_views.sql
-- Dashboard-friendly read models.

begin;

create or replace view vw_latest_pad_status as
select distinct on (i.panel_id, i.pad_id)
  i.panel_id,
  i.pad_id,
  i.captured_at,
  i.severity_score,
  i.status,
  i.robot_id,
  i.model_version,
  i.image_path
from inspections i
order by i.panel_id, i.pad_id, i.captured_at desc;

create or replace view vw_pad_weekly_trend as
with weekly as (
  select
    i.panel_id,
    i.pad_id,
    date_trunc('week', i.captured_at) as week_start,
    avg(i.severity_score)::numeric(6,4) as avg_score,
    max(i.severity_score)::numeric(6,4) as max_score,
    min(i.severity_score)::numeric(6,4) as min_score,
    count(*)::int as samples
  from inspections i
  where i.captured_at >= now() - interval '12 weeks'
  group by i.panel_id, i.pad_id, date_trunc('week', i.captured_at)
)
select *
from weekly
order by panel_id, pad_id, week_start desc;

create or replace view vw_open_alerts as
select
  a.id,
  a.panel_id,
  a.pad_id,
  a.state,
  a.reason,
  a.opened_at,
  a.updated_at,
  a.last_severity,
  a.slope_4w,
  a.acknowledged_by,
  a.acknowledged_at
from alerts a
where a.active = true
order by a.updated_at desc;

commit;
