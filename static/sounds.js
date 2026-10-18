// Alert sounds for when ScoreHub is open. A website cannot change the sound of a notification while the site
// is closed (the phone or browser decides that), so when a goal notification arrives and a ScoreHub tab is
// visible, the tab plays the sound chosen here and the notification itself is kept silent.
(function () {
  var KEY = "scorehub:sound";
  var ctx = null;

  function get() { try { return localStorage.getItem(KEY) || "default"; } catch (e) { return "default"; } }
  function set(v) { try { localStorage.setItem(KEY, v); } catch (e) {} }
  function audio() {
    if (!ctx) {
      var C = window.AudioContext || window.webkitAudioContext;
      if (!C) return null;
      try { ctx = new C(); } catch (e) { return null; }
    }
    return ctx;
  }
  // Browsers only allow sound after the visitor has tapped or pressed something on the page.
  function unlock() { var c = audio(); if (c && c.state === "suspended") c.resume(); }
  ["pointerdown", "keydown", "touchstart"].forEach(function (ev) { addEventListener(ev, unlock, {passive: true}); });

  function tone(c, freq, start, dur, type, vol, extra) {
    var o = c.createOscillator(), g = c.createGain();
    o.type = type || "sine";
    o.frequency.setValueAtTime(freq, c.currentTime + start);
    g.gain.setValueAtTime(0.0001, c.currentTime + start);
    g.gain.exponentialRampToValueAtTime(vol || 0.3, c.currentTime + start + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + start + dur);
    o.connect(g); g.connect(c.destination);
    if (extra) extra(o, c, start, dur);
    o.start(c.currentTime + start); o.stop(c.currentTime + start + dur + 0.05);
  }
  function trill(o, c, start, dur) {  // fast wobble: a referee's whistle
    var l = c.createOscillator(), d = c.createGain();
    l.frequency.value = 32; d.gain.value = 140;
    l.connect(d); d.connect(o.frequency);
    l.start(c.currentTime + start); l.stop(c.currentTime + start + dur + 0.05);
  }
  var SOUNDS = {
    ping:    function (c) { tone(c, 880, 0, 0.25, "sine", 0.35); tone(c, 1320, 0.15, 0.35, "sine", 0.3); },
    chime:   function (c) { [523, 659, 784, 1047].forEach(function (f, i) { tone(c, f, i * 0.14, 0.5, "triangle", 0.28); }); },
    whistle: function (c) { tone(c, 2800, 0, 0.45, "sine", 0.3, trill); tone(c, 2800, 0.6, 0.7, "sine", 0.3, trill); },
    horn:    function (c) { tone(c, 233, 0, 0.9, "sawtooth", 0.22); tone(c, 236, 0, 0.9, "sawtooth", 0.22); tone(c, 466, 0, 0.9, "square", 0.05); }
  };
  var LABELS = {default: "Phone's default", ping: "Ping", chime: "Chime", whistle: "Whistle", horn: "Horn",
                off: "Silent while the site is open"};

  // Returns true if a sound actually played.
  function play(name) {
    var fn = SOUNDS[name], c = audio();
    if (!fn || !c) return false;
    if (c.state === "suspended") { try { c.resume(); } catch (e) {} }
    if (c.state !== "running") return false;
    fn(c);
    return true;
  }
  window.ScoreHubSound = {get: get, set: set, play: play, labels: LABELS};

  // The service worker asks every open tab to play the sound when a goal notification arrives.
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("message", function (e) {
      if (!e.data || e.data.type !== "play" || !e.ports || !e.ports[0]) return;
      var name = get();
      // "played" tells the worker it may keep the notification silent. "Default" and failures keep the phone's own sound.
      var ok = name === "off" || (name !== "default" && play(name));
      e.ports[0].postMessage({played: ok});
    });
  }
})();