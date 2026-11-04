/* Only a display preference is stored; no account or session data. */
(function(){
'use strict';
const key='urchin:theme',root=document.documentElement;
let saved;try{saved=localStorage.getItem(key);}catch{}
let explicit=saved==='light'||saved==='dark';
const device=window.matchMedia?.('(prefers-color-scheme: dark)');
function apply(theme){
  root.dataset.theme=theme;root.style.colorScheme=theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content',theme==='dark'?'#101b29':'#f5f8fc');
  document.querySelectorAll('[data-theme-toggle]').forEach(button=>{
    button.setAttribute('aria-label',theme==='dark'?'Switch to light theme':'Switch to dark theme');
    button.setAttribute('title',theme==='dark'?'Light theme':'Dark theme');
    button.setAttribute('aria-pressed',String(theme==='dark'));
  });
}
apply(explicit?saved:device?.matches?'dark':'light');
document.addEventListener('DOMContentLoaded',()=>apply(root.dataset.theme));
document.addEventListener('click',event=>{
  if(!event.target.closest('[data-theme-toggle]'))return;
  const theme=root.dataset.theme==='dark'?'light':'dark';explicit=true;
  try{localStorage.setItem(key,theme);}catch{}
  apply(theme);
});
device?.addEventListener?.('change',event=>{if(!explicit)apply(event.matches?'dark':'light');});
window.addEventListener('storage',event=>{
  if(event.key!==key)return;
  explicit=event.newValue==='light'||event.newValue==='dark';
  apply(explicit?event.newValue:device?.matches?'dark':'light');
});
})();
