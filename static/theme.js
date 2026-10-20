/* Theme switch: Auto (follow the device) -> Light -> Dark. The choice is remembered in this browser only. */
(function () {
  var KEY = "scorehub:theme", order = ["auto", "light", "dark"], names = {auto: "Auto", light: "Light", dark: "Dark"};
  function saved() { try { var v = localStorage.getItem(KEY); return order.indexOf(v) > -1 ? v : "auto"; } catch (e) { return "auto"; } }
  var btn = document.getElementById("themeToggle");
  function paint() { if (btn) btn.textContent = "Theme: " + names[saved()]; if (window.ScoreHubTheme) window.ScoreHubTheme.apply(); }
  if (btn) btn.addEventListener("click", function () {
    var next = order[(order.indexOf(saved()) + 1) % order.length];
    try { localStorage.setItem(KEY, next); } catch (e) {}
    paint();
  });
  paint();
})();