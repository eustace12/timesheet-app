-- Employees may withdraw their own leave request while it still awaits review.
-- The request is removed; the immutable journal keeps the withdrawal.
create or replace function public.withdraw_leave_atomic(
    p_id bigint, p_user_id bigint, p_at text,
    p_expected_submitted_at text default null, p_actor_user_id bigint default null
) returns jsonb language plpgsql security invoker set search_path = public, pg_temp as $$
declare
    saved public.leave_requests;
begin
    perform 1 from public.users where id = p_user_id for update;
    select * into saved from public.leave_requests where id = p_id for update;
    if not found or saved.user_id <> p_user_id then return null; end if;
    if saved.status <> 'submitted' or (p_expected_submitted_at is not null
        and saved.submitted_at <> p_expected_submitted_at) then
        raise exception 'WORKFLOW_CONFLICT: This request was already reviewed. Refresh to see the decision.';
    end if;
    delete from public.leave_requests where id = p_id;
    insert into public.workflow_events(kind, document_id, user_id, actor_user_id, action, status, comment, created_at)
    values('leave_requests', p_id, p_user_id, p_actor_user_id, 'withdrawn', 'withdrawn', '', p_at);
    return to_jsonb(saved);
end;
$$;

revoke all on function public.withdraw_leave_atomic(bigint,bigint,text,text,bigint) from public, anon, authenticated;
grant execute on function public.withdraw_leave_atomic(bigint,bigint,text,text,bigint) to service_role;

-- A withdrawal can now delete a request that a concurrent review is waiting on.
-- Re-check after taking the row lock so the journal never records a phantom review.
create or replace function public.review_document_atomic(
    p_kind text, p_id bigint, p_status text, p_comment text, p_reviewed_at text,
    p_expected_submitted_at text default null, p_reopen boolean default false,
    p_holidays jsonb default '[]'::jsonb, p_actor_user_id bigint default null
) returns jsonb language plpgsql security invoker set search_path = public, pg_temp as $$
declare
    saved jsonb;
    person bigint;
    required_status text := case when p_reopen then 'accepted' else 'submitted' end;
begin
    if p_status not in ('accepted', 'needs_changes') or length(p_comment) > 1000
        or (p_status = 'needs_changes' and length(trim(p_comment)) = 0)
        or (p_reopen and (p_kind <> 'timesheets' or p_status <> 'needs_changes')) then
        raise exception 'WORKFLOW_CONFLICT: Choose a valid decision and provide a reason for changes.';
    end if;
    if p_kind = 'timesheets' then
        select user_id into person from public.timesheets where id = p_id;
    elsif p_kind = 'leave_requests' then
        select user_id into person from public.leave_requests where id = p_id;
    else
        raise exception 'WORKFLOW_CONFLICT: Unknown document type.';
    end if;
    if person is null then return null; end if;
    perform 1 from public.users where id = person for update;
    if p_kind = 'timesheets' then
        select to_jsonb(t) into saved from public.timesheets t where id = p_id for update;
    else
        select to_jsonb(l) into saved from public.leave_requests l where id = p_id for update;
    end if;
    if saved is null then return null; end if;
    if saved->>'status' <> required_status or (p_expected_submitted_at is not null
        and saved->>'submitted_at' <> p_expected_submitted_at) then
        raise exception 'WORKFLOW_CONFLICT: This request changed after you opened it. Refresh before reviewing it.';
    end if;
    if p_kind = 'leave_requests' and p_status = 'accepted' then
        perform public.assert_leave_reservation(person, (saved->>'start_date')::date,
            (saved->>'end_date')::date, saved->>'leave_type', p_holidays, p_id);
    end if;
    if p_kind = 'timesheets' then
        update public.timesheets set status = p_status, review_comment = trim(p_comment),
            reviewed_at = p_reviewed_at where id = p_id returning to_jsonb(timesheets) into saved;
    else
        update public.leave_requests set status = p_status, review_comment = trim(p_comment),
            reviewed_at = p_reviewed_at where id = p_id returning to_jsonb(leave_requests) into saved;
    end if;
    insert into public.workflow_events(kind, document_id, user_id, actor_user_id, action, status, comment, created_at)
    values(p_kind, p_id, person, p_actor_user_id, case when p_reopen then 'reopened' else 'reviewed' end,
        p_status, trim(p_comment), p_reviewed_at);
    return saved;
end;
$$;
