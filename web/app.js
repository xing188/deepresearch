const state = { projects: [], current: null, tab: "overview", toastTimer: null };
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = (value = "") => String(value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) throw new Error(data?.error || "请求失败，请重试。");
  return data;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("show"), 2300);
}

function dateLabel(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric" }).format(new Date(value));
}

function statusLabel(status) {
  return ({ planning: "规划中", active: "进行中", complete: "已完成" })[status] || "规划中";
}

async function loadProjects(preferredId) {
  state.projects = await api("/api/projects");
  $("#project-count").textContent = state.projects.length;
  renderProjectList();
  if (preferredId) await openProject(preferredId);
  else if (state.current) {
    const fresh = state.projects.find(project => project.id === state.current.id);
    if (fresh) { state.current = fresh; renderProject(); }
    else { state.current = null; renderHome(); }
  } else renderHome();
}

function renderProjectList() {
  const list = $("#project-list");
  list.replaceChildren();
  for (const project of state.projects.slice(0, 8)) {
    const button = document.createElement("button");
    button.className = `project-link${state.current?.id === project.id ? " active" : ""}`;
    button.innerHTML = `<span class="mini-project-icon">◦</span><span>${escapeHTML(project.title)}</span>`;
    button.addEventListener("click", () => openProject(project.id));
    list.append(button);
  }
}

function renderHome() {
  $("#empty-state").hidden = false;
  $("#project-view").hidden = true;
  $("#breadcrumb-current").textContent = "研究项目";
}

function openProject(projectId) {
  const project = state.projects.find(item => item.id === projectId);
  if (!project) return;
  state.current = project;
  state.tab = "overview";
  renderProjectList();
  renderProject();
  switchTab("overview");
  $("#main-scroll").scrollTop = 0;
}

function renderProject() {
  const project = state.current;
  if (!project) return renderHome();
  $("#empty-state").hidden = true;
  $("#project-view").hidden = false;
  $("#breadcrumb-current").textContent = project.title;
  $("#project-title").textContent = project.title;
  $("#project-question").textContent = project.question;
  $("#project-date").textContent = `创建于 ${dateLabel(project.created_at)}`;
  const done = project.tasks.filter(task => task.status === "done").length;
  const total = project.tasks.length;
  const progress = total ? Math.round(done / total * 100) : 0;
  $("#progress-value").textContent = `${progress}%`;
  $("#progress-bar").style.width = `${progress}%`;
  $("#task-count").textContent = total;
  $("#source-count").textContent = project.sources.length;
  $("#finding-count").textContent = project.findings.length;
  $("#tab-source-count").textContent = project.sources.length;
  $("#tab-finding-count").textContent = project.findings.length;
  const status = $("#project-status");
  status.textContent = statusLabel(project.status);
  status.className = `status-pill ${project.status}`;
  $("#objective-text").textContent = project.objective || "尚未设置研究目标。编辑目标，明确这项研究最终希望回答的问题。";
  $("#objective-text").classList.toggle("empty", !project.objective);
  renderTasks();
  renderLatest();
  renderSources();
  renderFindings();
  renderReport();
}

function renderTasks() {
  const groups = $("#task-groups");
  groups.replaceChildren();
  const phases = ["Frame", "Map", "Gather", "Synthesize"];
  const labels = { Frame: "界定问题", Map: "绘制研究地图", Gather: "收集证据", Synthesize: "综合分析" };
  for (const phase of phases) {
    const tasks = state.current.tasks.filter(task => task.phase === phase);
    if (!tasks.length) continue;
    const group = document.createElement("section");
    group.className = "task-group";
    group.innerHTML = `<div class="task-group-heading"><span class="phase-dot"></span><strong>${labels[phase] || phase}</strong><span class="phase-count">${tasks.filter(task => task.status === "done").length} / ${tasks.length}</span></div><div class="task-rows"></div>`;
    const rows = $(".task-rows", group);
    for (const task of tasks) {
      const label = document.createElement("label");
      label.className = "task-row";
      label.innerHTML = `<input class="task-check" type="checkbox" ${task.status === "done" ? "checked" : ""} aria-label="完成：${escapeHTML(task.title)}"><span class="task-title ${task.status === "done" ? "done" : ""}">${escapeHTML(task.title)}</span>`;
      $("input", label).addEventListener("change", async event => {
        const nextStatus = event.target.checked ? "done" : "todo";
        try {
          state.current = await api(`/api/projects/${state.current.id}/tasks/${task.id}`, { method: "PATCH", body: JSON.stringify({ status: nextStatus }) });
          await loadProjects(state.current.id);
        } catch (error) { showToast(error.message); }
      });
      rows.append(label);
    }
    groups.append(group);
  }
}

function renderLatest() {
  const latest = [
    ...state.current.sources.map(item => ({ ...item, kind: "source", sort: item.added_at })),
    ...state.current.findings.map(item => ({ ...item, kind: "finding", sort: item.added_at })),
  ].sort((a, b) => b.sort.localeCompare(a.sort)).slice(0, 3);
  const container = $("#latest-items");
  container.replaceChildren();
  if (!latest.length) {
    const empty = document.createElement("p"); empty.className = "no-latest"; empty.textContent = "来源和研究发现会显示在这里。"; container.append(empty); return;
  }
  for (const item of latest) {
    const row = document.createElement("div"); row.className = "latest-item";
    const name = item.kind === "source" ? item.title : item.text;
    row.innerHTML = `<span class="latest-icon">${item.kind === "source" ? "↗" : "✎"}</span><span><strong>${escapeHTML(name)}</strong><small>${item.kind === "source" ? "研究来源" : "研究发现"} · ${dateLabel(item.sort)}</small></span>`;
    container.append(row);
  }
}

function renderSources() {
  const panel = $("#tab-sources");
  const entries = [...state.current.sources].reverse();
  const cards = entries.map(source => `<article class="record-card"><div class="record-top"><span class="record-icon">↗</span><div class="record-copy"><div class="record-title">${escapeHTML(source.title)}</div><div class="record-meta">${escapeHTML(source.publisher || "未注明发布方")} · ${dateLabel(source.added_at)}</div><a class="record-url" href="${escapeHTML(source.url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(source.url)}</a></div></div>${source.note ? `<p class="record-note">${escapeHTML(source.note)}</p>` : ""}</article>`).join("");
  panel.innerHTML = `<div class="list-view"><div class="list-main"><div class="list-toolbar"><div><h2>研究来源</h2><p>记录并整理支撑研究的材料。</p></div><span class="muted">${entries.length} 个来源</span></div><div class="source-list">${cards || `<div class="empty-list"><div><strong>还没有研究来源</strong>添加论文、报告或可信网页。</div></div>`}</div></div><form class="side-form" id="source-form"><h3>添加来源</h3><div class="form-fields"><div class="wide"><label for="source-title">来源标题 <span class="required">*</span></label><input id="source-title" name="title" required maxlength="180" placeholder="报告、论文或网页标题"></div><div class="wide"><label for="source-url">URL <span class="required">*</span></label><input id="source-url" name="url" type="url" required maxlength="1500" placeholder="https://"></div><div><label for="source-publisher">发布方</label><input id="source-publisher" name="publisher" maxlength="120" placeholder="作者或机构"></div><div><label for="source-note">研究备注</label><input id="source-note" name="note" maxlength="500" placeholder="关键信息"></div></div><button class="primary-button" type="submit">保存来源 <span>＋</span></button></form></div>`;
  $("#source-form", panel).addEventListener("submit", addSource);
}

function renderFindings() {
  const panel = $("#tab-findings");
  const entries = [...state.current.findings].reverse();
  const cards = entries.map(finding => {
    const source = state.current.sources.find(item => item.id === finding.source_id);
    return `<article class="record-card finding-card"><div class="record-top"><span class="record-icon">✎</span><div class="record-copy"><div class="record-meta">研究发现 · ${dateLabel(finding.added_at)}</div><div class="finding-text">${escapeHTML(finding.text)}</div>${source ? `<div class="finding-source">来源：<a href="${escapeHTML(source.url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(source.title)}</a></div>` : ""}</div></div></article>`;
  }).join("");
  const options = state.current.sources.map(source => `<option value="${escapeHTML(source.id)}">${escapeHTML(source.title)}</option>`).join("");
  panel.innerHTML = `<div class="list-view"><div class="list-main"><div class="list-toolbar"><div><h2>研究发现</h2><p>记录事实、解释和仍待验证的判断。</p></div><span class="muted">${entries.length} 条发现</span></div><div class="finding-list">${cards || `<div class="empty-list"><div><strong>还没有记录研究发现</strong>把重要证据或暂时结论整理在这里。</div></div>`}</div></div><form class="side-form" id="finding-form"><h3>记录一条发现</h3><label for="finding-text">内容 <span class="required">*</span></label><textarea id="finding-text" name="text" rows="6" required maxlength="3000" placeholder="写下发现的证据、观点或待验证的问题"></textarea><label for="finding-source">关联来源 <span class="optional">可选</span></label><select id="finding-source" name="source_id"><option value="">暂不关联</option>${options}</select><button class="primary-button" type="submit">保存发现 <span>＋</span></button></form></div>`;
  $("#finding-form", panel).addEventListener("submit", addFinding);
}

function renderReport() {
  const project = state.current;
  const sources = project.sources.map((source, index) => `<li class="report-source"><a href="${escapeHTML(source.url)}">${index + 1}. ${escapeHTML(source.title)}</a>${source.publisher ? ` · ${escapeHTML(source.publisher)}` : ""}</li>`).join("");
  const findings = project.findings.map(item => `<li>${escapeHTML(item.text)}</li>`).join("");
  const tasks = project.tasks.map(item => `<li>${escapeHTML(item.title)}：${item.status === "done" ? "已完成" : "待完成"}</li>`).join("");
  $("#tab-report").innerHTML = `<div class="report-shell"><div class="report-toolbar"><div><h2>报告预览</h2><p class="muted">根据当前来源和发现实时生成</p></div><button class="secondary-button report-download">下载 Markdown</button></div><article class="report-sheet"><span class="report-badge">DeepResearch · Research Notes</span><h1>${escapeHTML(project.title)}</h1><p class="report-question"><strong>研究问题</strong><br>${escapeHTML(project.question)}</p><h2>研究目标</h2><p>${escapeHTML(project.objective || "尚未设置研究目标。")}</p><h2>研究发现</h2>${findings ? `<ul>${findings}</ul>` : "<p>暂无研究发现。</p>"}<h2>研究流程</h2><ul>${tasks}</ul><h2>参考来源</h2>${sources ? `<ol>${sources}</ol>` : "<p>暂无来源。</p>"}<div class="report-draft-note">状态：${statusLabel(project.status)}。这份报告由你手动收集的来源和发现整理而成；其中内容仍需核实，目前不代表已执行网络搜索或 AI 文献审查。</div></article></div>`;
  $(".report-download", $("#tab-report")).addEventListener("click", downloadReport);
}

function switchTab(tab) {
  state.tab = tab;
  $$(".section-tab").forEach(button => button.classList.toggle("active", button.dataset.tab === tab));
  $$("#project-view .tab-panel").forEach(panel => panel.hidden = panel.id !== `tab-${tab}`);
}

async function addSource(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const project = await api(`/api/projects/${state.current.id}/sources`, { method: "POST", body: JSON.stringify(Object.fromEntries(form.entries())) });
    await loadProjects(project.id); switchTab("sources"); showToast("来源已保存");
  } catch (error) { showToast(error.message); }
}

async function addFinding(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const project = await api(`/api/projects/${state.current.id}/findings`, { method: "POST", body: JSON.stringify(Object.fromEntries(form.entries())) });
    await loadProjects(project.id); switchTab("findings"); showToast("研究发现已保存");
  } catch (error) { showToast(error.message); }
}

function openModal() {
  $("#create-form").reset(); $("#form-error").textContent = ""; $("#modal-backdrop").hidden = false; setTimeout(() => $("#question-input").focus(), 30);
}
function closeModal() { $("#modal-backdrop").hidden = true; }

async function createProject(event) {
  event.preventDefault();
  const button = $("#create-form button[type=submit]");
  button.disabled = true;
  $("#form-error").textContent = "";
  try {
    const project = await api("/api/projects", { method: "POST", body: JSON.stringify({ question: $("#question-input").value, title: $("#title-input").value, objective: $("#objective-input").value }) });
    closeModal(); await loadProjects(project.id); showToast("研究项目已创建");
  } catch (error) { $("#form-error").textContent = error.message; }
  finally { button.disabled = false; }
}

function markdownReport(project) {
  const lines = [`# ${project.title}`, "", `**研究问题：** ${project.question}`, "", "## 研究目标", "", project.objective || "尚未设置研究目标。", "", "## 研究发现", "", ...(project.findings.length ? project.findings.map(item => `- ${item.text}`) : ["暂无研究发现。"]), "", "## 研究流程", "", ...project.tasks.map(item => `- [${item.status === "done" ? "x" : " "}] ${item.title}`), "", "## 参考来源", "", ...(project.sources.length ? project.sources.map((source, index) => `${index + 1}. [${source.title}](${source.url})${source.publisher ? ` — ${source.publisher}` : ""}${source.note ? `\n   - ${source.note}` : ""}`) : ["暂无来源。"]), "", `> 状态：${statusLabel(project.status)}。本报告由手动记录的来源与发现整理而成，尚未执行网络搜索或 AI 文献审查。`, ""];
  return lines.join("\n");
}

function downloadReport() {
  if (!state.current) return showToast("先打开一个研究项目");
  const blob = new Blob(["\ufeff", markdownReport(state.current)], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
  anchor.href = url; anchor.download = `${state.current.title.replace(/[\\/:*?"<>|]/g, "-").slice(0, 60) || "research-report"}.md`; anchor.click(); URL.revokeObjectURL(url); showToast("报告已下载");
}

function bindEvents() {
  $("#new-project").addEventListener("click", openModal);
  $("#empty-create").addEventListener("click", openModal);
  $("#close-modal").addEventListener("click", closeModal);
  $("#cancel-modal").addEventListener("click", closeModal);
  $("#modal-backdrop").addEventListener("click", event => { if (event.target === event.currentTarget) closeModal(); });
  document.addEventListener("keydown", event => { if (event.key === "Escape") closeModal(); });
  $("#create-form").addEventListener("submit", createProject);
  $("#export-report").addEventListener("click", downloadReport);
  $("#run-research").addEventListener("click", async () => {
    try { const project = await api(`/api/projects/${state.current.id}/run`, { method: "POST", body: "{}" }); await loadProjects(project.id); showToast("研究已开始，按流程记录证据与发现"); }
    catch (error) { showToast(error.message); }
  });
  $("#copy-question").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(state.current.question); showToast("研究问题已复制"); }
    catch { showToast("复制失败，请手动选择研究问题"); }
  });
  $("#reset-tasks").addEventListener("click", async () => {
    if (!state.current.tasks.some(task => task.status !== "todo")) return showToast("所有步骤已经是待完成状态");
    try {
      for (const task of state.current.tasks.filter(task => task.status !== "todo")) await api(`/api/projects/${state.current.id}/tasks/${task.id}`, { method: "PATCH", body: JSON.stringify({ status: "todo" }) });
      await loadProjects(state.current.id); showToast("研究流程已重置");
    } catch (error) { showToast(error.message); }
  });
  $("#edit-objective").addEventListener("click", async () => {
    const objective = window.prompt("研究目标", state.current.objective || "");
    if (objective === null) return;
    try { const project = await api(`/api/projects/${state.current.id}/objective`, { method: "PATCH", body: JSON.stringify({ objective }) }); await loadProjects(project.id); showToast("研究目标已更新"); }
    catch (error) { showToast(error.message); }
  });
  $$(".section-tab").forEach(button => button.addEventListener("click", () => switchTab(button.dataset.tab)));
  $$('[data-open-tab]').forEach(button => button.addEventListener("click", () => switchTab(button.dataset.openTab)));
}

bindEvents();
loadProjects().catch(error => { showToast(error.message); $("#empty-state").hidden = false; });
