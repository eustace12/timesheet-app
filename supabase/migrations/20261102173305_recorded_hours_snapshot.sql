-- Serialize recorded-hour edits with monthly submission, including periods
-- without a timesheet row. Applied functions retain existing access restrictions.
create or replace function public.lock_time_entry_employee()
returns trigger language plpgsql security invoker set search_path = public, pg_temp as $$
begin
    if TG_OP = 'INSERT' then
        perform 1 from public.users where id = NEW.user_id for update;
    elsif TG_OP = 'DELETE' then
        perform 1 from public.users where id = OLD.user_id for update;
    else
        perform 1 from public.users where id in (OLD.user_id, NEW.user_id) order by id for update;
    end if;
    if TG_OP = 'DELETE' then return OLD; end if;
    return NEW;
end;
$$;
drop trigger if exists a_time_entries_employee_lock on public.time_entries;
create trigger a_time_entries_employee_lock before insert or update or delete on public.time_entries
    for each row execute function public.lock_time_entry_employee();
revoke all on function public.lock_time_entry_employee() from public, anon, authenticated;
grant execute on function public.lock_time_entry_employee() to service_role;

create or replace function public.submit_timesheet_atomic(p_values jsonb)
returns jsonb language plpgsql security invoker set search_path = public, pg_temp as $$
declare
    saved public.timesheets;
    person bigint := (p_values->>'user_id')::bigint;
    current_leave jsonb;
    expected_leave jsonb;
    resubmitting boolean;
    current_minutes jsonb;
begin
    perform 1 from public.users where id = person for update;
    if not found then raise exception 'WORKFLOW_CONFLICT: Employee no longer exists.'; end if;
    select * into saved from public.timesheets where user_id = person
        and year = (p_values->>'year')::integer and month = (p_values->>'month')::integer for update;
    if found and saved.status <> 'needs_changes' then
        raise exception 'WORKFLOW_CONFLICT: This month is already submitted or approved. Refresh before continuing.';
    end if;
    resubmitting := saved.id is not null;
    if coalesce((p_values->>'validate_daily_hours_snapshot')::boolean, false) then
        select coalesce(jsonb_object_agg(work_date::text, minutes), '{}'::jsonb)
            into current_minutes from (
                select work_date, sum(minutes) as minutes from public.time_entries
                where user_id = person
                    and work_date >= make_date((p_values->>'year')::integer, (p_values->>'month')::integer, 1)
                    and work_date < (make_date((p_values->>'year')::integer, (p_values->>'month')::integer, 1) + interval '1 month')::date
                group by work_date
            ) daily;
        if current_minutes is distinct from p_values->'minute_snapshot' then
            raise exception 'WORKFLOW_CONFLICT: Recorded hours changed while this document was generated. Refresh and submit again.';
        end if;
    end if;

    if coalesce((p_values->>'validate_leave_snapshot')::boolean, false) then
        select coalesce(jsonb_agg(jsonb_build_object('id', id, 'start_date', start_date,
            'end_date', end_date, 'leave_type', leave_type) order by id), '[]'::jsonb)
        into current_leave from public.leave_requests where user_id = person and status = 'accepted'
            and start_date::date < (make_date((p_values->>'year')::integer, (p_values->>'month')::integer, 1) + interval '1 month')::date
            and end_date::date >= make_date((p_values->>'year')::integer, (p_values->>'month')::integer, 1);
        select coalesce(jsonb_agg(item order by (item->>'id')::bigint), '[]'::jsonb)
            into expected_leave from jsonb_array_elements(p_values->'leave_snapshot') item;
        if current_leave <> expected_leave then
            raise exception 'WORKFLOW_CONFLICT: Approved leave changed while this document was generated. Refresh and submit again.';
        end if;
    end if;
    insert into public.timesheets(user_id, year, month, format, vacation, sick,
        custom_days, custom_reason, working_days, total_hours, filename, storage_key,
        leave_snapshot, submitted_at, status, review_comment, reviewed_at)
    values(person, (p_values->>'year')::integer, (p_values->>'month')::integer,
        p_values->>'format', p_values->>'vacation', p_values->>'sick',
        p_values->>'custom_days', p_values->>'custom_reason',
        (p_values->>'working_days')::integer, (p_values->>'total_hours')::numeric,
        p_values->>'filename', p_values->>'storage_key', coalesce(p_values->'leave_snapshot','[]'::jsonb),
        p_values->>'submitted_at', 'submitted', '', null)
    on conflict(user_id, year, month) do update set
        format = excluded.format, vacation = excluded.vacation, sick = excluded.sick,
        custom_days = excluded.custom_days, custom_reason = excluded.custom_reason,
        working_days = excluded.working_days, total_hours = excluded.total_hours,
        filename = excluded.filename, storage_key = excluded.storage_key,
        leave_snapshot = excluded.leave_snapshot, submitted_at = excluded.submitted_at,
        status = 'submitted', review_comment = '', reviewed_at = null
    returning * into saved;
    insert into public.workflow_events(kind, document_id, user_id, actor_user_id, action, status, comment, created_at)
    values('timesheets', saved.id, person, (p_values->>'actor_user_id')::bigint,
        case when resubmitting then 'resubmitted' else 'submitted' end, 'submitted', '', p_values->>'submitted_at');
    return to_jsonb(saved);
end;
$$;

revoke all on function public.submit_timesheet_atomic(jsonb) from public, anon, authenticated;
grant execute on function public.submit_timesheet_atomic(jsonb) to service_role;
notify pgrst, 'reload schema';
