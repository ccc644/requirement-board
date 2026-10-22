/* ============================================================================
   滚动容器溯源（一次复制，粘贴到浏览器 Console 回车）
   ----------------------------------------------------------------------------
   目的：用户要求「顶部导航条滚动时钉住」。上一版用 position:fixed 做到了，但那是靠
   硬编码 left:240px + 手工补占位，导致顶栏内容与页面内容左对齐差 16px。
   改回常规文档流后对齐修好了、冻结没了。

   现在要判断能不能改用 position:sticky（既能钉住、又不需要任何硬编码偏移）。
   sticky 生效的硬条件：① 存在可滚动的祖先；② 该祖先到本元素之间**没有**
   overflow: hidden / clip / auto 的中间层（否则会另起滚动上下文而失效）。

   本片段把顶栏到 body 之间每一层的 overflow / height / display / align-items
   全部打出来，并直接判定 sticky 是否可行 + 真正的滚动容器是谁。

   只读：不点击、不提交、不改数据。
   ============================================================================ */
(() => {
  const start = document.querySelector('[class*="st-key-kb_app_header"]');
  if (!start) { console.log('❌ 没找到顶栏容器'); return 'not found'; }
  const g = (e, p) => getComputedStyle(e)[p];
  const r = (e) => { const b = e.getBoundingClientRect();
    return { x: +b.x.toFixed(1), y: +b.y.toFixed(1), w: +b.width.toFixed(1), h: +b.height.toFixed(1) }; };

  console.log('%c=== 滚动容器溯源 ===', 'font-weight:bold;font-size:13px');

  const rows = [];
  let el = start, i = 0;
  while (el && i < 12) {
    const cs = getComputedStyle(el);
    const scrollable = el.scrollHeight > el.clientHeight + 2;
    rows.push({
      层: i,
      节点: el.tagName.toLowerCase()
        + (el.getAttribute('data-testid') ? '[' + el.getAttribute('data-testid') + ']' : ''),
      overflow: cs.overflow,
      overflowY: cs.overflowY,
      height: cs.height,
      可滚动: scrollable ? '是' : '',
      display: cs.display,
      alignItems: cs.alignItems,
      盒子: JSON.stringify(r(el)),
      内容高: el.scrollHeight,
    });
    i += 1;
    el = el.parentElement;
  }
  if (console.table) console.table(rows); else console.log(rows);

  /* ---- 判定 1：谁是真正的滚动容器 ---- */
  let p = start.parentElement, scroller = null, blocker = null;
  while (p) {
    const cs = getComputedStyle(p);
    const scrollable = p.scrollHeight > p.clientHeight + 2;
    if (scrollable && /auto|scroll/.test(cs.overflowY)) { scroller = p; break; }
    if (blocker === null && /hidden|clip/.test(cs.overflowY) && cs.overflowY !== 'visible') blocker = p;
    p = p.parentElement;
  }
  console.log('\n--- 判定 ---');
  console.log('可滚动祖先 =',
    scroller ? scroller.tagName.toLowerCase() + '[' + (scroller.getAttribute('data-testid') || '') + ']'
             + ' (overflow-y=' + g(scroller, 'overflowY') + ')' : '❌ 未找到');
  console.log('中间 overflow 阻断层 =',
    blocker ? blocker.tagName.toLowerCase() + '[' + (blocker.getAttribute('data-testid') || '')
              + '] overflow-y=' + g(blocker, 'overflowY') : '无');
  console.log('顶栏当前 position =', g(start, 'position'), '| top =', g(start, 'top'), '| z-index =', g(start, 'zIndex'));

  /* ---- 判定 2：stMain 的横向行为（决定 sticky 会不会变成「内容宽度」而不满宽） ---- */
  const main = document.querySelector('[data-testid="stMain"]');
  const block = document.querySelector('[data-testid="stMainBlockContainer"]');
  if (main) console.log('\nstMain  display=' + g(main, 'display') + ' align-items=' + g(main, 'alignItems')
    + ' overflow-y=' + g(main, 'overflowY') + ' height=' + g(main, 'height'));
  if (block) console.log('blockContainer width=' + g(block, 'width') + ' maxWidth=' + g(block, 'maxWidth')
    + ' alignSelf=' + g(block, 'alignSelf') + ' paddingTop=' + g(block, 'paddingTop'));

  /* ---- 判定 3：实测 sticky 能不能钉住（临时加样式，滚动后看是否位移） ---- */
  console.log('\n--- 实测 sticky（临时试，随后自动还原）---');
  const before = start.getBoundingClientRect().top;
  const oldPos = start.style.position, oldTop = start.style.top, oldZ = start.style.zIndex;
  start.style.position = 'sticky';
  start.style.top = '0px';
  start.style.zIndex = '1000';
  const sy = window.scrollY;
  const sc = scroller || document.scrollingElement;
  if (sc === document.scrollingElement) window.scrollTo(0, 400);
  else sc.scrollTop = 400;
  const afterTop = start.getBoundingClientRect().top;
  const appliedY = (sc === document.scrollingElement ? window.scrollY : sc.scrollTop);
  console.log('滚动量 =', appliedY);
  console.log('顶栏 top：滚动前', +before.toFixed(1), '→ 滚动后', +afterTop.toFixed(1));
  console.log(afterTop <= 1.5 && appliedY > 100
    ? '✅ sticky 生效（顶栏钉在视口顶）'
    : (appliedY <= 100
        ? '⚠️ 页面没能滚动（内容不够长或滚错容器），本项无法判定'
        : '❌ sticky 未生效（顶栏跟着滚走了）——需改回 fixed 方案'));
  // 还原
  if (sc === document.scrollingElement) window.scrollTo(0, sy); else sc.scrollTop = sy;
  start.style.position = oldPos; start.style.top = oldTop; start.style.zIndex = oldZ;
  console.log('（样式已还原，页面回到原滚动位置）');

  console.log('\n%c=== 结束，请整段复制回来 ===', 'font-weight:bold');
  return '滚动溯源完成';
})()
