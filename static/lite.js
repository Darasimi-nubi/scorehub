// Data saver: a footer switch, plus a gentle offer on slow connections. The choice is kept in a cookie so the
// server can send lighter pages (no crest pictures, no web font, no auto-playing video, slower refreshing).
(function () {
  function set(on) {
    try { document.cookie = "lite=" + (on ? "1" : "0") + "; path=/; max-age=31536000; SameSite=Lax"; } catch (e) {}
    try { localStorage.setItem("scorehub:lite", on ? "1" : "0"); } catch (e) {}
    window.location.reload();
  }
  var btn = document.getElementById("liteToggle");
  if (btn) btn.addEventListener("click", function () { set(!window.SH_LITE); });

  // Load a highlights video only when asked.
  document.addEventListener("click", function (e) {
    var b = e.target.closest(".hl-load");
    if (!b) return;
    var f = document.createElement("iframe");
    f.src = b.dataset.src; f.title = b.dataset.title || "Highlights"; f.allowFullscreen = true;
    f.allow = "accelerometer; encrypted-media; picture-in-picture; fullscreen";
    f.setAttribute("referrerpolicy", "strict-origin-when-cross-origin");
    b.parentNode.replaceChild(f, b);
  });

  // Offer data saver when the browser says the connection is slow or the visitor asked for less data. Never switches on by itself.
  if (window.SH_LITE) return;
  var c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  var slow = c && (c.saveData || /(^|-)2g$/.test(c.effectiveType || ""));
  var asked = false;
  try { asked = !!localStorage.getItem("scorehub:lite"); } catch (e) {}
  if (!slow || asked) return;
  var bar = document.createElement("div");
  bar.className = "lite-bar";
  bar.innerHTML = '<span>Slow connection? Data saver loads pages with fewer pictures and uses less data.</span>' +
                  '<button type="button" class="yes">Turn on</button><button type="button" class="no" aria-label="Dismiss">No thanks</button>';
  bar.querySelector(".yes").addEventListener("click", function () { set(true); });
  bar.querySelector(".no").addEventListener("click", function () {
    try { localStorage.setItem("scorehub:lite", "0"); } catch (e) {}
    bar.remove();
  });
  document.body.insertBefore(bar, document.body.firstChild);
})();