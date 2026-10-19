// Predictions: a random id kept in this browser (no account), and the "Who will win?" box on football match pages.
(function () {
  var KEY = "scorehub:uid";
  function uid() {
    var v = "";
    try { v = localStorage.getItem(KEY) || ""; } catch (e) {}
    if (!/^[a-f0-9]{16,40}$/.test(v)) {
      var a = new Uint8Array(12);
      (window.crypto || window.msCrypto).getRandomValues(a);
      v = Array.prototype.map.call(a, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
      try { localStorage.setItem(KEY, v); } catch (e) {}
    }
    return v;
  }
  window.ScoreHubPredict = {uid: uid};

  var box = document.getElementById("predict");
  if (!box) return;
  var id = box.dataset.id, me = uid(), state = null, timer = null;
  var msg = box.querySelector(".p-msg");

  function pct(n, total) { return total ? Math.round(n * 100 / total) : 0; }
  function draw() {
    if (!state) return;
    var open = state.open && new Date(state.kickoff) > new Date();
    box.hidden = false;
    box.querySelectorAll(".p-btn").forEach(function (b) {
      var k = b.dataset.pick, n = state.counts[k];
      b.querySelector(".p-pct").textContent = state.total ? pct(n, state.total) + "%" : "";
      b.querySelector(".p-bar").style.width = pct(n, state.total) + "%";
      b.disabled = !open;
      b.classList.toggle("mine", state.mine === k);
      b.classList.toggle("won", !!state.outcome && state.outcome === k);
    });
    var lead = open ? "Who will win? Pick before kick-off." : (state.status === "finished" ? "Final predictions" : "Predictions are closed");
    box.querySelector(".p-title").textContent = lead;
    var parts = [];
    if (state.total) parts.push(state.total + (state.total === 1 ? " prediction" : " predictions"));
    if (state.mine) {
      if (state.outcome) parts.push(state.mine === state.outcome ? "You got it right: +3 points" : "Your pick was wrong this time");
      else parts.push("Your pick is saved" + (open ? ". You can change it until kick-off." : "."));
    } else if (!open) parts.push("You didn't make a pick");
    msg.textContent = parts.join(" · ");
    if (open) {
      clearTimeout(timer);
      var ms = new Date(state.kickoff) - new Date();
      if (ms > 0 && ms < 2147483000) timer = setTimeout(draw, ms + 500);  // lock the buttons at kick-off
    }
  }
  function load() {
    fetch("/api/predict/" + id + "?uid=" + me).then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (d) { state = d; draw(); }).catch(function () {});
  }
  box.addEventListener("click", function (e) {
    var b = e.target.closest(".p-btn");
    if (!b || b.disabled) return;
    fetch("/api/predict/" + id, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({uid: me, pick: b.dataset.pick})})
      .then(function (r) { return r.json().then(function (d) { return [r.ok, d]; }); })
      .then(function (x) {
        if (x[0]) { state = x[1]; draw(); }
        else { msg.textContent = x[1].error === "closed" ? "Predictions are closed for this match." : x[1].error === "slow" ? "Too fast. Wait a moment." : "Couldn't save your pick."; load(); }
      }).catch(function () { msg.textContent = "Couldn't save your pick. Try again."; });
  });
  load();
})();