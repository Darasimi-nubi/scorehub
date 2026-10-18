// Receives pushes (even when the site is closed) and opens the match when tapped.
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });

// Ask one open page to play the visitor's chosen sound. Resolves true if it did (so the notification can stay silent).
function askToPlay(client) {
  return new Promise(function (resolve) {
    var ch = new MessageChannel();
    var t = setTimeout(function () { resolve(false); }, 800);
    ch.port1.onmessage = function (e) { clearTimeout(t); resolve(!!(e.data && e.data.played)); };
    try { client.postMessage({type: "play"}, [ch.port2]); } catch (err) { clearTimeout(t); resolve(false); }
  });
}

self.addEventListener("push", function (event) {
  var d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) {}
  event.waitUntil(
    self.clients.matchAll({type: "window", includeUncontrolled: true}).then(function (list) {
      var visible = list.filter(function (c) { return c.visibilityState === "visible"; });
      return Promise.all(visible.map(askToPlay));
    }).catch(function () { return []; }).then(function (results) {
      var quiet = results.some(Boolean);  // a page played the chosen sound: don't add the phone's sound on top
      return self.registration.showNotification(d.title || "ScoreHub", {
        body: d.body || "",
        tag: d.tag,
        renotify: true,
        silent: quiet,
        icon: "/static/icon-192.png",
        badge: "/static/icon-192.png",
        data: { url: d.url || "/" }
      });
    })
  );
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) {
      if (list[i].url.indexOf(url) !== -1 && "focus" in list[i]) return list[i].focus();
    }
    return self.clients.openWindow(url);
  }));
});