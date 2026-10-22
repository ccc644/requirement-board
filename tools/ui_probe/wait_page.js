// 等待页面标题切换完成。
// 占位符 __LABEL__ = 导航项中文名（JSON 字符串，形如 "📋 我的看板"）。
// 判据：去掉 emoji 前缀后的中文名出现在 .kb-page-title 文本里
// （「我的看板」页标题带用户名后缀，故用「包含」而非「相等」）。
(async function () {
  const label = __LABEL__;
  const want = label.replace(/^\S+\s*/, '');
  for (let i = 0; i < 80; i++) {
    const t = document.querySelector('.kb-page-title');
    if (t && (t.innerText || '').indexOf(want) >= 0) {
      return { ok: true, title: (t.innerText || '').trim(), want: want };
    }
    await new Promise(function (r) { setTimeout(r, 250); });
  }
  const cur = document.querySelector('.kb-page-title');
  return { ok: false, current: cur ? (cur.innerText || '').trim() : null, want: want };
})()
