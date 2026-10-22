/* ============================================================================
   需求看板 · 界面改版量测片段（一次复制，粘贴到浏览器 Console 回车）
   ----------------------------------------------------------------------------
   为什么用它：本机 Edge 的渲染进程在当前沙箱里起不来，无法自动截图量测；
   而「卡片间距 / 导航项上下留白 / 标题内边距」这类细节靠肉眼看截图会在
   分辨率边界上失真。此片段直接读真实 DOM 的 getBoundingClientRect 与
   getComputedStyle，把客观数值打出来。

   只读：不点击、不提交、不改任何数据。
   用法：F12 → Console → 粘贴整段 → 回车 → 把输出（含 console.table）整段复制回来。
   ============================================================================ */
(() => {
  const S = '[data-testid="stSidebar"]';
  const H = '[class*="st-key-kb_app_header"]';
  const q = (s) => document.querySelector(s);
  const r = (e) => { if (!e) return null; const b = e.getBoundingClientRect();
    return { x: +b.x.toFixed(1), y: +b.y.toFixed(1), w: +b.width.toFixed(1), h: +b.height.toFixed(1), bottom: +b.bottom.toFixed(1), right: +b.right.toFixed(1) }; };
  const px = (e, p) => e ? getComputedStyle(e)[p] : null;

  console.log('%c=== 需求看板 界面量测 ===', 'font-weight:bold;font-size:14px');
  console.log('视口', window.innerWidth + '×' + window.innerHeight, '| DPR', window.devicePixelRatio);

  /* ---------- 1. 侧边栏 ---------- */
  const sb = q(S);
  console.log('\n--- 1) 侧边栏 ---');
  console.log('宽度', sb ? r(sb).w : null, '（目标 224）');
  console.log('内容区 padding-left/right',
    px(q(S + ' [data-testid="stSidebarContent"]'), 'paddingLeft'),
    px(q(S + ' [data-testid="stSidebarContent"]'), 'paddingRight'),
    '| scrollbar-gutter', px(q(S + ' [data-testid="stSidebarContent"]'), 'scrollbarGutter'));
  const brand = q(S + ' .kb-brand');
  console.log('品牌区', r(brand), '| 下边框', px(brand, 'borderBottomWidth'), px(brand, 'borderBottomColor'));

  /* ---------- 2. 导航项（重点：上下留白是否均匀 / 项间距） ---------- */
  const navRoot = q(S + ' [class*="st-key-kb_nav"]');
  const btns = navRoot ? [...navRoot.querySelectorAll('button')] : [];
  console.log('\n--- 2) 导航项 --- 共', btns.length, '项');
  const navRows = btns.map((b, i) => {
    const bb = r(b);
    const inner = b.firstElementChild || b;
    // 用 Range 精确取文字矩形，判断文字在按钮里是否垂直居中
    const rg = document.createRange(); rg.selectNodeContents(inner);
    const tb = rg.getBoundingClientRect();
    const cs = getComputedStyle(b);
    const prev = i > 0 ? btns[i - 1].getBoundingClientRect() : null;
    return {
      序号: i + 1,
      文案: (b.innerText || '').trim().slice(0, 12),
      态: b.getAttribute('kind'),
      盒高: bb.h,
      文字高: +tb.height.toFixed(1),
      上留白: +(tb.top - bb.y).toFixed(1),
      下留白: +(bb.bottom - tb.bottom).toFixed(1),
      与上一项间距: prev ? +(bb.y - prev.bottom).toFixed(1) : null,
      字号: cs.fontSize,
      行高: cs.lineHeight,
      内边距: cs.paddingTop + ' / ' + cs.paddingLeft,
      圆角: cs.borderRadius,
      文字左起: +(tb.x - bb.x).toFixed(1),
      文字对齐: cs.textAlign,
    };
  });
  if (console.table) console.table(navRows); else console.log(navRows);
  if (btns.length) {
    const first = btns[0].getBoundingClientRect(), last = btns[btns.length - 1].getBoundingClientRect();
    console.log('导航区总高', +(last.bottom - first.top).toFixed(1), '| 品牌区底 → 首项顶', +(first.top - (brand ? brand.getBoundingClientRect().bottom : first.top)).toFixed(1));
  }

  /* ---------- 3. 侧边栏底部（问卷 CTA / LLM / 用户区） ---------- */
  console.log('\n--- 3) 侧边栏底部 ---');
  const cta = q(S + ' [class*="st-key-kb_survey_cta"] button');
  console.log('问卷CTA', r(cta), '| 字号', px(cta, 'fontSize'), '| 底色', px(cta, 'backgroundColor'), '| 圆角', px(cta, 'borderRadius'));
  const foot = q('[class*="st-key-kb_sidebar_footer"]');
  console.log('底部用户区', r(foot), '| position', px(foot, 'position'), '| 贴底距离',
    foot ? +(r(sb).bottom - r(foot).bottom).toFixed(1) : null);

  /* ---------- 4. 顶栏（重点：各件垂直中心是否一致 / 左右边界是否等于内容区） ---------- */
  console.log('\n--- 4) 顶栏 ---');
  const hd = q(H);
  const block = q('[data-testid="stMainBlockContainer"]');
  console.log('顶栏', r(hd), '| padding', px(hd, 'paddingTop') + ' ' + px(hd, 'paddingBottom') + ' ' + px(hd, 'paddingLeft') + ' ' + px(hd, 'paddingRight'),
    '| 底色', px(hd, 'backgroundColor'), '| 下边框', px(hd, 'borderBottomWidth'), px(hd, 'borderBottomColor'), '| position', px(hd, 'position'));
  if (hd && block) {
    const hb = r(hd), bb = r(block);
    const bpad = parseFloat(px(block, 'paddingLeft'));
    console.log('顶栏左内边界', +(hb.x + parseFloat(px(hd, 'paddingLeft'))).toFixed(1),
      '| 内容区左内边界', +(bb.x + bpad).toFixed(1), '  ← 两值应相等');
    console.log('顶栏右内边界', +(hb.right - parseFloat(px(hd, 'paddingRight'))).toFixed(1),
      '| 内容区右内边界', +(bb.right - parseFloat(px(block, 'paddingRight'))).toFixed(1), '  ← 两值应相等');
  }
  console.log('主区顶部内边距 stMainBlockContainer.paddingTop', px(block, 'paddingTop'), '（目标 0px）');
  const hTitle = hd ? hd.querySelector('div[style*="font-size"]') : null;
  const hSearch = hd ? hd.querySelector('input') : null;
  const hBell = hd ? [...hd.querySelectorAll('button')].find(b => (b.innerText || '').includes('🔔')) : null;
  const hWho = hd ? hd.querySelector('.kb-who') : null;
  const centers = {};
  [['标题', hTitle], ['搜索', hSearch], ['铃铛', hBell], ['用户', hWho]].forEach(([k, e]) => {
    if (e) { const b = e.getBoundingClientRect(); centers[k] = +(b.top + b.height / 2).toFixed(1); }
  });
  console.log('各件垂直中心 y', centers, ' ← 数值接近即视为对齐');
  console.log('搜索框 高', hSearch ? r(hSearch).h : null, '| 字号', px(hSearch, 'fontSize'));
  console.log('用户胶囊 .kb-who 左边框', px(hWho, 'borderLeftWidth'), '| 左内边距', px(hWho, 'paddingLeft'));
  // 顶栏与下方第一张卡片之间
  const firstCard = q('[class*="st-key-kb_card_"]');
  const firstMetric = q('[data-testid="stMetric"]');
  const nextEl = firstCard || firstMetric;
  console.log('顶栏底 → 下方首块顶', hd && nextEl ? +(r(nextEl).y - r(hd).bottom).toFixed(1) : null);

  /* ---------- 5. 页头（标题 / 副标题 / 主操作按钮） ---------- */
  console.log('\n--- 5) 页头 ---');
  const pt = q('.kb-page-title'), ps = q('.kb-page-sub');
  const actBtn = q('[class*="st-key-kb_act_"] button');
  const tb = (e) => { if (!e) return null; const rg = document.createRange(); rg.selectNodeContents(e); const b = rg.getBoundingClientRect(); return { y: +b.y.toFixed(1), h: +b.height.toFixed(1), center: +(b.y + b.height / 2).toFixed(1) }; };
  console.log('标题', r(pt), '| 字号', px(pt, 'fontSize'), '| 文字', tb(pt));
  console.log('副标题', r(ps), '| 字号', px(ps, 'fontSize'), '| 文字', tb(ps));
  console.log('主操作按钮', r(actBtn), '| 字号', px(actBtn, 'fontSize'), '| 底色', px(actBtn, 'backgroundColor'));
  if (actBtn && pt) {
    const ab = r(actBtn);
    console.log('按钮中心 y', +(ab.y + ab.h / 2).toFixed(1), '| 标题文字中心 y', tb(pt).center, ' ← 差 ≤4px 即视觉齐平');
  }
  if (pt && block) console.log('标题左起', r(pt).x, '| 内容区左内边界', +(r(block).x + parseFloat(px(block, 'paddingLeft'))).toFixed(1), ' ← 两值应相等');
  if (pt && nextEl) console.log('标题底 → 首块顶', +(r(nextEl).y - (r(pt).y + r(pt).h)).toFixed(1));

  /* ---------- 6. KPI 卡片行（重点：卡片间距 / 内边距 / 数值字号） ---------- */
  console.log('\n--- 6) KPI 卡片行 ---');
  const metrics = [...document.querySelectorAll('[data-testid="stMetric"]')];
  console.log('metric 数量', metrics.length);
  if (metrics.length > 1) {
    const gaps = [];
    for (let i = 1; i < metrics.length; i++) gaps.push(+(r(metrics[i]).x - r(metrics[i - 1]).right).toFixed(1));
    console.log('相邻卡片水平间距', gaps, ' ← 目标约 12-16px；出现 0 或负数表示卡片贴在一起');
    console.log('卡片宽', r(metrics[0]).w, '| 行总宽', +(r(metrics[metrics.length - 1]).right - r(metrics[0]).x).toFixed(1));
  }
  if (metrics[0]) {
    const m = metrics[0];
    const lbl = m.querySelector('[data-testid="stMetricLabel"]');
    const val = m.querySelector('[data-testid="stMetricValue"]');
    console.log('首卡', r(m), '| padding', px(m, 'paddingTop') + ' ' + px(m, 'paddingLeft'), '| 底色', px(m, 'backgroundColor'), '| 圆角', px(m, 'borderRadius'), '| 边框', px(m, 'borderTopWidth'));
    console.log('指标名 字号', px(lbl, 'fontSize'), '| 下外边距', px(lbl, 'marginBottom'), '| 颜色', px(lbl, 'color'));
    console.log('指标值 字号', px(val, 'fontSize'), '| 字重', px(val, 'fontWeight'), '| 字距', px(val, 'letterSpacing'));
    if (lbl) console.log('卡片顶 → 指标名顶', +(r(lbl).y - r(m).y).toFixed(1));
  }

  /* ---------- 7. 卡片与卡片标题条 ---------- */
  console.log('\n--- 7) 卡片 / 标题条 ---');
  const cards = [...document.querySelectorAll('[class*="st-key-kb_card_"]')];
  console.log('卡片数', cards.length);
  if (cards.length > 1) console.log('卡片纵向间距', +(r(cards[1]).y - r(cards[0]).bottom).toFixed(1));
  if (cards[0]) {
    const c = cards[0];
    console.log('首卡', r(c), '| 内边距', px(c, 'paddingTop') + ' ' + px(c, 'paddingRight') + ' ' + px(c, 'paddingBottom') + ' ' + px(c, 'paddingLeft'),
      '| 圆角', px(c, 'borderRadius'), '| 底色', px(c, 'backgroundColor'), '| 阴影', px(c, 'boxShadow'));
  }
  const hds = [...document.querySelectorAll('.kb-card-hd')];
  if (hds.length && cards.length) {
    const h0 = hds[0], c0 = cards[0];
    console.log('标题条', r(h0), '| 字号', px(h0, 'fontSize'), '| 下内边距', px(h0, 'paddingBottom'), '| 负外边距', px(h0, 'marginLeft'), '| 分隔线', px(h0, 'borderBottomWidth'), px(h0, 'borderBottomColor'));
    console.log('标题条左起', r(h0).x, '| 卡片左边界', r(c0).x, ' ← 应相等（整宽分隔线）');
    console.log('标题条右边界', r(h0).right, '| 卡片右边界', r(c0).right, ' ← 应相等');
    if (cards[1]) {
      const c1 = cards[1];
      console.log('标题条底 → 卡内首个子块顶', +(r(c1.querySelector('*')).y - (r(h0).y + r(h0).h)).toFixed(1));
    }
  }
  const inCardMetric = cards[0] ? cards[0].querySelector('[data-testid="stMetric"]') : null;
  if (inCardMetric) console.log('卡内 metric 内边距', px(inCardMetric, 'paddingTop'), '| 底色', px(inCardMetric, 'backgroundColor'), ' ← 应为 0px / 透明（避免双边框）');

  console.log('\n%c=== 量测结束，请把以上输出整段复制回来 ===', 'font-weight:bold');
  return '量测完成';
})()
