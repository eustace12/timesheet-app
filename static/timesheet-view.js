/* Retained monthly editor: fields, preview and calendar state survive navigation. */
window.PeopleViews = window.PeopleViews || {};
window.PeopleViews.timesheet = function(root, person) {
'use strict';
const byId = id => root.querySelector('#' + id);
let disposed = false, documentPending = false;
/* ── State ── */
let pickers          = null;
let holidaysCache    = {};
const STORAGE_KEY = WorkspaceCore.draftKey(person);
const LAST_PERIOD_KEY = 'timesheetLastPeriodV3:'+person;
let periodCursor=null,periodInfo=null,periodRevision=0,initialized=false;
let viewActive=false,backgroundTimer=null,backgroundRequest=null;
const isPeriodLocked=()=>Boolean(periodInfo?.timesheet&&['submitted','accepted'].includes(periodInfo.timesheet.status));
let activeCalType    = null;  // 'sick' | 'custom'
let suppressCalClose = false; // skip onCalClose during programmatic clear()
let vacationPeriods  = [];    // [{start: Number, end: Number}]
let sickPeriods      = [];

const UI = JSON.parse(root.querySelector('#timesheet-copy').textContent);

const SVG = {
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
};

function getSelectedFormat() {
    const checked = root.querySelector('input[name="format"]:checked');
    return checked ? checked.value : 'pdf';
}

function updateGenerateLabel() {
    byId('generateBtnLabel').textContent = UI.downloadCopy;
}

function isDownloadResponse(ct) {
    return ct.includes('application/pdf')
        || ct.includes('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
}

/* ── Helpers ── */
function getYM() {
    return {
        year:  parseInt(byId('year').value),
        month: parseInt(byId('month').value),
    };
}

function formatUi(template, values) {
    return template.replace(/\{(\w+)\}/g, (_, key) => String(values[key] ?? ''));
}

function setResult(result, type, heading, message) {
    const title = document.createElement('h3');
    const body = document.createElement('p');
    title.textContent = heading;
    body.textContent = message;
    result.className = `result ${type}`;
    result.replaceChildren(title, body);
}

function setHint(hint, type, label, message) {
    const labelNode = document.createElement('span');
    labelNode.className = 'hint-label';
    labelNode.textContent = `${label}:`;
    hint.className = `hint ${type}`;
    hint.replaceChildren(labelNode, document.createTextNode(` ${message}`));
    hint.style.display = 'block';
}

function holidayCacheKey(year) {
    return String(year);
}

function getHolidayList(year) {
    return holidaysCache[holidayCacheKey(year)] || [];
}

async function loadHolidays(year, force = false) {
    const key = holidayCacheKey(year);
    if (!force && holidaysCache[key] !== undefined) return holidaysCache[key];
    try {
        const r = await window.PeopleWorkspace.request(`/holidays/${year}`);
        const d = await r.json();
        const list = Array.isArray(d) ? d : (d.holidays || []);
        holidaysCache[key] = list;
        return list;
    } catch (e) {
        console.error('Holidays fetch failed:', e);
        holidaysCache[key] = [];
        return [];
    }
}

function periodsFromString(str) {
    if (!str) return [];
    return str.split(',').map(part => {
        const t = part.trim();
        if (!t) return null;
        if (t.includes('-')) {
            const [a, b] = t.split('-').map(Number);
            return { start: a, end: b };
        }
        const n = parseInt(t, 10);
        return { start: n, end: n };
    }).filter(Boolean);
}

function readStored(key){try{return JSON.parse(localStorage.getItem(key)||'null');}catch{return null;}}
function validPeriod(value){return value&&Number.isInteger(Number(value.year))&&Number(value.year)>=2020&&Number(value.year)<=2099&&Number(value.month)>=1&&Number(value.month)<=12;}
function loadFormState(){
  if(root.dataset.periodExplicit==='true')return;
  const last=readStored(LAST_PERIOD_KEY)||readStored(STORAGE_KEY);
  if(validPeriod(last)){byId('year').value=last.year;byId('month').value=last.month;}
}
function periodDraft(target){
  const saved=readStored(WorkspaceCore.periodDraftKey(person,target.year,target.month));
  if(saved)return saved;
  const legacy=readStored(STORAGE_KEY);
  return legacy&&Number(legacy.year)===target.year&&Number(legacy.month)===target.month?legacy:null;
}
function saveFormState(target=periodCursor||getYM(),force=false){
  if(!force&&root.dataset.viewReady==='loading')return;
  try{
    // Annual leave belongs to approved requests on the server, never to the draft.
    const state={year:target.year,month:target.month,custom_reason:byId('custom_reason').value,
      format:getSelectedFormat(),
      sick:byId('sick').getAttribute('data-value')||'',custom_days:byId('custom_days').getAttribute('data-value')||''};
    localStorage.setItem(WorkspaceCore.periodDraftKey(person,target.year,target.month),JSON.stringify(state));
    localStorage.setItem(LAST_PERIOD_KEY,JSON.stringify({year:target.year,month:target.month}));
  }catch{/* The current editor also retains unsaved values in memory. */}
}
function applyPeriodValues(target){
  const saved=periodInfo?.timesheet;
  const state=(isPeriodLocked()?saved:periodDraft(target)||saved)||{};
  vacationPeriods=periodsFromString(isPeriodLocked()?state.vacation||'':approvedVacationRanges());
  sickPeriods=periodsFromString(state.sick||'');
  syncPeriodInput('vacation');syncPeriodInput('sick');
  setDateInput('custom_days',state.custom_days||'');
  byId('custom_reason').value=state.custom_reason||'';
  byId('hours_per_day').value=periodInfo?.hours_per_day||8;
  const format=['pdf','xlsx'].includes(state.format)?state.format:'pdf';
  root.querySelector('input[name="format"][value="'+format+'"]').checked=true;
  renderPeriodBadges('vacation');renderPeriodBadges('sick');
  updateDisplay('custom',Array.from(parseDaySet(state.custom_days||''),day=>new Date(target.year,target.month-1,day)));updateGenerateLabel();
}
function renderPeriodStatus(){
  const banner=byId('period-status');if(!banner)return;
  const sheet=periodInfo?.timesheet,locked=isPeriodLocked();
  byId('submitBtn').disabled=locked||Boolean(periodInfo?.unavailable)||person==='none';
  byId('submitBtn').textContent=locked?(sheet.status==='accepted'?'Approved':'Awaiting review'):UI.submitLabel;
  byId('submitBtn').setAttribute('aria-label',byId('submitBtn').textContent);
  root.querySelectorAll('.timesheet-absences button:not([data-step]):not([data-request-leave])').forEach(button=>{button.disabled=locked;});
  byId('custom_reason').readOnly=locked;
  root.querySelectorAll('input[name=format]').forEach(input=>{input.disabled=locked;});
  byId('previewBtn').disabled=Boolean(periodInfo?.unavailable)||(locked&&sheet.format!=='pdf');
  byId('generateBtn').disabled=Boolean(periodInfo?.unavailable);
  byId('previewBtn').title=locked&&sheet.format!=='pdf'?'Download the saved Excel file to review this timesheet.':'Preview the PDF document';
  byId('generateBtnLabel').textContent=locked?'Download submitted '+sheet.format.toUpperCase():UI.downloadCopy;
  banner.replaceChildren();banner.hidden=!sheet&&!periodInfo?.unavailable;
  if(banner.hidden)return;
  const label=document.createElement('strong');
  label.textContent=periodInfo?.unavailable?'Review status unavailable':sheet.status==='accepted'?'Approved and saved':sheet.status==='submitted'?'Your timesheet is awaiting review':'Changes requested';
  const text=document.createElement('p');
  text.textContent=periodInfo?.unavailable?'Your draft is kept. Check the status before submitting.':sheet.review_comment||(locked?'The submitted document is saved. The accountant can return it if changes are needed.':'Update this month and send it to the accountant again.');
  const description=document.createElement('div');description.append(label,text);banner.append(description);
  if(sheet){const link=document.createElement('a');link.className='button';link.href='/history/timesheets/'+sheet.id+'/download';link.textContent='Download submitted '+(sheet.format||'pdf').toUpperCase();banner.append(link);}
  else{const retry=document.createElement('button');retry.type='button';retry.className='button';retry.textContent='Retry';retry.addEventListener('click',refreshPeriod);banner.append(retry);}
  banner.dataset.status=sheet?.status||'unavailable';
}
async function getPeriodInfo(target){
  try{
    const response=await window.PeopleWorkspace.request('/timesheets/period?year='+target.year+'&month='+target.month);
    if(!response.ok)throw new Error();return await response.json();
  }catch{return {unavailable:true,timesheet:null,approved_vacation:''};}
}
async function refreshPeriod({background=false}={}){
  const target=getYM(),version=++periodRevision;
  const info=await getPeriodInfo(target);
  if(disposed||version!==periodRevision)return;
  if(background&&(!viewActive||document.hidden||documentPending||root.dataset.viewReady==='loading'))return;
  if(info.unavailable){periodInfo={...periodInfo,unavailable:true};renderPeriodStatus();return;}
  periodInfo=info;
  if(isPeriodLocked()){
    applyPeriodValues(target);await updateStats();
  }else{
    vacationPeriods=periodsFromString(approvedVacationRanges());syncPeriodInput('vacation');renderPeriodBadges('vacation');await updateStats();
  }
  renderPeriodStatus();
}
function stopBackgroundRefresh(){
  if(backgroundTimer!==null)window.clearInterval(backgroundTimer);
  backgroundTimer=null;
}
function refreshVisiblePeriod(){
  if(!viewActive||disposed||document.hidden||!initialized||documentPending||root.dataset.viewReady==='loading')return;
  if(!backgroundRequest){
    backgroundRequest=refreshPeriod({background:true}).catch(()=>{
      if(!disposed){periodInfo={...periodInfo,unavailable:true};renderPeriodStatus();}
    }).finally(()=>{backgroundRequest=null;});
  }
  return backgroundRequest;
}
function watchVisiblePeriod(){
  stopBackgroundRefresh();
  if(viewActive&&!disposed&&!document.hidden)backgroundTimer=window.setInterval(refreshVisiblePeriod,30000);
}
function visibilityChanged(){watchVisiblePeriod();if(!document.hidden)refreshVisiblePeriod();}
window.addEventListener('focus',refreshVisiblePeriod);
document.addEventListener('visibilitychange',visibilityChanged);

function getHolidayName(day, month, year) {
    const list = getHolidayList(year);
    const h = list.find(h => h.month === month && h.day === day);
    return h ? h.name : null;
}

/* ── Calendar backdrop ── */
function openPeriodPicker(fp) {
    if (fp.selectedDates.length) {
        suppressCalClose = true;
        fp.clear(false);
        queueMicrotask(() => { suppressCalClose = false; });
    }
    fp.open();
}

function openCalendar(type) {
    if(isPeriodLocked())return;
    byId('calBackdrop').classList.add('active');
    if (!pickers) {
        activeCalType = type;
        return;
    }
    activeCalType = type;
    if (type === 'sick') {
        openPeriodPicker(pickers.sickPicker);
    } else if (type === 'custom') {
        pickers.customPicker.open();
    }
}

function closeCalendar() {
    byId('calBackdrop').classList.remove('active');
    if (pickers) {
        if (pickers.sickPicker.isOpen)     pickers.sickPicker.close();
        if (pickers.customPicker.isOpen)   pickers.customPicker.close();
    }
    activeCalType = null;
}

function onCalClose() {
    if (suppressCalClose) return;
    byId('calBackdrop').classList.remove('active');
    activeCalType = null;
}

/* ── onDayCreate: highlight holidays ── */
function onDayCreate(dObj, dStr, fp, dayElem) {
    const d = dayElem.dateObj;
    if (!d) return;
    if (dayElem.classList.contains('prevMonthDay') || dayElem.classList.contains('nextMonthDay')) return;
    const name = getHolidayName(d.getDate(), d.getMonth() + 1, d.getFullYear());
    if (name) {
        dayElem.classList.add('is-holiday');
        dayElem.setAttribute('title', name);
    }
}

/* ── Replace editable month/year controls with static labels ── */
function staticizeMonthYearHeader(instance) {
    const { year, month } = getYM();
    const monthNav = instance.calendarContainer.querySelector('.flatpickr-current-month');
    if (!monthNav) return;

    const monthDropdown = monthNav.querySelector('.flatpickr-monthDropdown-months');
    if (monthDropdown) {
        const monthName = new Date(year, month - 1, 1).toLocaleString(UI.locale, { month: 'long' });
        const span = document.createElement('span');
        span.className = 'cur-month';
        span.textContent = monthName;
        monthDropdown.replaceWith(span);
    }

    const yearWrapper = monthNav.querySelector('.numInputWrapper');
    if (yearWrapper && !monthNav.querySelector('.cur-year-static')) {
        const span = document.createElement('span');
        span.className = 'cur-year-static';
        span.textContent = String(year);
        yearWrapper.replaceWith(span);
    }
}

/* ── onReady: close btn + holiday legend + optional done btn ── */
function makeOnReady(isMultiple) {
    return function(selectedDates, dateStr, instance) {
        instance.prevMonthNav.style.display = 'none';
        instance.nextMonthNav.style.display = 'none';

        staticizeMonthYearHeader(instance);

        const monthNav = instance.calendarContainer.querySelector('.flatpickr-current-month');
        if (monthNav) { monthNav.style.pointerEvents = 'none'; monthNav.style.cursor = 'default'; }

        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.setAttribute('aria-label','Close calendar');
        closeBtn.className = 'cal-close';
        closeBtn.innerHTML = SVG.close;
        closeBtn.addEventListener('click', e => { e.stopPropagation(); closeCalendar(); });
        instance.calendarContainer.querySelector('.flatpickr-months').appendChild(closeBtn);

        const { year, month } = getYM();
        const list = getHolidayList(year) || [];
        const monthHols = list.filter(h => h.month === month);
        if (monthHols.length) {
            const strip = document.createElement('div');
            strip.className = 'cal-holiday-strip visible';
            const names = [...new Set(monthHols.map(h => h.name))];
            strip.textContent = names.join(' · ');
            instance.calendarContainer.appendChild(strip);
        }

        if (isMultiple) {
            const footer = document.createElement('div');
            footer.className = 'cal-footer';
            const doneButton = document.createElement('button');
            doneButton.type = 'button';
            doneButton.className = 'cal-done';
            doneButton.textContent = UI.done;
            doneButton.addEventListener('click', closeCalendar);
            footer.appendChild(doneButton);
            instance.calendarContainer.appendChild(footer);
        }
    };
}

/* ── Period picker onChange (type bound at init — no activeCalType race) ── */
function makePeriodOnChange(type) {
    return function(dates, dateStr, instance) {
        if (dates.length !== 2) return;
        const start = dates[0].getDate();
        const end   = dates[1].getDate();
        sickPeriods.push({ start, end });
        renderPeriodBadges('sick');
        syncPeriodInput('sick');
        setTimeout(() => {
            suppressCalClose = true;
            instance.clear(false);
            closeCalendar();
            updateStats();
            queueMicrotask(() => { suppressCalClose = false; });
        }, 150);
    };
}

/* ── Calendar init ── */
function initCalendars() {
    const { year, month } = getYM();
    const minDate = new Date(year, month - 1, 1);
    const maxDate = new Date(year, month,     0);

    const base = {
        minDate,
        maxDate,
        dateFormat: 'd M',
        locale: {firstDayOfWeek:1},
        clickOpens: false,
        allowInput: false,
        monthSelectorType: 'static',
        onDayCreate,
        onClose: onCalClose,
    };

    const rangeBase = {
        ...base,
        mode: 'range',
        onReady: makeOnReady(false),
    };

    const sickPicker = flatpickr(byId('sickPickerInput'), {
        ...rangeBase,
        onChange: makePeriodOnChange('sick'),
    });

    const customPicker = flatpickr(byId('custom_days'), {
        ...base,
        mode: 'multiple',
        conjunction: ', ',
        onChange(dates) {
            setDateInput('custom_days', fmtDates(dates));
            updateDisplay('custom', dates);
            updateStats();
        },
        onReady: makeOnReady(true),
    });

    return { sickPicker, customPicker };
}

/* ── Period badge rendering ── */
function renderPeriodBadges(type) {
    const { year, month } = getYM();
    const mName   = new Date(year, month - 1, 1).toLocaleString(UI.locale, { month: 'short' });
    const periods = type === 'vacation' ? vacationPeriods : sickPeriods;
    const divId   = type === 'vacation' ? 'vacationDisplay' : 'sickDisplay';
    const div     = byId(divId);

    const approved=type==='vacation'?parseDaySet(periodInfo?.approved_vacation||''):new Set();
    div.innerHTML = periods.map((p, i) => {
        const fixed=Array.from({length:p.end-p.start+1},(_,n)=>p.start+n).filter(day=>approved.has(day)).length;
        const fullyApproved=fixed===p.end-p.start+1;
        const days  = p.end - p.start + 1;
        const label = p.start === p.end
            ? `${mName} ${p.start}`
            : `${mName} ${p.start}–${p.end} (${days} calendar days)`;
        return `<div class="date-badge ${type}${fullyApproved?' approved-period':''}">
            <span class="badge-dot"></span>
            <span>${label}</span>
            ${fixed?'<small>'+(fullyApproved?'Approved':'Includes approved dates')+'</small>':''}
            ${fullyApproved?'':`<button type="button" class="badge-remove" aria-label="Remove ${fixed?'optional vacation dates':type+' period'}" onclick="removePeriod('${type}',${i})">${SVG.close}</button>`}
        </div>`;
    }).join('');
}

function removePeriod(type, index) {
    if(isPeriodLocked())return;
    if (type !== 'sick') return;
    sickPeriods.splice(index, 1);
    renderPeriodBadges('sick');
    syncPeriodInput('sick');
    updateStats();
}

function syncPeriodInput(type) {
    if(type==='vacation'&&!isPeriodLocked()){
        vacationPeriods=periodsFromString(approvedVacationRanges());renderPeriodBadges('vacation');
    }
    const periods = type === 'vacation' ? vacationPeriods : sickPeriods;
    const id      = type === 'vacation' ? 'vacation' : 'sick';
    const value   = periods.map(p => p.start === p.end ? String(p.start) : `${p.start}-${p.end}`).join(',');
    setDateInput(id, value);
}

/* ── Clear functions ── */
function approvedVacationRanges(){
    return daysToRanges(Array.from(parseDaySet(periodInfo?.approved_vacation||'')));
}
function clearSick() {
    if(isPeriodLocked())return;
    sickPeriods = [];
    setDateInput('sick', '');
    byId('sickDisplay').innerHTML = '';
    updateStats();
}
function clearCustom() {
    if(isPeriodLocked())return;
    pickers?.customPicker?.clear();
    setDateInput('custom_days', '');
    byId('customDisplay').innerHTML = '';
    updateStats();
}

/* ── setDateInput: keeps value + data-value in sync ── */
function setDateInput(id, value) {
    const el = byId(id);
    el.value = value;
    if (value) el.setAttribute('data-value', value);
    else el.removeAttribute('data-value');
}

/* ── fmtDates: for custom (multiple) picker ── */
function fmtDates(dates) {
    if (!dates || !dates.length) return '';
    const { year, month } = getYM();
    const days = dates
        .filter(d => d.getMonth() + 1 === month && d.getFullYear() === year)
        .map(d => d.getDate())
        .sort((a, b) => a - b);
    if (!days.length) return '';
    if (days.length === 1) return String(days[0]);
    let consecutive = true;
    for (let i = 1; i < days.length; i++) {
        if (days[i] !== days[i - 1] + 1) { consecutive = false; break; }
    }
    return consecutive ? `${days[0]}-${days[days.length - 1]}` : days.join(',');
}

/* ── updateDisplay: for custom type badge ── */
function updateDisplay(type, dates) {
    const { year, month } = getYM();
    const mName = new Date(year, month - 1, 1).toLocaleString(UI.locale, { month: 'short' });
    const ids   = { vacation: 'vacationDisplay', sick: 'sickDisplay', custom: 'customDisplay' };

    const div = byId(ids[type]);
    if (!dates || !dates.length) { div.innerHTML = ''; return; }

    const days = dates.map(d => d.getDate()).sort((a, b) => a - b);
    let text;
    if (days.length === 1) {
        text = `${mName} ${days[0]}`;
    } else {
        const isRange = days[days.length - 1] - days[0] === days.length - 1;
        text = isRange
            ? `${mName} ${days[0]}–${days[days.length - 1]} (${days.length} ${UI.dayShort})`
            : `${days.length} ${UI.dayShort}: ${days.join(', ')}`;
    }

    div.innerHTML = `<div class="date-badge ${type}">
        <span class="badge-dot"></span>
        <span>${text}</span>
        <button type="button" class="badge-remove" aria-label="Remove other absence" onclick="clearCustom()">${SVG.close}</button>
    </div>`;
}

/* ── parseDaySet ── */
function parseDaySet(str) {
    const set = new Set();
    if (!str) return set;
    for (const part of str.split(',')) {
        const t = part.trim();
        if (t.includes('-')) {
            const [a, b] = t.split('-').map(Number);
            for (let i = a; i <= b; i++) set.add(i);
        } else {
            const n = parseInt(t);
            if (!isNaN(n)) set.add(n);
        }
    }
    return set;
}

/* ── Stats calculation ── */
function approvedOtherDays(){
    let snapshot=isPeriodLocked()?periodInfo.timesheet.leave_snapshot:periodInfo?.approved_absences;
    if(typeof snapshot==='string'){try{snapshot=JSON.parse(snapshot);}catch{snapshot=[];}}
    const {year,month}=getYM(),prefix=year+'-'+String(month).padStart(2,'0')+'-';
    const result=new Map();
    for(const row of Array.isArray(snapshot)?snapshot:[]){
        if(row.leave_type==='Vacation')continue;
        for(let day=1;day<=new Date(year,month,0).getDate();day++){
            const iso=prefix+String(day).padStart(2,'0');
            if(iso>=row.start_date&&iso<=row.end_date)result.set(day,row.leave_type==='Unpaid'?'Unpaid leave':'Other approved leave');
        }
    }
    return result;
}
function renderApprovedOther(){
    let area=byId('approvedOtherDisplay');
    if(!area){area=document.createElement('div');area.id='approvedOtherDisplay';area.className='selected-dates';byId('customDisplay').before(area);}
    area.replaceChildren();
    for(const [day,label] of approvedOtherDays()){
        const badge=document.createElement('div');badge.className='date-badge custom approved-period';
        const text=document.createElement('span');text.textContent=day+' · '+label;
        const status=document.createElement('small');status.textContent='Approved';badge.append(text,status);area.append(badge);
    }
}
async function calculateStats() {
    const { year, month } = getYM();
    const total = new Date(year, month, 0).getDate();

    await loadHolidays(year);
    if (disposed) return;
    const holDays = new Set(
        (getHolidayList(year) || []).filter(h => h.month === month).map(h => h.day)
    );

    const vacSet    = parseDaySet(byId('vacation').getAttribute('data-value') || '');
    const sickSet   = parseDaySet(byId('sick').getAttribute('data-value') || '');
    const customSet = parseDaySet(byId('custom_days').getAttribute('data-value') || '');
    for(const day of approvedOtherDays().keys())customSet.add(day);
    const overlap   = [...vacSet].filter(d => sickSet.has(d));

    let working = 0, vacDays = 0, sickDays = 0, customDays = 0;

    for (let d = 1; d <= total; d++) {
        const dow     = new Date(year, month - 1, d).getDay();
        const weekend = dow === 0 || dow === 6;
        const holiday = holDays.has(d);
        const vac     = vacSet.has(d);
        const sick    = sickSet.has(d);
        const custom  = customSet.has(d);

        if (vac)    vacDays++;
        if (sick)   sickDays++;
        if (custom) customDays++;
        if (!weekend && !holiday && !vac && !sick && !custom) working++;
    }

    return { total, working, holidays: holDays.size, vacDays, sickDays, customDays, hours: working * (Number(byId('hours_per_day').value)||8), overlap };
}

function renderMonthOverview(scheduledHours){
    const grid=byId('monthOverview');if(!grid)return;
    const {year,month}=getYM();grid.replaceChildren();
    root.querySelector('[data-month-overview-period]').textContent=new Date(year,month-1,1).toLocaleDateString('en-GB',{month:'long',year:'numeric'});
    const now=new Date(),today=root.querySelector('[data-step="today"]');if(today)today.hidden=year===now.getFullYear()&&month===now.getMonth()+1;
    if(isPeriodLocked()&&Math.abs(scheduledHours-periodInfo.timesheet.total_hours)>.001){
        const note=document.createElement('p');note.className='saved-schedule-note';note.textContent='This saved timesheet uses recorded hours. Open the submitted document to see the exact daily breakdown.';grid.append(note);return;
    }
    const vacation=parseDaySet(byId('vacation').getAttribute('data-value')||''),sick=parseDaySet(byId('sick').getAttribute('data-value')||''),custom=parseDaySet(byId('custom_days').getAttribute('data-value')||'');
    const approvedOther=approvedOtherDays();for(const day of approvedOther.keys())custom.add(day);
    for(const label of ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']){const node=document.createElement('span');node.className='month-weekday';node.textContent=label;grid.append(node);}
    const offset=(new Date(year,month-1,1).getDay()+6)%7;
    for(let n=0;n<offset;n++){const blank=document.createElement('span');blank.setAttribute('aria-hidden','true');grid.append(blank);}
    for(let day=1;day<=new Date(year,month,0).getDate();day++){
        const date=new Date(year,month-1,day),holiday=getHolidayName(day,month,year),weekend=[0,6].includes(date.getDay());
        const kind=vacation.has(day)?'leave':sick.has(day)?'sick':custom.has(day)?'other':holiday?'holiday':weekend?'weekend':'work';
        const text=approvedOther.get(day)||(kind==='work'?(Number(byId('hours_per_day').value)||8)+'h':{leave:'Leave',sick:'Sick',other:'Other',holiday:'Holiday',weekend:'—'}[kind]);
        const cell=document.createElement('div');cell.className='month-day '+kind;cell.setAttribute('role','listitem');cell.title=holiday||text;cell.setAttribute('aria-label',date.toLocaleDateString('en-GB',{day:'numeric',month:'long',weekday:'long'})+' · '+(holiday&&kind==='holiday'?holiday:text));
        const number=document.createElement('strong');number.textContent=day;const label=document.createElement('small');label.textContent=text;cell.append(number,label);
        if(!isPeriodLocked()&&(kind==='work'||kind==='sick'||(kind==='other'&&!approvedOther.has(day)))){
            const pick=document.createElement('button');pick.type='button';pick.className='month-day-pick';
            pick.setAttribute('aria-haspopup','menu');pick.dataset.day=day;pick.dataset.kind=kind;pick.setAttribute('aria-label','Change '+cell.getAttribute('aria-label'));
            pick.addEventListener('click',()=>openDayMenu(day,kind,pick));cell.classList.add('editable');cell.append(pick);
        }
        grid.append(cell);
    }
}

/* ── Day menu: mark a day straight from the month grid ── */
let dayMenu=null;
function dayDate(day){const {year,month}=getYM();return new Date(year,month-1,day);}
function closeDayMenu({restoreFocus=false}={}){
    if(!dayMenu)return;const anchor=dayMenu.anchor;dayMenu.remove();dayMenu=null;
    document.removeEventListener('pointerdown',dayMenuOutside,true);window.removeEventListener('resize',dismissDayMenu);window.removeEventListener('scroll',positionDayMenu,true);
    if(restoreFocus&&anchor?.isConnected)anchor.focus();
}
function dismissDayMenu(){closeDayMenu();}
function positionDayMenu(){
    if(!dayMenu)return;
    // The grid re-renders on refresh; follow the current cell for the same day.
    if(!dayMenu.anchor.isConnected){const next=root.querySelector('.month-day-pick[data-day="'+dayMenu.day+'"]');if(!next){closeDayMenu();return;}dayMenu.anchor=next;}
    const rect=dayMenu.anchor.getBoundingClientRect(),width=dayMenu.offsetWidth,height=dayMenu.offsetHeight;
    dayMenu.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+'px';
    dayMenu.style.top=(rect.bottom+6+height>window.innerHeight?Math.max(8,rect.top-height-6):rect.bottom+6)+'px';
}
function dayMenuOutside(event){if(dayMenu&&!dayMenu.contains(event.target)&&event.target!==dayMenu.anchor)closeDayMenu();}
function setDayKind(day,kind){
    if(isPeriodLocked())return;
    const sick=parseDaySet(byId('sick').getAttribute('data-value')||''),custom=parseDaySet(byId('custom_days').getAttribute('data-value')||'');
    sick.delete(day);custom.delete(day);
    if(kind==='sick')sick.add(day);
    if(kind==='other')custom.add(day);
    sickPeriods=periodsFromString(daysToRanges(Array.from(sick)));syncPeriodInput('sick');renderPeriodBadges('sick');
    const dates=Array.from(custom).sort((a,b)=>a-b).map(dayDate);
    pickers?.customPicker?.setDate(dates,false);setDateInput('custom_days',fmtDates(dates));updateDisplay('custom',dates);
    saveFormState();updateStats();
    if(kind==='other'&&!byId('custom_reason').value.trim()){
        const editor=root.querySelector('.absence-editor');if(editor)editor.open=true;
        const reason=byId('custom_reason');reason.focus({preventScroll:true});reason.scrollIntoView?.({block:'center'});
    }
}
function openDayMenu(day,kind,anchor){
    if(dayMenu?.anchor===anchor){closeDayMenu({restoreFocus:true});return;}
    closeDayMenu();
    const date=dayDate(day),iso=date.getFullYear()+'-'+String(date.getMonth()+1).padStart(2,'0')+'-'+String(day).padStart(2,'0');
    const menu=document.createElement('div');menu.className='day-menu';menu.setAttribute('role','menu');menu.anchor=anchor;menu.day=day;menu.kind=kind;
    const heading=document.createElement('p');heading.className='day-menu-title';heading.textContent=date.toLocaleDateString('en-GB',{weekday:'long',day:'numeric',month:'long'});
    menu.setAttribute('aria-label',heading.textContent);menu.append(heading);
    const choices=[['work','Working day'],['sick','Sick day'],['other','Other absence']].filter(([value])=>value!==kind);
    for(const [value,label] of choices){
        const item=document.createElement('button');item.type='button';item.setAttribute('role','menuitem');item.className='day-menu-item '+value;item.textContent=label;
        item.addEventListener('click',()=>{closeDayMenu({restoreFocus:value!=='other'});setDayKind(day,value);});menu.append(item);
    }
    const leave=document.createElement('button');leave.type='button';leave.setAttribute('role','menuitem');leave.className='day-menu-item leave';leave.textContent='Request leave…';
    leave.dataset.requestLeave='';leave.dataset.leaveCopy=JSON.stringify({start_date:iso,end_date:iso,leave_type:'Vacation'});
    leave.addEventListener('click',()=>setTimeout(()=>closeDayMenu()));menu.append(leave);
    menu.addEventListener('keydown',event=>{
        const items=Array.from(menu.querySelectorAll('[role=menuitem]')),index=items.indexOf(document.activeElement);
        if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeDayMenu({restoreFocus:true});}
        else if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();items[index<0?(event.key==='ArrowDown'?0:items.length-1):(index+(event.key==='ArrowDown'?1:items.length-1))%items.length].focus();}
        else if(event.key==='Tab')closeDayMenu();
    });
    root.append(menu);dayMenu=menu;positionDayMenu();
    document.addEventListener('pointerdown',dayMenuOutside,true);window.addEventListener('resize',dismissDayMenu);window.addEventListener('scroll',positionDayMenu,true);
    menu.querySelector('[role=menuitem]').focus();
}

async function updateStats() {
    const s = await calculateStats();
    if(disposed||!s)return;

    const scheduledHours=s.hours;
    if(isPeriodLocked()){s.hours=periodInfo.timesheet.total_hours;s.working=periodInfo.timesheet.working_days??s.working;}
    renderMonthOverview(scheduledHours);
    if(dayMenu){const cell=root.querySelector('.month-day-pick[data-day="'+dayMenu.day+'"]');if(!cell||cell.dataset.kind!==dayMenu.kind)closeDayMenu();else{dayMenu.anchor=cell;positionDayMenu();}}
    byId('statTotalDays').textContent     = s.total;
    byId('statWorkingDays').textContent   = s.working;
    byId('statHolidays').textContent      = s.holidays;
    byId('statExpectedHours').textContent = `${s.hours} ${UI.hours}`;

    byId('statVacation').textContent = s.vacDays ? `${s.vacDays} calendar ${s.vacDays===1?'day':'days'}` : '—';
    byId('statSick').textContent     = s.sickDays ? `${s.sickDays} calendar ${s.sickDays===1?'day':'days'}` : '—';
    byId('statCustom').textContent   = s.customDays ? `${s.customDays} calendar ${s.customDays===1?'day':'days'}` : '—';

    byId('statsBox').style.display = 'block';

    const hasAbsences = s.vacDays + s.sickDays + s.customDays > 0;
    byId('absenceChips').style.display = hasAbsences ? 'flex' : 'none';

    const hint  = byId('validationHint');
    const total = s.vacDays + s.sickDays + s.customDays;

    if(isPeriodLocked()){setHint(hint,'info','Saved month',s.working+' working '+(s.working===1?'day':'days')+' · '+s.hours+' hours in the submitted document.');}
    else if (s.overlap && s.overlap.length > 0) {
        setHint(hint, 'warning', UI.warning, formatUi(UI.overlapHint, { days: s.overlap.join(', ') }));
    } else if (total > 0) {
        if (s.working < 5) {
            setHint(hint, 'warning', UI.warning, formatUi(UI.fewWorkingDays, {
                working: s.working,
                hours: s.hours,
            }));
        } else {
            setHint(hint, 'success', UI.note, formatUi(UI.absenceSummary, {
                absences: total,
                working: s.working,
                hours: s.hours,
            }));
        }
    } else {
        setHint(hint, 'info', UI.info, formatUi(UI.fullMonth, {
            working: s.working,
            hours: s.hours,
        }));
    }
    renderApprovedOther();saveFormState();
}

/* ── Day range formatting ── */
function daysToRanges(days) {
    if (!days.length) return '';
    const sorted = [...days].sort((a, b) => a - b);
    const ranges = [];
    let start = sorted[0], end = sorted[0];
    for (let i = 1; i < sorted.length; i++) {
        if (sorted[i] === end + 1) end = sorted[i];
        else {
            ranges.push(start === end ? String(start) : `${start}-${end}`);
            start = end = sorted[i];
        }
    }
    ranges.push(start === end ? String(start) : `${start}-${end}`);
    return ranges.join(',');
}

async function showPreview(){
    if(documentPending||periodInfo?.unavailable||root.dataset.viewReady==='loading')return;
    if(isPeriodLocked()){
        if(periodInfo.timesheet.format==='pdf')await window.PeopleWorkspace.previewDocument('/admin/timesheets/'+periodInfo.timesheet.id+'/view',byId('employee_name').value+' · Submitted timesheet');
        return;
    }
    const form=byId('timesheetForm');if(!form.reportValidity())return;
    const data=new FormData(form);
    for(const id of ['vacation','sick','custom_days'])data.set(id,byId(id).getAttribute('data-value')||'');
    const {year,month}=getYM(),period=new Date(year,month-1,1).toLocaleDateString('en-GB',{month:'long',year:'numeric'});
    await window.PeopleWorkspace.previewDraft(data,byId('employee_name').value+' · '+period+' · PDF preview');
}

/* Per-month drafts and authoritative review state. */
const controls=Array.from(root.querySelectorAll('input,select,textarea,button'));
const disabledBefore=controls.map(control=>control.disabled);
function editorBusy(value){
  byId('timesheetForm').inert=value;
  controls.forEach((control,index)=>{control.disabled=value||disabledBefore[index];});
  root.dataset.viewReady=value?'loading':'ready';
}
async function rebuildPeriod(){
  const target=getYM(),version=++periodRevision;periodCursor=target;editorBusy(true);
  closeCalendar();if(pickers)Object.values(pickers).forEach(picker=>picker.destroy());pickers=null;
  const [info]=await Promise.all([getPeriodInfo(target),loadHolidays(target.year)]);
  if(disposed||version!==periodRevision)return;
  periodInfo=info;applyPeriodValues(target);pickers=initCalendars();
  const raw=byId('custom_days').getAttribute('data-value')||'';
  const customDates=Array.from(parseDaySet(raw)).map(day=>new Date(target.year,target.month-1,day));
  if(customDates.length){pickers.customPicker.setDate(customDates,false);updateDisplay('custom',customDates);}
  await updateStats();if(disposed||version!==periodRevision)return;
  editorBusy(false);renderPeriodStatus();saveFormState(target,true);initialized=true;
}
async function reinit(){
  if(periodCursor)saveFormState(periodCursor,true);
  const step=document.activeElement?.closest?.('[data-step]')?.dataset.step;
  await rebuildPeriod();
  if(step&&!disposed){const button=root.querySelector('[data-step="'+step+'"]');if(button&&!button.hidden)button.focus();}
  if(!disposed&&root.dataset.viewReady==='ready')window.PeopleWorkspace.periodChanged(root,getYM());
}
loadFormState();editorBusy(true);
const ready=rebuildPeriod().catch(()=>{if(!disposed){editorBusy(false);periodInfo={unavailable:true};renderPeriodStatus();}});

byId('year').addEventListener('change',  reinit);
byId('month').addEventListener('change', reinit);
// One stepper replaces separate year/month fields; the hidden fields stay the source of truth.
root.querySelectorAll('[data-step]').forEach(button=>button.addEventListener('click',()=>{
    const {year,month}=getYM(),now=new Date();
    const next=button.dataset.step==='today'?new Date(now.getFullYear(),now.getMonth(),1):new Date(year,month-1+Number(button.dataset.step),1);
    if(next.getFullYear()<2020||next.getFullYear()>2099||(next.getFullYear()===year&&next.getMonth()+1===month))return;
    byId('year').value=next.getFullYear();byId('month').value=next.getMonth()+1;
    byId('month').dispatchEvent(new Event('change',{bubbles:true}));
}));
root.querySelectorAll('#timesheetForm [required]').forEach(field => {
    field.addEventListener('invalid', () => field.setCustomValidity(UI.requiredField));
    field.addEventListener('input', () => field.setCustomValidity(''));
    field.addEventListener('change', () => field.setCustomValidity(''));
});
['employee_name', 'custom_reason'].forEach(id => {
    byId(id).addEventListener('input', () => saveFormState());
});
root.querySelectorAll('input[name="format"]').forEach(el => {
    el.addEventListener('change', () => saveFormState());
});

/* ── Form submit ── */
byId('timesheetForm').addEventListener('submit', async e => {
    e.preventDefault();
    if(documentPending||periodInfo?.unavailable||root.dataset.viewReady==='loading')return;
    const form     = e.target;
    if(isPeriodLocked()){const link=document.createElement('a');link.href='/history/timesheets/'+periodInfo.timesheet.id+'/download';document.body.append(link);link.click();link.remove();return;}
    const formData = new FormData(form);

    // Sync data-value for hidden date fields
    ['vacation', 'sick', 'custom_days'].forEach(id => {
        const val = byId(id).getAttribute('data-value') || '';
        formData.set(id, val);
    });

    const btn     = byId('generateBtn');
    const loading = byId('loading');
    const result  = byId('result');

    documentPending       = true;
    form.inert            = true;
    btn.disabled          = true;
    loading.style.display = 'block';
    result.style.display  = 'none';

    const fmt = getSelectedFormat();
    byId('loadingText').textContent =
        fmt === 'xlsx' ? UI.generatingExcel : UI.generatingPdf;

    try {
        const resp = await window.PeopleWorkspace.request('/generate', { method: 'POST', body: formData });
        const ct   = resp.headers.get('content-type') || '';

        if (isDownloadResponse(ct)) {
            const blob = await resp.blob();
            const url  = URL.createObjectURL(blob);
            const a    = Object.assign(document.createElement('a'), { href: url });

            const cd   = resp.headers.get('content-disposition') || '';
            const m    = cd.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
            const defaultName = fmt === 'xlsx' ? 'timesheet.xlsx' : 'timesheet.pdf';
            a.download = m ? m[1].replace(/['"]/g, '') : defaultName;

            document.body.appendChild(a);
            a.click();
            URL.revokeObjectURL(url);
            document.body.removeChild(a);

            setResult(
                result,
                'success',
                UI.success,
                fmt === 'xlsx' ? UI.downloadExcelSuccess : UI.downloadPdfSuccess,
            );
        } else {
            const error=await resp.json().catch(()=>({}));throw new Error(error.error||UI.requestFailed);
        }
    } catch (err) {
        setResult(result, 'error', UI.error, err.message||UI.requestFailed);
    } finally {
        documentPending       = false;
        form.inert            = false;
        btn.disabled          = false;
        loading.style.display = 'none';
        result.style.display  = 'block';
    }
});

byId('submitBtn').addEventListener('click', async () => {
    if(documentPending||periodInfo?.unavailable||root.dataset.viewReady==='loading'||isPeriodLocked())return;
    if(!byId('timesheetForm').reportValidity())return;
    const form     = byId('timesheetForm');
    const formData = new FormData(form);

    ['vacation', 'sick', 'custom_days'].forEach(id => {
        const val = byId(id).getAttribute('data-value') || '';
        formData.set(id, val);
    });

    const btn     = byId('submitBtn');
    const loading = byId('loading');
    const result  = byId('result');

    documentPending       = true;
    form.inert            = true;
    btn.disabled          = true;
    loading.style.display = 'block';
    result.style.display  = 'none';
    byId('loadingText').textContent = UI.submitting;

    try {
        const resp = await window.PeopleWorkspace.request('/timesheets/submit', { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.ok) {
            await refreshPeriod();
            window.dispatchEvent(new CustomEvent('people:data-changed'));
            setResult(result, 'success', UI.submitted, UI.submittedMessage);
        } else {
            throw new Error(data.error||UI.submitFailed);
        }
    } catch (err) {
        setResult(result, 'error', UI.error, err.message||UI.submitFailed);
    } finally {
        documentPending       = false;
        form.inert            = false;
        btn.disabled          = false;
        loading.style.display = 'none';
        result.style.display  = 'block';
        renderPeriodStatus();
    }
});
const handlers = {clearSick,clearCustom,openCalendar,updateGenerateLabel,showPreview,closeCalendar,removePeriod};
return {ready,
  activate(){viewActive=true;Object.assign(window,handlers);watchVisiblePeriod();refreshVisiblePeriod();},
  pause(){viewActive=false;stopBackgroundRefresh();closeCalendar();closeDayMenu();saveFormState();},
  destroy(){disposed=true;viewActive=false;stopBackgroundRefresh();window.removeEventListener('focus',refreshVisiblePeriod);document.removeEventListener('visibilitychange',visibilityChanged);closeCalendar();closeDayMenu();if(pickers)Object.values(pickers).forEach(p=>p.destroy());}
};
};
