// 点击侧边栏导航项。
// 占位符：__LABEL__ = 导航项中文名（JSON 字符串）、__SIDEBAR__ = 侧边栏选择器。
(function () {
  const SB = '__SIDEBAR__';
  const root = document.querySelector(SB + ' [class*="st-key-kb_nav"]');
  if (!root) return { ok: false, reason: 'nav container not found' };
  const want = __LABEL__;
  const btn = Array.prototype.slice.call(root.querySelectorAll('button'))
    .find(function (b) { return (b.innerText || '').trim().indexOf(want) === 0; });
  if (!btn) {
    return {
      ok: false, reason: 'nav item not found', want: want,
      available: Array.prototype.slice.call(root.querySelectorAll('button'))
        .map(function (b) { return (b.innerText || '').trim(); })
    };
  }
  const already = btn.getAttribute('kind') === 'primary';
  btn.click();
  return { ok: true, clicked: want, alreadyActive: already };
})()
