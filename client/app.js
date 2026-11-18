const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
let currentId = null;
let currentRun = null;
let tab = 'activity';
let pollTimer;
let toastTimer;
let config;
let renderedPlanId = null;
let runFetchedAt = 0;
const activeStatuses = new Set(['running','planning']);
const labels = {planning:'生成计划中',awaiting_confirmation:'待确认计划',running:'研究中',complete:'已完成',failed:'失败',cancelled:'已停止',interrupted:'已中断',budget_exceeded:'已达预算上限'};
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path, options={}) {
  const response = await fetch(path,{headers:{'Content-Type':'application/json'},...options});
  if (response.status === 401) { location.replace('/login'); throw new Error('登录已失效，请重新登录。'); }
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请检查输入和服务状态。');
  return data;
}
function toast(message){ $('#toast').textContent=message; $('#toast').classList.add('show'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),3500); }
function readBudget(prefix) {
  return {
    max_seconds: Number($(`#${prefix}-minutes`).value) * 60,
    max_model_calls: Number($(`#${prefix}-calls`).value),
    max_tokens: Number($(`#${prefix}-tokens`).value),
  };
}
function renderPlan(run) {
  const visible = run.status === 'awaiting_confirmation';
  $('#plan-review').hidden = !visible;
  if (!visible || renderedPlanId === run.id) return;
  renderedPlanId = run.id;
  const plan = run.plan || {};
  $('#plan-objective').value = plan.objective || '';
  $('#plan-outline').value = (plan.outline || []).join('\n');
  $('#plan-focus').value = plan.focus || '';
  $('#plan-exclusions').value = plan.exclusions || '';
  $('#plan-sources').value = plan.source_preferences || '';
  const budget = run.budget;
  $('#confirm-minutes').value = Math.ceil(budget.max_seconds / 60);
  $('#confirm-calls').value = budget.max_model_calls;
  $('#confirm-tokens').value = budget.max_tokens;
  $('#plan-error').hidden = true;
}
function duration(seconds) {
  const value = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(value / 60)).padStart(2,'0')}:${String(value % 60).padStart(2,'0')}`;
}
function renderBudgetMonitor() {
  const run = currentRun;
  const budget = run?.budget;
  $('#budget-monitor').hidden = !budget?.max_seconds;
  if (!budget?.max_seconds) return;
  const stages = {translating:'翻译报告中',planning:'生成研究计划',awaiting_confirmation:'等待确认（已暂停计时）',running:'准备执行',researching:'分配研究任务',searching:'搜索网页资料',analyzing:'分析与归纳证据',writing:'撰写报告',complete:'报告已完成',failed:'执行失败',cancelled:'已停止',budget_exceeded:'预算已用完',interrupted:'服务重启中断'};
  $('#current-stage').textContent = run.stage === 'answering' ? '回答报告追问' : stages[run.stage] || labels[run.status];
  const elapsed = (run.elapsed_seconds || 0) + (activeStatuses.has(run.status) ? (Date.now() - runFetchedAt) / 1000 : 0);
  $('#elapsed-time').textContent = duration(elapsed);
  const usage = run.usage || {};
  const tokens = (usage.input_tokens || 0) + (usage.output_tokens || 0);
  const bars = [
    ['运行时间', elapsed, budget.max_seconds, `${duration(elapsed)} / ${Math.round(budget.max_seconds / 60)} 分钟`],
    ['模型调用', usage.model_calls || 0, budget.max_model_calls, `${usage.model_calls || 0} / ${budget.max_model_calls} 次`],
    ['总 tokens', tokens, budget.max_tokens, `${tokens.toLocaleString()} / ${budget.max_tokens.toLocaleString()}`],
  ];
  $('#budget-bars').innerHTML = bars.map(([label, used, limit, text]) => {
    const percent = Math.min(100, Math.max(0, used / limit * 100));
    return `<div><div class="budget-bar-title"><span>${label}</span><strong>${text}</strong></div><div class="budget-track ${percent >= 80 ? 'warning' : ''}" role="progressbar" aria-label="${label}" aria-valuenow="${Math.floor(used)}" aria-valuemin="0" aria-valuemax="${Math.max(limit,Math.ceil(used))}" aria-valuetext="${text}"><span style="width:${percent}%"></span></div></div>`;
  }).join('');
  $('#usage-note').textContent = usage.usage_incomplete ? '部分失败或已取消请求未返回 token 用量，显示值可能低于实际计费；模型调用次数包含这些请求。' : '包含规划阶段用量；等待确认不计时。token 按接口返回用量累计，并发在途请求可能使最终用量略超预算。';
}
setInterval(() => { if(currentRun && activeStatuses.has(currentRun.status)) renderBudgetMonitor(); }, 1000);
function setTab(name){tab=name; $$('.tabs button').forEach(b=>{b.classList.toggle('active',b.dataset.tab===name);b.setAttribute('aria-selected',String(b.dataset.tab===name));}); $$('.panel').forEach(p=>p.hidden=p.id!==`panel-${name}`);}
async function loadSettings(){config=await api('/api/settings'); $('#submit-research').disabled=!config.ready; $('#document-upload').disabled=!config.ready; $('#connection').textContent=config.ready?'API 已配置':'配置 API'; $('#connection').classList.toggle('ready',config.ready); $('#setup-notice').hidden=config.ready; $('#base-url').value=config.base_url; $('#model').value=config.model; $('#api-key').value=''; $('#search-key').value=''; $('#model-key-state').textContent=config.api_key_set?'已保存，留空保留原值':''; $('#search-key-state').textContent=config.search_key_set?'已保存，留空保留原值':'';}
async function loadHistory(){const runs=await api('/api/runs'); $('#history').innerHTML=runs.length?runs.map(r=>`<button class="history-item ${r.id===currentId?'selected':''}" data-id="${escape(r.id)}" title="${escape(r.question)}"><span class="dot ${escape(r.status)}"></span><span class="history-title">${escape(r.question)}</span></button>`).join(''):'<span class="history-empty">暂无研究记录</span>'; $$('.history-item').forEach(b=>b.onclick=()=>openRun(b.dataset.id));return runs;}
function showNew(){currentId=null;currentRun=null;renderedPlanId=null;clearTimeout(pollTimer); $('#composer').hidden=false;$('#run-view').hidden=true;$('#page-title').textContent='新建研究';history.replaceState(null,'',location.pathname);loadHistory().catch(e=>toast(e.message));$('#question').focus();}
async function openRun(id){clearTimeout(pollTimer);currentId=id;currentRun=null;renderedPlanId=null;setTab('activity');history.replaceState(null,'',`#${id}`);$('#composer').hidden=true;$('#run-view').hidden=false;await refreshRun();await loadHistory();}
function renderRun(run){currentRun=run;runFetchedAt=Date.now();renderPlan(run);renderBudgetMonitor();$('#page-title').textContent='研究详情';$('#run-question').textContent=run.question;$('#run-status').textContent=labels[run.status]||run.status;$('#run-status').className=`badge ${run.status}`;$('#run-meta').textContent=`${new Date(run.created_at).toLocaleString('zh-CN')} · LangChain Open Deep Research`;$('#cancel').hidden=!activeStatuses.has(run.status)&&run.status!=='awaiting_confirmation';$('#cancel').textContent=run.status==='awaiting_confirmation'?'取消计划':'■ 停止';$('#running-indicator').hidden=!activeStatuses.has(run.status);$('#running-indicator').innerHTML='<span class="spinner"></span>'+(run.status==='planning'?'正在生成研究计划':'研究进行中');const partial=!run.report&&!activeStatuses.has(run.status)&&(run.sources.length||(run.notes||[]).length);$('#download').hidden=!run.report&&!partial;$('#download').textContent=run.report?'↓ 下载报告':'↓ 下载已收集资料';$('#download').href=`/api/runs/${encodeURIComponent(run.id)}/${run.report?'report':'materials'}`;$('#run-error').hidden=!run.error;$('#run-error').textContent=run.error;$('#brief').textContent=run.brief||'研究范围尚未生成。';$('#events').innerHTML=run.events.map(e=>`<li class="${escape(e.kind)}"><time>${new Date(e.time).toLocaleTimeString('zh-CN')}</time><p>${escape(e.message)}</p></li>`).join('');$('#usage').innerHTML=`<span>模型调用<strong>${run.usage.model_calls||0}</strong></span><span>输入 tokens<strong>${(run.usage.input_tokens||0).toLocaleString()}</strong></span><span>输出 tokens<strong>${(run.usage.output_tokens||0).toLocaleString()}</strong></span>`;$('#panel-report').innerHTML=run.report_html||'<p class="report-empty">报告尚未生成。</p>';$('#source-count').textContent=run.sources.length;$('#sources').innerHTML=run.sources.length?run.sources.map((s,i)=>{let url;try{url=new URL(s.url);if(!['http:','https:'].includes(url.protocol))return '';}catch{return '';}return `<div class="source-row"><span class="source-number">${i+1}</span><div><a href="${escape(url.href)}" target="_blank" rel="noreferrer">${escape(s.title)}</a><small>${escape(url.hostname)}</small></div></div>`;}).join(''):'<p class="report-empty">暂无检索来源。</p>';}
async function refreshRun(){const id=currentId;if(!id)return;try{const run=await api(`/api/runs/${encodeURIComponent(id)}`);if(id!==currentId)return;const previous=currentRun?.status;renderRun(run);if(activeStatuses.has(run.status))pollTimer=setTimeout(refreshRun,1800);else{if(previous==='running'&&run.status==='complete')setTab('report');await loadHistory();}}catch(e){toast(e.message);if(id===currentId)pollTimer=setTimeout(refreshRun,5000);}}
async function openSettings(){try{await loadSettings();$('#settings-error').hidden=true;$('#settings-success').hidden=true;$('#settings-dialog').showModal();}catch(e){toast(e.message);}}
$('#new').onclick=showNew;$('#settings-open').onclick=openSettings;$('#connection').onclick=openSettings;$('#notice-settings').onclick=openSettings;$('#settings-close').onclick=()=>$('#settings-dialog').close();$$('.tabs button').forEach(b=>b.onclick=()=>setTab(b.dataset.tab));$$('[data-example]').forEach(b=>b.onclick=()=>{$('#question').value=b.dataset.example;$('#question').focus();});
$('#research-form').onsubmit=async e=>{e.preventDefault();if(!config?.ready)return openSettings();const button=$('#submit-research');button.disabled=true;try{const run=await api('/api/runs',{method:'POST',body:JSON.stringify({question:$('#question').value,depth:$('input[name=depth]:checked').value,budget:readBudget('budget')})});await openRun(run.id);}catch(error){toast(error.message);}finally{button.disabled=false;}};
$('#settings-form').onsubmit=async e=>{e.preventDefault();$('#settings-error').hidden=true;$('#settings-success').hidden=true;$('#save-settings').disabled=true;try{await api('/api/settings',{method:'PUT',body:JSON.stringify({base_url:$('#base-url').value,model:$('#model').value,api_key:$('#api-key').value,search_key:$('#search-key').value})});await loadSettings();$('#settings-success').textContent='个人配置已保存，仅用于你的研究。';$('#settings-success').hidden=false;}catch(error){$('#settings-error').textContent=error.message;$('#settings-error').hidden=false;}finally{$('#save-settings').disabled=false;}};
$('#test-connection').onclick=async()=>{$('#test-connection').disabled=true;$('#test-connection').textContent='测试中…';$('#settings-error').hidden=true;$('#settings-success').hidden=true;try{const result=await api('/api/settings/test',{method:'POST',body:'{}'});$('#settings-success').textContent=result.message;$('#settings-success').hidden=false;}catch(error){$('#settings-error').textContent=error.message;$('#settings-error').hidden=false;}finally{$('#test-connection').disabled=false;$('#test-connection').textContent='测试已保存配置';}};
$('#cancel').onclick=async()=>{if(!currentId)return;$('#cancel').disabled=true;try{await api(`/api/runs/${encodeURIComponent(currentId)}/cancel`,{method:'POST',body:'{}'});clearTimeout(pollTimer);await refreshRun();toast('研究已停止');}catch(e){toast(e.message);}finally{$('#cancel').disabled=false;}};
$('#plan-form').onsubmit = async event => {
  event.preventDefault();
  const id = currentId;
  if (!id) return;
  const outline = $('#plan-outline').value.split('\n').map(line => line.trim()).filter(Boolean);
  $('#plan-error').hidden = true;
  if (!outline.length || outline.length > 8 || outline.some(line => line.length > 200)) {
    $('#plan-error').textContent = '提纲应为 1–8 项，每行一项，每项不超过 200 字。';
    $('#plan-error').hidden = false;
    return;
  }
  $('#confirm-plan').disabled = true;
  try {
    await flushPlanDraft(id);
    const run = await api(`/api/runs/${encodeURIComponent(id)}/confirm`, {
      method: 'POST',
      body: JSON.stringify({
        plan: {
          objective: $('#plan-objective').value,
          outline,
          focus: $('#plan-focus').value,
          exclusions: $('#plan-exclusions').value,
          source_preferences: $('#plan-sources').value,
        },
        budget: readBudget('confirm'),
      }),
    });
    if (id === currentId) {
      clearTimeout(pollTimer);
      renderRun(run);
      setTab('activity');
      pollTimer = setTimeout(refreshRun, 1000);
      $('#panel-activity').scrollIntoView({behavior:'smooth',block:'start'});
    }
    await loadHistory();
    toast('已按确认计划开始研究');
  } catch (error) {
    $('#plan-error').textContent = error.message;
    $('#plan-error').hidden = false;
  } finally {
    $('#confirm-plan').disabled = false;
  }
};
document.addEventListener('DOMContentLoaded', async()=>{try{await loadSettings();if(!config.ready)await openSettings();const runs=await loadHistory();const id=location.hash.slice(1);if(id&&runs.some(r=>r.id===id))await openRun(id);else if(runs.find(r=>activeStatuses.has(r.status)))await openRun(runs.find(r=>activeStatuses.has(r.status)).id);}catch(e){toast(e.message);}});
