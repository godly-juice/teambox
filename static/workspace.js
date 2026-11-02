/* Operator workspace. All member/model text uses TB.h/textContent. Existing API
   requests stay in workspace-data.js; this file adds no backend privileges. */
(function(){'use strict';
  var TB=window.TB,h=TB.h;
  function icon(name){return h('img',{class:'ui-icon',src:'/static/assets/icons/'+name+'.svg',alt:''});}
  function link(label,href,cls){return h('a',{href:href,class:cls||'text-link',text:label});}
  function reset(el){el.textContent='';return el;}
  function ago(value){if(!value)return 'Time unavailable';var n=new Date(value.replace(' ','T')),s=Math.max(0,(Date.now()-n.getTime())/1000);if(!isFinite(s))return value;if(s<60)return 'just now';if(s<3600)return Math.floor(s/60)+' min ago';if(s<86400)return Math.floor(s/3600)+' hours ago';return n.toLocaleDateString(undefined,{month:'short',day:'numeric'});}
  function clock(value){if(!value)return '';var d=new Date(value.replace(' ','T'));return isFinite(d.getTime())?d.toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'}):value;}
  function empty(title,text,action){return h('div',{class:'workspace-empty'},h('h3',{text:title}),h('p',{text:text}),action||null);}
  TB.renderWorkspace=function(){
    var owner=TB.me&&TB.me.id,view=document.getElementById('view'),alive=true,loading=false,pending=false,lastChat='',chatResult=null,state=null,resultCache={},chatGeneration=0;
    document.body.dataset.screen='workspace';view.className='page workspace-page';reset(view);
    var queue=h('div'),result=h('section',{class:'latest-result'}),meta=h('div',{class:'queue-meta'}),preview=h('span',{class:'preview-notice',text:'Design preview · sample data'});
    var status=h('div',{class:'lumi-status'},h('span',{class:'status-dot'}),h('span',{text:'Checking connection…'}));
    var context=h('p',{class:'lumi-context',text:'A shared place to think, build, and move the work forward.'});
    var modelError=h('p',{class:'model-connection-note',role:'status','aria-live':'polite'});
    var history=h('div',{class:'chat-history','aria-label':'Conversation with Lumi',tabindex:'0'}),notice=h('div',{class:'app-status',role:'status','aria-live':'polite'});
    var input=h('textarea',{id:'workspaceChatInput',placeholder:'Ask Lumi or assign a task…',maxlength:'20000','aria-label':'Your message to Lumi',disabled:true});
    var send=h('button',{type:'submit',text:'Send',disabled:true});
    var form=h('form',{class:'lumi-composer',onsubmit:submit},h('label',{class:'sr-only',for:'workspaceChatInput',text:'Your message to Lumi'}),input,send);
    function jump(id){var node=document.getElementById(id);if(!node)return;node.setAttribute('tabindex','-1');node.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});node.focus({preventScroll:true});}
    input.addEventListener('keydown',function(ev){if(ev.key==='Enter'&&(ev.ctrlKey||ev.metaKey)){ev.preventDefault();form.requestSubmit();}});
    var left=h('section',{class:'work-surface','aria-labelledby':'workspaceTitle'},
      h('div',{class:'workspace-heading'},h('h1',{id:'workspaceTitle',text:'Work in motion'}),h('p',{text:'Your team. Your projects. The next useful step.'}),preview,h('button',{class:'mobile-jump',type:'button',text:'Back to Lumi',onclick:function(){jump('lumiTitle');}})),
      queue,meta,result);
    var right=h('aside',{class:'lumi-panel','aria-labelledby':'lumiTitle'},h('h2',{id:'lumiTitle',text:'Lumi'}),status,context,modelError,h('button',{class:'mobile-jump',type:'button',text:'View your work',onclick:function(){jump('workspaceTitle');}}),history,notice,form,h('p',{class:'composer-hint',text:'Ctrl + Enter to send · conversations stay with your account.'}));
    var root=h('div',{class:'operator-workspace'},left,right),narrow=matchMedia('(max-width: 620px)');
    function arrange(){if(narrow.matches){root.appendChild(right);root.appendChild(left);}else{root.appendChild(left);root.appendChild(right);}}
    arrange();narrow.addEventListener('change',arrange);view.appendChild(root);view.focus({preventScroll:true});
    function active(){return alive&&root.isConnected&&TB.me&&TB.me.id===owner;}
    function updateStatus(){
      if(!active())return;var label='Checking connection…',kind='',model=null;
      if(state&&state.models.status==='ready'&&!state.models.stale&&state.models.data&&chatResult&&chatResult.chat) model=state.models.data.models.find(function(m){return m.id===chatResult.chat.model;});
      modelError.textContent=state&&state.models.status==='error'?(state.models.error||'Shared-model availability could not be checked.'):'';
      if(pending){label='Lumi is answering';kind='sending';}
      else if(chatResult){
        if(chatResult.status==='empty'){label='Lumi is not connected yet';}
        else if(chatResult.status==='unavailable'){label='Shared model unavailable';kind='error';}
        else if(chatResult.status==='error'||chatResult.status==='refused'){label='Connection needs attention';kind='error';}
        else if(model&&model.stateGroup==='running'){label='Available to help';kind='ready';}
        else if(model){label='Model: '+(model.state||'status unconfirmed');}
        else{label='Availability unconfirmed';}
      }
      status.className='lumi-status '+kind;status.lastChild.textContent=label;
      input.disabled=!(chatResult&&chatResult.canSend);send.disabled=input.disabled||pending;
    }
    function errorBlock(resource,retry){var box=h('div',{class:'source-error',role:'status'},h('strong',{text:resource.stale?'Saved view · refresh failed':'This view could not load'}),h('div',{text:resource.error||'The connection did not answer.'}));if(retry)box.appendChild(h('button',{type:'button',class:'ghost small',onclick:refresh,text:'Try again'}));return box;}
    function paint(snapshot){
      if(!active())return;state=snapshot;reset(queue);reset(meta);reset(result);
      var r=state.builds,rows=r.data&&r.data.builds?r.data.builds.slice():[];
      rows.sort(function(a,b){var rank=function(x){return /^(running|testing)$/.test(x.status)?0:x.status==='queued'?1:x.statusGroup==='review'?2:x.statusGroup==='attention'?3:4;};return rank(a)-rank(b)||String(a.created||a.updated).localeCompare(String(b.created||b.updated));});
      var currentRows=rows.filter(function(b){return b.statusGroup!=='done';});
      if(r.status==='error')queue.appendChild(errorBlock(r,true));
      if(!rows.length){
        if(r.status==='idle'||r.status==='loading')queue.appendChild(empty('Loading your work…','Checking your projects and recent builds.'));
        else if(r.status!=='error')queue.appendChild(empty('A clear space for your next idea.','Start an AI build in one of your projects. Its progress and result will appear here.',link('Open your projects','#/projects','btn ghost small')));
      }else{
        var table=h('table',{class:'work-list','aria-label':'Your AI builds'},h('thead',{},h('tr',{},h('th',{scope:'col',text:'Task'}),h('th',{scope:'col',text:'Model'}),h('th',{scope:'col',text:'State'}))));
        var body=h('tbody');(currentRows.length?currentRows:rows).slice(0,5).forEach(function(b){var s=b.status,group=b.statusGroup,st=group==='active'&&s==='running'?'In progress':s||'Status unknown';
          var glyph=group==='attention'?'warning-circle':group==='done'||group==='review'?'check-circle':'clock';
          var model=state.models.data&&state.models.data.models.find(function(m){return m.id===b.model;});
          body.appendChild(h('tr',{},h('td',{},h('a',{class:'task-title',href:'#/build/'+encodeURIComponent(b.id)},icon('file-text'),h('span',{text:b.title||b.sandbox||'Untitled build'}))),h('td',{class:'work-owner',title:model?model.name:b.model,text:model?model.name:b.model||'AI'}),h('td',{},h('span',{class:'work-state '+(s==='queued'?'queued':group)},icon(glyph),h('span',{text:st})))));
        });table.appendChild(body);queue.appendChild(table);
      }
      var freshness=r.fetchedAt?(r.stale?'Saved view from ':'Updated ')+ago(r.fetchedAt):'Waiting for a connection';
      meta.appendChild(h('p',{text:state.backgroundQueueConnected?'Team requests take priority over background work.':'Your AI builds are shown here. Background work is not available yet.'}));
      meta.appendChild(h('span',{class:'source-note'},h('span',{text:freshness}),h('br'),link('All builds','#/builds')));
      var completed=rows.filter(function(b){return b.statusGroup==='done'||b.statusGroup==='review';}).sort(function(a,b){return String(b.updated).localeCompare(String(a.updated));})[0];
      result.appendChild(h('h2',{text:'Latest result'}));
      if(completed){
        result.appendChild(h('div',{class:'result-header'},icon('file-text'),h('div',{},h('strong',{text:completed.title||'AI build'}),h('small',{text:(completed.status||'Build result')+' · '+ago(completed.updated||completed.created)}))));
        var key=completed.id+':'+completed.updated,excerpt=h('pre',{class:'result-excerpt','data-result-key':key,text:resultCache[key]&&resultCache[key].text||completed.last||completed.verdict||'Open the build to review its changes, checkpoints, and test results.'});result.appendChild(excerpt);
        if(!resultCache[key]){resultCache[key]={loading:true};TB.api('GET','/api/builds/'+encodeURIComponent(completed.id)).then(function(detail){var d=detail.build||detail,text=d.summary||d.last||completed.last||completed.verdict||'Open the build for details.';resultCache[key]={text:String(text)};var current=result.querySelector('.result-excerpt');if(active()&&current&&current.getAttribute('data-result-key')===key)current.textContent=String(text);}).catch(function(){resultCache[key]={text:completed.last||completed.verdict||'Open the build for details.'};});}
        result.appendChild(h('a',{class:'result-link',href:'#/build/'+encodeURIComponent(completed.id)},h('span',{text:'Review the result'}),icon('arrow-up-right')));
      }
      else{result.appendChild(h('p',{class:'work-subtitle',text:'Finished work will appear here, with its changes and test results ready to review.'}));}
      updateStatus();
    }
    function paintChat(data){
      if(!active())return;chatResult=data;var stamp=JSON.stringify(data.chat&&data.chat.messages||[]),nearBottom=history.scrollHeight-history.scrollTop-history.clientHeight<75;
      if(stamp!==lastChat||!history.firstChild){lastChat=stamp;reset(history);
        if(data.chat&&data.chat.messages.length){data.chat.messages.slice(-30).forEach(function(m){var label=m.role==='user'?'You':m.role==='assistant'?'Lumi':'Connection notice';history.appendChild(h('article',{class:'chat-entry '+m.role},h('span',{class:'chat-author'},label,h('time',{datetime:m.at||undefined,text:clock(m.at)})),h('div',{class:'chat-content',text:m.content})));});}
        else{history.appendChild(h('div',{class:'chat-empty'},h('h3',{text:data.status==='empty'?'A conversation starts here.':'Lumi is one message away.'}),h('p',{text:data.unavailableReason||data.error||'Ask about your project or work through an idea together.'}),link('Models & chat','#/models')));}
        if(nearBottom||pending)history.scrollTop=history.scrollHeight;
      }
      notice.textContent=data.refused||data.error||data.unavailableReason||(data.fallback?'Lumi’s last reply was a saved welcome message; live model availability is unconfirmed.':'');
      if(data.chat&&data.chat.model)context.textContent='Your saved conversation. Pick up where you left off.';
      updateStatus();
    }
    function refresh(){if(!active()||loading||document.hidden)return;loading=true;TB.workspaceData.load().catch(function(e){if(active())notice.textContent=e.message;}).finally(function(){loading=false;});if(!pending){var read=++chatGeneration;TB.workspaceData.getLumiChat().then(function(data){if(active()&&!pending&&read===chatGeneration)paintChat(data);});}}
    function submit(ev){ev.preventDefault();if(!active()||pending||!input.value.trim()||input.disabled)return;var value=input.value.trim();++chatGeneration;pending=true;notice.textContent='';updateStatus();TB.workspaceData.sendLumi(value).then(function(data){if(!active())return;pending=false;if(data.chat&&input.value.trim()===value)input.value='';paintChat(data);history.scrollTop=history.scrollHeight;input.focus();});}
    var unsubscribe=TB.workspaceData.subscribe(paint);paint(TB.workspaceData.getState());refresh();
    var timer=setInterval(refresh,30000);function visible(){if(!document.hidden)refresh();}document.addEventListener('visibilitychange',visible);
    TB.disposeView=function(){alive=false;clearInterval(timer);unsubscribe();document.removeEventListener('visibilitychange',visible);narrow.removeEventListener('change',arrange);};
    fetch('/api/ui-preview',{credentials:'same-origin'}).then(function(r){if(!r.ok)throw new Error('not preview');return r.json();}).then(function(p){if(p.preview&&active())document.body.classList.add('is-preview');}).catch(function(){});
  };
})();
