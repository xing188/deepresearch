if (new URLSearchParams(location.search).get('registered') === '1') {
  document.querySelector('#login-success').hidden = false;
  document.querySelector('#login-name').focus();
  history.replaceState(null, '', '/login');
}

document.querySelector('#login-form').onsubmit = async event => {
  event.preventDefault();
  const button = document.querySelector('#login-submit');
  const error = document.querySelector('#login-error');
  button.disabled = true;
  error.hidden = true;
  try {
    const response = await fetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:document.querySelector('#login-name').value, password:document.querySelector('#login-password').value})});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '登录失败，请检查输入。');
    location.replace('/');
  } catch (e) { error.textContent = e.message; error.hidden = false; }
  finally { button.disabled = false; }
};
