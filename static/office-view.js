/* Voluntary office plans: own-day writes, retained time drafts and a shared week. */
window.PeopleViews = window.PeopleViews || {};
window.PeopleViews.office = function(root, person) {
  'use strict';
  const query = selector => root.querySelector(selector);
  let data = JSON.parse(query('#office-data').textContent), disposed = false, firstActivation = true;
  let storage; try { storage = sessionStorage; } catch {}
  const drafts = WorkspaceDrafts.create(storage), pending = new Set();
  const form = query('#officeHoursForm'), editor = query('[data-office-editor]');
  const weekend = query('[data-office-weekends]'), feedback = query('[data-office-feedback]');
  const editorKey = 'urchin:office-editor:' + person + ':' + data.week_start;
  let editing = null, undo = null, revision = 0;
  const label = date => data.days.find(day => day.date === date)?.label || date;
  const row = date => data.my_plans[date];
  const text = (tag, className, value) => {
    const element = document.createElement(tag); element.className = className;
    if (value !== undefined) element.textContent = value; return element;
  };
  function message(value, error = false) {
    feedback.textContent = value; feedback.parentElement.classList.toggle('has-error', error);
    query('[data-office-refresh]').hidden = !error;
  }
  function capture() { if (editing) drafts.capture(form, person); }
  function closeEditor() { capture(); editor.hidden = true; try { storage?.removeItem(editorKey); } catch {} }
  function openEditor(date, focus = true) {
    if (!row(date)?.planned || pending.has(date) || (editing && pending.has(editing))) return;
    capture(); editing = date; editor.hidden = false;
    delete form.dataset.dirty; form.querySelector('[data-draft-status]')?.remove();
    form.dataset.draftId = 'office-hours:' + date;
    form.elements.work_date.value = date;
    form.elements.expected_updated_at.value = row(date).updated_at;
    form.elements.start_time.value = row(date).start_time || '';
    form.elements.end_time.value = row(date).end_time || '';
    query('[data-office-editor-title]').textContent = 'Times for ' + label(date);
    query('[data-office-form-error]').hidden = true;
    drafts.restore(form, person);
    try { storage?.setItem(editorKey, date); } catch {}
    if (focus) { editor.scrollIntoView?.({block: 'nearest', behavior: 'smooth'}); form.elements.start_time.focus({preventScroll: true}); }
  }
  function render() {
    if (disposed) return;
    let count = 0;
    for (const day of data.days) {
      const plan = row(day.date), button = query('[data-office-date="' + day.date + '"]');
      const selected = Boolean(plan?.planned), edit = query('[data-office-hours="' + day.date + '"]');
      if (selected) count++;
      button.setAttribute('aria-pressed', String(selected));
      button.setAttribute('aria-busy', String(pending.has(day.date)));
      button.setAttribute('aria-label', (selected ? 'Remove office plan for ' : 'Plan office visit for ') + day.label);
      button.disabled = !data.person_id || pending.has(day.date);
      button.querySelector('.office-day-state').textContent = pending.has(day.date) ? 'Saving…' : selected ? '✓ In office' : 'Choose day';
      edit.hidden = !selected; edit.disabled = pending.has(day.date) || Boolean(editing && pending.has(editing));
      edit.replaceChildren();
      if (plan?.start_time) edit.append(text('span', '', plan.start_time + '–'), text('span', '', plan.end_time));
      else edit.textContent = 'Add time';
      edit.setAttribute('aria-label', (plan?.start_time ? 'Edit times for ' : 'Add times for ') + day.label);
    }
    query('[data-office-summary]').textContent = count ? count + ' planned ' + (count === 1 ? 'day' : 'days') : 'Your plans, your choice';
    const weekendPlans = data.days.filter(day => day.weekend).reduce((sum, day) => sum + day.people.length, 0);
    query('[data-office-weekend-count]').textContent = weekendPlans ? '(' + weekendPlans + ' ' + (weekendPlans === 1 ? 'plan' : 'plans') + ')' : '';
    query('.office-my-days').classList.toggle('with-weekend', weekend.checked);
    const board = query('[data-office-team]');
    const opened = new Set(Array.from(board.querySelectorAll('details[open]'), element => element.closest('[data-office-team-day]').dataset.officeTeamDay));
    board.replaceChildren(); board.classList.toggle('with-weekend', weekend.checked);
    for (const day of data.days) {
      query('[data-office-choice="' + day.date + '"]').hidden = day.weekend && !weekend.checked;
      const card = text('article', 'office-team-day' + (day.today ? ' is-today' : ''));
      card.dataset.officeTeamDay = day.date; card.hidden = day.weekend && !weekend.checked;
      const head = text('div', 'office-team-day-head'), title = text('h3', '', day.weekday + ' ' + day.day + ' ' + day.month);
      head.append(title, text('span', 'office-count', String(day.people.length))); card.append(head);
      if (!day.people.length) card.append(text('p', 'office-empty', 'No plans yet'));
      const list = text('div', 'office-people'); let extra;
      day.people.forEach((plan, index) => {
        const entry = text('div', 'office-person'), avatar = text('span', 'office-avatar', plan.full_name.trim()[0] || '?');
        const description = text('div', ''); description.append(text('strong', '', plan.full_name),
          text('small', '', plan.start_time ? plan.start_time + '–' + plan.end_time : 'Time not specified'));
        entry.append(avatar, description);
        if (index < 5) list.append(entry);
        else {
          if (!extra) { extra = document.createElement('details'); extra.open = opened.has(day.date); extra.append(text('summary', '', 'Show ' + (day.people.length - 5) + ' more')); }
          extra.append(entry);
        }
      });
      card.append(list); if (extra) card.append(extra); board.append(card);
    }
    const disabled = editing && pending.has(editing);
    form.querySelectorAll('button,input[type=time]').forEach(control => { control.disabled = Boolean(disabled); });
  }
  async function save(date, planned, start = '', end = '', expectedVersion) {
    if (disposed || pending.has(date)) return false;
    const before = row(date) ? {...row(date)} : null;
    const body = new FormData();
    Object.entries({_csrf_token: form.elements._csrf_token.value, work_date: date, planned: planned ? '1' : '0',
      start_time: start, end_time: end, expected_updated_at: expectedVersion ?? before?.updated_at ?? '', expected_person_id: data.person_id}).forEach(([key, value]) => body.set(key, String(value)));
    const previousUndo = undo, requestRevision = ++revision;
    pending.add(date); undo = null; query('[data-office-undo]').hidden = true;
    message('Saving ' + label(date) + '…'); render();
    try {
      const response = await PeopleWorkspace.request('/office/plans', {method: 'POST', body});
      const result = await response.json().catch(() => null);
      if (!response.ok || !result?.ok) throw new Error(result?.error || 'Could not confirm this change. Your previous plan is kept.');
      if (disposed) return false;
      if (String(result.plan.user_id) !== String(data.person_id)) throw new Error('The selected employee changed. Refresh this week.');
      data.my_plans[date] = result.plan;
      PeopleWorkspace.invalidateScreens?.(['/', '/workspace']);
      if (editing === date) form.elements.expected_updated_at.value = result.plan.updated_at;
      const day = data.days.find(day => day.date === date);
      day.people = day.people.filter(plan => String(plan.user_id) !== String(data.person_id));
      if (result.plan.planned) day.people.push({...result.plan, full_name: data.person_name});
      day.people.sort((a, b) => a.full_name.localeCompare(b.full_name));
      if (!planned && before?.planned) { undo = {date, plan: before}; query('[data-office-undo]').hidden = false; }
      if (!planned && editing === date) closeEditor();
      message(label(date) + ' · saved');
      return true;
    } catch (error) {
      if (!disposed) {
        if (previousUndo && requestRevision === revision) { undo = previousUndo; query('[data-office-undo]').hidden = false; }
        message(error.message, true);
        if (editing === date && !editor.hidden) { const note = query('[data-office-form-error]'); note.textContent = error.message; note.hidden = false; capture(); }
      }
      return false;
    } finally { pending.delete(date); render(); }
  }
  async function refresh({rebaseDraft = false} = {}) {
    if (pending.size || disposed) return;
    const version = ++revision; capture();
    try {
      const response = await PeopleWorkspace.request('/office/data?week=' + data.week_start);
      if (!response.ok) throw new Error('Could not refresh this week. Your current plans and drafts are kept.');
      const fresh = await response.json();
      if (disposed || version !== revision) return;
      if (String(fresh.person_id) !== String(data.person_id)) throw new Error('The selected employee changed. Reload the workspace before editing.');
      data = fresh; render(); PeopleWorkspace.invalidateScreens?.(['/', '/workspace']);
      if (editing && row(editing)?.planned) {
        if (rebaseDraft) form.elements.expected_updated_at.value = row(editing).updated_at;
        if (form.dataset.dirty !== 'true') {
          form.elements.expected_updated_at.value = row(editing).updated_at;
          form.elements.start_time.value = row(editing).start_time || '';
          form.elements.end_time.value = row(editing).end_time || '';
        }
      }
      if (editing && !row(editing)?.planned) closeEditor();
      if (rebaseDraft) query('[data-office-form-error]').hidden = true;
      message(rebaseDraft && form.dataset.dirty === 'true' ? 'Week refreshed. Your draft times are kept; review them before saving.' : 'Week refreshed.');
    } catch (error) { if (!disposed && version === revision) message(error.message, true); }
  }
  root.addEventListener('click', event => {
    const toggle = event.target.closest('[data-office-date]');
    if (toggle) { event.preventDefault(); const date = toggle.dataset.officeDate; save(date, !row(date)?.planned); }
    const edit = event.target.closest('[data-office-hours]');
    if (edit) { event.preventDefault(); openEditor(edit.dataset.officeHours); }
  });
  weekend.addEventListener('change', render);
  query('[data-office-close]').addEventListener('click', closeEditor);
  query('[data-office-refresh]').addEventListener('click', () => refresh({rebaseDraft: true}));
  query('[data-office-undo]').addEventListener('click', () => { if (undo) { const previous = undo; save(previous.date, true, previous.plan.start_time || '', previous.plan.end_time || ''); } });
  form.addEventListener('input', () => { form.dataset.dirty = 'true'; drafts.capture(form, person); query('[data-office-form-error]').hidden = true; });
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (!editing || !form.reportValidity()) return;
    const start = form.elements.start_time.value, end = form.elements.end_time.value;
    if (Boolean(start) !== Boolean(end) || (start && start >= end)) {
      const error = query('[data-office-form-error]'); error.textContent = 'Choose both times, with departure after arrival, or leave both empty.'; error.hidden = false; return;
    }
    if (await save(editing, true, start, end, form.elements.expected_updated_at.value)) drafts.clear(form, person);
  });
  query('[data-office-clear-times]').addEventListener('click', async () => {
    if (!editing) return;
    if (await save(editing, true, '', '', form.elements.expected_updated_at.value)) { form.elements.start_time.value = ''; form.elements.end_time.value = ''; drafts.clear(form, person); }
  });
  render();
  try { const last = storage?.getItem(editorKey); if (last && row(last)?.planned) openEditor(last, false); } catch {}
  return {activate() { if (firstActivation) firstActivation = false; else refresh(); }, pause: capture,
    destroy() { disposed = true; revision++; capture(); }};
};
