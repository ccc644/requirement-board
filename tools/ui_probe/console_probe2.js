/* ============================================================================
   顶栏横向缩进溯源（一次复制，粘贴到浏览器 Console 回车）
   ----------------------------------------------------------------------------
   背景：实测顶栏「盒」x=16 w=622.4，而卡片/页头内容左边界是 16。
   顶栏自带 16px 内边距，于是顶栏文字落在 32，比页头标题多缩进 16px——顶栏内容
   没有和页面内容左对齐。需要先查明是谁在给顶栏这层缩进，再决定怎么抵消。

   做法：从顶栏容器往上走 8 层祖先，打出每层的盒子与内外边距、
   padding、max-width、flex 相关属性，找出真正约束宽度的那一层。

   只读：不点击、不提交、不改数据。输出很短，整段复制回来即可。
   ============================================================================ */
(() => {
  const start = document.querySelector('[class*="st-key-kb_app_header"]')
             || document.querySelector('[class*="st-key-kb_app_head"]');
  if (!start) { console.log('❌ 没找到顶栏容器 [class*="st-key-kb_app_header"]'); return 'not found'; }

  const r = (e) => { const b = e.getBoundingClientRect();
    return { x: +b.x.toFixed(1), w: +b.width.toFixed(1), right: +b.right.toFixed(1) }; };
  const g = (e, p) => getComputedStyle(e)[p];

  console.log('%c=== 顶栏横向缩进溯源 ===', 'font-weight:bold;font-size:13px');
  const block = document.querySelector('[data-testid="stMainBlockContainer"]');
  console.log('基准 · stMainBlockContainer', r(block),
    '| padding L/R', g(block, 'paddingLeft') + '/' + g(block, 'paddingRight'),
    '| max-width', g(block, 'maxWidth'),
    '| align-self', g(block, 'alignSelf'),
    '| 父', block.parentElement && block.parentElement.tagName + '.' + (block.parentElement.getAttribute('data-testid') || ''));
  const main = document.querySelector('[data-testid="stMain"]');
  if (main) console.log('基准 · stMain', r(main), '| display', g(main, 'display'),
    '| flex-direction', g(main, 'flexDirection'), '| align-items', g(main, 'alignItems'),
    '| padding L/R', g(main, 'paddingLeft') + '/' + g(main, 'paddingRight'));

  console.log('\n--- 从顶栏往上 8 层 ---');
  let el = start, i = 0;
  const chain = [];
  while (el && i < 9) {
    chain.push({
      层: i,
      节点: el.tagName.toLowerCase()
        + (el.getAttribute('data-testid') ? '[' + el.getAttribute('data-testid') + ']' : '')
        + ([...el.classList].filter(c => c.indexOf('st-key-') === 0 || c === 'stMainBlockContainer' || c === 'stVerticalBlock').map(c => '.' + c).join('')),
      类名: (el.className || '').toString().slice(0, 60),
      x: r(el).x,
      宽: r(el).w,
      right: r(el).right,
      padL: g(el, 'paddingLeft'),
      padR: g(el, 'paddingRight'),
      marL: g(el, 'marginLeft'),
      marR: g(el, 'marginRight'),
      maxW: g(el, 'maxWidth'),
      display: g(el, 'display'),
      flex: g(el, 'flex'),
      alignSelf: g(el, 'alignSelf'),
      width: g(el, 'width'),
    });
    i += 1;
    el = el.parentElement;
  }
  if (console.table) console.table(chain); else console.log(chain);

  // 顶栏文字实际左起点
  const t = start.querySelector('div[style*="font-size"]');
  if (t) {
    const rg = document.createRange(); rg.selectNodeContents(t);
    console.log('顶栏标题文字左起 x =', +rg.getBoundingClientRect().x.toFixed(1),
      '| 页头标题文字左起 x =', (() => {
        const p = document.querySelector('.kb-page-title'); return p ? +p.getBoundingClientRect().x.toFixed(1) : null;
      })(), ' ← 两者应相等');
  }
  console.log('\n%c=== 结束，请整段复制回来 ===', 'font-weight:bold');
  return '溯源完成';
})()
