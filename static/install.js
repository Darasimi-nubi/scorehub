// "Add to home screen" prompt and the share button.
(function () {
  var KEY = "scorehub:install-dismissed";
  function dismissed() {
    try { var t = parseInt(localStorage.getItem(KEY) || "0", 10); return t && Date.now() - t < 14 * 864e5; } catch (e) { return false; }
  }
  function standalone() {
    return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  }

  var deferred = null;
  function banner(text, withButton) {
    if (document.getElementById("installBar")) return;
    var bar = document.createElement("div");
    bar.id = "installBar";
    bar.className = "install-bar";
    bar.innerHTML = '<span></span><button type="button" class="install-go"></button><button type="button" class="install-x" aria-label="Close">&times;</button>';
    bar.firstChild.textContent = text;
    var go = bar.querySelector(".install-go");
    if (withButton) { go.textContent = "Install"; } else { go.hidden = true; }
    document.body.appendChild(bar);
    bar.querySelector(".install-x").addEventListener("click", function () {
      try { localStorage.setItem(KEY, String(Date.now())); } catch (e) {}
      bar.remove();
    });
    go.addEventListener("click", function () {
      if (!deferred) return;
      deferred.prompt();
      deferred.userChoice.then(function () { deferred = null; bar.remove(); });
    });
  }

  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferred = e;
    if (!dismissed() && !standalone()) setTimeout(function () { if (deferred) banner("Add ScoreHub to your home screen", true); }, 15000);
  });
  window.addEventListener("appinstalled", function () {
    var bar = document.getElementById("installBar"); if (bar) bar.remove();
  });
  // iPhone Safari has no install event: show a short how-to instead.
  var ua = navigator.userAgent || "";
  if (/iphone|ipad|ipod/i.test(ua) && /safari/i.test(ua) && !/crios|fxios/i.test(ua) && !standalone() && !dismissed()) {
    setTimeout(function () { banner("To install: tap Share, then Add to Home Screen", false); }, 15000);
  }

  // Share button on match pages: the phone's share sheet (WhatsApp, X, ...) or copy the link.
  document.addEventListener("click", function (e) {
    var b = e.target.closest(".share-btn");
    if (!b) return;
    var data = { title: b.dataset.title, text: b.dataset.text, url: b.dataset.url };
    if (navigator.share) {
      navigator.share(data).catch(function () {});
      return;
    }
    var done = function () { var old = b.textContent; b.textContent = "Link copied"; setTimeout(function () { b.textContent = old; }, 1800); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(data.url).then(done, function () { window.prompt("Copy this link", data.url); });
    else window.prompt("Copy this link", data.url);
  });
})();