const $=id=>document.getElementById(id);
let csrf='',user=null,result=null,filter='all',poll=null,enabled=false,browserMode=false,browserPending=false,browserCancelled=false;
async function api(path,body){
 const response=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});
 let data;try{data=await response.json();}catch{throw new Error('The server returned an unreadable response. Try again later.');}
 if(!response.ok){const detail=data.detail;const error=new Error(typeof detail==='string'?detail:detail?.message||'The request could not be completed.');error.code=detail?.code;error.status=response.status;
  if(response.status===401&&!path.startsWith('login')&&path!=='session'){clearTimeout(poll);user=null;result=null;renderRows();$('dashboard').hidden=true;$('connect').hidden=false;$('disconnect').hidden=true;$('login-fields').disabled=true;$('browser-login').disabled=true;$('login-message').textContent='Your session ended. Refresh this page to sign in again.';}
  throw error;
 }return data;
}
function progress(title,detail){$('progress').hidden=false;$('progress-title').textContent=title;$('progress-detail').textContent=detail;}
function showAccount(){
 $('connect').hidden=true;$('dashboard').hidden=false;$('disconnect').hidden=false;$('account-name').textContent='@'+user.username;
}
async function loadStories(autoCompare=false){
 showAccount();progress('Finding your stories','Reading stories from your own Instagram account.');$('dashboard-message').textContent='';
 try{const data=await api('stories');$('story').replaceChildren();
  const stories=data.stories.sort((a,b)=>new Date(b.taken_at)-new Date(a.taken_at));
  for(const story of stories){const o=document.createElement('option');o.value=story.id;o.textContent=story.kind+' · '+new Date(story.taken_at).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});$('story').append(o);}
  $('story').disabled=!stories.length;$('compare').disabled=!stories.length;$('progress').hidden=true;
  if(!stories.length){$('dashboard-message').textContent='No available stories were returned. Post a story in Instagram, then reload this page. Expired viewer lists cannot be recovered.';return;}
  const existing=await api('job');
  if(existing.job?.status==='running'){watch();return;}
  if(existing.job?.status==='succeeded'){await getResults();return;}
  if(autoCompare)await start();
 }catch(e){$('progress').hidden=true;$('dashboard-message').textContent=e.message;}
}
$('login-form').addEventListener('submit',async event=>{
 event.preventDefault();$('login-message').textContent='Connecting to Instagram…';$('login-fields').disabled=true;
 try{const data=await api('login',{username:$('username').value.trim(),password:$('password').value,code:$('code').value.trim(),consent:$('consent').checked});csrf=data.csrf;user=data.user;$('login-form').reset();$('login-message').textContent='';await loadStories(true);}
 catch(e){$('login-message').textContent=e.message;if(e.code==='two_factor'){$('two-factor').open=true;$('code').focus();}else{$('password').value='';$('code').value='';}}
 finally{$('login-fields').disabled=!enabled;}
});
function browserButtonState(){$('browser-login').disabled=!enabled||!browserMode||browserPending||!$('browser-consent').checked;}
$('browser-consent').addEventListener('change',browserButtonState);
$('browser-login').addEventListener('click',async()=>{
 browserPending=true;browserCancelled=false;browserButtonState();$('browser-consent').disabled=true;$('browser-cancel').hidden=false;
 $('login-message').textContent='Sign in in the Instagram window and complete any prompts there. When you reach the home page, the window will close and Story Circle will try to read your account. This can take a few minutes.';
 try{const data=await api('login/browser',{consent:$('browser-consent').checked});csrf=data.csrf;user=data.user;
  if(browserCancelled){await api('disconnect',{});location.reload();return;}
  $('login-message').textContent='';await loadStories(true);
 }catch(e){if(!browserCancelled)$('login-message').textContent=e.message;}
 finally{browserPending=false;$('browser-consent').disabled=false;$('browser-cancel').hidden=true;browserButtonState();}
});
$('browser-cancel').addEventListener('click',async()=>{
 browserCancelled=true;$('browser-cancel').disabled=true;
 try{await api('disconnect',{});location.reload();}
 catch(e){$('login-message').textContent=e.message+' Close the Instagram window to stop connecting.';$('browser-cancel').disabled=false;}
});
async function start(){
 clearTimeout(poll);result=null;renderRows();resetStats();$('dashboard-message').textContent='';$('compare').disabled=true;$('story').disabled=true;progress('Starting your comparison','Large connection lists can take several minutes. You can disconnect to stop.');
 try{await api('compare',{story_id:$('story').value});watch();}catch(e){$('progress').hidden=true;$('dashboard-message').textContent=e.message;$('compare').disabled=false;$('story').disabled=false;}
}
$('compare').addEventListener('click',start);
$('story').addEventListener('change',()=>{result=null;resetStats();renderRows();$('dashboard-message').textContent='Select “Compare this story” to collect viewers for this story.';});
async function watch(){
 $('compare').disabled=true;$('story').disabled=true;
 try{const data=await api('job');const job=data.job;if(!job)throw new Error('No comparison is running.');
  if(job.status==='running'){progress(job.stage,job.count?`${job.count.toLocaleString()} accounts read in this list. Keep this page open.`:'Preparing your account snapshot…');poll=setTimeout(watch,1500);return;}
  $('progress').hidden=true;$('compare').disabled=false;$('story').disabled=false;
  if(job.status==='failed')throw new Error(job.error);
  await getResults();
 }catch(e){$('progress').hidden=true;$('dashboard-message').textContent=e.message;$('compare').disabled=!user;$('story').disabled=!user;}
}
async function getResults(){result=await api('results');$('story').value=result.story.id;
 const c=result.counts;$('followers-viewed').textContent=c.follower_viewers.toLocaleString();$('following-viewed').textContent=c.following_viewers.toLocaleString();$('missing').textContent=c.not_listed.toLocaleString();$('followers-total').textContent=`of ${c.followers.toLocaleString()} followers`;$('following-total').textContent=`of ${c.following.toLocaleString()} accounts you follow`;$('captured').textContent='Collected '+new Date(result.captured_at).toLocaleString();$('accuracy').textContent=result.note;renderRows();
}
function resetStats(){for(const id of ['followers-viewed','following-viewed','missing'])$(id).textContent='—';$('followers-total').textContent='Waiting for a comparison';$('following-total').textContent='People you follow';$('captured').textContent='Results are private to this session';}
function visibleRows(){const q=$('search').value.trim().replace(/^@/,'').toLowerCase(),rel=$('relationship').value;return(result?.rows||[]).filter(r=>r.username.toLowerCase().includes(q)&&(filter==='all'||(filter==='viewed'?r.viewed:!r.viewed))&&(rel==='all'||rel==='followers'&&r.follower||rel==='following'&&r.following||rel==='mutual'&&r.follower&&r.following||rel==='outside'&&!r.follower&&!r.following));}
function renderRows(){const rows=visibleRows();$('rows').replaceChildren();$('empty').hidden=rows.length>0;$('table-wrap').hidden=!rows.length;$('download').disabled=!rows.length;$('result-count').textContent=rows.length.toLocaleString()+' accounts';$('empty').querySelector('h2').textContent=result?'No matching accounts':'Your comparison will appear here';$('empty').querySelector('p').textContent=result?'Try another status, relationship, or username.':'No uploads or pasted lists needed.';
 const fragment=document.createDocumentFragment();for(const r of rows){const tr=document.createElement('tr');const username=document.createElement('td');username.textContent='@'+r.username;const rel=document.createElement('td');rel.textContent=r.follower&&r.following?'Mutual follow':r.follower?'Follows you':r.following?'You follow':'Outside your circle';const status=document.createElement('td');const badge=document.createElement('span');badge.className='status'+(r.viewed?' viewed':'');badge.textContent=r.viewed?'Viewed':'Not listed';status.append(badge);tr.append(username,rel,status);fragment.append(tr);}$('rows').append(fragment);
}
document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(t=>{t.classList.toggle('active',t===b);t.setAttribute('aria-pressed',String(t===b));});renderRows();}));
$('search').addEventListener('input',renderRows);$('relationship').addEventListener('change',renderRows);
$('download').addEventListener('click',()=>{const escape=value=>'"'+String(value).replace(/^[=+@\-\t\r]/,"'$&").replaceAll('"','""')+'"';const content=[['username','follows_you','you_follow','story_status'],...visibleRows().map(r=>[r.username,r.follower,r.following,r.viewed?'Viewed':'Not in returned viewer list'])].map(row=>row.map(escape).join(',')).join('\r\n');const url=URL.createObjectURL(new Blob([content],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='story-circle-'+result.story.id+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
$('disconnect').addEventListener('click',async()=>{clearTimeout(poll);$('disconnect').disabled=true;try{await api('disconnect',{});user=null;result=null;csrf='';location.reload();}catch(e){$('dashboard-message').textContent=e.message;$('disconnect').disabled=false;}});
async function init(){try{const data=await api('session');csrf=data.csrf;user=data.user;enabled=data.enabled;browserMode=Boolean(data.browser_login);
 $('login-form').hidden=browserMode;$('browser-connect').hidden=!browserMode;$('login-fields').disabled=!enabled||browserMode;browserButtonState();
 $('server-state').textContent=enabled?(browserMode?`Local browser connection · ${data.session_minutes}-minute session. Sign in with your own account on this computer.`:`Connect for a ${data.session_minutes}-minute session. ${data.mode==='personal'?'This installation is limited to the owner’s allowed accounts.':'Each account gets its own private session.'}`):'Instagram login is not enabled on this server. The operator must finish setup before you can connect.';
 if(browserMode)$('retention-note').textContent='A separate temporary browser window opens on this computer. Story Circle keeps the session and results in local process memory until disconnect or expiry. Disconnecting clears Story Circle’s copy; it does not revoke the session in Instagram.';
 if(user)await loadStories(false);
 }catch(e){$('server-state').textContent='Could not reach the connection service. Instagram login is unavailable.';$('login-message').textContent=e.message;}}
init();
