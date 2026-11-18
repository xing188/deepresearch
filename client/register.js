document.querySelector('#register-form').onsubmit = async event => {
  event.preventDefault();
  const button = document.querySelector('#login-submit');
  const error = document.querySelector('#login-error');
  button.disabled = true; error.hidden = true;
  try {
    const password = document.querySelector('#register-password').value;
    if (password !== document.querySelector('#register-repeat').value) throw new Error('两次输入的密码不一致。');
    const response = await fetch('/api/auth/register', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:document.querySelector('#register-name').value,password})});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '注册失败，请检查输入。');
    location.replace('/login?registered=1');
  } catch(e) { error.textContent=e.message; error.hidden=false; }
  finally { button.disabled=false; }
};
