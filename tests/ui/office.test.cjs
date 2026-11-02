const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {execFileSync}=require('node:child_process');
const {resolve}=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=resolve(__dirname,'../..');
const fixtures=JSON.parse(execFileSync('python3',[resolve(__dirname,'render_fixtures.py')],{encoding:'utf8',maxBuffer:4*1024*1024}));
async function settled(app,day=0){await until(()=>app.d.querySelector('[data-office-date="'+app.dates[day]+'"]').getAttribute('aria-busy')==='false');}
async function until(predicate){for(let n=0;n<120;n++){if(predicate())return;await new Promise(r=>setTimeout(r,5));}throw new Error('Office UI did not settle');}
function setup({seed=[],restoredStorage=[]}={}){
  const errors=[],calls=[],invalidations=[];let fail=false,drop=false,hold=null,release=null,version=0;
  const console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(fixtures['1']['/office'],{url:'https://people.example/office',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  const w=dom.window,d=w.document,view=d.querySelector('#people-view');
  const base=JSON.parse(d.querySelector('#office-data').textContent),own={};
  const dates=base.days.map(day=>day.date);
  for(const item of seed){const work_date=dates[item.day||0];own[work_date]={user_id:base.person_id,work_date,planned:true,start_time:null,end_time:null,updated_at:'v'+(++version),...item};}
  function board(){const data=JSON.parse(JSON.stringify(base));data.my_plans=JSON.parse(JSON.stringify(own));for(const day of data.days){day.people=[];const row=own[day.date];if(row?.planned)day.people.push({...row,full_name:data.person_name});}return data;}
  d.querySelector('#office-data').textContent=JSON.stringify(board());
  for(const [key,value] of restoredStorage)w.sessionStorage.setItem(key,value);
  w.fetch=async()=>new Response(JSON.stringify(board()),{headers:{'content-type':'application/json'}});
  w.PeopleWorkspace={invalidateScreens(paths){invalidations.push(paths);},async request(path,options){
    if(path.startsWith('/office/data?'))return w.fetch(path);
    calls.push({path,body:options.body});
    if(hold)await hold;
    if(fail){fail=false;return new Response(JSON.stringify({error:'Connection unavailable. Try again.'}),{status:503});}
    const body=options.body,date=body.get('work_date'),before=own[date],planned=body.get('planned')==='1';
    const start=planned?(body.get('start_time')||null):(before?.start_time||null),end=planned?(body.get('end_time')||null):(before?.end_time||null);
    const identical=before&&before.planned===planned&&before.start_time===start&&before.end_time===end;
    if(body.get('expected_updated_at')!==(before?.updated_at||'')&&!identical)return new Response(JSON.stringify({error:'This plan changed. Refresh this week and try again.'}),{status:409});
    own[date]=identical?before:{user_id:base.person_id,work_date:date,planned,start_time:start,end_time:end,updated_at:'v'+(++version)};
    if(drop){drop=false;throw new Error('Connection lost before confirmation');}
    return new Response(JSON.stringify({ok:true,plan:own[date]}),{headers:{'content-type':'application/json'}});
  }};
  w.eval(readFileSync(resolve(root,'static/workspace-drafts.js'),'utf8'));
  w.eval(readFileSync(resolve(root,'static/office-view.js'),'utf8'));
  const controller=w.PeopleViews.office(view,String(base.person_id));controller.activate();
  return {w,d,view,dates,calls,errors,own,invalidations,controller,
    toggle(day=0){d.querySelector('[data-office-date="'+dates[day]+'"]').click();},
    edit(day=0){d.querySelector('[data-office-hours="'+dates[day]+'"]').click();},
    fail(){fail=true;},drop(){drop=true;},remote(day=0){own[dates[day]]={...own[dates[day]],start_time:'11:00',end_time:'18:00',updated_at:'v'+(++version)};},
    hold(){hold=new Promise(resolve=>{release=resolve;});},release(){hold=null;release?.();},
    storage(){return Array.from({length:w.sessionStorage.length},(_,n)=>{const key=w.sessionStorage.key(n);return [key,w.sessionStorage.getItem(key)];});},
    close(){controller.destroy();w.close();}};
}
test('one tap saves a day, updates who is coming, and invalidates only the Home summary',async()=>{
  const app=setup();try{
    app.toggle();await until(()=>app.d.querySelector('[data-office-date]').getAttribute('aria-pressed')==='true');
    assert.equal(app.calls.length,1);assert.equal(app.calls[0].body.get('start_time'),'');
    assert.match(app.d.querySelector('[data-office-team]').textContent,/Alice Example/);
    assert.ok(app.invalidations.some(paths=>paths.includes('/')));assert.match(app.d.querySelector('[data-office-feedback]').textContent,/saved/);
    assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});
test('editing and clearing times preserve the chosen office day',async()=>{
  const app=setup({seed:[{day:0}]});try{
    app.edit();const form=app.d.querySelector('#officeHoursForm');form.elements.start_time.value='09:30';form.elements.end_time.value='17:00';
    form.requestSubmit();await until(()=>app.own[app.dates[0]].start_time==='09:30');await settled(app);
    assert.equal(app.own[app.dates[0]].planned,true);assert.equal(app.calls[0].body.get('planned'),'1');
    app.d.querySelector('[data-office-clear-times]').click();await until(()=>app.own[app.dates[0]].start_time===null);await settled(app);
    assert.equal(app.own[app.dates[0]].planned,true);assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});
test('removing a timed visit can be undone, including after a failed Undo request',async()=>{
  const app=setup({seed:[{day:0,start_time:'09:00',end_time:'18:00'}]});try{
    app.toggle();await until(()=>app.d.querySelector('[data-office-date]').getAttribute('aria-pressed')==='false');
    assert.doesNotMatch(app.d.querySelector('[data-office-team]').textContent,/Alice Example/);
    app.fail();app.d.querySelector('[data-office-undo]').click();await until(()=>app.d.querySelector('[data-office-refresh]').hidden===false);
    assert.equal(app.d.querySelector('[data-office-undo]').hidden,false);
    app.d.querySelector('[data-office-undo]').click();await until(()=>app.own[app.dates[0]].planned);await settled(app);
    assert.equal(app.own[app.dates[0]].start_time,'09:00');assert.equal(app.own[app.dates[0]].end_time,'18:00');assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});
test('failed save keeps the previous state; a lost success response is safe to retry',async()=>{
  const app=setup();try{
    app.fail();app.toggle();await until(()=>app.d.querySelector('[data-office-refresh]').hidden===false);
    assert.equal(app.d.querySelector('[data-office-date]').getAttribute('aria-pressed'),'false');
    app.drop();app.toggle();await until(()=>app.d.querySelector('[data-office-feedback]').textContent.includes('Connection lost'));
    assert.equal(app.own[app.dates[0]].planned,true);assert.equal(app.d.querySelector('[data-office-date]').getAttribute('aria-pressed'),'false');
    app.toggle();await until(()=>app.d.querySelector('[data-office-date]').getAttribute('aria-pressed')==='true');assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});
test('pending save prevents duplicate taps and editor switching without losing a second draft',async()=>{
  const app=setup({seed:[{day:0},{day:1}]});try{
    app.edit();const form=app.d.querySelector('#officeHoursForm');form.elements.start_time.value='10:00';form.elements.end_time.value='18:00';
    app.hold();form.requestSubmit();app.edit(1);app.toggle();
    assert.equal(form.elements.work_date.value,app.dates[0]);assert.equal(app.calls.length,1);
    app.release();await until(()=>app.own[app.dates[0]].start_time==='10:00');await settled(app);assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});
test('time drafts survive changing the edited day and reopening the page; tokens are excluded',async()=>{
  const app=setup({seed:[{day:0},{day:1}]});let saved;
  try{
    app.edit();const form=app.d.querySelector('#officeHoursForm');form.elements.start_time.value='10:15';form.elements.start_time.dispatchEvent(new app.w.Event('input',{bubbles:true}));
    app.edit(1);form.elements.start_time.value='11:30';form.elements.start_time.dispatchEvent(new app.w.Event('input',{bubbles:true}));
    app.edit();assert.equal(form.elements.start_time.value,'10:15');saved=app.storage();assert.doesNotMatch(JSON.stringify(saved),/_csrf_token/);
  }finally{app.close();}
  const restored=setup({seed:[{day:0},{day:1}],restoredStorage:saved});try{assert.equal(restored.d.querySelector('#officeHoursForm').elements.start_time.value,'10:15');}finally{restored.close();}
});
test('an explicit refresh resolves a stale save while keeping typed times; background refresh preserves the old version',async()=>{
  const app=setup({seed:[{day:0}]});try{
    app.edit();const form=app.d.querySelector('#officeHoursForm');form.elements.start_time.value='09:30';form.elements.end_time.value='16:30';
    form.elements.start_time.dispatchEvent(new app.w.Event('input',{bubbles:true}));app.remote();form.requestSubmit();
    await until(()=>!app.d.querySelector('[data-office-form-error]').hidden);
    app.d.querySelector('[data-office-refresh]').click();await until(()=>app.d.querySelector('[data-office-feedback]').textContent.includes('draft times are kept'));
    assert.equal(form.elements.start_time.value,'09:30');assert.equal(form.elements.end_time.value,'16:30');
    form.requestSubmit();await until(()=>app.own[app.dates[0]].start_time==='09:30');await settled(app);assert.equal(app.own[app.dates[0]].planned,true);assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});
test('weekend plans are discoverable and displayed on the same page',()=>{
  const app=setup({seed:[{day:5}]});try{
    assert.match(app.d.querySelector('[data-office-weekend-count]').textContent,/1 plan/);
    const day=app.d.querySelector('[data-office-choice="'+app.dates[5]+'"]');assert.equal(day.hidden,true);
    const toggle=app.d.querySelector('[data-office-weekends]');toggle.checked=true;toggle.dispatchEvent(new app.w.Event('change'));
    assert.equal(day.hidden,false);assert.equal(app.d.querySelector('[data-office-team-day="'+app.dates[5]+'"]').hidden,false);
  }finally{app.close();}
});
test('closing optional times keeps the draft without reopening the editor after reload',()=>{
  const app=setup({seed:[{day:0}]});let saved;
  try{
    app.edit();const input=app.d.querySelector('[name=start_time]');input.value='10:20';input.dispatchEvent(new app.w.Event('input',{bubbles:true}));
    app.d.querySelector('[data-office-close]').click();saved=app.storage();assert.equal(app.d.querySelector('[data-office-editor]').hidden,true);
  }finally{app.close();}
  const next=setup({seed:[{day:0}],restoredStorage:saved});try{
    assert.equal(next.d.querySelector('[data-office-editor]').hidden,true);next.edit();assert.equal(next.d.querySelector('[name=start_time]').value,'10:20');
  }finally{next.close();}
});
