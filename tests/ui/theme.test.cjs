const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {resolve}=require('node:path');
const {JSDOM}=require('jsdom');
function app({systemDark=false,saved,storageFails=false}={}){
 const dom=new JSDOM('<!doctype html><html><head><meta name="theme-color" content="#fff"></head><body><button data-theme-toggle>Theme</button></body></html>',{url:'https://people.example',runScripts:'outside-only'});
 const w=dom.window;w.matchMedia=()=>({matches:systemDark,addEventListener(){}});
 if(saved)w.localStorage.setItem('urchin:theme',saved);
 if(storageFails)Object.defineProperty(w,'localStorage',{get(){throw new Error('storage blocked');}});
 w.eval(readFileSync(resolve(__dirname,'../../static/theme.js'),'utf8'));w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
 return {w,d:w.document,close(){w.close();}};
}
test('theme follows the device initially and a saved user choice wins',()=>{
 for(const [options,expected] of [[{systemDark:true},'dark'],[{systemDark:true,saved:'light'},'light'],[{saved:'invalid'},'light']]){
  const a=app(options);try{assert.equal(a.d.documentElement.dataset.theme,expected);}finally{a.close();}
 }
});
test('theme switch updates the whole page and survives another page load',()=>{
 const a=app();try{
  a.d.querySelector('button').click();assert.equal(a.d.documentElement.dataset.theme,'dark');assert.equal(a.w.localStorage.getItem('urchin:theme'),'dark');
  assert.equal(a.d.querySelector('button').getAttribute('aria-label'),'Switch to light theme');
  const b=app({saved:a.w.localStorage.getItem('urchin:theme')});try{assert.equal(b.d.documentElement.dataset.theme,'dark');}finally{b.close();}
  a.d.querySelector('button').click();assert.equal(a.d.documentElement.dataset.theme,'light');
 }finally{a.close();}
});
test('theme works when browser storage is blocked',()=>{
 const a=app({storageFails:true});try{a.d.querySelector('button').click();assert.equal(a.d.documentElement.dataset.theme,'dark');}finally{a.close();}
});

test('password visibility preserves input and submit progress keeps credentials enabled',()=>{
 const dom=new JSDOM('<form data-account-login><input id="account-password" name="password" type="password" value="synthetic-form-value"><button type="button" data-password-toggle>Show</button><button type="submit">Sign in</button></form>',{url:'https://people.example/login',runScripts:'outside-only'});
 const w=dom.window,d=w.document;
 try{
  w.eval(readFileSync(resolve(__dirname,'../../static/account-login.js'),'utf8'));
  const password=d.querySelector('input'),toggle=d.querySelector('[data-password-toggle]'),submit=d.querySelector('[type=submit]');
  toggle.click();assert.equal(password.type,'text');assert.equal(password.value,'synthetic-form-value');assert.equal(toggle.getAttribute('aria-label'),'Hide password');
  toggle.click();assert.equal(password.type,'password');
  d.querySelector('form').dispatchEvent(new w.Event('submit',{cancelable:true}));assert.equal(submit.disabled,true);assert.equal(password.disabled,false);
  assert.equal(new w.FormData(d.querySelector('form')).get('password'),'synthetic-form-value');
  w.dispatchEvent(new w.Event('pageshow'));assert.equal(submit.disabled,false);
 }finally{w.close();}
});
