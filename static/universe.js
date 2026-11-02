/* The starfield is the interactive scene, not a UI illustration. No network, audio, or model activity. */
(function(){'use strict';
  var canvas=document.getElementById('starfield'),scene=document.getElementById('universeScene'),button=document.getElementById('motionToggle');
  if(!canvas||!button)return;
  var ctx=canvas.getContext('2d',{alpha:true});if(!ctx)return;
  var reduce=matchMedia('(prefers-reduced-motion: reduce)'),choice=null;
  try{choice=localStorage.getItem('tb-motion');}catch(e){}
  var enabled=!reduce.matches&&choice!=='off',width=0,height=0,dpr=1,stars=[],raf=0,last=0,time=0,px=0,py=0,x=0,y=0;
  function size(){width=innerWidth;height=innerHeight;dpr=Math.min(devicePixelRatio||1,1.5);canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);canvas.style.width=width+'px';canvas.style.height=height+'px';ctx.setTransform(dpr,0,0,dpr,0,0);stars=[];var n=width<620?65:155;for(var i=0;i<n;i++)stars.push({x:Math.random()*width,y:Math.random()*height,z:.25+Math.random()*.75,r:.35+Math.random()*.9,p:Math.random()*6.28});draw(0);}
  function draw(dt){ctx.clearRect(0,0,width,height);time+=dt;x+=(px-x)*.035;y+=(py-y)*.035;var isAuth=document.body.dataset.screen==='auth';stars.forEach(function(s){if(enabled){s.y-=dt*(isAuth?3.2:1.3)*s.z;if(s.y<-10)s.y=height+10;}var sx=s.x+x*24*s.z,sy=s.y+y*17*s.z,alpha=.32+(.5+.5*Math.sin(time*.45+s.p))*.42;ctx.fillStyle='rgba(229,237,246,'+alpha.toFixed(3)+')';ctx.beginPath();ctx.arc(sx,sy,s.r*s.z,0,Math.PI*2);ctx.fill();});if(scene){scene.style.setProperty('--scene-x',(x*-10).toFixed(2)+'px');scene.style.setProperty('--scene-y',(y*-7).toFixed(2)+'px');}}
  function frame(now){raf=0;if(!enabled||document.hidden)return;var dt=last?Math.min((now-last)/1000,.06):0;last=now;draw(dt);raf=requestAnimationFrame(frame);}
  function update(){button.setAttribute('aria-pressed',enabled?'true':'false');button.setAttribute('aria-label',enabled?'Pause starfield motion':'Enable starfield motion');button.querySelector('span').textContent=enabled?'Motion on':'Motion off';button.querySelector('img').src='/static/assets/icons/'+(enabled?'pause':'play')+'.svg';cancelAnimationFrame(raf);raf=0;last=0;if(enabled&&!document.hidden)raf=requestAnimationFrame(frame);else{px=py=x=y=0;draw(0);}}
  button.addEventListener('click',function(){enabled=!enabled&&!reduce.matches;choice=enabled?'on':'off';try{localStorage.setItem('tb-motion',choice);}catch(e){}update();});
  window.addEventListener('pointermove',function(ev){if(!enabled)return;px=(ev.clientX/width-.5)*2;py=(ev.clientY/height-.5)*2;},{passive:true});
  document.addEventListener('pointerleave',function(){px=py=0;});
  document.addEventListener('visibilitychange',update);reduce.addEventListener('change',function(){enabled=!reduce.matches&&choice!=='off';update();});
  var resizeTimer;window.addEventListener('resize',function(){clearTimeout(resizeTimer);resizeTimer=setTimeout(size,120);});size();update();
})();
