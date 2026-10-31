/* Header search, like Sofascore's: one box for teams, competitions and matches. Press / to open it, arrow keys to move, Enter to go.
   Players are searched on their own page (each lookup uses a limited API allowance), so the box links there instead. */
(function () {
  var root = document.getElementById("gsearch");
  if (!root) return;
  var btn = document.getElementById("gsBtn"), panel = document.getElementById("gsPanel"), input = document.getElementById("gsInput"),
      out = document.getElementById("gsResults"), closeBtn = document.getElementById("gsClose");
  var RKEY = "scorehub:recent", timer = null, seq = 0, ctl = null, lite = !!window.SH_LITE;

  function recent() { try { var v = JSON.parse(localStorage.getItem(RKEY) || "[]"); return Array.isArray(v) ? v.slice(0, 6) : []; } catch (e) { return []; } }
  function saveRecent(item) {
    try {
      var list = recent().filter(function (r) { return r.url !== item.url; });
      list.unshift({name: item.name, sub: item.sub || "", url: item.url, crest: item.crest || ""});
      localStorage.setItem(RKEY, JSON.stringify(list.slice(0, 6)));
    } catch (e) {}
  }
  function clearRecent() { try { localStorage.removeItem(RKEY); } catch (e) {} }

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function heading(text, extra) {
    var h = el("div", "gs-h"); h.appendChild(el("span", null, text));
    if (extra) h.appendChild(extra);
    return h;
  }
  function badge(name, crest) {
    if (crest && !lite) { var img = el("img", "gs-crest"); img.src = crest; img.alt = ""; img.loading = "lazy"; img.width = 28; img.height = 28; return img; }
    var s = el("span", "gs-crest gs-initials", (name || "?").replace(/[^A-Za-z0-9 ]/g, "").split(/\s+/).slice(0, 2).map(function (w) { return w.charAt(0); }).join("").toUpperCase());
    return s;
  }
  function row(item) {
    var a = el("a", "gs-item"); a.href = item.url; a.setAttribute("role", "option");
    a.appendChild(item.node || badge(item.name, item.crest));
    var t = el("span", "gs-t"); t.appendChild(el("b", null, item.name));
    if (item.sub) t.appendChild(el("small", null, item.sub));
    a.appendChild(t);
    if (item.right) a.appendChild(item.right);
    a.addEventListener("click", function () { if (item.remember !== false) saveRecent(item); });
    return a;
  }
  function sportName(s) { return {football: "Football", basketball: "Basketball", hockey: "Hockey", tennis: "Tennis", cricket: "Cricket"}[s] || ""; }
  function when(m) {
    var d = new Date(m.kickoff);
    if (m.status === "live") return {text: "LIVE", cls: "gs-live"};
    if (m.status === "finished" && m.home_score != null) return {text: m.home_score + " - " + m.away_score, cls: "gs-score"};
    if (isNaN(d)) return {text: "", cls: ""};
    var day = d.toLocaleDateString([], {weekday: "short", day: "numeric", month: "short"}), hm = d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
    return {text: day + " " + hm, cls: "gs-when"};
  }

  function render(d, q) {
    out.textContent = "";
    var any = false;
    if (d.teams && d.teams.length) {
      any = true; out.appendChild(heading("Teams"));
      d.teams.forEach(function (t) { out.appendChild(row({name: t.name, crest: t.crest, sub: [sportName(t.sport), t.league].filter(Boolean).join(" · "), url: t.url})); });
    }
    if (d.competitions && d.competitions.length) {
      any = true; out.appendChild(heading("Competitions"));
      d.competitions.forEach(function (c) { out.appendChild(row({name: c.name, sub: [sportName(c.sport), c.country].filter(Boolean).join(" · "), url: c.url})); });
    }
    if (d.matches && d.matches.length) {
      any = true; out.appendChild(heading("Matches"));
      d.matches.forEach(function (m) {
        var w = when(m), r = el("span", w.cls, w.text);
        out.appendChild(row({name: m.home + " v " + m.away, sub: m.league, url: m.url, right: r, node: el("span", "gs-crest gs-ball", "⚽"), remember: false}));
      });
    }
    if (!any) out.appendChild(el("p", "gs-empty", "No teams, competitions or matches found for “" + q + "”."));
    var link = el("a", "gs-item gs-players"); link.href = "/players?q=" + encodeURIComponent(q); link.setAttribute("role", "option");
    link.appendChild(el("span", "gs-crest gs-ball", "🔍"));
    var t = el("span", "gs-t"); t.appendChild(el("b", null, "Search players for “" + q + "”")); t.appendChild(el("small", null, "Footballer profiles")); link.appendChild(t);
    out.appendChild(link);
    select(-1);
  }

  function renderIdle() {
    out.textContent = "";
    var r = recent();
    if (r.length) {
      var clear = el("button", "gs-clear", "Clear"); clear.type = "button";
      clear.addEventListener("click", function () { clearRecent(); renderIdle(); });
      out.appendChild(heading("Recent", clear));
      r.forEach(function (x) { out.appendChild(row({name: x.name, sub: x.sub, url: x.url, crest: x.crest, remember: false})); });
    }
    var holder = el("div"); out.appendChild(holder);
    fetch("/api/teams").then(function (res) { return res.json(); }).then(function (d) {
      if (input.value.trim() || !d.teams || !d.teams.length) return;
      holder.appendChild(heading("Playing soon"));
      d.teams.slice(0, 8).forEach(function (t) { holder.appendChild(row({name: t.name, crest: t.crest, url: "/team/" + t.key})); });
      if (!r.length) select(-1);
    }).catch(function () {});
    if (!r.length) out.appendChild(el("p", "gs-hint", "Type a team, competition or match. Press / any time to search."));
  }

  function items() { return Array.prototype.slice.call(out.querySelectorAll("a.gs-item")); }
  var sel = -1;
  function select(i) {
    var list = items();
    list.forEach(function (a) { a.classList.remove("on"); a.removeAttribute("aria-selected"); });
    sel = i;
    if (i >= 0 && list[i]) { list[i].classList.add("on"); list[i].setAttribute("aria-selected", "true"); list[i].scrollIntoView({block: "nearest"}); }
  }

  function run() {
    var q = input.value.trim(), n = ++seq;
    if (ctl) { try { ctl.abort(); } catch (e) {} }
    if (q.length < 2) { renderIdle(); return; }
    ctl = window.AbortController ? new AbortController() : null;
    fetch("/api/search?q=" + encodeURIComponent(q), ctl ? {signal: ctl.signal} : undefined).then(function (r) { return r.json(); }).then(function (d) {
      if (n !== seq) return;
      if (d.error) { out.textContent = ""; out.appendChild(el("p", "gs-empty", "You're searching too fast. Wait a few seconds.")); return; }
      render(d, q);
    }).catch(function () {});
  }

  function open() {
    if (!panel.hidden) { input.focus(); return; }
    panel.hidden = false; root.classList.add("open"); btn.setAttribute("aria-expanded", "true");
    document.documentElement.classList.add("gs-lock");
    input.value = ""; renderIdle(); input.focus();
  }
  function close() {
    if (panel.hidden) return;
    panel.hidden = true; root.classList.remove("open"); btn.setAttribute("aria-expanded", "false");
    document.documentElement.classList.remove("gs-lock");
    btn.focus({preventScroll: true});
  }

  btn.addEventListener("click", open);
  closeBtn.addEventListener("click", close);
  input.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(run, 160); });
  document.addEventListener("mousedown", function (e) { if (!panel.hidden && !root.contains(e.target)) close(); });
  document.addEventListener("keydown", function (e) {
    var tag = (e.target && e.target.tagName || "").toLowerCase(), typing = tag === "input" || tag === "textarea" || tag === "select" || (e.target && e.target.isContentEditable);
    if (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); open(); return; }
    if (panel.hidden) return;
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); var l = items(); if (l.length) select(Math.min(sel + 1, l.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); select(Math.max(sel - 1, -1)); }
    else if (e.key === "Enter" && e.target === input) {
      var list = items(), pick = sel >= 0 ? list[sel] : list[0];
      if (pick) { e.preventDefault(); pick.click(); if (!pick.defaultPrevented) window.location.href = pick.href; }
    }
  });
})();