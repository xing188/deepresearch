/* Templates, account-scoped drafts and a navigable report reading view. */
const researchTemplates = [
  {id:'literature', name:'文献综述', hint:'梳理观点、方法与证据缺口', icon:'01', prompt:'请围绕【研究主题】撰写文献综述。\n时间范围：【例如：近三年】\n重点：主要研究问题、代表性论文、方法与结果对比、争议和研究空白。\n优先使用原始论文和公开数据；保留可核实链接，区分已有证据与推测。'},
  {id:'competitor', name:'竞品分析', hint:'对比产品能力与适用场景', icon:'02', prompt:'请对【产品 A】、【产品 B】和【产品 C】做竞品分析。\n目标用户：【填写用户群体】\n对比维度：核心功能、价格、优势与限制、适用场景。\n优先参考官方网站和近期公开评测，注明数据日期，用表格呈现差异并给出选择建议。'},
  {id:'industry', name:'行业调研', hint:'了解趋势、参与者与风险', icon:'03', prompt:'请调研【行业名称】在【地区】的发展现状。\n时间范围：【例如：近两年】\n重点：市场格局、主要参与者、技术或政策变化、应用机会与风险。\n注明关键数据的统计口径、日期及来源；无法核实的数据请明确标注。'},
  {id:'technical', name:'技术选型', hint:'权衡架构、成本与实施难度', icon:'04', prompt:'请比较【技术方案 A】与【技术方案 B】，用于【具体业务场景】。\n约束条件：【预算、数据规模、团队能力或部署环境】\n重点：架构、效果、成本、维护难度和安全边界。\n参考官方文档与公开评测，给出决策表、推荐方案及需要进一步验证的假设。'},
];
let selectedTemplate = '';
let composerSession;
const planSessions = new Map();
const followupInputs = new Map();

async function draftRequest(path, method = 'GET', payload) {
  const response = await fetch(path, {method, headers:{'Content-Type':'application/json'},
    ...(payload === undefined ? {} : {body:JSON.stringify(payload), keepalive:true})});
  if (response.status === 401) { location.replace('/login'); throw new Error('请重新登录'); }
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(typeof result.detail === 'string' ? result.detail : '请检查字段长度和预算范围后重试。');
    error.status = response.status; throw error;
  }
  return result;
}

class DraftSession {
  constructor(path, read, apply, status) {
    Object.assign(this, {path, read, apply, status, revision:0, loaded:false, pending:undefined, conflict:false});
  }
  async load() {
    clearTimeout(this.timer);
    if (this.busy) await this.busy.catch(()=>{});
    const result = await draftRequest(this.path);
    this.revision = result.revision; this.pending = undefined; this.conflict = false;
    this.apply(result); this.loaded = true;
    this.status(result.data ? '已恢复草稿 · 修改后自动保存' : '草稿自动保存至当前账号', false);
  }
  changed() {
    if (!this.loaded) return;
    this.pending = this.read();
    if (this.conflict) return;
    this.status('正在保存…', false);
    clearTimeout(this.timer);
    this.timer = setTimeout(()=>this.flush().catch(()=>{}), 650);
  }
  async flush() {
    if (!this.loaded) throw new Error('请等待草稿载入，或点击重新载入后重试。');
    if (this.conflict) throw new Error('另一页面已更新草稿，请先重新载入。');
    clearTimeout(this.timer);
    if (this.busy) { await this.busy; return this.flush(); }
    if (this.pending === undefined) return this.revision;
    this.busy = (async()=>{
      while (this.pending !== undefined) {
        const data = this.pending; this.pending = undefined;
        try {
          const result = await draftRequest(this.path, 'PUT', {revision:this.revision, data});
          this.revision = result.revision;
          this.status('已自动保存 · ' + new Date(result.updated_at).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'}), false);
        } catch(error) {
          if (this.pending === undefined) this.pending = data;
          this.conflict = error.status === 409;
          this.status('未保存：' + error.message, true);
          throw error;
        }
      }
    })();
    try { await this.busy; return this.revision; }
    finally { this.busy = null; }
  }
}

function setBudget(prefix, budget) {
  if (!budget) return;
  $(`#${prefix}-minutes`).value = budget.max_seconds / 60;
  $(`#${prefix}-calls`).value = budget.max_model_calls;
  $(`#${prefix}-tokens`).value = budget.max_tokens;
}
function updateBudgetSummary() {
  const b = readBudget('budget');
  $('#budget-summary').textContent = `${b.max_seconds / 60 || '—'} 分钟 / ${b.max_model_calls || '—'} 次调用 / ${b.max_tokens ? (b.max_tokens / 10000).toLocaleString('zh-CN') + ' 万' : '—'} tokens`;
}
function readComposer() {
  return {question:$('#question').value, depth:$('input[name=depth]:checked').value,
    report_language:$('#report-language').value, budget:readBudget('budget'),
    document_ids:uploads.map(d=>d.id), template:selectedTemplate};
}
function renderTemplates() {
  $('#research-templates').innerHTML = researchTemplates.map(t=>`<button type="button" class="template-card" data-template="${t.id}" aria-pressed="${t.id === selectedTemplate}"><span class="template-icon">${t.icon}</span><strong>${t.name}</strong><small>${t.hint}</small></button>`).join('');
  $$('[data-template]').forEach(button=>button.onclick=()=>{
    const template = researchTemplates.find(t=>t.id === button.dataset.template);
    if ($('#question').value.trim() && !confirm('使用模板会替换当前研究问题，继续吗？上传资料和预算将保留。')) return;
    selectedTemplate = template.id; $('#question').value = template.prompt;
    renderTemplates(); scheduleComposerDraft(); $('#question').focus();
  });
}
function scheduleComposerDraft() { composerSession?.changed(); }
async function flushComposerDraft() { return composerSession.flush(); }
async function composerSubmitted() {
  clearTimeout(composerSession.timer);
  composerSession.pending = undefined; composerSession.loaded = false;
  $('#research-form').reset(); selectedTemplate = ''; uploads = []; renderTemplates(); updateBudgetSummary();
  // Submission may have preserved a newer draft from another device.
  try { await composerSession.load(); }
  catch(error) { $('#draft-status').textContent = '草稿载入失败，请重新载入：' + error.message; $('#reload-draft').hidden = false; }
}

const originalRenderPlan = renderPlan;
renderPlan = function(run) {
  originalRenderPlan(run);
  if (run.status !== 'awaiting_confirmation') return;
  let session = planSessions.get(run.id);
  const ids = ['objective','outline','focus','exclusions','sources'];
  const apply = item=>{
    if (currentId !== run.id || currentRun?.status !== 'awaiting_confirmation' || !item.data) return;
    const data = item.data;
    ids.forEach(id=>$('#plan-'+id).value = data[id === 'sources' ? 'source_preferences' : id] || '');
    setBudget('confirm', data.budget);
  };
  if (!session) {
    session = new DraftSession(`/api/runs/${encodeURIComponent(run.id)}/draft`, ()=>({
      objective:$('#plan-objective').value, outline:$('#plan-outline').value,
      focus:$('#plan-focus').value, exclusions:$('#plan-exclusions').value,
      source_preferences:$('#plan-sources').value, budget:readBudget('confirm'),
    }), apply, (message, error)=>{
      if (currentId !== run.id) return;
      $('#plan-draft-status').textContent = message;
      $('#reload-plan-draft').hidden = !error;
    });
    planSessions.set(run.id, session);
  } else session.apply = apply;
  // Reload on revisiting a plan, while polling leaves the current edits intact.
  if (session.visible) return;
  planSessions.forEach(s=>s.visible = false); session.visible = true;
  $('#confirm-plan').disabled = true;
  $('#plan-form').inert = true;
  session.load().catch(error=>{
    $('#plan-draft-status').textContent = '草稿载入失败：' + error.message;
    $('#reload-plan-draft').hidden = false;
  }).finally(()=>{
    if (currentId === run.id) { $('#plan-form').inert = false; $('#confirm-plan').disabled = !session.loaded; }
  });
};
async function flushPlanDraft(id) { if (planSessions.has(id)) await planSessions.get(id).flush(); }

function enhanceReport(run) {
  const body = $('#panel-report .report-body');
  if (!body || !run.report) return;
  const headings = [...body.querySelectorAll('h2,h3,h4')];
  if (!headings.length) headings.push(...body.querySelectorAll('h1'));
  const layout = document.createElement('div'); layout.className = 'report-reading-layout';
  body.before(layout);
  const content = document.createElement('div'); content.className = 'report-content';
  layout.append(content); content.append(body);
  if (headings.length) {
    const toc = document.createElement('details'); toc.className = 'report-toc'; toc.open = innerWidth > 1050;
    const caption = document.createElement('summary'); caption.textContent = '报告目录 · ' + headings.length + ' 节'; toc.append(caption);
    const nav = document.createElement('nav'); nav.setAttribute('aria-label', '报告章节'); toc.append(nav);
    headings.forEach((heading, index)=>{
      heading.id = 'report-section-' + index;
      const button = document.createElement('button'); button.textContent = heading.textContent;
      button.className = 'toc-level-' + heading.tagName.slice(1);
      button.onclick = ()=>{
        nav.querySelectorAll('button').forEach(b=>b.removeAttribute('aria-current'));
        button.setAttribute('aria-current','location');
        heading.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block:'start'});
        heading.tabIndex = -1; heading.focus({preventScroll:true});
      };
      nav.append(button);
    });
    layout.prepend(toc);
  }
  const summaryHeading = headings.find(h=>/^(?:\d+[.、\s]*)?(?:执行摘要|摘要|核心结论|主要结论|研究结论|结论|summary|executive summary|key findings|conclusions?)\b/i.test(h.textContent.trim()) || /^(?:\d+[.、\s]*)?(?:执行摘要|摘要|核心结论|主要结论|研究结论|结论)(?:$|[\s：:])/.test(h.textContent.trim()));
  let excerpts = [];
  if (summaryHeading) {
    for (let node = summaryHeading.nextElementSibling; node; node = node.nextElementSibling) {
      if (/^H[1-6]$/.test(node.tagName)) break;
      if (node.matches('p')) excerpts.push(node.textContent);
      if (node.matches('ul,ol')) excerpts.push(...[...node.children].map(li=>li.textContent));
    }
  }
  if (!excerpts.length) excerpts = [...body.querySelectorAll('p')].filter(p=>!p.closest('table,blockquote')).slice(0,2).map(p=>p.textContent);
  if (excerpts.length) {
    const summary = document.createElement('details'); summary.className = 'report-overview'; summary.open = true;
    const caption = document.createElement('summary'); caption.textContent = summaryHeading ? '摘要与要点' : '内容速览';
    const note = document.createElement('small'); note.textContent = summaryHeading ? '摘自「' + summaryHeading.textContent + '」' : '报告开头摘录';
    summary.append(caption, note);
    excerpts.filter(s=>s.trim()).slice(0,4).forEach(text=>{
      const p = document.createElement('p'); p.textContent = text.length > 360 ? text.slice(0,360) + '…' : text;
      summary.append(p);
    });
    content.prepend(summary);
  }
}

function renderRecovery(run) {
  const notice = $('#recovery-notice');
  notice.hidden = !run.can_resume && !run.can_retry_plan;
  notice.replaceChildren();
  if (!notice.hidden) {
    const text = document.createElement('div');
    const title = document.createElement('strong'); title.textContent = run.can_retry_plan ? '这次研究可以重新规划' : '已保存研究进度，可以继续';
    const hint = document.createElement('p'); hint.textContent = run.can_retry_plan ? '保留上传资料和累计用量，重新生成计划后由你确认。' : '复用已保存的资料和执行节点；尚未完成的节点可能重跑。继续前可确认追加预算。';
    text.append(title,hint);
    const button = document.createElement('button'); button.className = 'primary'; button.textContent = run.can_retry_plan ? '重新生成计划 →' : '从断点继续 →';
    button.onclick = ()=>$('#resume-run').click(); notice.append(text,button);
  }
  const steps = ['理解问题','搜索资料','整理证据','撰写报告'];
  let stage = run.status === 'complete' ? 4 : ({planning:0,awaiting_confirmation:0,researching:1,searching:1,analyzing:2,writing:3}[run.stage] ?? 0);
  if (run.status === 'interrupted' || run.status === 'failed' || run.status === 'cancelled' || run.status === 'budget_exceeded') {
    const writing = run.events.some(e=>e.kind === 'writing');
    stage = writing ? 3 : run.notes?.length ? 2 : run.sources?.length ? 1 : 0;
  }
  $('#research-steps').hidden = run.operation !== 'research';
  $('#research-steps').innerHTML = steps.map((name,i)=>`<li class="${i < stage ? 'done' : i === stage ? 'current' : ''}" ${i === stage ? 'aria-current="step"' : ''}><span>${i < stage ? '✓' : i+1}</span>${name}</li>`).join('');
}

const productivityRenderRun = renderRun;
renderRun = function(run) {
  const prompt = $('#followup-prompt');
  if (prompt && currentRun?.id) followupInputs.set(currentRun.id, {prompt:prompt.value, mode:$('#followup-mode').value});
  productivityRenderRun(run);
  enhanceReport(run); renderRecovery(run);
  const previous = followupInputs.get(run.id);
  if ($('#followup-prompt') && previous) { $('#followup-prompt').value = previous.prompt; $('#followup-mode').value = previous.mode; }
};

const productivityOpenRun = openRun;
openRun = async function(id) {
  flushVisibleDrafts();
  planSessions.forEach(s=>s.visible = false);
  collapseMobileSidebar();
  return productivityOpenRun(id);
};
const productivityShowNew = showNew;
showNew = function() {
  flushVisibleDrafts();
  planSessions.forEach(s=>s.visible = false);
  collapseMobileSidebar();
  productivityShowNew();
};
$('#new').onclick = showNew;

function collapseMobileSidebar() {
  $('.sidebar').classList.remove('mobile-expanded');
  $('#mobile-sidebar-toggle').setAttribute('aria-expanded','false');
  $('#mobile-sidebar-toggle span').textContent = '展开 ⌄';
}
$('#mobile-sidebar-toggle').onclick = ()=>{
  const open = $('.sidebar').classList.toggle('mobile-expanded');
  $('#mobile-sidebar-toggle').setAttribute('aria-expanded',String(open));
  $('#mobile-sidebar-toggle span').textContent = open ? '收起 ⌃' : '展开 ⌄';
};

document.addEventListener('DOMContentLoaded', async()=>{
  renderTemplates(); updateBudgetSummary();
  composerSession = new DraftSession('/api/drafts/composer', readComposer, item=>{
    $('#research-form').reset(); selectedTemplate = ''; uploads = [];
    if (item.data) {
      const d = item.data; $('#question').value = d.question;
      $(`input[name=depth][value="${d.depth}"]`).checked = true;
      $('#report-language').value = d.report_language;
      selectedTemplate = d.template; setBudget('budget', d.budget); uploads = item.documents || [];
    }
    // Rendering restored uploads must not enqueue a write while loading.
    const loaded = composerSession.loaded; composerSession.loaded = false;
    renderUploads(); composerSession.loaded = loaded;
    renderTemplates(); updateBudgetSummary();
  }, (message,error)=>{ $('#draft-status').textContent = message; $('#reload-draft').hidden = !error; });
  async function loadComposer() {
    $('#research-form').inert = true; $('#research-templates').inert = true;
    try { await composerSession.load(); }
    catch(error) { $('#draft-status').textContent = '草稿载入失败：' + error.message; $('#reload-draft').hidden = false; }
    finally { $('#research-form').inert = !composerSession.loaded; $('#research-templates').inert = !composerSession.loaded; }
  }
  $('#reload-draft').onclick = ()=>{
    if (composerSession.pending !== undefined && !confirm('重新载入会替换此页面尚未保存的内容，继续吗？')) return;
    loadComposer();
  };
  $('#clear-draft').onclick = async()=>{
    if (uploadBusy) return toast('请等待资料上传完成');
    if (!confirm('清空当前研究草稿？已生成的研究记录将保留。')) return;
    try { await composerSession.flush(); composerSession.pending = null; await composerSession.flush(); await composerSession.load(); toast('草稿已清空'); }
    catch(error) { toast(error.message); }
  };
  $('#research-form').addEventListener('input', ()=>{ updateBudgetSummary(); scheduleComposerDraft(); });
  $('#research-form').addEventListener('change', scheduleComposerDraft);
  $$('[data-example]').forEach(button=>button.addEventListener('click',()=>{ selectedTemplate = ''; renderTemplates(); scheduleComposerDraft(); }));
  $('#plan-form').addEventListener('input', ()=>planSessions.get(currentId)?.changed());
  $('#reload-plan-draft').onclick = async()=>{
    const id = currentId, session = planSessions.get(id);
    if (!session || !confirm('重新载入会替换此页面尚未保存的计划修改，继续吗？')) return;
    try { await session.load(); $('#confirm-plan').disabled = false; }
    catch(error) { toast(error.message); }
  };
  await loadComposer();
});

function flushVisibleDrafts() {
  const tasks = [];
  if (composerSession?.loaded) tasks.push(composerSession.flush());
  for (const s of planSessions.values()) if (s.pending !== undefined) tasks.push(s.flush());
  return Promise.allSettled(tasks);
}
document.addEventListener('visibilitychange', ()=>{ if (document.hidden) flushVisibleDrafts(); });
window.addEventListener('pagehide', flushVisibleDrafts);
