(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports) module.exports=api;
  else root.WorkspaceCore=api;
})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const screens=new Set(['/','/workspace','/timesheet','/documents','/leave','/history','/leave-balances','/team-calendar','/office','/admin','/admin/users','/admin/audit']);
  function workspaceURL(value,origin){
    try{
      const url=new URL(value,origin);
      if(url.origin!==origin||!['http:','https:'].includes(url.protocol)) return null;
      return screens.has(url.pathname)||/^\/admin\/users\/\d+\/edit$/.test(url.pathname)?url:null;
    }catch{return null;}
  }
  function routeKey(value,person,origin){
    const url=new URL(value,origin);
    url.searchParams.sort();
    return `${person}:${url.pathname==='/documents'?'/timesheet':url.pathname==='/workspace'?'/':url.pathname}${url.search}`;
  }
  function signInURL(value,current,origin){
    try{
      const supplied=new URL(value,origin);
      if(supplied.origin!==origin||supplied.pathname!=='/login'||supplied.username||supplied.password)return null;
      const target=workspaceURL(current,origin)||new URL('/',origin);
      const login=new URL('/login',origin);login.searchParams.set('next',target.pathname+target.search);
      return login.pathname+login.search;
    }catch{return null;}
  }
  const draftKey=person=>`timesheetFormV2:${person}`;
  const periodDraftKey=(person,year,month)=>`timesheetFormV3:${person}:${year}-${String(month).padStart(2,'0')}`;
  function parsedDate(value){
    if(!/^\d{4}-\d{2}-\d{2}$/.test(value||''))return null;
    const d=new Date(value+'T00:00:00Z');
    return Number.isFinite(d.getTime())&&d.toISOString().slice(0,10)===value?d:null;
  }
  function workingDays(start,end,holidays){
    const first=parsedDate(start),last=parsedDate(end);
    if(!first||!last||last<first||(last-first)/86400000>3660)return null;
    const excluded=new Set(holidays); let days=0;
    for(let day=new Date(first);day<=last;day.setUTCDate(day.getUTCDate()+1)){
      if(day.getUTCDay()!==0&&day.getUTCDay()!==6&&!excluded.has(day.toISOString().slice(0,10)))days++;
    }
    return days;
  }
  return {workspaceURL,routeKey,signInURL,draftKey,periodDraftKey,workingDays};
});
