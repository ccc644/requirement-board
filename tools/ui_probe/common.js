// 页面探针公共库：几何 / 计算样式 / 文本矩形 / 间距。
// 由 _ui_measure.ps1 通过 CDP Runtime.evaluate 注入，之后各量测脚本复用。
(function () {
  const box = function (e) {
    if (!e) return null;
    const r = e.getBoundingClientRect();
    return {
      x: +r.x.toFixed(1), y: +r.y.toFixed(1), w: +r.width.toFixed(1), h: +r.height.toFixed(1),
      bottom: +r.bottom.toFixed(1), right: +r.right.toFixed(1)
    };
  };

  const cs = function (e, props) {
    if (!e) return null;
    const s = getComputedStyle(e);
    const list = props || [
      'fontSize', 'fontWeight', 'lineHeight', 'color', 'backgroundColor',
      'paddingTop', 'paddingBottom', 'paddingLeft', 'paddingRight',
      'marginTop', 'marginBottom', 'gap', 'borderRadius', 'borderLeftWidth',
      'borderBottomWidth', 'borderTopWidth', 'display', 'alignItems',
      'justifyContent', 'textAlign', 'minHeight', 'height', 'width', 'letterSpacing'
    ];
    const o = {};
    list.forEach(function (p) { o[p] = s[p]; });
    return o;
  };

  // 精确文字矩形：Range 只量文字，用于「字挤不挤」「垂直是否居中」的客观判据
  const text = function (e) {
    if (!e) return null;
    const r = document.createRange();
    r.selectNodeContents(e);
    const b = r.getBoundingClientRect();
    const s = getComputedStyle(e);
    const lh = parseFloat(s.lineHeight) || 0;
    return {
      x: +b.x.toFixed(1), y: +b.y.toFixed(1), w: +b.width.toFixed(1), h: +b.height.toFixed(1),
      lineHeight: s.lineHeight, fontSize: s.fontSize,
      lines: lh ? Math.round(b.height / lh) : null
    };
  };

  const vgap = function (a, b) {
    if (!a || !b) return null;
    return +(b.getBoundingClientRect().top - a.getBoundingClientRect().bottom).toFixed(1);
  };

  const hgap = function (a, b) {
    if (!a || !b) return null;
    return +(b.getBoundingClientRect().left - a.getBoundingClientRect().right).toFixed(1);
  };

  // 文字块在容器内的上下留白：判断垂直居中 / 是否挤
  const pad = function (outer, innerTextNode) {
    if (!outer || !innerTextNode) return null;
    const o = outer.getBoundingClientRect();
    const t = innerTextNode;
    return {
      slackTop: +(t.y - o.top).toFixed(1),
      slackBottom: +(o.bottom - (t.y + t.h)).toFixed(1),
      slackLeft: +(t.x - o.left).toFixed(1)
    };
  };

  const byText = function (sel, txt) {
    return Array.prototype.slice.call(document.querySelectorAll(sel))
      .find(function (e) { return (e.innerText || '').trim().indexOf(txt) === 0; }) || null;
  };

  const btnByText = function (txt, scope) {
    const root = scope || document;
    return Array.prototype.slice.call(root.querySelectorAll('button'))
      .find(function (b) { return (b.innerText || '').trim().indexOf(txt) === 0; }) || null;
  };

  window.__kbProbe = {
    box: box, cs: cs, text: text, vgap: vgap, hgap: hgap,
    pad: pad, byText: byText, btnByText: btnByText
  };
  return 'probe-ready';
})()
