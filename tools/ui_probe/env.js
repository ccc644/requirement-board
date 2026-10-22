// 环境自证：列出当前真实 DOM 上存在的 data-testid 与 button[kind]，
// 以及所有 st-key-* 的命中与位置。用于「选择器存在性自证」纪律（19 号文档 t34 起）。
(function () {
  const testids = {};
  document.querySelectorAll('[data-testid]').forEach(function (e) {
    const t = e.getAttribute('data-testid');
    testids[t] = (testids[t] || 0) + 1;
  });

  const kinds = {};
  document.querySelectorAll('button[kind]').forEach(function (b) {
    const k = b.getAttribute('kind');
    kinds[k] = (kinds[k] || 0) + 1;
  });

  const keyed = Array.prototype.slice.call(document.querySelectorAll('[class*="st-key-"]')).map(function (e) {
    const cls = Array.prototype.slice.call(e.classList).find(function (c) { return c.indexOf('st-key-') === 0; });
    const r = e.getBoundingClientRect();
    return { cls: cls, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
  });

  const buttons = Array.prototype.slice.call(document.querySelectorAll('button')).slice(0, 40).map(function (b) {
    const r = b.getBoundingClientRect();
    return {
      text: (b.innerText || '').trim().slice(0, 24),
      kind: b.getAttribute('kind'),
      testid: b.getAttribute('data-testid'),
      box: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }
    };
  });

  return {
    testids: testids,
    buttonKinds: kinds,
    keyed: keyed,
    buttons: buttons,
    viewport: { w: innerWidth, h: innerHeight },
    dpr: devicePixelRatio
  };
})()
