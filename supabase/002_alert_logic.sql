-- 002_alert_logic.sql
-- Alert evaluation and automatic alert lifecycle management.

begin;

create or replace function get_effective_threshold(
  p_panel_id text,
  p_pad_id text
)
returns table (
  warn_threshold numeric,
  critical_threshold numeric,
  recovery_threshold numeric,
  slope_threshold numeric
)
language sql
stable
as $$
  select
    t.warn_threshold,
    t.critical_threshold,
    t.recovery_threshold,
    t.slope_threshold
  from thresholds t
  where
    (t.panel_id = p_panel_id and t.pad_id = p_pad_id)
    or (t.panel_id is null and t.pad_id is null)
  order by
    case when t.panel_id is null and t.pad_id is null then 1 else 0 end,
    t.updated_at desc
  limit 1;
$$;

create or replace function evaluate_pad_alert(
  p_panel_id text,
  p_pad_id text
)
returns table (
  state text,
  reason text,
  current_score numeric,
  slope_4w numeric
)
language plpgsql
as $$
declare
  v_warn numeric;
  v_critical numeric;
  v_recovery numeric;
  v_slope_thr numeric;
  v_current numeric;
  v_slope double precision;
begin
  select
    th.warn_threshold,
    th.critical_threshold,
    th.recovery_threshold,
    th.slope_threshold
  into
    v_warn,
    v_critical,
    v_recovery,
    v_slope_thr
  from get_effective_threshold(p_panel_id, p_pad_id) th;

  select i.severity_score
  into v_current
  from inspections i
  where i.panel_id = p_panel_id
    and i.pad_id = p_pad_id
  order by i.captured_at desc
  limit 1;

  select regr_slope(
    i.severity_score::double precision,
    extract(epoch from i.captured_at)
  )
  into v_slope
  from inspections i
  where i.panel_id = p_panel_id
    and i.pad_id = p_pad_id
    and i.captured_at >= now() - interval '28 days';

  if v_current is null then
    return;
  end if;

  if v_current >= v_critical then
    return query select 'OPEN'::text, 'CRITICAL_THRESHOLD'::text, v_current, coalesce(v_slope, 0)::numeric;
    return;
  end if;

  if coalesce(v_slope, 0) >= coalesce(v_slope_thr, 0) then
    return query select 'OPEN'::text, 'RISING_TREND'::text, v_current, coalesce(v_slope, 0)::numeric;
    return;
  end if;

  if v_current <= v_recovery then
    return query select 'RESOLVED'::text, 'RECOVERY_THRESHOLD'::text, v_current, coalesce(v_slope, 0)::numeric;
    return;
  end if;

  if v_current >= v_warn then
    return query select 'OPEN'::text, 'WARN_THRESHOLD'::text, v_current, coalesce(v_slope, 0)::numeric;
  else
    return query select 'RESOLVED'::text, 'NORMAL_RANGE'::text, v_current, coalesce(v_slope, 0)::numeric;
  end if;
end;
$$;

create or replace function upsert_alert_from_latest_inspection(
  p_panel_id text,
  p_pad_id text
)
returns void
language plpgsql
as $$
declare
  v_state text;
  v_reason text;
  v_current numeric;
  v_slope numeric;
  v_active_id bigint;
begin
  select e.state, e.reason, e.current_score, e.slope_4w
  into v_state, v_reason, v_current, v_slope
  from evaluate_pad_alert(p_panel_id, p_pad_id) e
  limit 1;

  if v_state is null then
    return;
  end if;

  select a.id
  into v_active_id
  from alerts a
  where a.panel_id = p_panel_id
    and a.pad_id = p_pad_id
    and a.active = true
  order by a.opened_at desc
  limit 1;

  if v_state = 'OPEN' then
    if v_active_id is null then
      insert into alerts (
        panel_id,
        pad_id,
        state,
        reason,
        opened_at,
        updated_at,
        last_severity,
        slope_4w,
        active
      )
      values (
        p_panel_id,
        p_pad_id,
        'OPEN',
        v_reason,
        now(),
        now(),
        v_current,
        v_slope,
        true
      );
    else
      update alerts
      set
        state = case when state = 'ACKNOWLEDGED' then 'ACKNOWLEDGED' else 'OPEN' end,
        reason = v_reason,
        updated_at = now(),
        last_severity = v_current,
        slope_4w = v_slope
      where id = v_active_id;
    end if;
  else
    if v_active_id is not null then
      update alerts
      set
        state = 'RESOLVED',
        reason = v_reason,
        updated_at = now(),
        resolved_at = now(),
        active = false,
        last_severity = v_current,
        slope_4w = v_slope
      where id = v_active_id;
    end if;
  end if;
end;
$$;

create or replace function trg_inspections_apply_alert()
returns trigger
language plpgsql
as $$
begin
  perform upsert_alert_from_latest_inspection(new.panel_id, new.pad_id);
  return new;
end;
$$;

drop trigger if exists inspections_apply_alert_trigger on inspections;

create trigger inspections_apply_alert_trigger
after insert on inspections
for each row
execute function trg_inspections_apply_alert();

commit;
