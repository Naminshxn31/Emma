// Khun Miki's Thai words as a caption -- the PREVIEW and the scene recordings only
// (scripts/preview_ceremony_show.py injects it; scripts/record_ceremony_scenes.py adds it).
// Review 18 Nov: his line used to go out as a subtitle with no slide, and the page clears the
// stage for such a line -- so between his question and her answer the preview showed a cut
// the hall never sees. His voice goes out as sound alone now (the page's state is the day's)
// and his words are drawn here, over the subtitle band, by a layer the real page never has.
(function () {
  'use strict';
  var el = null;
  function make() {
    var css = document.createElement('style');
    css.textContent =
      '#pv-host{position:fixed;left:0;right:0;bottom:0;height:var(--mirror-foot,22vh);display:flex;' +
      'align-items:center;justify-content:center;z-index:2147483646;pointer-events:none;opacity:0;' +
      'transition:opacity .35s cubic-bezier(.4,0,.2,1);padding:0 6vw;text-align:center}' +
      '#pv-host.on{opacity:1}' +
      // (the same see-through box as her subtitle, his name in amber above his words)
      '#pv-host div{display:flex;flex-direction:column;align-items:center;gap:.3vh;padding:1vh 2.4vw;border-radius:1.2vh;' +
      'background:rgba(4,6,20,.58);border:1px solid rgba(255,210,122,.3);max-width:88vw}' +
      '#pv-host b{font:600 1.6vh "Segoe UI",Tahoma,sans-serif;letter-spacing:.2em;color:#ffd27a;text-transform:uppercase}' +
      '#pv-host span{font:600 3.3vh "Noto Sans Thai","Leelawadee UI",Tahoma,sans-serif;color:#fff3dc;line-height:1.35}';
    document.head.appendChild(css);
    el = document.createElement('div');
    el.id = 'pv-host';
    el.setAttribute('aria-live', 'polite');
    var box = document.createElement('div');
    var who = document.createElement('b');
    who.textContent = 'คุณมิกิ';
    box.appendChild(who);
    box.appendChild(document.createElement('span'));
    el.appendChild(box);
    document.body.appendChild(el);
  }
  // text = his words (shown), '' or null = hidden
  window.__pvHostCaption = function (text) {
    if (!document.body) return;
    if (!el) make();
    if (text) { el.querySelector('span').textContent = String(text); el.classList.add('on'); }
    else el.classList.remove('on');
  };
})();
