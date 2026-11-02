/* Tab-scoped drafts: editable fields only; never tokens, passwords, or files. */
(function(){
'use strict';
window.WorkspaceDrafts={create(storage){
  const memory=new Map();
  const key=(form,owner)=>'urchin:draft:v1:'+owner+':'+(form.dataset.draftId||new URL(form.action,location.origin).pathname);
  const fields=form=>Array.from(form.elements).filter(field=>field.name&&!/csrf|token|password|secret/i.test(field.name)&&!['hidden','password','file','submit','button','reset'].includes(field.type));
  function read(name){try{return storage?.getItem(name)||memory.get(name);}catch{return memory.get(name);}}
  function hint(form){
    let node=form.querySelector('[data-draft-status]');
    if(!node){node=document.createElement('p');node.dataset.draftStatus='1';node.className='form-draft-status';node.setAttribute('role','status');(form.querySelector('.drawer-body')||form).appendChild(node);}
    node.textContent='Draft kept in this tab';
  }
  return {
    capture(form,owner){
      if(form.dataset.dirty!=='true')return;
      const values=fields(form).map(field=>({name:field.name,value:field.value,checked:field.checked,type:field.type}));
      const name=key(form,owner),payload=JSON.stringify(values);memory.set(name,payload);
      try{storage?.setItem(name,payload);}catch{/* The retained view still keeps the draft. */}
      hint(form);
    },
    restore(form,owner){
      const raw=read(key(form,owner));if(!raw)return false;
      try{
        const values=JSON.parse(raw);if(!Array.isArray(values))return false;
        const editable=fields(form);
        for(const item of values){
          for(const field of editable.filter(field=>field.name===item.name)){
            if(field.type==='checkbox'||field.type==='radio')field.checked=field.value===item.value&&Boolean(item.checked);
            else field.value=item.value;
          }
        }
        form.dataset.dirty='true';hint(form);return true;
      }catch{return false;}
    },
    clear(form,owner){
      const name=key(form,owner);memory.delete(name);try{storage?.removeItem(name);}catch{}
      delete form.dataset.dirty;form.querySelector('[data-draft-status]')?.remove();
    }
  };
}};
})();
