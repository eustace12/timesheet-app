const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {execFileSync}=require('node:child_process');
const {resolve}=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');
const fixtures=JSON.parse(execFileSync('python3',[resolve(__dirname,'render_fixtures.py')],{encoding:'utf8',maxBuffer:4*1024*1024}));
const root=resolve(__dirname,'../..'),origin='https://people.example';
async function until(predicate){
  for(let n=0;n<100;n++){if(predicate())return;await new Promise(resolve=>setTimeout(resolve,5));}
  throw new Error('UI did not settle');
}
function setup({realTimesheet=false,approvedVacation='',approvedAbsences=[],savedSheet=null,productionRole=null,adminPage=null,fakeIntervals=false}={}){
  const errors=[],calls=[];let selected=productionRole?String(fixtures.production_people[productionRole]):'1',failRoute=null,demoMode=productionRole==='accountant'?'accountant':'employee',reviewError=null,heldPath=null,heldRequest=null,releaseRequest=null,expiredPath=null;
  const console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error.message));
  const dom=new JSDOM(productionRole?fixtures.production[productionRole]['/timesheet']:fixtures['1']['/timesheet'],{url:origin+'/timesheet',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  const w=dom.window,d=w.document;
  w.scrollTo=()=>{};
  const intervals=new Map();let intervalId=0,visible=true;
  if(fakeIntervals){
    w.setInterval=(callback)=>{const id=++intervalId;intervals.set(id,callback);return id;};
    w.clearInterval=id=>intervals.delete(id);
  }
  Object.defineProperty(d,'hidden',{configurable:true,get:()=>!visible});
  Object.defineProperty(d,'visibilityState',{configurable:true,get:()=>visible?'visible':'hidden'});
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
  w.URL.createObjectURL=()=> 'blob:https://people.example/test-pdf';w.URL.revokeObjectURL=()=>{};
  const insert=d.head.insertBefore.bind(d.head);
  d.head.insertBefore=(node,before)=>{const result=insert(node,before);if(node.tagName==='LINK')setTimeout(()=>node.dispatchEvent(new w.Event('load')),0);return result;};
  w.WorkspacePDF={open:async(buffer,container)=>({pageCount:1,async render(){container.replaceChildren(d.createElement('canvas'));},destroy(){container.replaceChildren();}})};
  w.eval(readFileSync(resolve(root,'static/vendor/flatpickr/flatpickr.min.js'),'utf8'));
  if(realTimesheet){w.eval(readFileSync(resolve(root,'static/timesheet-view.js'),'utf8'));}
  else w.PeopleViews={timesheet:()=>({activate(){},pause(){},destroy(){}})};
  w.fetch=async(value,options={})=>{
    const url=new URL(String(value),origin),path=url.pathname;calls.push({path,options});
    if(path===heldPath)await heldRequest;
    if(path===expiredPath)return new Response(JSON.stringify({error:'Sign in to continue. Your draft is kept.',login_url:'/login?next=/leave/submit'}),{status:401,headers:{'content-type':'application/json'}});
    if(path==='/timesheets/period')return new Response(JSON.stringify({year:Number(url.searchParams.get('year')),month:Number(url.searchParams.get('month')),hours_per_day:8,approved_vacation:approvedVacation,approved_absences:approvedAbsences,timesheet:savedSheet}),{headers:{'content-type':'application/json'}});
    if(path==='/office/data'){const html=(productionRole?fixtures.production[productionRole]:fixtures[selected])['/office'];const doc=new w.DOMParser().parseFromString(html,'text/html');return new Response(doc.querySelector('#office-data').textContent,{headers:{'content-type':'application/json'}});}
    if(path==='/leave/assessment')return new Response(JSON.stringify({working_days:2,calendar_days:2,can_submit:true,reason:'',balances:[{year:2026,requested:2,after_request:18}],team:[],team_count:0}),{headers:{'content-type':'application/json'}});
    if(path===failRoute)throw new Error('Offline');
    let html;
    if(path==='/demo/role'){demoMode=options.body.get('role');html=fixtures[selected][new URL(options.body.get('next'),origin).pathname];}
    else if(path==='/profile'){
      selected=options.body.get('user_id');assert.ok(selected,'Employee ID must be sent even while the selector is disabled');
      html=fixtures[selected][options.body.get('next').split('?')[0]];
    }else if(/^\/admin\/timesheets\/\d+\/review$/.test(path)){
      if(reviewError)return new Response(JSON.stringify({error:reviewError}),{status:409,headers:{'content-type':'application/json'}});
      if(options.body.get('status')==='needs_changes'&&!options.body.get('review_comment'))return new Response(JSON.stringify({error:'Add a comment so the employee knows what to change.'}),{status:400,headers:{'content-type':'application/json'}});
      html=fixtures.reviewed_admin;
    }else if(/^\/admin\/timesheets\/\d+\/view$/.test(path))return new Response('%PDF-test',{headers:{'content-type':'application/pdf'}});
    else if(path==='/generate')return new Response('%PDF-draft',{headers:{'content-type':'application/pdf'}});
    else if(path==='/leave/submit')return new Response(JSON.stringify({ok:true,id:1}),{headers:{'content-type':'application/json'}});
    else if(path.startsWith('/holidays/'))return new Response(JSON.stringify({holidays:[],warning:null}),{headers:{'content-type':'application/json'}});
    else if(path==='/admin/users'&&options.method==='POST')html=productionRole?fixtures.production_updated_people:fixtures.updated_people;
    else if(path==='/admin'&&adminPage)html=adminPage;
    else html=(productionRole?fixtures.production[productionRole]:fixtures[selected])[path==='/documents'?'/timesheet':path];
    if(!html)throw new Error('Unknown screen '+path);
    html=html.replaceAll('data-demo-role="employee"','data-demo-role="'+demoMode+'"');
    const response=new Response(html,{headers:{'content-type':'text/html'}});
    Object.defineProperty(response,'url',{value:origin+(['/profile','/demo/role'].includes(path)?options.body.get('next'):/\/review$/.test(path)?'/admin':url.pathname+url.search)});return response;
  };
  for(const file of ['workspace-core.js','workspace-drafts.js','people-experience.js','office-view.js','leave-date-picker.js','workspace.js'])w.eval(readFileSync(resolve(root,'static',file),'utf8'));
  return {w,d,calls,errors,fail(path){failRoute=path;},rejectReview(message){reviewError=message;},
    expire(path){expiredPath=path;},
    setApprovedVacation(value){approvedVacation=value;},
    setApprovedAbsences(value){approvedAbsences=value;},
    setVisibility(value){visible=value;d.dispatchEvent(new w.Event('visibilitychange'));},
    async tickIntervals(){await Promise.all(Array.from(intervals.values(),callback=>callback()));},
    hold(path){heldPath=path;heldRequest=new Promise(resolve=>{releaseRequest=resolve;});},
    release(){heldPath=null;releaseRequest?.();},close(){w.close();}};
}
test('opening and sending leave keeps URL, monthly draft and persistent shell',async()=>{
  const app=setup();try{
    const {w,d}=app,view=d.querySelector('#people-view'),sidebar=d.querySelector('.people-sidebar');
    d.querySelector('#month').value='7';d.querySelector('[data-request-leave]').click();
    assert.equal(d.querySelector('dialog').open,true);assert.equal(w.location.pathname,'/timesheet');assert.equal(d.querySelector('#people-view'),view);
    d.querySelector('#start_date').value='2026-11-09';d.querySelector('#end_date').value='2026-11-10';d.querySelector('#leaveForm').requestSubmit();
    await until(()=>!d.querySelector('dialog').open);
    assert.equal(d.querySelector('#month').value,'7');assert.equal(d.querySelector('.people-sidebar'),sidebar);assert.equal(w.location.pathname,'/timesheet');
    assert.match(d.querySelector('.workspace-toast').textContent,/Leave request sent/);
    assert.equal(app.calls.filter(call=>call.path==='/leave/submit').length,1);assert.deepEqual(app.errors,[]);
    d.querySelector('.toast-action').click();await until(()=>w.location.pathname==='/leave');assert.equal(d.querySelector('.workspace-toast').hidden,true);
  }finally{app.close();}
});
test('section changes and browser Back restore the same editor without document navigation',async()=>{
  const app=setup();try{
    const {w,d}=app,editor=d.querySelector('#people-view'),sidebar=d.querySelector('.people-sidebar');
    d.querySelector('#month').value='7';d.querySelector('.people-nav a[href="/leave"]').click();await until(()=>w.location.pathname==='/leave');
    assert.equal(d.querySelector('.people-sidebar'),sidebar);w.history.back();await until(()=>d.querySelector('#people-view')===editor);
    assert.equal(d.querySelector('#month').value,'7');assert.equal(w.location.pathname,'/timesheet');assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});
test('employee switch sends selected ID and replaces previous employee views',async()=>{
  const app=setup();try{
    const {w,d}=app,sidebar=d.querySelector('.people-sidebar'),select=d.querySelector('#selected_person');
    select.value='2';select.dispatchEvent(new w.Event('change',{bubbles:true}));await until(()=>!select.disabled);
    assert.equal(select.value,'2');assert.equal(d.querySelector('#employee_name').value,'Bob Example');assert.equal(d.querySelector('[data-request-person]').textContent,'Bob Example');
    assert.equal(d.querySelector('.people-sidebar'),sidebar);assert.equal(w.location.pathname,'/timesheet');assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});
test('failed navigation preserves the editor and offers a retry',async()=>{
  const app=setup();try{
    const {d}=app,editor=d.querySelector('#people-view');app.fail('/leave');
    d.querySelector('#month').value='7';d.querySelector('.people-nav a[href="/leave"]').click();await until(()=>!d.querySelector('.workspace-toast').hidden);
    assert.equal(d.querySelector('#people-view'),editor);assert.equal(d.querySelector('#month').value,'7');
    assert.match(d.querySelector('.workspace-toast').textContent,/current work is still here/);assert.equal(d.querySelector('.toast-action').textContent,'Retry');assert.equal(d.querySelector('.navigation-progress').hidden,true);
  }finally{app.close();}
});
test('saving People updates the persistent selector without reloading',async()=>{
  const app=setup();try{
    const {d,w}=app;d.querySelector('.people-nav a[href="/admin/users"]').click();await until(()=>w.location.pathname==='/admin/users');
    const form=d.querySelector('#people-view form[action="/admin/users"]');form.elements.username.value='charlie';form.elements.full_name.value='Charlie Example';form.requestSubmit();
    await until(()=>Array.from(d.querySelector('#selected_person').options).some(option=>option.text==='Charlie Example'));
    assert.equal(d.querySelector('#selected_person').value,'1');assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('unsaved leave notes survive reload and stay separate for different employees',async()=>{
  const app=setup();try{
    const {w,d}=app;d.querySelector('[data-request-leave]').click();
    const note=d.querySelector('#comment');note.value='Keep my draft note';note.dispatchEvent(new w.Event('input',{bubbles:true}));
    const saved=Array.from({length:w.sessionStorage.length},(_,n)=>w.sessionStorage.getItem(w.sessionStorage.key(n))).join(' ');
    assert.match(saved,/Keep my draft note/);
    d.querySelector('[data-close-leave]').click();
    const select=d.querySelector('#selected_person');select.value='2';select.dispatchEvent(new w.Event('change',{bubbles:true}));await until(()=>!select.disabled);
    assert.equal(d.querySelector('#comment').value,'');
    select.value='1';select.dispatchEvent(new w.Event('change',{bubbles:true}));await until(()=>!select.disabled);
    assert.equal(d.querySelector('#comment').value,'Keep my draft note');
  }finally{app.close();}
});

test('drafts never persist CSRF or hidden fields',async()=>{
  const app=setup();try{
    const {d,w}=app;d.querySelector('.people-nav a[href="/admin/users"]').click();await until(()=>w.location.pathname==='/admin/users');
    const field=d.querySelector('#username');field.value='unsaved-name';field.dispatchEvent(new w.Event('input',{bubbles:true}));
    const values=Array.from({length:w.sessionStorage.length},(_,n)=>w.sessionStorage.getItem(w.sessionStorage.key(n))).join(' ');
    assert.match(values,/unsaved-name/);assert.doesNotMatch(values,/_csrf_token/);
  }finally{app.close();}
});


test('demo role switches return to the same employee draft without changing employee',async()=>{
  const app=setup();try{
    const {d,w}=app,editor=d.querySelector('#people-view'),person=d.querySelector('#selected_person').value;
    d.querySelector('#month').value='7';d.querySelector('#demo-role-selector button[value="accountant"]').click();
    await until(()=>w.location.pathname==='/admin'&&!d.querySelector('#demo-role-selector button').disabled);
    assert.equal(d.body.dataset.demoRole,'accountant');assert.equal(d.querySelector('#selected_person').value,person);
    d.querySelector('#demo-role-selector button[value="employee"]').click();await until(()=>d.querySelector('#people-view')===editor);
    assert.equal(d.body.dataset.demoRole,'employee');assert.equal(d.querySelector('#month').value,'7');assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('saving one review keeps another unsaved review comment',async()=>{
  const app=setup();try{
    const {d,w}=app;d.querySelector('.people-nav a[href="/admin"]').click();await until(()=>w.location.pathname==='/admin');
    const old=d.querySelector('#people-view'),forms=d.querySelectorAll('.review-form');assert.equal(forms.length,2);
    forms[1].elements.review_comment.value='Keep this second review note';forms[1].elements.review_comment.dispatchEvent(new w.Event('input',{bubbles:true}));
    forms[0].requestSubmit(forms[0].querySelector('button[value="accepted"]'));await until(()=>d.querySelector('#people-view')!==old);
    assert.equal(d.querySelectorAll('.review-form')[1].elements.review_comment.value,'Keep this second review note');assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('failed review shows the server reason inside its form and retains editable fields',async()=>{
  const app=setup();try{
    const {d,w}=app;d.querySelector('.people-nav a[href="/admin"]').click();await until(()=>w.location.pathname==='/admin');
    const form=d.querySelector('.review-form');form.elements.review_comment.value='Check absence dates';app.rejectReview('This document has changed. Reopen it before reviewing.');form.requestSubmit(form.querySelector('button[value="needs_changes"]'));
    await until(()=>form.querySelector('[data-form-error]')?.textContent);
    assert.match(form.querySelector('[data-form-error]').textContent,/document has changed/);assert.equal(form.elements.review_comment.value,'Check absence dates');assert.equal(form.inert,false);assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('PDF preview opens in the workspace and canceling profile deletion sends no write',async()=>{
  const app=setup();try{
    const {d,w}=app;d.querySelector('.people-nav a[href="/admin"]').click();await until(()=>w.location.pathname==='/admin');
    d.querySelector('[data-document-preview]').click();await until(()=>d.querySelector('#document-preview canvas'));
    assert.equal(w.location.pathname,'/admin');assert.equal(d.querySelector('#document-preview').open,true);d.querySelector('[data-close-document]').click();
    d.querySelector('.people-nav a[href="/admin/users"]').click();await until(()=>w.location.pathname==='/admin/users');
    const form=d.querySelector('[data-confirm-delete]');form.requestSubmit(form.querySelector('button'));assert.equal(d.querySelector('#delete-person-dialog').open,true);
    d.querySelector('[data-cancel-delete]').click();assert.equal(app.calls.some(call=>call.path.endsWith('/delete')),false);assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('closing PDF during page loading prevents late canvas insertion',async()=>{
  const dom=new JSDOM('<div id="pages"></div>',{runScripts:'outside-only'});
  try{
    const w=dom.window,container=w.document.querySelector('#pages');
    let finishPage,requested=false,destroyed=false;
    const pendingPage=new Promise(resolve=>{finishPage=resolve;});
    w.__pdfjs={GlobalWorkerOptions:{},getDocument:()=>({destroy:async()=>{destroyed=true;},promise:Promise.resolve({numPages:1,
      getPage(){requested=true;return pendingPage;}})})};
    const source=readFileSync(resolve(root,'static/pdf-preview.js'),'utf8')
      .replace("import('/static/vendor/pdfjs/pdf.min.mjs')","Promise.resolve(window.__pdfjs)");
    w.eval(source);
    const document=await w.WorkspacePDF.open(new ArrayBuffer(1),container,()=>true);
    const rendering=document.render();await until(()=>requested);document.destroy();
    finishPage({getViewport(){throw new Error('A closed document must not render');}});
    await rendering;
    assert.equal(destroyed,true);assert.equal(container.children.length,0);
  }finally{dom.window.close();}
});

test('approval inbox keeps search and review drafts when details close',async()=>{
  const app=setup();try{
    const {d,w}=app;d.querySelector('.people-nav a[href="/admin"]').click();await until(()=>w.location.pathname==='/admin');
    const search=d.querySelector('[data-record-search]');search.value='Bob';search.dispatchEvent(new w.Event('input',{bubbles:true}));
    const visible=Array.from(d.querySelectorAll('[data-open-review]')).filter(row=>!row.hidden);
    assert.equal(visible.length,1);assert.match(visible[0].textContent,/Bob Example/);
    visible[0].click();const drawer=d.querySelector('#review-drawer');assert.equal(drawer.open,true);
    const comment=drawer.querySelector('textarea');comment.value='Keep context while checking another request';comment.dispatchEvent(new w.Event('input',{bubbles:true}));
    drawer.querySelector('[data-close-review]').click();visible[0].click();assert.equal(drawer.querySelector('textarea').value,'Keep context while checking another request');
    assert.equal(search.value,'Bob');assert.equal(w.location.pathname,'/admin');assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('employee sees the requested balance and team availability before sending leave',async()=>{
  const app=setup();try{
    const {d,w}=app;d.querySelector('[data-request-leave]').click();
    d.querySelector('#start_date').value='2026-11-09';d.querySelector('#end_date').value='2026-11-10';
    d.querySelector('#end_date').dispatchEvent(new w.Event('change',{bubbles:true}));
    await until(()=>d.querySelector('[data-leave-context]').textContent.includes('18 days remaining'));
    assert.match(d.querySelector('[data-leave-context]').textContent,/No other approved or pending/);
    assert.match(d.querySelector('[data-leave-days]').textContent,/2 working days/);
    assert.equal(d.querySelector('#leaveSubmit').disabled,false);assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('a correction link opens its requested month after the retained editor changed period',async()=>{
  const app=setup();try{
    const {w,d}=app;
    const year=d.querySelector('#year').value,month=d.querySelector('#month').value;
    const target='/timesheet?year='+year+'&month='+month;
    await w.PeopleWorkspace.navigate(target);
    const retained=d.querySelector('#people-view');d.querySelector('#month').value=String(Number(month)%12+1);
    await w.PeopleWorkspace.navigate('/');await w.PeopleWorkspace.navigate(target);
    assert.equal(d.querySelector('#month').value,month);
    assert.notEqual(d.querySelector('#people-view'),retained);assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});


test('requesting changes with an empty comment explains the problem in the open drawer without a write',async()=>{
  const app=setup();try{
    const {d,w}=app;await w.PeopleWorkspace.navigate('/admin');d.querySelector('[data-open-review]').click();
    const drawer=d.querySelector('#review-drawer'),form=drawer.querySelector('form'),comment=form.elements.review_comment;
    form.requestSubmit(form.querySelector('button[value="needs_changes"]'));
    assert.match(form.querySelector('[data-form-error]')?.textContent||'',/Add a comment/);
    assert.equal(comment.getAttribute('aria-invalid'),'true');assert.equal(d.activeElement,comment);
    assert.equal(app.calls.some(call=>call.path.endsWith('/review')),false);assert.equal(drawer.open,true);
    comment.value='Please verify the absence dates';comment.dispatchEvent(new w.Event('input',{bubbles:true}));
    assert.equal(form.querySelector('[data-form-error]').hidden,true);assert.equal(comment.hasAttribute('aria-invalid'),false);
    form.requestSubmit(form.querySelector('button[value="accepted"]'));await until(()=>d.querySelector('#review-drawer')!==drawer);
    assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('draft preview renders a PDF without submitting or altering the selected export format',async()=>{
  const app=setup();try{
    const {w,d}=app;d.querySelector('input[value="xlsx"]').checked=true;
    const data=new w.FormData(d.querySelector('#timesheetForm'));data.set('vacation','5-6');
    await w.PeopleWorkspace.previewDraft(data,'Alice · November 2026');
    const call=app.calls.find(call=>call.path==='/generate');assert.ok(call);assert.equal(call.options.method,'POST');
    assert.equal(call.options.body.get('format'),'pdf');assert.equal(call.options.body.get('vacation'),'5-6');
    assert.equal(d.querySelector('input[value="xlsx"]').checked,true);assert.equal(d.querySelector('#document-preview').open,true);
    assert.ok(d.querySelector('#document-preview canvas'));assert.equal(app.calls.some(call=>call.path==='/timesheets/submit'),false);
    assert.equal(d.querySelector('[data-document-open]').textContent,'Download PDF preview');
    d.querySelector('[data-close-document]').click();assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('a one-day leave request starts with matching dates and keeps an explicit later end date',async()=>{
  const app=setup();try{
    const {d,w}=app;d.querySelector('[data-request-leave]').click();const start=d.querySelector('#start_date'),end=d.querySelector('#end_date');
    start.value='2026-11-05';start.dispatchEvent(new w.Event('change',{bubbles:true}));assert.equal(end.value,'2026-11-05');
    end.value='2026-11-10';start.value='2026-11-06';start.dispatchEvent(new w.Event('change',{bubbles:true}));assert.equal(end.value,'2026-11-10');
    start.value='2026-11-12';start.dispatchEvent(new w.Event('change',{bubbles:true}));assert.equal(end.value,'2026-11-12');
    const stored=Array.from({length:w.sessionStorage.length},(_,n)=>w.sessionStorage.getItem(w.sessionStorage.key(n))).join(' ');
    assert.match(stored,/2026-11-12/);assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('changing a prior decision starts a fresh comment instead of reusing the old decision text',async()=>{
  const app=setup();try{
    const {d,w}=app;await w.PeopleWorkspace.navigate('/admin');const old=d.querySelector('#people-view'),form=d.querySelector('.review-form');
    form.requestSubmit(form.querySelector('button[value="accepted"]'));await until(()=>d.querySelector('#people-view')!==old);
    const updated=d.querySelector('.review-form');assert.equal(updated.elements.review_comment.value,'');
    assert.match(updated.closest('.request-detail').textContent,/Approved in tests/);assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});


test('clearing optional absences keeps approved leave and the displayed hours consistent',async()=>{
  const app=setup({realTimesheet:true,approvedVacation:'5'});try{
    const {w,d}=app;await until(()=>d.querySelector('#people-view').dataset.viewReady==='ready');
    const before=d.querySelector('#statExpectedHours').textContent;assert.equal(d.querySelector('#vacation').value,'5');
    w.clearVacation();await until(()=>d.querySelector('#statExpectedHours').textContent===before);
    assert.equal(d.querySelector('#vacation').value,'5');assert.match(d.querySelector('#vacationDisplay').textContent,/Approved/);
    assert.equal(d.querySelector('#vacationDisplay .badge-remove'),null);assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('a saved timesheet displays its own absence snapshot after a later leave approval',async()=>{
  const app=setup({realTimesheet:true,approvedVacation:'5',savedSheet:{id:1,status:'submitted',vacation:'',sick:'',custom_days:'',custom_reason:'',format:'pdf',total_hours:168,working_days:21}});try{
    const {d}=app;await until(()=>d.querySelector('#people-view').dataset.viewReady==='ready');
    assert.equal(d.querySelector('#vacation').value,'');assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('changing the selected month updates the address and retains the editor under that period',async()=>{
  const app=setup();try{
    const {d,w}=app,editor=d.querySelector('#people-view'),year=Number(d.querySelector('#year').value),month=Number(d.querySelector('#month').value)%12+1;
    d.querySelector('#month').value=String(month);w.PeopleWorkspace.periodChanged(editor,{year,month});
    assert.equal(new URL(w.location.href).searchParams.get('month'),String(month));
    const target=w.location.href;await w.PeopleWorkspace.navigate('/');await w.PeopleWorkspace.navigate(target);
    assert.equal(d.querySelector('#people-view'),editor);assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('Other date picker updates the visible badge and monthly hours without a runtime error',async()=>{
  const app=setup({realTimesheet:true});try{
    const {w,d}=app;await until(()=>d.querySelector('#people-view').dataset.viewReady==='ready');
    const year=Number(d.querySelector('#year').value),month=Number(d.querySelector('#month').value);
    const before=Number(d.querySelector('#statExpectedHours').textContent.split(' ')[0]);
    let day=1;while([0,6].includes(new w.Date(year,month-1,day).getDay()))day++;
    d.querySelector('#custom_reason').value='Personal day';
    d.querySelector('#custom_days')._flatpickr.setDate(w.Array.of(new w.Date(year,month-1,day)),true);
    await until(()=>Number(d.querySelector('#statExpectedHours').textContent.split(' ')[0])===before-8);
    assert.match(d.querySelector('#customDisplay').textContent,new RegExp(String(day)));
    assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});


test('company employee shell keeps its SSO identity and opens leave without a demo selector',async()=>{
  const app=setup({realTimesheet:true,productionRole:'employee'});try{
    const {w,d}=app;await until(()=>d.querySelector('#people-view').dataset.viewReady==='ready');
    assert.equal(d.querySelector('#selected_person'),null);assert.equal(d.querySelector('#demo-role-selector'),null);
    assert.equal(d.querySelector('#submitBtn').disabled,false);
    d.querySelector('[data-request-leave]').click();assert.equal(d.querySelector('#leave-drawer').open,true);
    assert.match(d.querySelector('[data-request-person]').textContent,/Alice Example/);
    d.querySelector('[data-close-leave]').click();await w.PeopleWorkspace.navigate('/history');
    assert.equal(w.location.pathname,'/history');assert.equal(d.querySelector('.company-identity').dataset.person,'1');
    assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('company accountant saves People successfully without changing the signed-in identity',async()=>{
  const app=setup({productionRole:'accountant'});try{
    const {w,d}=app;await w.PeopleWorkspace.navigate('/admin/users');
    const identity=d.querySelector('.company-identity').dataset.person;
    const form=d.querySelector('#people-view form[action="/admin/users"]');form.elements.username.value='dora';form.elements.full_name.value='Dora Example';
    form.requestSubmit();await until(()=>d.querySelector('#people-view').textContent.includes('Dora Example'));
    assert.equal(d.querySelector('.company-identity').dataset.person,identity);
    assert.equal(d.querySelector('#selected_person'),null);
    await w.PeopleWorkspace.navigate('/admin/audit');assert.equal(d.querySelector('.people-breadcrumb strong').textContent,'Audit log');
    assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('approved unpaid days reduce hours and keep their label separate from annual leave',async()=>{
  const app=setup({realTimesheet:true,approvedAbsences:[{start_date:'2026-11-09',end_date:'2026-11-09',leave_type:'Unpaid'}]});try{
    const {w,d}=app;await until(()=>d.querySelector('#people-view').dataset.viewReady==='ready');
    d.querySelector('#year').value='2026';d.querySelector('#month').value='11';d.querySelector('#month').dispatchEvent(new w.Event('change'));
    await until(()=>d.querySelector('#people-view').dataset.viewReady==='ready'&&d.querySelector('#approvedOtherDisplay').textContent.includes('Unpaid leave'));
    assert.equal(d.querySelector('#statExpectedHours').textContent,'160 Hours');
    assert.equal(d.querySelector('#vacation').value,'');assert.match(d.querySelector('#monthOverview').textContent,/Unpaid leave/);
    w.clearCustom();await until(()=>d.querySelector('#statExpectedHours').textContent==='160 Hours');
    assert.match(d.querySelector('#approvedOtherDisplay').textContent,/Approved/);assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('typing a monthly reason and changing format save the current period draft immediately',async()=>{
  const app=setup({realTimesheet:true});try{
    const {w,d}=app;await until(()=>d.querySelector('#people-view').dataset.viewReady==='ready');
    const year=Number(d.querySelector('#year').value),month=Number(d.querySelector('#month').value);
    const reason=d.querySelector('#custom_reason');reason.value='Family appointment';reason.dispatchEvent(new w.Event('input',{bubbles:true}));
    const excel=d.querySelector('input[name="format"][value="xlsx"]');excel.checked=true;excel.dispatchEvent(new w.Event('change',{bubbles:true}));
    const draft=JSON.parse(w.localStorage.getItem(w.WorkspaceCore.periodDraftKey('1',year,month)));
    assert.equal(draft.custom_reason,'Family appointment');assert.equal(draft.format,'xlsx');
    assert.deepEqual(JSON.parse(w.localStorage.getItem('timesheetLastPeriodV3:1')),{year,month});
    assert.equal(Array.from({length:w.localStorage.length},(_,n)=>w.localStorage.key(n)).some(key=>key.includes('undefined')),false);
    assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('browser Back closes the PDF overlay and restores the retained editor',async()=>{
  const app=setup();try{
    const {w,d}=app,editor=d.querySelector('#people-view');d.querySelector('#month').value='7';
    d.querySelector('.people-nav a[href="/admin"]').click();await until(()=>w.location.pathname==='/admin');
    d.querySelector('[data-document-preview]').click();await until(()=>d.querySelector('#document-preview canvas'));
    assert.equal(d.querySelector('#document-preview').open,true);
    w.history.back();await until(()=>d.querySelector('#people-view')===editor);
    assert.equal(d.querySelector('#document-preview').open,false);assert.equal(d.querySelector('#month').value,'7');
    assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('approved leave opens as a read-only request and remains available after closing',async()=>{
  const app=setup({adminPage:fixtures.approved_leave_admin});try{
    const {w,d}=app;d.querySelector('.people-nav a[href="/admin"]').click();await until(()=>w.location.pathname==='/admin');
    d.querySelector('[data-record-filter="accepted"]').click();
    const button=d.querySelector('[data-open-review^="review-leave-"]');assert.ok(button,'Approved leave must be present in the real fixture');
    button.click();await until(()=>d.querySelector('#review-drawer').open);
    assert.match(d.querySelector('.review-drawer-body').textContent,/approved and saved/);
    assert.equal(d.querySelector('.review-drawer-body .review-form'),null);
    d.querySelector('[data-close-review]').click();button.click();await until(()=>d.querySelector('#review-drawer').open);
    assert.equal(app.calls.some(call=>call.options.method==='POST'),false);assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('leave fields cannot accept edits that would be discarded while the request is saving',async()=>{
  const app=setup();try{
    const {w,d}=app;d.querySelector('[data-request-leave]').click();
    const form=d.querySelector('#leaveForm');form.elements.start_date.value='2026-11-09';form.elements.end_date.value='2026-11-10';
    app.hold('/leave/submit');form.requestSubmit();await until(()=>app.calls.some(call=>call.path==='/leave/submit'));
    assert.equal(form.inert,true);form.requestSubmit();assert.equal(app.calls.filter(call=>call.path==='/leave/submit').length,1);
    app.release();await until(()=>!d.querySelector('#leave-drawer').open);
    assert.equal(form.inert,false);assert.deepEqual(app.errors,[]);
  }finally{app.release();app.close();}
});

test('monthly download locks its snapshot until completion and ignores overlapping document actions',async()=>{
  const app=setup({realTimesheet:true});try{
    const {w,d}=app;await until(()=>d.querySelector('#people-view').dataset.viewReady==='ready');
    const click=w.HTMLAnchorElement.prototype.click;w.HTMLAnchorElement.prototype.click=function(){if(!this.download)click.call(this);};
    const form=d.querySelector('#timesheetForm');app.hold('/generate');form.requestSubmit();await until(()=>app.calls.some(call=>call.path==='/generate'));
    assert.equal(form.inert,true);form.requestSubmit();d.querySelector('#submitBtn').click();
    assert.equal(app.calls.filter(call=>call.path==='/generate').length,1);
    assert.equal(app.calls.some(call=>call.path==='/timesheets/submit'),false);
    app.release();await until(()=>!d.querySelector('#generateBtn').disabled);
    assert.equal(form.inert,false);assert.deepEqual(app.errors,[]);
  }finally{app.release();app.close();}
});

test('expired account navigation captures drafts before opening sign-in',async()=>{
  const app=setup({realTimesheet:true});try{
    const {w,d}=app;await until(()=>d.querySelector('#people-view').dataset.viewReady==='ready');
    const year=Number(d.querySelector('#year').value),month=Number(d.querySelector('#month').value);
    const reason=d.querySelector('#custom_reason');reason.value='Keep this absence reason';reason.dispatchEvent(new w.Event('input',{bubbles:true}));
    d.querySelector('[data-request-leave]').click();const note=d.querySelector('#comment');note.value='Keep this leave note';note.dispatchEvent(new w.Event('input',{bubbles:true}));d.querySelector('[data-close-leave]').click();
    app.expire('/leave');d.querySelector('.people-nav a[href="/leave"]').click();
    await until(()=>d.querySelector('.navigation-progress').hidden);
    assert.ok(app.errors.some(message=>/navigation/i.test(message)),'The browser should be sent to sign-in');
    const monthly=JSON.parse(w.localStorage.getItem(w.WorkspaceCore.periodDraftKey('1',year,month)));
    assert.equal(monthly.custom_reason,'Keep this absence reason');
    const drafts=Array.from({length:w.sessionStorage.length},(_,n)=>w.sessionStorage.getItem(w.sessionStorage.key(n))).join(' ');
    assert.match(drafts,/Keep this leave note/);assert.doesNotMatch(drafts,/_csrf_token/);
    assert.ok(app.errors.every(message=>/navigation/i.test(message)),'Only jsdom’s native navigation limitation is expected');
  }finally{app.close();}
});

test('leave calendar selects one day or a range, clears dates, and restores the employee draft',async()=>{
  const app=setup();try{
    const {w,d}=app,start=d.querySelector('#start_date'),end=d.querySelector('#end_date');
    d.querySelector('[data-request-leave]').click();
    assert.ok(d.querySelector('[data-leave-calendar] .flatpickr-calendar'),'A calendar must be visible inside the leave drawer');
    start.value='2026-11-02';end.value='2026-11-02';start.dispatchEvent(new w.Event('change',{bubbles:true}));
    const day=label=>d.querySelector('[data-leave-calendar] .flatpickr-day[aria-label="'+label+'"]');
    day('November 10, 2026').click();assert.equal(start.value,'2026-11-10');assert.equal(end.value,'2026-11-10');
    day('November 19, 2026').click();assert.equal(start.value,'2026-11-10');assert.equal(end.value,'2026-11-19');
    assert.match(d.querySelector('[data-leave-date-summary]').textContent,/10 Nov 2026/);
    assert.match(d.querySelector('[data-leave-date-summary]').textContent,/19 Nov 2026/);
    d.querySelector('[data-close-leave]').click();d.querySelector('[data-request-leave]').click();
    assert.equal(start.value,'2026-11-10');assert.equal(end.value,'2026-11-19');
    const stored=Array.from({length:w.sessionStorage.length},(_,n)=>w.sessionStorage.getItem(w.sessionStorage.key(n))).join(' ');
    assert.match(stored,/2026-11-19/);
    d.querySelector('[data-clear-leave-dates]').click();assert.equal(start.value,'');assert.equal(end.value,'');
    await until(()=>!d.querySelector('#leaveSubmit').disabled);
    d.querySelector('#leaveForm').requestSubmit();await new Promise(resolve=>setTimeout(resolve,5));
    assert.equal(app.calls.some(call=>call.path==='/leave/submit'),false);
    assert.match(d.querySelector('#leaveResult').textContent,/Choose/);
    day('November 20, 2026').click();assert.equal(d.querySelector('#leaveResult').hidden,true);
    assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('leave range can cross a year boundary and stays ISO-formatted for submission',async()=>{
  const app=setup();try{
    const {w,d}=app,start=d.querySelector('#start_date'),end=d.querySelector('#end_date');
    start.value='2026-12-01';end.value='2026-12-01';start.dispatchEvent(new w.Event('change',{bubbles:true}));
    const day=label=>d.querySelector('[data-leave-calendar] [aria-label="'+label+'"]');
    day('December 30, 2026').click();
    d.querySelector('[data-leave-calendar] [aria-label="Next month"]').click();
    day('January 4, 2027').click();
    assert.equal(start.value,'2026-12-30');assert.equal(end.value,'2027-01-04');
    assert.equal(day('January 4, 2027').getAttribute('aria-pressed'),'true');
    assert.equal(day('January 4, 2027').getAttribute('role'),'button');
    await until(()=>!d.querySelector('#leaveSubmit').disabled);
    d.querySelector('#leaveForm').requestSubmit();await until(()=>app.calls.some(call=>call.path==='/leave/submit'));
    const sent=app.calls.find(call=>call.path==='/leave/submit').options.body;
    assert.equal(sent.get('start_date'),'2026-12-30');assert.equal(sent.get('end_date'),'2027-01-04');assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});


async function novemberEditor(app){
  const {d,w}=app;await until(()=>d.querySelector('#people-view').dataset.viewReady==='ready');
  if(d.querySelector('#year').value!=='2026'||d.querySelector('#month').value!=='11'){
    d.querySelector('#year').value='2026';d.querySelector('#month').value='11';
    d.querySelector('#month').dispatchEvent(new w.Event('change',{bubbles:true}));
    await until(()=>d.querySelector('#people-view').dataset.viewReady==='ready');
  }
}

test('returning to an open month and background refresh apply approvals without losing its draft',async()=>{
  const app=setup({realTimesheet:true,productionRole:'employee',fakeIntervals:true});try{
    const {d,w}=app;await novemberEditor(app);const editor=d.querySelector('#people-view');
    d.querySelector('#sickPickerInput')._flatpickr.setDate(w.Array.of(new w.Date(2026,10,5),new w.Date(2026,10,5)),true);
    await until(()=>d.querySelector('#sick').value==='5'&&d.querySelector('#statExpectedHours').textContent==='160 Hours');
    const reason=d.querySelector('#custom_reason');reason.value='Keep this draft reason';reason.dispatchEvent(new w.Event('input',{bubbles:true}));
    const excel=d.querySelector('input[name=format][value=xlsx]');excel.checked=true;excel.dispatchEvent(new w.Event('change',{bubbles:true}));
    app.setApprovedVacation('12');w.dispatchEvent(new w.Event('focus'));
    await until(()=>d.querySelector('#vacation').value==='12'&&d.querySelector('#statExpectedHours').textContent==='152 Hours');
    app.setApprovedVacation('12-13');await app.tickIntervals();
    await until(()=>d.querySelector('#vacation').value==='12-13'&&d.querySelector('#statExpectedHours').textContent==='144 Hours');
    await app.tickIntervals();
    assert.equal(d.querySelector('#people-view'),editor);assert.equal(w.location.pathname,'/timesheet');
    assert.equal(d.querySelector('#sick').value,'5');assert.equal(reason.value,'Keep this draft reason');assert.equal(excel.checked,true);
    assert.equal(d.querySelector('#statExpectedHours').textContent,'144 Hours');assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('background month refresh pauses while hidden or in another section and resumes on return',async()=>{
  const app=setup({realTimesheet:true,productionRole:'employee',fakeIntervals:true});try{
    const {d,w}=app;await novemberEditor(app);const editor=d.querySelector('#people-view');
    const requests=()=>app.calls.filter(call=>call.path==='/timesheets/period').length;
    app.setVisibility(false);const beforeHidden=requests();app.setApprovedVacation('12-13');
    w.dispatchEvent(new w.Event('focus'));await app.tickIntervals();assert.equal(requests(),beforeHidden);
    assert.equal(d.querySelector('#statExpectedHours').textContent,'168 Hours');
    app.setVisibility(true);await until(()=>d.querySelector('#statExpectedHours').textContent==='152 Hours');
    await w.PeopleWorkspace.navigate('/leave');const beforePaused=requests();
    w.dispatchEvent(new w.Event('focus'));await app.tickIntervals();assert.equal(requests(),beforePaused);
    app.setApprovedVacation('12-13,16');await w.PeopleWorkspace.navigate('/timesheet');
    await until(()=>d.querySelector('#statExpectedHours').textContent==='144 Hours');
    assert.equal(d.querySelector('#people-view'),editor);assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('refresh removes withdrawn approved dates while preserving manually entered absences',async()=>{
  const app=setup({realTimesheet:true,approvedVacation:'12-13',fakeIntervals:true});try{
    const {d,w}=app;await novemberEditor(app);
    d.querySelector('#vacationPickerInput')._flatpickr.setDate(w.Array.of(new w.Date(2026,10,9),new w.Date(2026,10,9)),true);
    await until(()=>d.querySelector('#vacation').value==='9,12-13');
    d.querySelector('#custom_reason').value='Keep the manual draft';
    app.setApprovedVacation('');await w.PeopleWorkspace.navigate('/leave');await w.PeopleWorkspace.navigate('/timesheet');
    await until(()=>d.querySelector('#vacation').value==='9');
    assert.equal(d.querySelector('#custom_reason').value,'Keep the manual draft');
    assert.equal(d.querySelector('#statExpectedHours').textContent,'160 Hours');
    assert.equal(d.querySelector('#vacationDisplay .approved-period'),null);assert.deepEqual(app.errors,[]);
  }finally{app.close();}
});

test('a late background approval response cannot rewrite a document while it is downloading',async()=>{
  const app=setup({realTimesheet:true,productionRole:'employee',fakeIntervals:true});let finishDownload;try{
    const {d,w}=app;await novemberEditor(app);
    const form=d.querySelector('#timesheetForm'),editor=d.querySelector('#people-view');
    const reason=d.querySelector('#custom_reason');reason.value='Keep the downloading snapshot';reason.dispatchEvent(new w.Event('input',{bubbles:true}));
    const draftKey=w.WorkspaceCore.periodDraftKey(String(fixtures.production_people.employee),2026,11);
    const beforeDraft=w.localStorage.getItem(draftKey);assert.ok(beforeDraft,'The employee draft must exist before downloading');
    const nativeClick=w.HTMLAnchorElement.prototype.click;w.HTMLAnchorElement.prototype.click=function(){if(!this.download)nativeClick.call(this);};
    let downloadStarted=false,periodDelivered=false,sentDownload;
    const pendingDownload=new Promise(resolve=>{finishDownload=resolve;}),priorFetch=w.fetch;
    w.fetch=async(value,options={})=>{
      const path=new URL(String(value),origin).pathname;
      if(path==='/generate'){downloadStarted=true;sentDownload=options.body;return pendingDownload;}
      const response=await priorFetch(value,options);if(path==='/timesheets/period')periodDelivered=true;return response;
    };
    app.hold('/timesheets/period');app.setApprovedVacation('12-13');
    const periodCount=app.calls.filter(call=>call.path==='/timesheets/period').length;
    w.dispatchEvent(new w.Event('focus'));
    await until(()=>app.calls.filter(call=>call.path==='/timesheets/period').length>periodCount);
    form.requestSubmit();await until(()=>downloadStarted);assert.equal(form.inert,true);
    assert.equal(sentDownload.get('vacation'),'');
    app.release();await until(()=>periodDelivered);await new Promise(resolve=>setImmediate(resolve));
    assert.equal(d.querySelector('#vacation').value,'','The download must keep the absence snapshot it started with');
    assert.equal(d.querySelector('#statExpectedHours').textContent,'168 Hours');
    assert.equal(reason.value,'Keep the downloading snapshot');assert.equal(w.localStorage.getItem(draftKey),beforeDraft);
    assert.equal(form.inert,true);assert.equal(d.querySelector('#people-view'),editor);
    finishDownload(new Response('%PDF-test',{headers:{'content-type':'application/pdf'}}));
    await until(()=>!d.querySelector('#generateBtn').disabled);
    await app.tickIntervals();await until(()=>d.querySelector('#vacation').value==='12-13'&&d.querySelector('#statExpectedHours').textContent==='152 Hours');
    assert.equal(reason.value,'Keep the downloading snapshot');assert.deepEqual(app.errors,[]);
  }finally{finishDownload?.(new Response('%PDF-test',{headers:{'content-type':'application/pdf'}}));app.release();app.close();}
});
