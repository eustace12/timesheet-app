/* Persistent shell, retained editors, and contextual actions. No script evaluation. */
(function(){
'use strict';
const {workspaceURL,routeKey,signInURL}=WorkspaceCore;
let draftStorage;try{draftStorage=sessionStorage;}catch{}
const drafts=WorkspaceDrafts.create(draftStorage);
const roleForm=document.querySelector('#demo-role-selector');
let demoRole=document.body.dataset.demoRole||'employee';
const roleRoutes={employee:'/',accountant:'/admin'};
const sidebar=document.querySelector('.people-sidebar');
const profile=document.querySelector('.profile-selector');
const select=profile?.querySelector('select');
const progress=document.querySelector('.navigation-progress');
const announcer=document.querySelector('#workspace-announcer');
const shellStyle=document.querySelector('link[href="/static/people-shell.css"]');
const notice=document.querySelector('.workspace-toast');
const dialog=document.querySelector('#leave-drawer');
const leaveForm=dialog.querySelector('form');
const leaveButton=leaveForm.querySelector('[type=submit]');
let person=select?.value||profile?.dataset.person||'none', active, sequence=0, writes=0, switching=false, signingIn=false, toastTimer;
const views=new Map(), pendingPages=new Map();
const key=url=>routeKey(url,person,location.origin);
const route=url=>workspaceURL(url,location.origin);
const noOp={activate(){},pause(){},destroy(){}};
sidebar.dataset.shellInstance=String(Date.now());

function toast(message,action,callback){
  clearTimeout(toastTimer);
  notice.querySelector('span').textContent=message;
  const button=notice.querySelector('.toast-action');
  button.hidden=!action;button.textContent=action||'';
  button.onclick=callback?()=>{notice.hidden=true;callback();}:null;
  notice.classList.remove('toast-error');notice.hidden=false;
  if(!action)toastTimer=setTimeout(()=>{notice.hidden=true;},6000);
}
function failure(message,retry){
  toast(message,retry?'Retry':null,retry);notice.classList.add('toast-error');
}
function clearFormError(form){
  const error=form.querySelector('[data-form-error]');if(error)error.hidden=true;
  form.querySelectorAll('[aria-invalid="true"]').forEach(field=>{field.removeAttribute('aria-invalid');
    const references=(field.getAttribute('aria-describedby')||'').split(' ').filter(id=>id&&id!==error?.id);
    if(references.length)field.setAttribute('aria-describedby',references.join(' '));else field.removeAttribute('aria-describedby');
  });
}
function formError(form,message,field){
  let error=form.querySelector('[data-form-error]');
  if(!error){error=document.createElement('p');error.dataset.formError='1';error.className='form-inline-error';error.setAttribute('role','alert');error.id='form-error-'+crypto.randomUUID();form.append(error);}
  error.textContent=message;error.hidden=false;
  if(field){field.after(error);field.setAttribute('aria-invalid','true');field.setAttribute('aria-describedby',((field.getAttribute('aria-describedby')||'')+' '+error.id).trim());field.focus();}
}
notice.querySelector('.toast-dismiss').addEventListener('click',()=>{notice.hidden=true;});
function busy(value){
  progress.hidden=!value;
  document.querySelector('#people-view')?.setAttribute('aria-busy',String(value));
}
function lockProfile(){if(select)select.disabled=person==='none'||writes>0||switching;roleForm?.querySelectorAll('button').forEach(button=>{button.disabled=writes>0||switching;});}
async function resumeSignIn(response){
  if(response.status!==401||signingIn)return;
  const data=await response.clone().json().catch(()=>null);
  const url=signInURL(data?.login_url,location.href,location.origin);if(!url)return;
  signingIn=true;saveDrafts();active?.controller?.pause();location.assign(url);
}
async function trackedFetch(url,options={}){
  const isWrite=options.method&&options.method.toUpperCase()!=='GET';
  if(isWrite){writes++;lockProfile();}
  try{const response=await fetch(url,{credentials:'same-origin',...options});await resumeSignIn(response);return response;}
  finally{if(isWrite){writes--;lockProfile();}}
}
function parsePage(html,url){
  const doc=new DOMParser().parseFromString(html,'text/html');
  const content=doc.querySelector('#people-view');
  if(!content)throw new Error('The section could not be loaded.');
  // Only declarative translation data is imported; executable scripts are never run.
  content.querySelectorAll('script:not([type="application/json"])').forEach(node=>node.remove());
  return {root:document.importNode(content,true),title:doc.title,url,
    styles:Array.from(doc.head.querySelectorAll('[data-view-style]'),node=>node.cloneNode(true)),
    person:doc.querySelector('#selected_person')?.value||doc.querySelector('.company-identity')?.dataset.person||'none',
    people:Array.from(doc.querySelectorAll('#selected_person option'),option=>({value:option.value,label:option.textContent})),
    role:doc.body.dataset.demoRole||'employee',controller:null,scroll:0,dirty:false};
}
function currentPage(){
  return {root:document.querySelector('#people-view'),title:document.title,url:location.href,
    styles:Array.from(document.head.querySelectorAll('[data-view-style]'),node=>node.cloneNode(true)),
    person,controller:null,scroll:window.scrollY,dirty:false};
}
function editableForms(entry){
  return Array.from(entry.root.querySelectorAll('form')).filter(form=>form.method.toLowerCase()==='post'&&form.id!=='timesheetForm');
}
function prepareEntry(entry){
  editableForms(entry).forEach(form=>{
    if(!form.dataset.draftId)form.dataset.draftId=new URL(form.getAttribute('action')||entry.url,location.origin).pathname;
    drafts.restore(form,person);
    if(form.dataset.dirty==='true')form.closest('details')?.setAttribute('open','');
  });
  entry.root.querySelectorAll('table:not(.preview-table)').forEach(table=>{
    const labels=Array.from(table.querySelectorAll('thead th'),cell=>cell.textContent.trim());
    if(!labels.length)return;table.classList.add('responsive-records');
    table.querySelectorAll('tbody tr').forEach(row=>{
      if(Array.from(row.cells).some(cell=>cell.colSpan>1)){row.classList.add('empty-row');return;}
      Array.from(row.cells).forEach((cell,index)=>{cell.dataset.label=labels[index]||'';});
    });
  });
}
function saveDrafts(){
  for(const entry of views.values())if(entry.person===person)editableForms(entry).forEach(form=>drafts.capture(form,person));
  if(leaveForm.dataset.draftOwner===person)drafts.capture(leaveForm,person);
}
function applyRole(role){
  demoRole=role;document.body.dataset.demoRole=role;sidebar.dataset.demoRole=role;
  roleForm?.querySelectorAll('button[name=role]').forEach(button=>button.setAttribute('aria-pressed',String(button.value===role)));
  const personal=sidebar.querySelector('.personal-nav'),accounting=sidebar.querySelector('.accounting-nav');
  if(personal&&accounting)sidebar.insertBefore(role==='accountant'?accounting:personal,role==='accountant'?personal:accounting);
  const label=profile?.querySelector('label');if(label)label.textContent=role==='accountant'?'Employee preview':'Employee';
}
function filterRecords(root,value){
  root.dataset.activeRecordFilter=value;
  const search=(root.querySelector('[data-record-search]')?.value||'').trim().toLocaleLowerCase();
  root.querySelectorAll('[data-record-filter]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.recordFilter===value)));
  root.querySelectorAll('[data-record-status]').forEach(card=>{card.hidden=(value!=='all'&&card.dataset.recordStatus!==value)||Boolean(search&&!(card.dataset.searchText||'').toLocaleLowerCase().includes(search));});
  const emptyHeading=root.querySelector('.empty-filter h3'),emptyText=root.querySelector('.empty-filter p');
  if(emptyHeading&&emptyText){const caughtUp=value==='submitted'&&!search;emptyHeading.textContent=caughtUp?"You're all caught up":'No matching requests';emptyText.textContent=caughtUp?'No requests are waiting for review in this period.':'Try another status, employee or month.';}
  root.querySelectorAll('.record-section').forEach(section=>{
    const cards=Array.from(section.querySelectorAll('[data-record-status]'));
    const empty=section.querySelector('.empty-filter');if(empty)empty.hidden=!cards.length||cards.some(card=>!card.hidden);
  });
}
function controller(entry){
  if(!entry.controller){
    const init=window.PeopleViews?.[entry.root.dataset.view];
    entry.controller=init?init(entry.root,person):noOp;
  }
  return entry.controller;
}
function trimCache(){
  for(const [name,entry] of views){
    if(views.size<=10)break;
    if(entry!==active&&entry.root.dataset.view!=='timesheet'){
      entry.controller?.destroy();views.delete(name);
    }
  }
}
async function fetchPage(url){
  const name=key(url);
  if(!pendingPages.has(name)){
    const request=(async()=>{
      const abort=new AbortController();const timer=setTimeout(()=>abort.abort(),20000);
      try{
        const response=await trackedFetch(url,{headers:{'X-Workspace-Navigation':'1'},signal:abort.signal});
        if(!response.ok)throw new Error('Unable to load this section.');
        return {html:await response.text(),url:response.url};
      }finally{clearTimeout(timer);}
    })();
    pendingPages.set(name,request);
    request.catch(()=>{}).finally(()=>{if(pendingPages.get(name)===request)pendingPages.delete(name);});
  }
  return pendingPages.get(name);
}
async function installStyles(entry,isCurrent){
  const nodes=entry.styles.map(source=>source.cloneNode(true));
  const loaded=nodes.map(node=>new Promise((resolve,reject)=>{
    node.dataset.pendingStyle='1';
    if(node.tagName==='LINK'){
      const timer=setTimeout(()=>reject(new Error('Styles could not be loaded.')),15000);
      node.media='not all';
      node.onload=()=>{clearTimeout(timer);resolve();};
      node.onerror=()=>{clearTimeout(timer);reject(new Error('Styles could not be loaded.'));};
    }
    document.head.insertBefore(node,shellStyle);
    if(node.tagName!=='LINK')resolve();
  }));
  try{
    await Promise.all(loaded);
    if(!isCurrent()){nodes.forEach(node=>node.remove());return false;}
    document.head.querySelectorAll('[data-view-style]:not([data-pending-style])').forEach(node=>node.remove());
    nodes.forEach(node=>{node.removeAttribute('media');delete node.dataset.pendingStyle;});
    return true;
  }catch(error){nodes.forEach(node=>node.remove());throw error;}
}
function setNavigation(url){
  const path=new URL(url,location.origin).pathname;
  const isLeave=['/leave','/leave-balances','/team-calendar'].includes(path);
  sidebar.querySelectorAll('.people-nav a').forEach(link=>{
    const target=new URL(link.href).pathname;
    const selected=target==='/'?['/','/workspace'].includes(path):target==='/timesheet'?['/timesheet','/documents'].includes(path):target==='/leave'?isLeave&&!(demoRole==='accountant'&&['/leave-balances','/team-calendar'].includes(path)):target==='/admin/users'?path.startsWith(target):target==='/team-calendar'?['/team-calendar','/office'].includes(path)&&demoRole==='accountant':target==='/office'?path==='/office'&&demoRole==='employee':target===path;
    link.classList.toggle('selected',selected);link.removeAttribute('aria-current');
    if(selected)link.setAttribute('aria-current','page');
  });
  document.querySelector('.people-breadcrumb strong').textContent=path==='/office'?'Office plans':isLeave?'Time off':['/','/workspace'].includes(path)?'Home':path==='/history'?'My submissions':path==='/admin/audit'?'Audit log':path==='/admin'?'Approvals':path.startsWith('/admin/users')?'People':'Monthly timesheet';
  document.body.dataset.activeSection=path.startsWith('/admin/users')?'people':['/admin','/admin/audit'].includes(path)?'approvals':'personal';
  const label=profile?.querySelector('label');if(label)label.textContent=demoRole==='accountant'?(path==='/leave-balances'?'Allowance for':'Employee preview'):'Employee';
  const next=profile?.querySelector('[name=next]');if(next)next.value=path;
}
async function activate(entry,url,{historyMode='push',restoreScroll=true,token=++sequence}={}){
  if(!await installStyles(entry,()=>token===sequence))return false;
  saveDrafts();
  if(new URL(active.url,location.origin).pathname===new URL(url,location.origin).pathname&&active.root.dataset.activeRecordFilter)entry.root.dataset.activeRecordFilter=active.root.dataset.activeRecordFilter;
  if(new URL(active.url,location.origin).pathname===new URL(url,location.origin).pathname){const previous=active.root.querySelector('[data-record-search]'),next=entry.root.querySelector('[data-record-search]');if(previous&&next)next.value=previous.value;}
  prepareEntry(entry);filterRecords(entry.root,entry.root.dataset.activeRecordFilter||entry.root.dataset.defaultRecordFilter||'all');
  active.scroll=window.scrollY;active.controller?.pause();
  active.root.replaceWith(entry.root);active=entry;
  controller(entry).activate();document.title=entry.title;setNavigation(url);
  if(historyMode==='push')history.pushState({workspace:true},'',url);
  else if(historyMode==='replace')history.replaceState({workspace:true},'',url);
  entry.url=url;
  announcer.textContent=entry.root.querySelector('h1')?.textContent||'Section loaded';
  entry.root.focus({preventScroll:true});
  window.scrollTo({top:restoreScroll?entry.scroll:0,behavior:'instant'});
  trimCache();return true;
}
function matchesRequestedPeriod(entry,url){
  if(!entry||entry.root.dataset.view!=='timesheet')return true;
  return ['year','month'].every(field=>!url.searchParams.has(field)||Number(entry.root.querySelector('#'+field)?.value)===Number(url.searchParams.get(field)));
}
async function navigate(value,{replace=false,force=false,fromHistory=false,keepScroll=false}={}){
  const url=route(value);if(!url||switching)return;
  const token=++sequence;const name=key(url.href);
  const previousURL=active.url;
  if(!force&&key(active.url)===name&&matchesRequestedPeriod(active,url)){if(fromHistory)setNavigation(url);busy(false);return;}
  busy(true);
  try{
    let entry=views.get(name);
    if(force||!entry||entry.dirty||!matchesRequestedPeriod(entry,url)){
      const page=await fetchPage(url.href);
      if(token!==sequence)return;
      entry=parsePage(page.html,page.url);
      if(entry.person!==person)throw new Error('Employee context changed. Select the employee again.');
    }
    if(token!==sequence)return;
    const old=views.get(name);if(keepScroll)entry.scroll=window.scrollY;
    if(await activate(entry,url.href,{historyMode:fromHistory?'none':replace?'replace':'push',token})){
      if(old&&old!==entry)old.controller?.destroy();
      views.set(name,entry);
    }
  }catch(error){
    if(token===sequence){
      if(fromHistory)history.replaceState({workspace:true},'',previousURL);
      failure('Could not open this section. Your current work is still here.',()=>navigate(value,{replace,force,keepScroll}));
    }
  }finally{if(token===sequence)busy(false);}
}
function invalidateData(paths){
  pendingPages.clear();
  for(const entry of views.values())if(entry.root.dataset.view!=='timesheet'&&(!paths||paths.includes(new URL(entry.url,location.origin).pathname)))entry.dirty=true;
}
function syncPeople(entry){
  if(!select)return;
  if(entry.person!==person){
    for(const view of views.values())if(view!==active)view.controller?.destroy();
    views.clear();pendingPages.clear();person=entry.person;leaveForm.reset();delete leaveForm.dataset.dirty;leaveForm.querySelector('[data-draft-status]')?.remove();leaveForm.dataset.draftOwner=person;drafts.restore(leaveForm,person);
  }
  select.replaceChildren(...entry.people.map(item=>{
    const option=document.createElement('option');option.value=item.value;option.textContent=item.label;
    option.selected=item.value===person;return option;
  }));
  dialog.querySelector('[data-request-person]').textContent=select.selectedOptions[0]?.textContent||'Select an employee';
  dialog.querySelector('.person-avatar').textContent=person==='none'?'?':select.selectedOptions[0].textContent.trim()[0];
  document.querySelector('.people-topbar [data-request-leave]').disabled=person==='none';
  lockProfile();
}
function periodChanged(root,{year,month}){
  if(root!==active.root||root.dataset.view!=='timesheet'||!Number.isInteger(year)||!Number.isInteger(month)||year<2020||year>2099||month<1||month>12)return;
  const url=new URL(active.url,location.origin),previous=key(active.url);url.searchParams.set('year',year);url.searchParams.set('month',month);
  const next=key(url.href),replaced=views.get(next);if(replaced&&replaced!==active)replaced.controller?.destroy();
  if(views.get(previous)===active)views.delete(previous);
  active.url=url.href;views.set(next,active);history.replaceState({workspace:true},'',url.href);
}
window.PeopleWorkspace={
  navigate,request:trackedFetch,periodChanged,
  invalidateScreens(paths){invalidateData(paths);},
  previewDocument(href,title){return openDocument({href:new URL(href,location.origin).href,dataset:{documentTitle:title}});},
  previewDraft(values,title){
    const data=new FormData();for(const [name,value] of values)data.append(name,value);data.set('format','pdf');
    return openDocument({href:location.origin+'/generate',dataset:{documentTitle:title}},{method:'POST',body:data});
  }
};
active=currentPage();views.set(key(location.href),active);prepareEntry(active);filterRecords(active.root,active.root.dataset.defaultRecordFilter||'all');leaveForm.dataset.draftOwner=person;drafts.restore(leaveForm,person);applyRole(demoRole);setNavigation(active.url);controller(active).activate();
history.replaceState({workspace:true},'',location.href);
if('scrollRestoration' in history)history.scrollRestoration='manual';
window.addEventListener('pagehide',saveDrafts);
window.addEventListener('popstate',()=>{
  document.querySelectorAll('dialog[open]').forEach(openDialog=>openDialog.close());
  navigate(location.href,{fromHistory:true});
});
window.addEventListener('people:notice',event=>failure(event.detail));
window.addEventListener('people:data-changed',()=>{
  invalidateData();
  if(active.root.dataset.view!=='timesheet')navigate(active.url,{replace:true,force:true,keepScroll:true});
});
document.addEventListener('click',event=>{
  const reveal=event.target.closest('[data-open-form]');if(reveal){const detail=document.getElementById(reveal.dataset.openForm);if(detail){detail.open=true;detail.querySelector('input:not([type=hidden])')?.focus();detail.scrollIntoView({block:'nearest',behavior:'smooth'});}return;}
  const mode=event.target.closest('[data-demo-role-trigger]');if(mode){event.preventDefault();changeDemoRole(mode.dataset.demoRoleTrigger);return;}
  const filter=event.target.closest('[data-record-filter]');if(filter){filterRecords(active.root,filter.dataset.recordFilter);return;}
  const documentLink=event.target.closest('[data-document-preview]');if(documentLink){event.preventDefault();openDocument(documentLink);return;}
  const request=event.target.closest('[data-request-leave]');
  if(request){event.preventDefault();openLeave(request);return;}
  const link=event.target.closest('a[href]');
  if(!link||event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||link.download||link.target)return;
  const url=route(link.href);if(!url)return;
  if(url.hash&&url.pathname===location.pathname)return;
  event.preventDefault();if(!switching)navigate(url.href);
});
// Warm only the intended navigation target, without replacing or discarding work.
let prefetchTimer;
document.addEventListener('pointerover',event=>{
  const link=event.target.closest('.people-nav a,.leave-tabs a');if(!link)return;
  clearTimeout(prefetchTimer);
  if(!views.has(key(link.href)))prefetchTimer=setTimeout(()=>{if(!views.has(key(link.href)))fetchPage(link.href).catch(()=>{});},120);
});
document.addEventListener('submit',async event=>{
  const form=event.target;
  if(form===roleForm){event.preventDefault();changeDemoRole(event.submitter?.value);return;}
  if(event.defaultPrevented||form===leaveForm||form===profile||form.id==='timesheetForm')return;
  if(form.closest('#people-view')===null)return;
  event.preventDefault();
  clearFormError(form);
  if(form.classList.contains('review-form')&&event.submitter?.value==='needs_changes'&&!form.elements.review_comment.value.trim()){
    formError(form,'Add a comment so the employee knows what to change.',form.elements.review_comment);return;
  }
  if(form.hasAttribute('data-confirm-delete')&&form.dataset.confirmed!=='true'){confirmDelete(form,event.submitter);return;}
  delete form.dataset.confirmed;
  const method=(form.method||'get').toUpperCase();
  const data=new FormData(form);if(event.submitter?.name)data.set(event.submitter.name,event.submitter.value);
  if(method==='GET'){
    const url=route(form.action);if(!url)return;
    url.search=new URLSearchParams(data).toString();navigate(url.href);return;
  }
  saveDrafts();
  const entry=active, token=sequence, currentPerson=person;
  const buttons=Array.from(form.querySelectorAll('button[type=submit],button:not([type])'));
  const disabled=buttons.map(button=>button.disabled);buttons.forEach(button=>{button.disabled=true;});
  form.setAttribute('aria-busy','true');form.inert=true;
  try{
    const response=await trackedFetch(form.action,{method,body:data});
    if((response.headers.get('content-type')||'').includes('application/json')){const data=await response.json();throw new Error(data.error||'Unable to save. Please try again.');}
    const html=await response.text();
    if(!(response.headers.get('content-type')||'').includes('text/html'))throw new Error('Unable to save. Check the details and try again.');
    const updated=parsePage(html,response.url);
    const validation=updated.root.querySelector('.alert.error');
    if(!response.ok||validation)throw new Error(validation?.textContent.trim()||'Unable to save. Check the details and try again.');
    saveDrafts();drafts.clear(form,currentPerson);invalidateData();
    if(entry===active&&token===sequence&&currentPerson===person){
      if(new URL(form.action).pathname.startsWith('/admin/users'))syncPeople(updated);
      updated.scroll=window.scrollY;
      const next=++sequence;
      if(await activate(updated,response.url,{historyMode:'replace',token:next}))views.set(key(response.url),updated);
    }
    toast('Changes saved.');
  }catch(error){if(form.isConnected)formError(form,error.message);else failure(error.message);}
  finally{buttons.forEach((button,index)=>{button.disabled=disabled[index];});form.removeAttribute('aria-busy');form.inert=false;}
});
profile?.addEventListener('submit',event=>{event.preventDefault();select?.dispatchEvent(new Event('change'));});
if(select)select.addEventListener('change',async()=>{
  const oldPerson=person,oldLabel=Array.from(select.options).find(o=>o.value===person)?.text;
  const nextPerson=select.value;
  saveDrafts();switching=true;lockProfile();busy(true);sequence++;
  active.controller?.pause();active.root.inert=true;
  const data=new FormData(profile);data.set('user_id',nextPerson);data.set('next',location.pathname+location.search);
  try{
    const response=await trackedFetch('/profile',{method:'POST',body:data});
    if(!response.ok)throw new Error('Unable to switch employee.');
    const entry=parsePage(await response.text(),response.url);
    if(entry.person!==nextPerson)throw new Error('Employee context did not update.');
    person=nextPerson;pendingPages.clear();
    for(const view of views.values())if(view!==active)view.controller?.destroy();
    const former=active;views.clear();
    await activate(entry,response.url,{historyMode:'replace',token:++sequence});
    former.controller?.destroy();views.set(key(response.url),entry);
    leaveForm.reset();delete leaveForm.dataset.dirty;leaveForm.querySelector('[data-draft-status]')?.remove();leaveForm.dataset.draftOwner=person;drafts.restore(leaveForm,person);syncPeople(entry);
    updateEstimate();toast('Switched to '+select.selectedOptions[0].text+'.');
  }catch(error){
    // Restore server and visible employee together before enabling old forms.
    select.value=oldPerson;data.set('user_id',oldPerson);
    try{
      const restored=await trackedFetch('/profile',{method:'POST',body:data});
      if(!restored.ok)throw new Error('Reconnection required.');
      const doc=parsePage(await restored.text(),restored.url);
      if(doc.person!==oldPerson)throw new Error('Reconnection required.');
      person=oldPerson;active.root.inert=false;
      failure('Could not switch employee. Still working as '+oldLabel+'.');
    }catch{
      failure('Connection lost. Reconnect before editing.',()=>location.reload());
      return;
    }
  }finally{if(!active.root.inert){switching=false;lockProfile();}busy(false);}
});

document.addEventListener('input',event=>{if(event.target.matches('[data-record-search]'))filterRecords(active.root,active.root.dataset.activeRecordFilter||'all');});
document.addEventListener('input',event=>{if(event.target.form)clearFormError(event.target.form);});
document.addEventListener('input',keepDraft);
document.addEventListener('change',keepDraft);
function keepDraft(event){
  const form=event.target.form;
  if(!form||form===profile||form===roleForm||form.id==='timesheetForm'||form.method.toLowerCase()!=='post')return;
  form.dataset.dirty='true';drafts.capture(form,person);
}
async function changeDemoRole(nextRole){
  if(!['employee','accountant'].includes(nextRole)||nextRole===demoRole||switching||writes)return;
  saveDrafts();const previous=demoRole,path=new URL(active.url,location.origin).pathname;
  if(previous==='employee'?!path.startsWith('/admin'):(path.startsWith('/admin')||['/leave-balances','/team-calendar'].includes(path)))roleRoutes[previous]=active.url;
  const target=roleRoutes[nextRole];switching=true;lockProfile();busy(true);sequence++;
  active.controller?.pause();active.root.inert=true;
  const data=new FormData(roleForm);data.set('role',nextRole);data.set('next',new URL(target,location.origin).pathname+new URL(target,location.origin).search);
  try{
    const response=await trackedFetch('/demo/role',{method:'POST',body:data});
    if(!response.ok)throw new Error('Role switch failed.');
    const fresh=parsePage(await response.text(),response.url);
    if(fresh.role!==nextRole)throw new Error('Role did not update.');
    const cached=views.get(key(target)),entry=cached&&!cached.dirty?cached:fresh;
    applyRole(nextRole);active.root.inert=false;entry.root.inert=false;
    await activate(entry,response.url,{historyMode:'push',token:++sequence});
    views.set(key(response.url),entry);
    toast(nextRole==='accountant'?'Accountant demo: review documents and manage the team.':'Employee demo: prepare timesheets and request leave.');
  }catch{
    data.set('role',previous);data.set('next',new URL(active.url,location.origin).pathname+new URL(active.url,location.origin).search);
    try{const restored=await trackedFetch('/demo/role',{method:'POST',body:data});if(!restored.ok)throw new Error();applyRole(previous);active.root.inert=false;failure('Could not switch demo role. Your draft is still here.');}
    catch{failure('Connection lost. Reconnect before editing.',()=>location.reload());}
  }finally{if(!active.root.inert)switching=false;lockProfile();busy(false);}
}
const deleteDialog=document.querySelector('#delete-person-dialog');
let pendingDelete;
function confirmDelete(form,submitter){
  pendingDelete={form,submitter};deleteDialog.querySelector('[data-delete-description]').textContent='Remove '+form.dataset.personName+' from the team?';deleteDialog.showModal();
}
deleteDialog?.querySelector('[data-cancel-delete]').addEventListener('click',()=>deleteDialog.close());
deleteDialog?.querySelector('[data-approve-delete]').addEventListener('click',()=>{
  const pending=pendingDelete;deleteDialog.close();if(!pending)return;
  pending.form.dataset.confirmed='true';if(pending.submitter)pending.form.requestSubmit(pending.submitter);else pending.form.requestSubmit();
});
deleteDialog?.addEventListener('close',()=>{pendingDelete=null;});
const documentDialog=document.querySelector('#document-preview');
let documentVersion=0,documentHandle,documentZoom=1,documentBlob,documentRequest;
function releaseDocument(){documentRequest?.abort();documentHandle?.destroy();documentHandle=null;if(documentBlob){URL.revokeObjectURL(documentBlob);documentBlob=null;}}
async function openDocument(link,options){
  if(!documentDialog)return;
  const url=new URL(link.href,location.origin);
  const draft=url.pathname==='/generate'&&options?.method==='POST';
  if(url.origin!==location.origin||(!draft&&!/^\/admin\/(timesheets|leave)\/\d+\/view$/.test(url.pathname)))return;
  const version=++documentVersion,state=documentDialog.querySelector('.document-preview-state');
  releaseDocument();documentRequest=new AbortController();
  documentDialog.querySelector('.document-pages').replaceChildren();
  documentDialog.querySelector('[data-document-pages-count]').textContent='';
  documentDialog.querySelector('[data-pdf-zoom=fit]').textContent='Fit width';
  documentDialog.querySelector('h2').textContent=link.dataset.documentTitle||'Document preview';
  const external=documentDialog.querySelector('[data-document-open]');external.hidden=draft;external.textContent=draft?'Download PDF preview':'Open document';external.href=link.href;
  if(draft){external.download='timesheet-preview.pdf';external.removeAttribute('target');}else{external.removeAttribute('download');external.target='_blank';}
  state.textContent='Loading document…';state.hidden=false;documentDialog.querySelectorAll('[data-pdf-zoom]').forEach(button=>{button.disabled=true;});documentDialog.showModal();
  try{
    const response=await trackedFetch(link.href,{...options,signal:documentRequest.signal});
    if(!response.ok||!(response.headers.get('content-type')||'').includes('application/pdf'))throw new Error();
    const buffer=await response.arrayBuffer();if(version!==documentVersion)return;
    if(draft){documentBlob=URL.createObjectURL(new Blob([buffer],{type:'application/pdf'}));external.href=documentBlob;external.hidden=false;}
    const handle=await WorkspacePDF.open(buffer,documentDialog.querySelector('.document-pages'),()=>version===documentVersion);
    if(version!==documentVersion){handle.destroy();return;}
    documentHandle=handle;documentZoom=1;await handle.render(documentZoom);
    if(version!==documentVersion)return;state.hidden=true;
    documentDialog.querySelector('[data-document-pages-count]').textContent=handle.pageCount+' '+(handle.pageCount===1?'page':'pages');
    documentDialog.querySelector('[data-pdf-zoom=fit]').textContent='Fit width';documentDialog.querySelectorAll('[data-pdf-zoom]').forEach(button=>{button.disabled=false;});
  }catch{if(version===documentVersion)state.textContent=draft?'Could not create the PDF preview. Close and try Preview again.':'Preview unavailable. Download the document or try opening it directly.';}
}
function closeDocument(){documentDialog?.close();}
documentDialog?.querySelectorAll('[data-close-document]').forEach(button=>button.addEventListener('click',closeDocument));
documentDialog?.addEventListener('close',()=>{if(documentDialog.open)return;documentVersion++;releaseDocument();});
documentDialog?.querySelectorAll('[data-pdf-zoom]').forEach(button=>button.addEventListener('click',async()=>{
  if(!documentHandle)return;const version=documentVersion;
  documentZoom=button.dataset.pdfZoom==='fit'?1:Math.max(.75,Math.min(3,documentZoom+(button.dataset.pdfZoom==='in'?.25:-.25)));
  documentDialog.querySelectorAll('[data-pdf-zoom]').forEach(control=>{control.disabled=true;});
  try{await documentHandle.render(documentZoom);if(version===documentVersion)documentDialog.querySelector('[data-pdf-zoom=fit]').textContent=documentZoom===1?'Fit width':Math.round(documentZoom*100)+'%';}
  catch{if(version===documentVersion){const state=documentDialog.querySelector('.document-preview-state');state.textContent='Could not render this zoom level. Try Fit width.';state.hidden=false;}}
  finally{if(version===documentVersion)documentDialog.querySelectorAll('[data-pdf-zoom]').forEach(control=>{control.disabled=false;});}
}));
// Contextual leave request: native dialog supplies focus containment and Escape.
let submittingLeave=false,estimateVersion=0;
function openLeave(trigger){
  if(switching||person==='none')return;
  active.controller?.pause();
  if(trigger?.dataset.leaveCopy){try{const values=JSON.parse(trigger.dataset.leaveCopy);for(const [name,value] of Object.entries(values))if(leaveForm.elements[name])leaveForm.elements[name].value=value;leaveForm.dataset.dirty='true';drafts.capture(leaveForm,person);}catch{}}
  dialog.querySelector('#leaveResult').hidden=true;
  updateEstimate();
  if(!dialog.open)dialog.showModal();
}
function closeLeave(){if(!submittingLeave)dialog.close();}
dialog.querySelectorAll('[data-close-leave]').forEach(button=>button.addEventListener('click',closeLeave));
dialog.addEventListener('cancel',event=>{if(submittingLeave)event.preventDefault();});
dialog.addEventListener('click',event=>{
  const box=dialog.getBoundingClientRect();
  if(event.target===dialog&&(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom))closeLeave();
});
const start=leaveForm.elements.start_date,end=leaveForm.elements.end_date;
const leaveDates=window.LeaveDatePicker?.create(leaveForm);
let assessmentReady=false;
async function updateEstimate(){
  leaveDates?.sync();
  dialog.querySelector('#leaveResult').hidden=true;
  const version=++estimateVersion,output=dialog.querySelector('[data-leave-days]'),context=dialog.querySelector('[data-leave-context]');
  assessmentReady=false;context.replaceChildren();end.min=start.value||'';end.setCustomValidity('');
  if(!start.value||!end.value){output.textContent='Select your dates';leaveButton.disabled=false;return;}
  if(start.value>end.value){end.setCustomValidity('End date must be on or after start date.');output.textContent='Check the date range';leaveButton.disabled=false;return;}
  output.textContent='Checking your dates…';leaveButton.disabled=true;
  try{
    const query=new URLSearchParams({start_date:start.value,end_date:end.value,leave_type:leaveForm.elements.leave_type.value});
    const data=await PeopleExperience.loadAssessment('/leave/assessment?'+query);
    if(version!==estimateVersion)return;
    output.textContent=data.working_days+' working '+(data.working_days===1?'day':'days')+' · '+data.calendar_days+' calendar '+(data.calendar_days===1?'day':'days');
    PeopleExperience.renderAssessment(context,data);assessmentReady=data.can_submit;leaveButton.disabled=!data.can_submit;
  }catch(error){if(version!==estimateVersion)return;output.textContent='Date check unavailable';const message=document.createElement('p');message.className='assessment-message warning';message.textContent=error.message+' Try again before sending.';context.append(message);leaveButton.disabled=false;}
}
start.addEventListener('change',()=>{if(start.value&&(!end.value||end.value<start.value))end.value=start.value;updateEstimate();});end.addEventListener('change',updateEstimate);leaveForm.elements.leave_type.addEventListener('change',updateEstimate);updateEstimate();
leaveForm.addEventListener('submit',async event=>{
  event.preventDefault();if(submittingLeave)return;
  if(!start.value||!end.value){const error=dialog.querySelector('#leaveResult');error.textContent='Choose your leave dates before sending the request.';error.hidden=false;leaveDates?.focus();return;}
  if(!leaveForm.reportValidity())return;
  if(!assessmentReady){await updateEstimate();if(!assessmentReady)return;}
  submittingLeave=true;leaveForm.inert=true;leaveButton.disabled=true;
  leaveButton.textContent='Sending request…';
  dialog.querySelectorAll('[data-close-leave]').forEach(button=>{button.disabled=true;});
  const error=dialog.querySelector('#leaveResult');error.hidden=true;
  try{
    const response=await trackedFetch('/leave/submit',{method:'POST',body:new FormData(leaveForm)});
    const data=await response.json();if(!response.ok||!data.ok)throw new Error(data.error||'Unable to send your request. Please try again.');
    drafts.clear(leaveForm,person);leaveForm.reset();updateEstimate();dialog.close();invalidateData();
    if(active.root.dataset.view!=='timesheet')navigate(active.url,{force:true,replace:true,keepScroll:true});
    toast('Leave request sent. Your team can now review it.','View requests',()=>navigate('/leave'));
  }catch(reason){error.textContent=reason.message;error.hidden=false;}
  finally{
    submittingLeave=false;leaveForm.inert=false;leaveButton.disabled=false;leaveButton.textContent='Send request →';
    dialog.querySelectorAll('[data-close-leave]').forEach(button=>{button.disabled=false;});
  }
});
})();
