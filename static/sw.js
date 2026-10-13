// Receives goal pushes (even when the site is closed) and opens the match when tapped.
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener("push", function (event) {
  var d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) {}
  event.waitUntil(self.registration.showNotification(d.title || "ScoreHub", {
    body: d.body || "",
    tag: d.tag,
    renotify: true,
    icon: "/static/icon-192.png",
    badge: "/static/icon-192.png",
    data: { url: d.url || "/" }
  }));
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
