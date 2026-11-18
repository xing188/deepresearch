let signedInAccount;
async function loadAccount() {
  signedInAccount = await api('/api/auth/me');
  const u = signedInAccount;
  $('#account-name').textContent = u.username;
  $('#account-role').textContent = u.role === 'admin' ? '管理员' : '成员';
  $('#accounts-open').hidden = u.role !== 'admin';
  $('#account-period').textContent = `${u.month} · 每月额度（UTC）`;
  $('#account-usage').innerHTML = [['研究',u.usage.runs,u.max_runs],['模型调用',u.usage.calls,u.max_calls],['tokens',u.usage.tokens,u.max_tokens]].map(([name,used,max]) => `<div><span>${name}</span><strong>${used.toLocaleString()} <small>/ ${max.toLocaleString()}</small></strong><progress value="${Math.min(used,max)}" max="${max || 1}" aria-label="${name}月用量"></progress></div>`).join('');
}
$('#logout').onclick = async () => { try { if (typeof flushVisibleDrafts === 'function') await flushVisibleDrafts(); await api('/api/auth/logout',{method:'POST',body:'{}'}); location.replace('/login'); } catch(e){toast(e.message);} };
$('#password-open').onclick = () => { $('#password-form').reset(); $('#password-error').hidden=true; $('#password-dialog').showModal(); };
$('#password-form').onsubmit = async event => {
  event.preventDefault();
  $('#password-submit').disabled = true;
  $('#password-error').hidden = true;
  try {
    if ($('#new-password').value !== $('#repeat-password').value) throw new Error('两次新密码不一致。');
    await api('/api/auth/password', {method:'POST',body:JSON.stringify({old_password:$('#old-password').value,new_password:$('#new-password').value})});
    $('#password-form').reset(); $('#password-dialog').close(); toast('密码已修改，其他设备的登录已失效');
  } catch(e) {$('#password-error').textContent=e.message;$('#password-error').hidden=false;}
  finally {$('#password-submit').disabled=false;}
};
function limitFields(u, prefix) {
  return `<div class="budget-fields"><label>每月研究次数<input id="${prefix}-runs" type="number" min="0" max="10000" value="${u.max_runs}" required></label><label>每月模型调用<input id="${prefix}-calls" type="number" min="0" max="100000" value="${u.max_calls}" required></label><label>每月 tokens<input id="${prefix}-tokens" type="number" min="0" max="1000000000" value="${u.max_tokens}" required></label></div>`;
}
async function loadAccounts() {
  const users = await api('/api/admin/users');
  $('#accounts-list').innerHTML = users.map((u,i) => `<form class="member-card" data-account="${escape(u.id)}"><div class="member-heading"><strong>${escape(u.username)} <small>${u.role==='admin'?'管理员':'成员'}</small></strong><label><input type="checkbox" name="enabled" ${u.enabled?'checked':''} ${u.id===signedInAccount.id?'disabled':''}>启用账号</label></div><p class="muted">本月已用：${u.usage.runs} 次研究 · ${u.usage.calls} 次调用 · ${u.usage.tokens.toLocaleString()} tokens</p>${limitFields(u,'member-'+i)}<button class="secondary" type="submit">保存额度与状态</button></form>`).join('');
  $$('.member-card').forEach((form,i) => form.onsubmit = async event => {
    event.preventDefault(); const button=form.querySelector('button'); button.disabled=true;
    try { await api('/api/admin/users/'+form.dataset.account,{method:'PATCH',body:JSON.stringify({...readLimits('member-'+i),enabled:form.elements.enabled.checked})}); await loadAccounts(); await loadAccount(); toast('账号已更新'); }
    catch(e){toast(e.message);} finally{button.disabled=false;}
  });
}
function readLimits(prefix) { return {max_runs:Number($(`#${prefix}-runs`).value),max_calls:Number($(`#${prefix}-calls`).value),max_tokens:Number($(`#${prefix}-tokens`).value)}; }
$('#accounts-open').onclick = async () => {try{await loadAccounts();$('#accounts-dialog').showModal();}catch(e){toast(e.message);}};
$('#create-account').onsubmit = async event => {
  event.preventDefault(); $('#create-account-submit').disabled=true; $('#account-create-error').hidden=true;
  try {
    await api('/api/admin/users',{method:'POST',body:JSON.stringify({username:$('#member-name').value,password:$('#member-password').value,...readLimits('new-member')})});
    $('#member-name').value=''; $('#member-password').value=''; await loadAccounts(); toast('账号已创建，请将账号与初始密码交给对应使用者');
  } catch(e){$('#account-create-error').textContent=e.message;$('#account-create-error').hidden=false;}
  finally{$('#create-account-submit').disabled=false;}
};
document.addEventListener('DOMContentLoaded',()=>loadAccount().catch(e=>toast(e.message)));
setInterval(()=>{if(!document.hidden)loadAccount().catch(()=>{});},15000);
