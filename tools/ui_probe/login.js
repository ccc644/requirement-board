// 登录（占位符 __USER__ / __PASS__ 由 _ui_measure.ps1 替换为 JSON 字符串）
(function () {
  const inputs = Array.prototype.slice.call(document.querySelectorAll('input'));
  const nameEl = inputs.find(function (i) { return i.placeholder && i.placeholder.indexOf('姓名') >= 0; });
  const passEl = inputs.find(function (i) { return i.type === 'password'; });
  if (!nameEl || !passEl) {
    return { ok: false, reason: 'login form not found', inputCount: inputs.length };
  }
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(nameEl, __USER__);
  nameEl.dispatchEvent(new Event('input', { bubbles: true }));
  setter.call(passEl, __PASS__);
  passEl.dispatchEvent(new Event('input', { bubbles: true }));

  const byTestId = document.querySelector('button[data-testid="stBaseButton-primaryFormSubmit"]');
  const byText = Array.prototype.slice.call(document.querySelectorAll('button'))
    .find(function (b) { return (b.innerText || '').trim() === '登录'; });
  const btn = byTestId || byText;
  if (!btn) {
    return {
      ok: false, reason: 'login button not found',
      candidateTestIds: Array.prototype.slice.call(document.querySelectorAll('button[data-testid]'))
        .map(function (b) { return b.getAttribute('data-testid'); })
    };
  }
  const info = { ok: true, via: byTestId ? 'testid' : 'text', testid: btn.getAttribute('data-testid') };
  btn.click();
  return info;
})()
