/* ============================================================================
   侧边栏底部分区位置溯源（一次复制，粘贴到浏览器 Console 回车）
   ----------------------------------------------------------------------------
   目的：用户要求把底部用户区「移到最下端」，但当前它上方仍留有空隙。
   已用 margin-top:auto 推底，怀疑是侧边栏内容溢出（内容高于视口）导致没有余量、
   或中途某一层没有真正撑满高度。

   本片段量出整条链每一层的：盒子高度 / 内容高度 / 是否溢出 / flex 相关属性
   + 底部用户区距离侧边栏底边的实际距离。据此可直接定位是哪一层断了。

   只读：不点击、不提交、不改数据。
   ============================================================================ */
(() => {
  const S = '[data-testid="stSidebar"]';
  const g = (e, p) => (e ? getComputedStyle(e)[p] : null);
  const box = (e) => {
    if (!e) return null;
    const b = e.getBoundingClientRect();
    return { y: +b.y.toFixed(1), h: +b.height.toFixed(1), bottom: +b.bottom.toFixed(1) };
  };

  console.log('%c=== 侧边栏底部溯源 ===', 'font-weight:bold;font-size:13px');
  console.log('视口高 innerHeight =', window.innerHeight);

  const chain = [
    ['section#stSidebar', document.querySelector(S)],
    ['  stSidebarContent', document.querySelector(S + ' [data-testid="stSidebarContent"]')],
    ['    stSidebarHeader', document.querySelector(S + ' [data-testid="stSidebarHeader"]')],
    ['    stSidebarUserContent', document.querySelector(S + ' [data-testid="stSidebarUserContent"]')],
    ['      > stVerticalBlock', document.querySelector(S + ' [data-testid="stSidebarUserContent"] > [data-testid="stVerticalBlock"]')],
    ['      > 任意子 div(纵向块)', document.querySelector(S + ' [data-testid="stSidebarUserContent"] > div')],
    ['        footer 用户区', document.querySelector('[class*="st-key-kb_sidebar_footer"]')],
    ['        footer 的父层', (document.querySelector('[class*="st-key-kb_sidebar_footer"]') || {}).parentElement || null],
  ];

  const rows = [];
  for (const [name, el] of chain) {
    if (!el) { rows.push({ 层: name, 存在: '✗' }); continue; }
    const cs = getComputedStyle(el);
    rows.push({
      层: name,
      存在: '✓',
      盒高: box(el).h,
      盒顶: box(el).y,
      盒底: box(el).bottom,
      内容高: el.scrollHeight,
      溢出: el.scrollHeight > el.clientHeight + 2 ? '是' : '',
      可用高clientH: el.clientHeight,
      display: cs.display,
      flexDir: cs.flexDirection,
      flexGrow: cs.flexGrow,
      flexBasis: cs.flexBasis,
      marginTop: cs.marginTop,
      paddingBottom: cs.paddingBottom,
      justifyContent: cs.justifyContent,
    });
  }
  if (console.table) console.table(rows); else console.log(rows);

  const sb = document.querySelector(S);
  const foot = document.querySelector('[class*="st-key-kb_sidebar_footer"]');
  console.log('\n--- 关键判据 ---');
  if (sb && foot) {
    const s = sb.getBoundingClientRect(), f = foot.getBoundingClientRect();
    console.log('侧边栏底边 y =', +s.bottom.toFixed(1));
    console.log('用户区底边 y =', +f.bottom.toFixed(1));
    console.log('用户区底边距侧边栏底边 =', +(s.bottom - f.bottom).toFixed(1), 'px  ← 目标 0~10px（含 padding）');
  }
  const content = document.querySelector(S + ' [data-testid="stSidebarContent"]');
  if (content) {
    console.log('侧边栏内容是否溢出 =',
      content.scrollHeight > content.clientHeight + 2
        ? `是（内容 ${content.scrollHeight} > 可视 ${content.clientHeight}）→ margin-top:auto 无余量可用`
        : `否（内容 ${content.scrollHeight} ≤ 可视 ${content.clientHeight}）→ 应能推到底，问题在 flex 链断点`);
  }
  console.log('\n%c=== 结束，请整段复制回来 ===', 'font-weight:bold');
  return '溯源完成';
})()
