-- Optional office intentions. These rows never affect attendance or payroll.
create table if not exists public.office_plans (
    user_id bigint not null references public.users(id) on delete cascade,
    work_date date not null check (work_date between date '2020-01-01' and date '2099-12-31'),
    planned boolean not null,
    start_time text,
    end_time text,
    updated_at text not null,
    primary key(user_id, work_date),
    check ((start_time is null and end_time is null) or
        (start_time is not null and end_time is not null
         and start_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
         and end_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
         and end_time > start_time))
);
create index if not exists office_plans_date_idx on public.office_plans(work_date, planned);
alter table public.office_plans enable row level security;
revoke all on public.office_plans from public, anon, authenticated;
grant select, insert, update on public.office_plans to service_role;

create or replace function public.set_office_plan_atomic(
    p_user_id bigint, p_work_date date, p_planned boolean,
    p_start_time text, p_end_time text, p_expected_updated_at text, p_updated_at text
) returns jsonb language plpgsql security invoker set search_path = public, pg_temp as $$
declare
    current_plan public.office_plans;
    saved public.office_plans;
    selected_start text := nullif(trim(p_start_time), '');
    selected_end text := nullif(trim(p_end_time), '');
    selected_updated_at text := p_updated_at;
begin
    if p_work_date is null or p_work_date < date '2020-01-01' or p_work_date > date '2099-12-31'
        or p_planned is null or p_user_id is null or p_user_id <= 0
        or p_expected_updated_at is null or length(p_expected_updated_at) > 100
        or p_updated_at is null or length(p_updated_at) > 100 or length(p_updated_at) < 20 then
        raise exception 'WORKFLOW_CONFLICT: Choose a valid office date and include the current version.';
    end if;
    if (selected_start is null) <> (selected_end is null)
        or (selected_start is not null and (
            selected_start !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
            or selected_end !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
            or selected_end <= selected_start)) then
        raise exception 'WORKFLOW_CONFLICT: Set both arrival and departure in HH:MM, with departure later than arrival.';
    end if;
    -- Serialize both the first insert and later updates for this employee.
    perform 1 from public.users where id = p_user_id for update;
    if not found then
        raise exception 'WORKFLOW_CONFLICT: This employee no longer exists.';
    end if;
    select * into current_plan from public.office_plans
        where user_id = p_user_id and work_date = p_work_date for update;
    if found then
        if not p_planned and selected_start is null then
            selected_start := current_plan.start_time;
            selected_end := current_plan.end_time;
        end if;
        -- A retry of the exact same intention succeeds without changing its token.
        if current_plan.planned = p_planned and current_plan.start_time is not distinct from selected_start
            and current_plan.end_time is not distinct from selected_end then
            return to_jsonb(current_plan);
        end if;
        if current_plan.updated_at <> p_expected_updated_at then
            raise exception 'WORKFLOW_CONFLICT: This office plan changed in another tab. Refresh this week and try again.';
        end if;
        if selected_updated_at::timestamptz <= current_plan.updated_at::timestamptz then
            selected_updated_at := to_char((current_plan.updated_at::timestamptz + interval '1 microsecond') at time zone 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US') || '+00:00';
        end if;
    elsif p_expected_updated_at <> '' then
        raise exception 'WORKFLOW_CONFLICT: This office plan changed in another tab. Refresh this week and try again.';
    end if;
    insert into public.office_plans(user_id, work_date, planned, start_time, end_time, updated_at)
        values(p_user_id, p_work_date, p_planned, selected_start, selected_end, selected_updated_at)
        on conflict(user_id, work_date) do update set planned = excluded.planned,
            start_time = excluded.start_time, end_time = excluded.end_time, updated_at = excluded.updated_at
        returning * into saved;
    return to_jsonb(saved);
end;
$$;

revoke all on function public.set_office_plan_atomic(bigint,date,boolean,text,text,text,text)
    from public, anon, authenticated;
grant execute on function public.set_office_plan_atomic(bigint,date,boolean,text,text,text,text) to service_role;
notify pgrst, 'reload schema';
