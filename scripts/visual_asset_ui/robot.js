/* Local dot typography needs no external font request. Navigation keeps its original handlers. */
(()=>{
  const nav=document.querySelector('#nav-library')?.parentElement;
  const top=document.querySelector('.top');
  if(nav&&top){nav.previousElementSibling?.classList.add('workspace-label');nav.classList.add('workspace-nav');nav.setAttribute('aria-label','工作空间');top.prepend(nav);}
  const heading=document.querySelector('#heading');
  if(heading){
    heading.textContent='让灵感，成为资产。';
    const glyphs={A:['01110','10001','10001','11111','10001','10001','10001'],S:['01111','10000','10000','01110','00001','00001','11110'],E:['11111','10000','10000','11110','10000','10000','11111'],T:['11111','00100','00100','00100','00100','00100','00100'],L:['10000','10000','10000','10000','10000','10000','11111'],I:['11111','00100','00100','00100','00100','00100','11111'],R:['11110','10001','10001','11110','10100','10010','10001']};
    const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');
    svg.classList.add('matrix-title');svg.setAttribute('viewBox','0 0 447 36');svg.setAttribute('aria-hidden','true');
    [...'ASSET ATELIER'].forEach((letter,i)=>{(glyphs[letter]||[]).forEach((row,y)=>[...row].forEach((v,x)=>{if(v==='1'){const dot=document.createElementNS(ns,'circle');dot.setAttribute('cx',String(i*35+x*5+2));dot.setAttribute('cy',String(y*5+2));dot.setAttribute('r','1.65');svg.append(dot);}}));});
    heading.before(svg);
  }
})();
/* Decorative gaze never intercepts library controls. */
(()=>{
  const head=document.querySelector('#robot-head');if(!head)return;
  const eyes=[...head.querySelectorAll('.robot-pupil')],face=head.querySelector('#robot-face');
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  let frame=0,targetX=0,targetY=0,x=0,y=0;
  function paint(){x+=(targetX-x)*.13;y+=(targetY-y)*.13;eyes.forEach(eye=>eye.setAttribute('transform',`translate(${x*17} ${y*12})`));face.setAttribute('transform',`translate(${x*3} ${y*2})`);frame=Math.abs(targetX-x)+Math.abs(targetY-y)>.003?requestAnimationFrame(paint):0;}
  function aim(event){if(event.pointerType==='touch'||reduced.matches)return;const rect=head.getBoundingClientRect();targetX=Math.max(-1,Math.min(1,(event.clientX-(rect.left+rect.width/2))/Math.max(200,innerWidth*.4)));targetY=Math.max(-1,Math.min(1,(event.clientY-(rect.top+rect.height*.43))/Math.max(160,innerHeight*.4)));if(!frame)frame=requestAnimationFrame(paint);}
  function reset(){targetX=targetY=0;if(!reduced.matches&&!frame)frame=requestAnimationFrame(paint);}
  window.addEventListener('pointermove',aim,{passive:true});document.documentElement.addEventListener('pointerleave',reset);window.addEventListener('blur',reset);
  reduced.addEventListener('change',()=>{if(reduced.matches){cancelAnimationFrame(frame);frame=0;x=y=targetX=targetY=0;eyes.forEach(eye=>eye.removeAttribute('transform'));face.removeAttribute('transform');}});
})();
