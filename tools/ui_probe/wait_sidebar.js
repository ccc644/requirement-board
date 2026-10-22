// 等待侧边栏出现（登录完成）
(async function () {
  for (let i = 0; i < 80; i++) {
    if (document.querySelector('[data-testid="stSidebar"]')) return true;
    await new Promise(function (r) { setTimeout(r, 250); });
  }
  return false;
})()
