// Following teams. The list lives in this browser (localStorage); push.js sends it to the server
// so notifications can be sent for those teams. Any button with class "follow-btn" and a data-key toggles a team.
(function () {
  var KEY = "scorehub:teams", NAMES = "scorehub:teamnames";
  function read(k, d) { try { return JSON.parse(localStorage.getItem(k) || d); } catch (e) { return JSON.parse(d); } }
  function write(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }

  var api = {
    list: function () { var l = read(KEY, "[]"); return Array.isArray(l) ? l : []; },
    names: function () { var n = read(NAMES, "{}"); return n && typeof n === "object" ? n : {}; },
    has: function (key) { return api.list().indexOf(key) !== -1; },
    toggle: function (key, name) {
      var l = api.list(), n = api.names(), i = l.indexOf(key);
      if (i === -1) { l.push(key); if (name) n[key] = name; } else { l.splice(i, 1); }
      write(KEY, l); write(NAMES, n);
      window.dispatchEvent(new Event("teams-changed"));
      return i === -1;
    }
  };
  window.ScoreHubTeams = api;

  function paint() {
    document.querySelectorAll(".follow-btn").forEach(function (b) {
      b.setAttribute("aria-pressed", api.has(b.dataset.key) ? "true" : "false");
    });
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest(".follow-btn");
    if (!b) return;
    api.toggle(b.dataset.key, b.dataset.name);
  });
  window.addEventListener("teams-changed", paint);
  window.addEventListener("storage", function (e) { if (e.key === KEY) { paint(); window.dispatchEvent(new Event("teams-changed")); } });

  // The match page swaps its contents every few seconds: repaint after each swap.
  var pending = null;
  new MutationObserver(function () {
    if (pending) return;
    pending = setTimeout(function () { pending = null; paint(); }, 50);
  }).observe(document.body, {childList: true, subtree: true});
  paint();
})();