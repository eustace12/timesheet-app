/* Context for leave decisions and an inbox that retains the request in place. */
(function(){
'use strict';
function node(tag,className,text){const element=document.createElement(tag);element.className=className||'';if(text!==undefined)element.textContent=text;return element;}
function renderAssessment(container,data,{review=false}={}){
  container.replaceChildren();
  if(!data.can_submit)container.append(node('p','assessment-message '+(review?'warning':'error'),data.reason));
  for(const balance of data.balances){
    const row=node('div','assessment-balance');
    row.append(node('span','',balance.year+' · '+balance.requested+' working '+(balance.requested===1?'day':'days')));
    row.append(node('strong','',balance.after_request===null?'Allowance not set':balance.after_request+' days remaining'));
    container.append(row);
    if(balance.after_request===null)container.append(node('p','assessment-message','Your team needs to confirm the allowance for this year.'));
    else if(balance.after_request<0&&data.can_submit)container.append(node('p','assessment-message warning','This request exceeds the available allowance. Check with your team before approval.'));
  }
  if(review&&data.already_reserved)container.append(node('p','assessment-message','This request is already included in the balance. Approval will not deduct the same days again.'));
  if(!data.balances.length)container.append(node('p','assessment-message','This leave type does not use the annual leave allowance.'));
  container.append(node('h4','assessment-title',data.team_count?'Other team absences on these dates':'Team availability'));
  if(!data.team_count)container.append(node('p','assessment-message','No other approved or pending team absences overlap these dates.'));
  else{
    const list=node('ul','assessment-team');
    for(const person of data.team){const item=node('li'),detail=node('span');detail.append(node('span','',person.status==='accepted'?'Approved':'Pending'));const dates=node('small','',person.start_date+' → '+person.end_date);detail.append(dates);item.append(node('strong','',person.full_name),detail);list.append(item);}
    container.append(list);
    if(data.team_count>data.team.length)container.append(node('p','assessment-message','And '+(data.team_count-data.team.length)+' more. Check the team calendar for the full picture.'));
    container.append(node('p','assessment-message','Overlapping team dates are shown for planning. They do not automatically block this request.'));
  }
}
async function loadAssessment(url){
  const response=await PeopleWorkspace.request(url);const data=await response.json().catch(()=>null);
  if(!response.ok||!data)throw new Error(data?.error||'Could not check these dates. Please try again.');return data;
}
const drawers=new WeakMap();
function closeReview(drawer){drawer.close();}
async function openReview(button){
  const root=button.closest('#people-view'),drawer=root.querySelector('#review-drawer');
  const card=document.getElementById(button.dataset.openReview);if(!card||!root.contains(card)||!drawer)return;
  if(!drawers.has(drawer)){
    drawers.set(drawer,{version:0});
    drawer.addEventListener('close',()=>{const state=drawers.get(drawer);state.version++;if(state.card){root.querySelector('.review-store').append(state.card);state.card=null;}});
    drawer.querySelector('[data-close-review]').addEventListener('click',()=>closeReview(drawer));
  }
  const state=drawers.get(drawer);state.card=card;const version=++state.version;
  drawer.querySelector('.review-drawer-body').replaceChildren(card);
  const details=card.querySelector('.review-details');if(details)details.open=true;
  drawer.showModal();
  const context=card.querySelector('[data-assessment-url]');
  if(context){
    context.textContent='Checking balance and team availability…';
    try{const data=await loadAssessment(context.dataset.assessmentUrl);if(drawer.open&&version===state.version)renderAssessment(context,data,{review:true});}
    catch(error){if(drawer.open&&version===state.version)context.replaceChildren(node('p','assessment-message warning',error.message+' You can still review the stored document.'));}
  }
}
document.addEventListener('click',event=>{const button=event.target.closest('[data-open-review]');if(button){event.preventDefault();openReview(button);}});
document.addEventListener('click',event=>{
  const button=event.target.closest('[data-balance-person-id]');if(!button)return;
  const selector=document.querySelector('.profile-selector select');
  if(!selector||selector.disabled||!Array.from(selector.options).some(option=>option.value===button.dataset.balancePersonId))return;
  event.preventDefault();
  if(selector.value===button.dataset.balancePersonId){
    const form=document.querySelector('#people-view .settings-form');
    form?.scrollIntoView({block:'center',behavior:'smooth'});form?.querySelector('[name=days]')?.focus({preventScroll:true});
    return;
  }
  selector.value=button.dataset.balancePersonId;selector.dispatchEvent(new Event('change',{bubbles:true}));
});
window.PeopleExperience={renderAssessment,loadAssessment};
})();
