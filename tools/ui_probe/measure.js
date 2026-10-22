// 单页量测：三段式外壳（侧边栏 / 顶栏 / 内容区）的真实几何、字号、间距。
// 占位符 __SIDEBAR__ 由 _ui_measure.ps1 替换为侧边栏选择器（当前为 [data-testid="stSidebar"]）。
(function () {
  const P = window.__kbProbe;
  const SB = '__SIDEBAR__';
  const q = function (s, root) { return (root || document).querySelector(s); };
  const qa = function (s, root) { return Array.prototype.slice.call((root || document).querySelectorAll(s)); };

  const sidebar = q(SB);
  const navRoot = q(SB + ' [class*="st-key-kb_nav"]');
  const navBtns = navRoot ? qa('button', navRoot) : [];

  // 每项导航：盒高、文字矩形、文字上下留白（判断是否「挤」）、项间距
  const navItems = navBtns.map(function (b, i) {
    const inner = b.firstElementChild || b;
    const t = P.text(inner);
    const bx = P.box(b);
    const prev = i > 0 ? navBtns[i - 1] : null;
    return {
      index: i,
      label: (b.innerText || '').trim(),
      kind: b.getAttribute('kind'),
      box: bx,
      cs: P.cs(b, ['fontSize', 'fontWeight', 'lineHeight', 'paddingTop', 'paddingBottom',
        'paddingLeft', 'paddingRight', 'textAlign', 'justifyContent', 'alignItems',
        'borderLeftWidth', 'borderRadius', 'backgroundColor', 'color', 'minHeight', 'height']),
      textBox: t,
      slackTop: t ? +(t.y - bx.y).toFixed(1) : null,
      slackBottom: t ? +((bx.y + bx.h) - (t.y + t.h)).toFixed(1) : null,
      gapFromPrev: prev ? P.vgap(prev, b) : null
    };
  });

  const brandEl = q(SB + ' .kb-brand');
  const brandT = q(SB + ' .kb-brand .t');
  const surveyCta = q(SB + ' [class*="st-key-kb_survey_cta"] button');
  const footer = q(SB + ' [class*="st-key-kb_sidebar_footer"]');
  const footerLogout = q('[class*="st-key-kb_sidebar_footer"] button');
  const footerAvatar = footer ? q('.kb-avatar', footer) : null;
  const llmExpander = P.byText(SB + ' [data-testid="stExpander"] summary, ' + SB + ' details summary', '⚙️ LLM');

  const header = q('[class*="st-key-kb_app_header"]');
  const headerTitle = header ? q('div[style*="font-size"]', header) : null;
  const searchInput = header ? q('input', header) : null;
  const bell = header ? P.btnByText('🔔', header) : null;
  const headerAvatar = header ? q('.kb-avatar', header) : null;
  const headerWho = header ? q('.kb-who', header) : null;

  const block = q('[data-testid="stMainBlockContainer"]');
  const main = q('[data-testid="stMain"]');

  const pageTitle = q('.kb-page-title');
  const pageSub = q('.kb-page-sub');
  const actBtn = qa('[class*="st-key-kb_act_"] button')[0] || null;
  // 页头所在列（判断按钮与标题基线是否齐）
  const actColWrap = actBtn ? actBtn.parentElement : null;

  const cards = qa('[class*="st-key-kb_card_"]');
  const metrics = qa('[data-testid="stMetric"]');
  const cardHds = qa('.kb-card-hd');

  const sidebarBox = P.box(sidebar);
  return {
    pageTitleText: pageTitle ? (pageTitle.innerText || '').trim() : null,
    viewport: { w: innerWidth, h: innerHeight },
    sidebar: {
      box: sidebarBox,
      cs: P.cs(sidebar, ['width', 'minWidth', 'maxWidth', 'backgroundColor', 'paddingLeft', 'paddingRight']),
      content: P.box(q(SB + ' [data-testid="stSidebarContent"]')),
      header: P.box(q(SB + ' [data-testid="stSidebarHeader"]')),
      userContent: P.box(q(SB + ' [data-testid="stSidebarUserContent"]'))
    },
    brand: {
      box: P.box(brandEl),
      cs: P.cs(brandEl, ['paddingTop', 'paddingBottom', 'paddingLeft', 'paddingRight', 'borderBottomWidth', 'marginBottom']),
      text: P.text(brandT),
      textCs: P.cs(brandT, ['fontSize', 'fontWeight', 'color', 'letterSpacing', 'lineHeight']),
      slackTop: brandT ? +(P.text(brandT).y - P.box(brandEl).y).toFixed(1) : null
    },
    nav: {
      root: P.box(navRoot),
      count: navBtns.length,
      items: navItems,
      brandToFirstNav: navItems.length ? P.vgap(brandEl, navBtns[0]) : null,
      firstNavTopMinusSidebarTop: navItems.length ? +(navItems[0].box.y - sidebarBox.y).toFixed(1) : null
    },
    navBottom: {
      lastNavToSurveyCta: navItems.length && surveyCta ? P.vgap(navBtns[navBtns.length - 1], surveyCta) : null,
      surveyCta: surveyCta ? { box: P.box(surveyCta), cs: P.cs(surveyCta, ['backgroundColor', 'color', 'fontSize', 'fontWeight', 'paddingTop', 'paddingBottom', 'paddingLeft', 'borderRadius', 'textAlign']) } : null,
      llmExpander: llmExpander ? P.box(llmExpander) : null,
      footer: footer ? { box: P.box(footer), cs: P.cs(footer, ['position', 'bottom', 'paddingTop', 'backgroundColor', 'borderTopWidth']) } : null,
      footerAvatar: footerAvatar ? { box: P.box(footerAvatar), cs: P.cs(footerAvatar, ['width', 'height', 'fontSize', 'borderRadius', 'backgroundColor']) } : null,
      logout: footerLogout ? { box: P.box(footerLogout), cs: P.cs(footerLogout, ['fontSize', 'color', 'backgroundColor', 'borderWidth', 'borderRadius', 'paddingTop', 'paddingBottom', 'textAlign']) } : null,
      footerBottomGap: footerLogout ? +(sidebarBox.y + sidebarBox.h - (P.box(footerLogout).y + P.box(footerLogout).h)).toFixed(1) : null
    },
    main: {
      box: P.box(main),
      cs: P.cs(main, ['paddingTop', 'paddingLeft', 'paddingRight', 'backgroundColor', 'overflowY'])
    },
    block: {
      box: P.box(block),
      cs: P.cs(block, ['paddingTop', 'paddingBottom', 'paddingLeft', 'paddingRight', 'maxWidth', 'width'])
    },
    header: {
      box: P.box(header),
      cs: P.cs(header, ['position', 'top', 'left', 'right', 'paddingTop', 'paddingBottom', 'paddingLeft',
        'paddingRight', 'backgroundColor', 'borderBottomWidth', 'borderBottomColor', 'boxShadow', 'zIndex']),
      title: headerTitle ? { box: P.box(headerTitle), text: P.text(headerTitle), cs: P.cs(headerTitle, ['fontSize', 'fontWeight', 'color', 'lineHeight']) } : null,
      search: searchInput ? { box: P.box(searchInput), cs: P.cs(searchInput, ['fontSize', 'height', 'paddingLeft', 'paddingRight', 'borderRadius', 'backgroundColor', 'borderWidth', 'borderColor', 'color']) } : null,
      bell: bell ? { box: P.box(bell), cs: P.cs(bell, ['fontSize', 'paddingTop', 'paddingBottom', 'paddingLeft', 'paddingRight', 'minHeight', 'height', 'borderRadius', 'backgroundColor', 'color']) } : null,
      avatar: headerAvatar ? { box: P.box(headerAvatar), cs: P.cs(headerAvatar, ['width', 'height', 'fontSize', 'borderRadius', 'backgroundColor']) } : null,
      who: headerWho ? { box: P.box(headerWho), cs: P.cs(headerWho, ['fontSize', 'color', 'lineHeight']) } : null,
      // 顶栏内部横向排布：标题→搜索 的间距、搜索→铃铛 的间距（判断是否有尴尬空档）
      titleToSearch: headerTitle && searchInput ? P.hgap(headerTitle, searchInput) : null,
      searchToBell: searchInput && bell ? P.hgap(searchInput, bell) : null,
      bellToWho: bell && headerWho ? P.hgap(bell, headerWho.closest('div[style*="display:flex"]') || headerWho) : null,
      // 顶栏内各件的垂直中心差（判断是否对齐）
      centers: (function () {
        const items = { title: headerTitle, search: searchInput, bell: bell, who: headerWho };
        const out = {};
        Object.keys(items).forEach(function (k) {
          const e = items[k];
          if (e) { const b = P.box(e); out[k] = +(b.y + b.h / 2).toFixed(1); }
        });
        return out;
      })(),
      // 顶栏左右内边界 vs 内容区左右边界（判断顶栏与内容是否对齐）
      leftEdge: header ? +P.box(header).x.toFixed(1) : null,
      rightEdge: header ? +P.box(header).right.toFixed(1) : null
    },
    pageHead: {
      title: pageTitle ? { box: P.box(pageTitle), text: P.text(pageTitle), cs: P.cs(pageTitle, ['fontSize', 'fontWeight', 'color', 'lineHeight', 'marginTop', 'marginBottom']) } : null,
      sub: pageSub ? { box: P.box(pageSub), text: P.text(pageSub), cs: P.cs(pageSub, ['fontSize', 'color', 'lineHeight', 'marginLeft']) } : null,
      action: actBtn ? { box: P.box(actBtn), cs: P.cs(actBtn, ['fontSize', 'height', 'minHeight', 'paddingTop', 'paddingBottom', 'borderRadius', 'backgroundColor', 'color', 'fontWeight']) } : null,
      // 主操作按钮中心 vs 标题文字中心（理想：相近 ⇒ 视觉齐平）
      actionCenterDelta: (actBtn && pageTitle) ? +(((P.box(actBtn).y + P.box(actBtn).h / 2) - (P.text(pageTitle).y + P.text(pageTitle).h / 2))).toFixed(1) : null,
      // 标题文字左边界 vs 内容区左内边界
      titleLeftVsBlock: (pageTitle && block) ? +(P.box(pageTitle).x - (P.box(block).x + parseFloat(getComputedStyle(block).paddingLeft))).toFixed(1) : null
    },
    gaps: {
      titleToFirstCard: (pageTitle && cards.length) ? P.vgap(pageTitle, cards[0]) : null,
      titleToFirstMetric: (pageTitle && metrics.length) ? P.vgap(pageTitle, metrics[0]) : null,
      betweenCards: cards.length > 1 ? P.vgap(cards[0], cards[1]) : null,
      betweenMetrics: metrics.length > 1 ? P.vgap(metrics[0], metrics[1]) : null,
      metricRowGap: metrics.length > 1 ? P.hgap(metrics[0], metrics[1]) : null
    },
    cards: cards.slice(0, 6).map(function (c) {
      return {
        box: P.box(c),
        cs: P.cs(c, ['backgroundColor', 'borderRadius', 'borderTopWidth', 'borderTopColor', 'borderStyle',
          'paddingTop', 'paddingBottom', 'paddingLeft', 'paddingRight', 'boxShadow', 'marginBottom']),
        inner: P.box(c.firstElementChild)
      };
    }),
    cardHds: cardHds.slice(0, 6).map(function (h) {
      const t = P.text(h);
      const b = P.box(h);
      return {
        text: (h.innerText || '').trim().slice(0, 36),
        box: b,
        cs: P.cs(h, ['fontSize', 'fontWeight', 'paddingTop', 'paddingBottom', 'paddingLeft',
          'marginTop', 'marginBottom', 'borderBottomWidth', 'lineHeight', 'color']),
        textBox: t,
        slackTop: +(t.y - b.y).toFixed(1)
      };
    }),
    metrics: metrics.slice(0, 6).map(function (m) {
      const lbl = q('label', m);
      const val = q('[data-testid="stMetricValue"]', m);
      return {
        box: P.box(m),
        cs: P.cs(m, ['backgroundColor', 'borderRadius', 'borderTopWidth', 'paddingTop', 'paddingBottom', 'paddingLeft', 'paddingRight', 'boxShadow']),
        label: lbl ? { text: (lbl.innerText || '').trim(), box: P.box(lbl), cs: P.cs(lbl, ['fontSize', 'color', 'fontWeight', 'lineHeight']) } : null,
        value: val ? { text: (val.innerText || '').trim(), box: P.box(val), cs: P.cs(val, ['fontSize', 'fontWeight', 'color', 'lineHeight', 'letterSpacing']) } : null,
        slackTop: lbl ? +(P.box(lbl).y - P.box(m).y).toFixed(1) : null
      };
    })
  };
})()
