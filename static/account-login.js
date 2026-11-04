(function(){
'use strict';
const form=document.querySelector('[data-account-login]');
if(!form)return;
const password=form.querySelector('#account-password'),toggle=form.querySelector('[data-password-toggle]');
toggle.addEventListener('click',()=>{
 const show=password.type==='password';password.type=show?'text':'password';
 toggle.textContent=show?'Hide':'Show';toggle.setAttribute('aria-label',show?'Hide password':'Show password');toggle.setAttribute('aria-pressed',String(show));
});
form.addEventListener('submit',()=>{const button=form.querySelector('[type=submit]');button.disabled=true;button.textContent='Signing in…';});
window.addEventListener('pageshow',()=>{const button=form.querySelector('[type=submit]');button.disabled=false;button.textContent='Sign in →';});
})();
