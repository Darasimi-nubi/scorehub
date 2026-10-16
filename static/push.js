// Alerts button: asks permission, subscribes this browser, and tells the server
// which followed teams and starred matches to send goals for. Re-registers on every visit (the server
// forgets subscribers if its storage is wiped on a redeploy).
(function () {
  var btn = document.getElementById("bellBtn");
  if (!btn || !("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) return;

  function favs() {
    try { return JSON.parse(localStorage.getItem("scorehub:favs") || "[]"); } catch (e) { return []; }
  }
  function toKey(b64) {
    var pad = "=".repeat((4 - b64.length % 4) % 4);
    var raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, function (c) { return c.charCodeAt(0); });
  }
  function post(url, body) {
    return fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  }
  function show(on) {
    btn.hidden = false;
    btn.classList.toggle("bell-on", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.querySelector("span").textContent = on ? "Alerts on" : "Alerts";
  }
  function register() { return navigator.serviceWorker.register("/sw.js"); }
  function currentSub() { return register().then(function () { return navigator.serviceWorker.ready; }).then(function (r) { return r.pushManager.getSubscription(); }); }
  function teams() { return window.ScoreHubTeams ? window.ScoreHubTeams.list() : []; }
  function sync(sub) { return post("/api/push/subscribe", {subscription: sub.toJSON(), matches: favs(), teams: teams()}); }

  fetch("/api/push/key").then(function (r) { return r.ok ? r.json() : Promise.reject(); }).then(function (cfg) {
    // Notifications are available on this server: show the button.
    show(false);
    if (Notification.permission === "granted") {
      currentSub().then(function (sub) { if (sub) { show(true); sync(sub); } });
    }
    function resync() { currentSub().then(function (sub) { if (sub) sync(sub); }); }
    window.addEventListener("favs-changed", resync);
    window.addEventListener("teams-changed", resync);

    btn.addEventListener("click", function () {
      currentSub().then(function (sub) {
        if (sub) {  // turn off
          post("/api/push/unsubscribe", {endpoint: sub.endpoint});
          return sub.unsubscribe().then(function () { show(false); });
        }
        if (Notification.permission === "denied") {
          alert("Notifications are blocked for this site. Allow them in your browser's site settings, then try again.");
          return;
        }
        return Notification.requestPermission().then(function (perm) {
          if (perm !== "granted") return;
          return navigator.serviceWorker.ready.then(function (reg) {
            return reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: toKey(cfg.key)});
          }).then(function (s) {
            return sync(s).then(function () {
              show(true);
              if (!favs().length && !teams().length) {
                alert("Alerts are on. Pick the teams you want goals, kick-off and full-time alerts for.");
                if (location.pathname !== "/following") location.href = "/following";
              }
            });
          });
        });
      }).catch(function () { alert("Couldn't turn on notifications in this browser."); });
    });
  }).catch(function () {});  // no keys on the server: stay hidden
})();