const {test}=require('node:test');
const assert=require('node:assert/strict');
const {workspaceURL,routeKey,draftKey,workingDays}=require('../static/workspace-core.js');
const origin='https://people.example';
test('only application screens use soft navigation; documents remain native downloads',()=>{
  for(const path of ['/','/documents','/leave','/history','/leave-balances','/team-calendar','/admin','/admin/users','/admin/users/7/edit']) assert.ok(workspaceURL(path,origin));
  for(const path of ['/admin/export?year=2026&month=10','/history/leave/1/download','/admin/leave/1/view','/leave/submit','https://elsewhere.example/leave','javascript:alert(1)']) assert.equal(workspaceURL(path,origin),null);
});
test('cached monthly drafts survive alias navigation and are isolated by employee',()=>{
  assert.equal(routeKey('/documents','alice',origin),routeKey('/timesheet','alice',origin));
  assert.notEqual(routeKey('/','alice',origin),routeKey('/','daniel',origin));
  assert.notEqual(draftKey('alice'),draftKey('daniel'));
  assert.notEqual(routeKey('/admin?year=2026&month=10','alice',origin),routeKey('/admin?year=2026&month=11','alice',origin));
});
test('leave preview counts working days including date boundaries and holidays',()=>{
  assert.equal(workingDays('2026-11-06','2026-11-10',[]),3);
  assert.equal(workingDays('2026-11-06','2026-11-10',['2026-11-09']),2);
  assert.equal(workingDays('2026-11-07','2026-11-08',[]),0);
  assert.equal(workingDays('2026-11-10','2026-11-10',[]),1);
  assert.equal(workingDays('2026-11-10','2026-11-09',[]),null);
  assert.equal(workingDays('2026-02-30','2026-03-01',[]),null);
});
test('monthly draft keys separate employee and period',()=>{
  const {periodDraftKey}=require('../static/workspace-core.js');
  assert.notEqual(periodDraftKey('alice',2026,7),periodDraftKey('alice',2026,8));
  assert.notEqual(periodDraftKey('alice',2026,7),periodDraftKey('bob',2026,7));
});

test('session renewal only opens local sign-in and resumes the current screen',()=>{
  const {signInURL}=require('../static/workspace-core.js');
  const login=signInURL('/login?next=/office/plans','/office?week=2026-11-02',origin);
  assert.equal(new URL(login,origin).pathname,'/login');
  assert.equal(new URL(login,origin).searchParams.get('next'),'/office?week=2026-11-02');
  for(const value of ['https://elsewhere.example/login','//elsewhere.example/login','/admin','javascript:alert(1)'])assert.equal(signInURL(value,'/office',origin),null);
  assert.equal(new URL(signInURL('/login','https://elsewhere.example/',origin),origin).searchParams.get('next'),'/');
});
