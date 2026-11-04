/* One inline leave range; ISO dates remain the form and draft contract. */
(function(){
'use strict';
window.LeaveDatePicker={create(form){
  const host=form.querySelector('[data-leave-calendar]');
  if(!host||typeof window.flatpickr!=='function')return null;
  const start=form.elements.start_date,end=form.elements.end_date;
  const summary=form.querySelector('[data-leave-date-summary]');
  const hint=form.querySelector('[data-leave-range-hint]');
  const clear=form.querySelector('[data-clear-leave-dates]');
  let selecting=false,lastValue='';
  const label=value=>value?flatpickr.formatDate(flatpickr.parseDate(value,'Y-m-d'),'d M Y'):'Choose a date';
  function accessibleDays(instance){
    const days=Array.from(instance.calendarContainer.querySelectorAll('.flatpickr-day'));
    for(const day of days){day.setAttribute('role','button');day.setAttribute('aria-pressed',String(day.classList.contains('selected')||day.classList.contains('inRange')));day.tabIndex=-1;}
    const current=days.filter(day=>!day.classList.contains('prevMonthDay')&&!day.classList.contains('nextMonthDay'));
    const target=current.find(day=>day.classList.contains('selected'))||current.find(day=>day.classList.contains('today'))||current[0];
    if(target)target.tabIndex=0;
  }
  function render(){
    form.querySelector('[data-leave-first]').textContent=label(start.value);
    form.querySelector('[data-leave-last]').textContent=label(end.value);
    hint.textContent=!start.value?'Choose your first day.':start.value===end.value
      ?'One day selected. Choose another date to extend your leave.'
      :'First and last day are both included.';
    clear.disabled=!start.value&&!end.value;
  }
  const picker=flatpickr(host.querySelector('[data-leave-range]'),{
    inline:true,appendTo:host,mode:'range',dateFormat:'Y-m-d',
    locale:{firstDayOfWeek:1},disableMobile:true,monthSelectorType:'static',
    onReady:(_dates,_value,instance)=>accessibleDays(instance),
    onMonthChange:(_dates,_value,instance)=>accessibleDays(instance),
    onYearChange:(_dates,_value,instance)=>accessibleDays(instance),
    onChange(dates,_value,instance){
      selecting=true;
      try{
        start.value=dates[0]?flatpickr.formatDate(dates[0],'Y-m-d'):'';
        end.value=dates.length?flatpickr.formatDate(dates[dates.length-1],'Y-m-d'):'';
        lastValue=start.value+':'+end.value;render();accessibleDays(instance);
        start.dispatchEvent(new Event('change',{bubbles:true}));
      }finally{selecting=false;}
    },
  });
  if(!picker?.calendarContainer)return null;
  for(const [control,title] of [[picker.prevMonthNav,'Previous month'],[picker.nextMonthNav,'Next month']]){
    control.setAttribute('role','button');control.setAttribute('tabindex','0');control.setAttribute('aria-label',title);
    control.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();control.click();}});
  }
  picker.currentYearElement.setAttribute('aria-label','Calendar year');
  picker.calendarContainer.addEventListener('keydown',event=>{
    if(event.key===' '&&event.target.classList.contains('flatpickr-day')){event.preventDefault();event.target.click();}
  });
  host.hidden=false;summary.hidden=false;form.querySelector('[data-leave-calendar-help]').hidden=false;
  form.querySelector('[data-leave-native-dates]').hidden=true;
  start.required=false;end.required=false;
  clear.addEventListener('click',()=>{picker.clear();render();});
  function sync(){
    if(selecting)return;
    const value=start.value+':'+end.value;
    if(value!==lastValue){
      picker.setDate(start.value?[start.value,...(end.value?[end.value]:[])]:[],false,'Y-m-d');
      if(start.value)picker.jumpToDate(start.value);else picker.jumpToDate(new Date());
      accessibleDays(picker);
      lastValue=value;
    }
    render();
  }
  sync();
  return {sync,focus(){host.focus();}};
}};
})();
