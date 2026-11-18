/* Research materials, evidence, report conversations and library controls. */
let uploads = [];
let uploadBusy = false;
let pendingAction = null;
let reportViewLanguage = 'original';
let historyRequest = 0;
let historySearchTimer;
const pendingPins = new Set();

loadHistory = async function () {
  const request = ++historyRequest;
  const query = $('#history-search').value.trim();
  const runs = await api('/api/runs?q=' + encodeURIComponent(query));
  if (request !== historyRequest) return runs;
  $('#history').innerHTML = runs.length ? runs.map(r => `<div class="history-entry ${r.id === currentId ? 'selected' : ''} ${r.pinned ? 'pinned' : ''}"><button class="history-item ${r.id === currentId ? 'selected' : ''}" data-id="${escape(r.id)}" title="${escape(r.question)}"><span class="dot ${escape(r.status)}"></span><span class="history-text"><span class="history-title">${escape(r.title || r.question)}</span>${r.tags?.length || r.pinned ? `<small>${r.pinned ? '<span class="pin-label">置顶</span>' : ''}${r.tags?.map(escape).join(' · ') || ''}</small>` : ''}</span></button><button class="history-pin" data-pin="${escape(r.id)}" data-pinned="${Boolean(r.pinned)}" title="${r.pinned ? '取消置顶' : '置顶'}" aria-label="${r.pinned ? '取消置顶' : '置顶'}：${escape(r.title || r.question)}" aria-pressed="${Boolean(r.pinned)}" ${pendingPins.has(r.id) ? 'disabled' : ''}><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m16 3 5 5-3 1-4 4v4l-3-3-7 7 7-7-4-4h4l4-4z"/></svg></button></div>`).join('') : '<span class="history-empty">没有匹配的研究记录</span>';
  $$('.history-item').forEach(b => b.onclick = () => openRun(b.dataset.id).catch(e => toast(e.message)));
  $$('.history-pin').forEach(b => b.onclick = async () => {
    const id = b.dataset.pin;
    if (pendingPins.has(id)) return;
    const pinned = b.dataset.pinned !== 'true';
    pendingPins.add(id);
    b.disabled = true;
    try {
      await api(`/api/runs/${encodeURIComponent(id)}/pin`, {method:'PATCH', body:JSON.stringify({pinned})});
      if (currentRun?.id === id) currentRun.pinned = pinned;
      await loadHistory();
      toast(pinned ? '研究记录已置顶' : '已取消置顶');
    } catch (error) { toast(error.message); }
    finally {
      pendingPins.delete(id);
      const button = $$('.history-pin').find(item => item.dataset.pin === id);
      if (button) button.disabled = false;
    }
  });
  return runs;
};
$('#history-search').addEventListener('input', () => {
  clearTimeout(historySearchTimer);
  historySearchTimer = setTimeout(() => loadHistory().catch(e => toast(e.message)), 220);
});

function renderUploads() {
  $('#upload-list').innerHTML = uploads.map(d => `<div class="upload-item"><span>▤ ${escape(d.name)}</span><button type="button" data-remove-doc="${escape(d.id)}" aria-label="移除 ${escape(d.name)}">×</button></div>`).join('');
  $$('[data-remove-doc]').forEach(b => b.onclick = () => { uploads = uploads.filter(d => d.id !== b.dataset.removeDoc); renderUploads(); scheduleComposerDraft(); });
}
$('#document-upload').onchange = async e => {
  const files = [...e.target.files];
  if (files.length + uploads.length > 5) { toast('每次研究最多添加 5 份资料'); e.target.value = ''; return; }
  uploadBusy = true;
  $('#document-upload').disabled = true;
  $('#submit-research').disabled = true;
  try {
    for (const file of files) {
      if (file.size > 10 * 1024 * 1024) throw new Error(`${file.name} 超过 10 MB`);
      const form = new FormData();
      form.append('file', file);
      const response = await fetch('/api/documents', {method:'POST', body:form});
      const item = await response.json();
      if (!response.ok) throw new Error(item.detail || '上传失败');
      uploads.push(item);
      renderUploads();
      scheduleComposerDraft();
    }
    toast('资料已上传，将与网页资料一起用于研究');
  } catch (error) { toast(error.message); }
  finally { uploadBusy = false; $('#document-upload').disabled = false; $('#submit-research').disabled = false; e.target.value = ''; }
};
$('#research-form').onsubmit = async e => {
  e.preventDefault();
  if (uploadBusy) return;
  if (!config?.ready) return openSettings();
  $('#submit-research').disabled = true;
  try {
    const draftRevision = await flushComposerDraft();
    const run = await api('/api/runs', {method:'POST', body:JSON.stringify({question:$('#question').value, depth:$('input[name=depth]:checked').value, report_language:$('#report-language').value, budget:readBudget('budget'), document_ids:uploads.map(d => d.id), draft_revision:draftRevision})});
    uploads = [];
    await composerSubmitted();
    renderUploads();
    await openRun(run.id);
  } catch (error) { toast(error.message); }
  finally { $('#submit-research').disabled = false; }
};

function canonical(url) {
  try { const u = new URL(url, location.origin); u.hash = ''; return u.href.replace(/\/$/, ''); }
  catch { return ''; }
}
function evidence(source) {
  $('#evidence-title').textContent = source.title;
  $('#evidence-time').textContent = source.retrieved_at ? '获取时间：' + new Date(source.retrieved_at).toLocaleString('zh-CN') : '旧记录未保存获取时间';
  const url = new URL(source.url, location.origin);
  $('#evidence-link').href = url.href;
  $('#evidence-link').hidden = !['http:', 'https:'].includes(url.protocol);
  $('#evidence-excerpt').textContent = source.excerpt || '这条旧来源没有保存历史摘录。可打开原链接自行核对；页面当前内容可能已发生变化。';
  $('#evidence-dialog').showModal();
}
function bindCitations(container, sources, markdown = currentRun?.report || '') {
  container.querySelectorAll('a[href]').forEach(a => {
    const source = sources.find(s => canonical(s.url) === canonical(a.getAttribute('href')));
    a.target = '_blank'; a.rel = 'noreferrer';
    if (source) {
      a.classList.add('citation-link');
      a.title = '查看来源证据';
      a.addEventListener('click', e => { e.preventDefault(); evidence(source); });
    }
  });
  // Citation numbers belong to the report's bibliography, not to search-result order.
  const numbered = new Map();
  for (const m of markdown.matchAll(/^\s*(?:[-*]\s*)?\[(\d+)\][^\n]*?(https?:\/\/[^\s<>)\]]+|\/api\/documents\/[\w-]+)/gm)) {
    const url = m[2].replace(/[.,;，。；]+$/, '');
    const source = sources.find(s => canonical(s.url) === canonical(url));
    if (source) numbered.set(m[1], source);
  }
  if (!numbered.size) return;
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.parentElement.closest('a,button,pre,code,textarea') && /\[\d+\]/.test(node.textContent)) nodes.push(node);
  }
  for (const node of nodes) {
    const fragment = document.createDocumentFragment();
    let last = 0;
    for (const m of node.textContent.matchAll(/\[(\d+)\]/g)) {
      const source = numbered.get(m[1]);
      if (!source) continue;
      fragment.append(document.createTextNode(node.textContent.slice(last, m.index)));
      const button = document.createElement('button');
      button.className = 'citation-number'; button.textContent = m[0];
      button.title = '查看来源证据：' + source.title;
      button.onclick = () => evidence(source);
      fragment.append(button);
      last = m.index + m[0].length;
    }
    if (last) { fragment.append(document.createTextNode(node.textContent.slice(last))); node.replaceWith(fragment); }
  }
}

const baseRenderRun = renderRun;
renderRun = function (run) {
  baseRenderRun(run);
  $('#run-question').textContent = run.title || run.question;
  $('#run-meta').textContent = `${new Date(run.created_at).toLocaleString('zh-CN')} · ${run.tags?.join(' · ') || 'LangChain Open Deep Research'}`;
  $('#resume-run').hidden = !run.can_resume && !run.can_retry_plan;
  $('#resume-run').textContent = run.can_retry_plan ? '重新生成计划 →' : '从断点继续 →';
  $('#manage-run').disabled = false;
  $('#sources').innerHTML = run.sources.length ? run.sources.map((s,i) => `<div class="source-row"><span class="source-number">${i+1}</span><div class="source-details"><button class="source-title" data-evidence="${i}">${escape(s.title)}</button><small>${s.kind === 'document' ? '上传资料' : escape(s.url)}${s.retrieved_at ? ' · ' + escape(new Date(s.retrieved_at).toLocaleDateString('zh-CN')) : ' · 无历史摘录'}</small><p class="source-preview">${escape(s.excerpt?.slice(0,160) || '旧记录未保存摘录，点击查看来源。')}</p></div></div>`).join('') : '<p class="report-empty">暂无来源，检索完成后将在此显示。</p>';
  $$('[data-evidence]').forEach(b => b.onclick = () => evidence(run.sources[Number(b.dataset.evidence)]));
  const running = activeStatuses.has(run.status);
  if (!run.report) reportViewLanguage='original';
  const translated = (run.translations || []).find(t=>t.language===reportViewLanguage);
  const report = translated?.report_html || run.report_html || run.partial_report_html || '<p class="report-empty">报告尚未生成。收集到资料后会显示阶段资料汇总。</p>';
  const draft = !run.report && run.partial_report_html;
  $('#panel-report').innerHTML = `<div class="report-toolbar"><div>${draft ? '<span class="badge">阶段资料汇总 · 尚非最终结论</span>' : '<span class="muted">研究成果</span>'}</div><div class="export-actions">${run.report ? `<div class="translation-controls"><select id="report-view-language" aria-label="选择原文或译文"><option value="original">原文${run.report_language==='zh'?' · 中文':run.report_language==='en'?' · English':''}</option><option value="zh">简体中文</option><option value="en">English</option></select><button class="secondary" id="translate-report" ${running?'disabled':''}>翻译报告</button></div><a class="secondary" href="/api/runs/${encodeURIComponent(run.id)}/export/docx?language=${translated?reportViewLanguage:'original'}">↓ Word</a><a class="secondary" href="/api/runs/${encodeURIComponent(run.id)}/export/pdf?language=${translated?reportViewLanguage:'original'}">↓ PDF</a>` : ''}${run.versions?.length ? '<button id="show-versions" class="secondary">版本记录</button>' : ''}</div></div><div class="report-body">${report}</div>${run.report ? `<section class="report-chat"><div class="chat-heading"><h2>围绕报告继续讨论</h2><span>复用已有资料</span></div><p class="budget-hint">可追问细节，或按要求生成修改版报告。不会重新联网搜索；旧版报告会保留。</p><div id="chat-messages">${(run.chats || []).map(c => `<div class="chat-message ${c.role === 'user' ? 'user' : 'assistant'}"><small>${c.role === 'user' ? (c.mode === 'revise' ? '修改要求' : '你的追问') : '研究助手'}</small><div>${c.html || escape(c.content)}</div></div>`).join('')}</div><form id="followup-form"><label for="followup-prompt">追问或修改要求</label><textarea id="followup-prompt" rows="3" maxlength="3000" required placeholder="例如：解释成本对比的依据；或将报告改为管理层摘要。" ${running ? 'disabled' : ''}></textarea><div class="followup-actions"><select id="followup-mode" aria-label="操作类型" ${running ? 'disabled' : ''}><option value="ask">追问报告</option><option value="revise">生成修改版报告</option></select><button class="primary" type="submit" ${running ? 'disabled' : ''}>${running ? '正在处理…' : '设置预算并发送 →'}</button></div></form></section>` : ''}`;
  if ($('#report-view-language')) {
    $('#report-view-language').value=reportViewLanguage;
    const updateTranslationButton=()=>{
      const language=$('#report-view-language').value;
      const cached=language==='original'||language===run.report_language||(run.translations||[]).some(t=>t.language===language);
      $('#translate-report').textContent=cached?'当前语言已可用':'生成译文 →';
      $('#translate-report').disabled=running||cached;
    };
    updateTranslationButton();
    $('#report-view-language').onchange=()=>{
      const language=$('#report-view-language').value;
      const cached=language==='original'||language===run.report_language||(run.translations||[]).some(t=>t.language===language);
      if(cached){reportViewLanguage=language;renderRun(run);setTab('report');}
      else{updateTranslationButton();toast('该语言尚无译文，请点击生成译文；当前仍显示原文。');}
    };
    $('#translate-report').onclick=()=>{
      pendingAction={runId:run.id,kind:'translate',language:$('#report-view-language').value};
      openAction('翻译报告的本次预算','翻译使用你自己的模型 API，并计入个人用量。保留原报告、链接和引用，生成后可切换原文与译文。');
    };
    $('#download').href=`/api/runs/${encodeURIComponent(run.id)}/report?language=${translated?reportViewLanguage:'original'}`;
  }
  bindCitations($('#panel-report'), run.sources, translated?.report || run.report);
  if ($('#show-versions')) $('#show-versions').onclick = showVersions;
  if ($('#followup-form')) $('#followup-form').onsubmit = e => {
    e.preventDefault();
    const prompt = $('#followup-prompt').value.trim();
    if (prompt.length < 2) return toast('请填写具体要求');
    pendingAction = {runId:run.id, kind:'followup', prompt, mode:$('#followup-mode').value};
    openAction('追问与修改的本次预算', '将复用已有报告、研究笔记与上传资料。修改报告会创建新版本，保留原内容。');
  };
};

function openAction(title, description) {
  $('#action-title').textContent = title;
  $('#action-description').textContent = description;
  $('#action-dialog').showModal();
}
$('#resume-run').onclick = () => {
  const retryPlan = currentRun?.can_retry_plan;
  pendingAction = {runId:currentId, kind:retryPlan ? 'retry-plan' : 'resume'};
  openAction(retryPlan ? '重新生成计划的本次预算' : '继续研究的本次预算', retryPlan ? '将重新执行规划，保留当前记录、上传资料与累计用量。计划生成后仍需你确认才开始搜索。' : '从最近保存的执行节点继续，复用已保存状态与资料。中断时尚未完成的节点可能重跑，已消耗用量不会清零。');
};
$('#action-form').onsubmit = async e => {
  e.preventDefault();
  if (!pendingAction) return;
  const action = pendingAction;
  $('#action-submit').disabled = true;
  try {
    await api(`/api/runs/${encodeURIComponent(action.runId)}/${action.kind}`, {method:'POST', body:JSON.stringify({budget:readBudget('extra'), ...(action.kind === 'followup' ? {prompt:action.prompt, mode:action.mode} : action.kind==='translate'?{language:action.language}:{})})});
    $('#action-dialog').close();
    pendingAction = null;
    if (action.kind === 'followup' && $('#followup-prompt')) $('#followup-prompt').value = '';
    if(action.kind==='translate')reportViewLanguage=action.language;
    if (currentId === action.runId) {
      clearTimeout(pollTimer);
      await refreshRun();
      setTab(['followup','translate'].includes(action.kind) ? 'report' : 'activity');
    }
  } catch (error) { toast(error.message); }
  finally { $('#action-submit').disabled = false; }
};

$('#manage-run').onclick = () => {
  $('#history-title').value = currentRun.title || '';
  $('#history-tags').value = (currentRun.tags || []).join(', ');
  $('#delete-run').disabled = activeStatuses.has(currentRun.status);
  $('#manage-dialog').showModal();
};
$('#manage-form').onsubmit = async e => {
  e.preventDefault();
  try {
    await api(`/api/runs/${encodeURIComponent(currentId)}`, {method:'PATCH', body:JSON.stringify({title:$('#history-title').value, tags:$('#history-tags').value.split(/[,，]/).map(t=>t.trim()).filter(Boolean)})});
    $('#manage-dialog').close();
    await refreshRun();
    await loadHistory();
    toast('记录已更新');
  } catch (error) { toast(error.message); }
};
$('#delete-run').onclick = async () => {
  if (!confirm('确定删除这条研究记录及其报告、上传资料、对话和版本吗？删除后无法恢复。')) return;
  try {
    await api(`/api/runs/${encodeURIComponent(currentId)}`, {method:'DELETE'});
    $('#manage-dialog').close();
    showNew();
    toast('研究记录已删除');
  } catch (error) { toast(error.message); }
};
async function showVersions() {
  $('#version-select').innerHTML = currentRun.versions.map(v => `<option value="${v.id}">${escape(new Date(v.created_at).toLocaleString('zh-CN'))} · ${escape(v.label)}</option>`).join('');
  $('#restore-version').disabled = activeStatuses.has(currentRun.status);
  $('#version-dialog').showModal();
  await loadVersion();
}
async function loadVersion() {
  try {
    const item = await api(`/api/runs/${encodeURIComponent(currentId)}/versions/${$('#version-select').value}`);
    $('#version-body').innerHTML = item.report_html;
    bindCitations($('#version-body'), currentRun.sources, item.report);
  } catch (error) { toast(error.message); }
}
$('#version-select').onchange = loadVersion;
$('#restore-version').onclick = async () => {
  try {
    await api(`/api/runs/${encodeURIComponent(currentId)}/versions/${$('#version-select').value}/restore`, {method:'POST', body:'{}'});
    $('#version-dialog').close();
    await refreshRun();
    toast('历史版本已恢复，原报告也已保留');
  } catch (error) { toast(error.message); }
};
$$('[data-close]').forEach(b => b.onclick = () => $('#' + b.dataset.close).close());
