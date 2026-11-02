/* Local PDF.js adapter; rendering does not depend on a browser PDF plug-in. */
(function(){
'use strict';
let library;
window.WorkspacePDF={async open(buffer,container,isCurrent){
  library=library||import('/static/vendor/pdfjs/pdf.min.mjs');
  const pdfjs=await library;
  pdfjs.GlobalWorkerOptions.workerSrc='/static/vendor/pdfjs/pdf.worker.min.mjs';
  const loadingTask=pdfjs.getDocument({data:new Uint8Array(buffer),standardFontDataUrl:'/static/vendor/pdfjs/standard_fonts/',useWasm:false,isEvalSupported:false});
  const pdf=await loadingTask.promise;
  let destroyed=false,renderTask,renderVersion=0;
  return {
    pageCount:pdf.numPages,
    async render(zoom=1){
      const version=++renderVersion;renderTask?.cancel();container.replaceChildren();
      for(let number=1;number<=pdf.numPages;number++){
        if(destroyed||!isCurrent()||version!==renderVersion)return;
        const page=await pdf.getPage(number);
        if(destroyed||!isCurrent()||version!==renderVersion)return;
        const base=page.getViewport({scale:1});
        const style=getComputedStyle(container),gutter=parseFloat(style.paddingLeft)+parseFloat(style.paddingRight);
        const width=Math.max(240,container.clientWidth-gutter),viewport=page.getViewport({scale:width/base.width*zoom});
        const resolution=Math.min(window.devicePixelRatio||1,2);
        const canvas=document.createElement('canvas');canvas.width=Math.ceil(viewport.width*resolution);canvas.height=Math.ceil(viewport.height*resolution);
        canvas.style.width=Math.floor(viewport.width)+'px';canvas.style.height=Math.floor(viewport.height)+'px';
        canvas.setAttribute('role','img');canvas.setAttribute('aria-label','Document page '+number+' of '+pdf.numPages);
        const section=document.createElement('section');section.className='pdf-page';section.append(canvas);container.append(section);
        renderTask=page.render({canvasContext:canvas.getContext('2d'),viewport,transform:resolution===1?null:[resolution,0,0,resolution,0,0]});
        try{await renderTask.promise;}catch(error){if(destroyed||version!==renderVersion||error.name==='RenderingCancelledException')return;throw error;}
        if(destroyed||!isCurrent()||version!==renderVersion)return;
        const content=await page.getTextContent();
        if(destroyed||!isCurrent()||version!==renderVersion)return;
        const text=document.createElement('p');text.className='sr-only';text.textContent=content.items.map(item=>item.str||'').join(' ');section.append(text);
      }
    },
    destroy(){if(destroyed)return;destroyed=true;renderVersion++;renderTask?.cancel();container.replaceChildren();loadingTask.destroy().catch(()=>{});}
  };
}};
})();
