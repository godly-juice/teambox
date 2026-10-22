/* Team box UI - plain JS, no framework. Hash routes: #/ #/tour #/sandboxes #/sandbox/<owner>/<name> #/builds #/build/<id>
   #/models #/chat/<id> #/byo #/caps #/profile #/admin #/m/<module>.  Every DOM node is built with h() (text, never innerHTML),
   so nothing a member or a model writes can run as code. Other agents add views for modules via TB.views[<module id>]. */
(function () {
  "use strict";
  var TB = window.TB = { views: {}, me: null };
  var BANNER = "TEAM PROJECT SPACE - on the team computer, not your computer and not Marjus's computer";

  function h(tag, attrs) {
    var el = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    });
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, c) {
    if (c == null || c === false) return;
    if (Array.isArray(c)) { c.forEach(function (x) { add(el, x); }); return; }
    el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  TB.h = h;

  function api(method, path, body) {
    var opt = { method: method, headers: { "X-Teambox": "1" }, credentials: "same-origin" };
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    return fetch(path, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var e = new Error(j.error || ("HTTP " + r.status)); e.status = r.status; throw e; }
        return j;
      });
    });
  }
  TB.api = api;

  var view = function () { return document.getElementById("view"); };
  function show() {
    var v = view(); v.textContent = "";
    for (var i = 0; i < arguments.length; i++) add(v, arguments[i]);
    v.focus({ preventScroll: true });
  }
  function err(e) { return h("div", { class: "err", role: "alert", text: (e && e.message) || String(e) }); }
  function banner() { return h("div", { class: "banner", role: "note" }, BANNER, h("small", { text: "Only you and the team's admins can see your projects - unless you press Share to feed. Tests run in a locked jail with no network." })); }
  function pill(text, kind) { return h("span", { class: "pill " + (kind || ""), text: text }); }
  function statusPill(s) { return pill(s || "-", /pass|promoted|ok/.test(s || "") ? "ok" : /FAIL|reject|error|interrupt/.test(s || "") ? "bad" : ""); }
  function fmtDiff(text) {
    var pre = h("pre", { class: "diff" });
    (text || "").split("\n").slice(0, 4000).forEach(function (ln) {
      var c = ln.indexOf("+") === 0 && ln.indexOf("+++") !== 0 ? "add" : ln.indexOf("-") === 0 && ln.indexOf("---") !== 0 ? "del" : ln.indexOf("@@") === 0 ? "hunk" : "";
      pre.appendChild(h("span", { class: c, text: ln + "\n" }));
    });
    return pre;
  }
  function copyBtn(getText, label) {
    return h("button", { class: "ghost small", type: "button", onclick: function (ev) {
      var t = getText(), b = ev.target;
      var done = function () { b.textContent = "Copied"; setTimeout(function () { b.textContent = label || "Copy"; }, 1500); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(done, function () { fallback(t); done(); });
      else { fallback(t); done(); }
    } }, label || "Copy");
  }
  function fallback(t) { var ta = h("textarea"); ta.value = t; document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); } catch (e) {} ta.remove(); }

  // ------------------------------------------------------------------ nav
  var NAV = [
    ["#/", "Home"], ["#/tour", "Tour"], ["#/projects", "Projects"], ["#/builds", "AI builds"], ["#/models", "Models & chat"],
    ["#/vault", "Vault"], ["#/byo", "Your own AI tool"], ["#/caps", "Your caps"], ["#/profile", "Profile"]
  ];
  function renderNav(mods) {
    var nav = document.getElementById("nav"); nav.textContent = "";
    NAV.forEach(function (n) { nav.appendChild(h("a", { href: n[0], text: n[1] })); });
    nav.appendChild(h("h4", { text: "Team" }));
    (mods || []).forEach(function (m) {
      nav.appendChild(h("a", { href: "#/m/" + m.id }, m.nav, m.status !== "live" ? h("span", { class: "soon", text: "coming soon" }) : null));
    });
    if (TB.me && TB.me.admin) { nav.appendChild(h("h4", { text: "Admin" })); nav.appendChild(h("a", { href: "#/admin", text: "Admin: review & promote" })); nav.appendChild(h("a", { href: "#/admin/invites", text: "Admin: invites & accounts" })); }
    nav.appendChild(h("h4", { text: "Help" }));
    nav.appendChild(h("a", { href: "/how-it-works", text: "How it works" }));
    if (TB.me && TB.me.signed_in_with === "session") nav.appendChild(h("button", { class: "ghost small", type: "button", id: "signOut", onclick: signOut }, "Sign out"));
    markNav();
  }
  function markNav() {
    var cur = location.hash || "#/";
    document.querySelectorAll("#nav a").forEach(function (a) {
      var href = a.getAttribute("href");
      a.classList.toggle("on", href === cur || (href !== "#/" && href.indexOf("#/") === 0 && cur.indexOf(href) === 0));
    });
  }

  // ------------------------------------------------------------------ views
  function vHome() {
    show(h("h1", { text: "Welcome" + (TB.me.display_name ? ", " + TB.me.display_name : "") }),
      h("p", {}, "This is the team box: the team's own computer. ", h("a", { href: "#/tour", text: "Take the 1-minute tour" }), " if you are new."),
      h("div", { class: "grid" },
        tile("Projects", "Your private project spaces (git repos).", "#/projects"),
        tile("AI builds", "Let a shared AI build something in a project.", "#/builds"),
        tile("Vault", "Your encrypted notes and files.", "#/vault"),
        tile("Models & chat", "Chat, start/stop models, save your own settings versions.", "#/models"),
        tile("Your own AI tool", "Connect Claude Code, Codex, Cursor or OpenCode.", "#/byo")),
      TB.me.dev_mode ? h("div", { class: "err", text: "DEV MODE is on: identity comes from a test header/cookie, not the sign-in. Never on the real box." }) : null,
      h("div", { id: "home-feed" }, h("p", { class: "muted", text: "Loading the home feed..." })));
    if (TB.homeFeed) TB.homeFeed(document.getElementById("home-feed"));     // static/feeds.js
  }
  function tile(t, d, href) { return h("a", { class: "card", href: href }, h("h3", { text: t }), h("p", { class: "muted", text: d })); }

  function vTour() {
    api("GET", "/api/tour").then(function (t) {
      var done = (t.progress && t.progress.done) || [];
      var bar = h("div"); bar.style.width = Math.round(100 * done.length / t.checkpoints.length) + "%";
      var list = h("ol", { class: "checkpoints" });
      t.checkpoints.forEach(function (c, i) {
        var isDone = done.indexOf(c.id) >= 0;
        list.appendChild(h("li", { class: isDone ? "done" : "", id: "cp-" + c.id },
          h("span", { class: "tick", "aria-label": isDone ? "done" : "not yet", text: isDone ? "✓" : String(i + 1) }),
          h("div", {}, h("a", { href: c.link, onclick: function () { api("POST", "/api/tour", { done: c.id }); }, text: c.title }),
            h("div", { text: c.text }), h("a", { class: "muted", href: "/how-it-works#" + c.id, text: "Details" }))));
      });
      var ta = h("textarea", { readonly: true, "aria-label": "Verification prompt for your own AI" }); ta.value = t.prompt;
      var tout = h("div");
      show(h("h1", { text: "What this site is" }), h("p", { text: t.intro }),
        h("div", { class: "progress", "aria-label": done.length + " of " + t.checkpoints.length + " checkpoints visited" }, bar),
        h("p", { class: "muted", text: done.length + " of " + t.checkpoints.length + " checkpoints visited - click each to see that area." }),
        list,
        h("p", { class: "bottom-text", id: "tour-bottom-text", text: t.bottom_text }),
        h("div", { class: "copybox card" }, h("div", { class: "row" }, h("strong", { text: "Text for your trusted AI" }), copyBtn(function () { return t.prompt; }, "Copy text")), ta),
        h("div", { class: "row" }, h("button", { onclick: function () { api("POST", "/api/tour", { seen: true }).then(function () { location.hash = "#/"; }).catch(function (e) { tout.textContent = ""; tout.appendChild(err(e)); }); } }, "Got it - go to Home"),
          h("button", { class: "ghost", onclick: function () { api("POST", "/api/tour", { reset: true }).then(vTour).catch(function (e) { tout.textContent = ""; tout.appendChild(err(e)); }); } }, "Reset progress")), tout);
      api("POST", "/api/tour", { seen: true });
    }).catch(function (e) { show(err(e)); });
  }

  function vSandboxes() {
    var q = TB.me.admin ? "?all=1" : "";
    api("GET", "/api/sandboxes" + q).then(function (j) {
      var name = h("input", { placeholder: "e.g. weather-bot", maxlength: "40", id: "newSandboxName" });
      var out = h("div");
      show(banner(), h("h1", { text: TB.me.admin ? "All projects (admin view)" : "Your projects" }),
        h("div", { class: "tbl" }, h("table", {}, h("tr", {}, h("th", { text: "Project" }), TB.me.admin ? h("th", { text: "Owner" }) : null, h("th", { text: "Created" }), h("th", { text: "" })),
          j.sandboxes.map(function (s) {
            return h("tr", {}, h("td", {}, h("a", { href: "#/project/" + s.owner + "/" + s.name, text: s.label || s.name })),
              TB.me.admin ? h("td", { text: s.owner_email || s.owner }) : null, h("td", { text: s.created || "" }), h("td", {}, s.busy ? pill(s.busy) : null));
          }))),
        h("div", { class: "card" }, h("h3", { text: "New project" }), h("label", { for: "newSandboxName", text: "Name" }), name,
          h("div", { class: "row" }, h("button", { onclick: function () {
            api("POST", "/api/sandboxes", { name: name.value }).then(function (r) { location.hash = "#/project/" + r.sandbox.owner + "/" + r.sandbox.name; })
              .catch(function (e) { out.textContent = ""; out.appendChild(err(e)); });
          } }, "Create")), out,
          h("p", { class: "muted", text: "Every new project starts from the clean starter template (README, guardrail files, an example) - never anyone's own files. It is private until you press Share to feed." })));
    }).catch(function (e) { show(err(e)); });
  }

  function vSandbox(owner, name) {
    Promise.all([api("GET", "/api/sandboxes/" + owner + "/" + name), api("GET", "/api/models").catch(function () { return { models: [] }; })]).then(function (r) {
      var s = r[0], models = r[1].models || [];
      var task = h("textarea", { id: "buildTask", placeholder: "What should the AI build? e.g. 'add a function that converts Celsius to Fahrenheit, with a test'" });
      var sel = h("select", { id: "buildModel" }, models.map(function (m) { return h("option", { value: m.id, text: m.name + " (" + m.state + ")" }); }));
      var out = h("div"), fileBox = h("div"), testOut = h("div");
      var mine = owner === TB.me.id;
      show(banner(), h("h1", { text: "Project: " + (s.label || s.name) }),
        h("p", { class: "muted", text: "Owner: " + (s.owner_email || s.owner) + (s.busy ? " - busy: " + s.busy : "") + " - " + s.size_mb + " MB" }),
        mine && TB.shareButton ? h("div", { class: "row" }, TB.shareButton({ kind: "project", project: name }, "project:" + owner + "/" + name), h("span", { class: "muted small", text: "Private until you share it; sharing posts a read-only copy and never gives access to the project." })) : null,
        h("div", { class: "card" }, h("h3", { text: "Clone with your own AI tool" }), h("pre", { text: "git clone " + s.clone_url }),
          h("div", { class: "row" }, copyBtn(function () { return "git clone " + s.clone_url; }), h("a", { href: "#/byo", text: "How (tokens, tools)" }))),
        mine ? h("div", { class: "card" }, h("h3", { text: "AI build" }), h("label", { for: "buildTask", text: "Task" }), task, h("label", { for: "buildModel", text: "Model" }), sel,
          models.length ? null : h("p", { class: "muted", text: "No shared models are visible to your account right now (see Models & chat)." }),
          h("div", { class: "row" }, h("button", { onclick: function () {
            api("POST", "/api/builds", { sandbox: name, task: task.value, model: sel.value }).then(function (b) { location.hash = "#/build/" + b.build.id; })
              .catch(function (e) { out.textContent = ""; out.appendChild(err(e)); });
          } }, "Start build"), h("button", { class: "ghost", onclick: function () {
            testOut.textContent = "Running the tests in the jail...";
            api("POST", "/api/sandboxes/" + owner + "/" + name + "/test", {}).then(function (t) {
              testOut.textContent = ""; testOut.appendChild(h("div", { class: t.ok ? "okbox" : "err", text: (t.ok ? "PASS" : "FAIL") + " - " + t.command })); testOut.appendChild(h("pre", { text: t.output }));
            }).catch(function (e) { testOut.textContent = ""; testOut.appendChild(err(e)); });
          } }, "Run tests now")), out, testOut) : null,
        h("h2", { text: "Builds and pushes" }),
        s.builds.length ? h("div", { class: "tbl" }, buildsTable(s.builds)) : h("p", { class: "muted", text: "None yet." }),
        h("h2", { text: "Files" }),
        h("div", { class: "row" }, s.files.map(function (f) { return h("button", { class: "ghost small", onclick: function () {
          api("GET", "/api/sandboxes/" + owner + "/" + name + "/file?path=" + encodeURIComponent(f)).then(function (j) {
            fileBox.textContent = ""; fileBox.appendChild(h("h3", { text: f })); fileBox.appendChild(h("pre", { text: j.text }));
          }).catch(function (e) { fileBox.textContent = ""; fileBox.appendChild(err(e)); });
        } }, f); })), fileBox,
        h("h2", { text: "History" }),
        h("div", { class: "tbl" }, h("table", {}, s.log.map(function (l) { return h("tr", {}, h("td", {}, h("code", { text: l[0] })), h("td", { text: l[1] }), h("td", { text: l[2] }), h("td", { text: l[3] })); }))));
    }).catch(function (e) { show(banner(), err(e)); });
  }

  function buildsTable(list) {
    return h("table", {}, h("tr", {}, h("th", { text: "Build" }), h("th", { text: "Kind" }), h("th", { text: "Project" }), h("th", { text: "Status" }), h("th", { text: "Verdict" }), h("th", { text: "When" })),
      list.map(function (b) {
        return h("tr", {}, h("td", {}, h("a", { href: "#/build/" + b.id, text: b.title || b.id })), h("td", { text: b.kind }),
          h("td", { text: (TB.me.admin && b.owner !== TB.me.id ? (b.owner_email || b.owner) + " / " : "") + b.sandbox }),
          h("td", {}, statusPill(b.status)), h("td", {}, b.verdict ? statusPill(b.verdict) : ""), h("td", { text: b.created }));
      }));
  }

  function vBuilds() {
    api("GET", "/api/builds" + (TB.me.admin ? "?all=1" : "")).then(function (j) {
      show(banner(), h("h1", { text: TB.me.admin ? "All builds (admin view)" : "Your builds" }),
        h("p", { class: "muted", text: "AI builds you started and pushes from your own AI tool. Running on the team box now: " + j.running + " of " + j.max_total + " at most. One per person at a time." }),
        j.builds.length ? h("div", { class: "tbl" }, buildsTable(j.builds)) : h("p", {}, "No builds yet. Open a ", h("a", { href: "#/projects", text: "project" }), " and start one."));
    }).catch(function (e) { show(err(e)); });
  }

  var pollT = null, stopAsked = {};
  function vBuild(id) {
    clearTimeout(pollT);
    api("GET", "/api/builds/" + id).then(function (b) {
      var out = h("div");
      var actions = h("div", { class: "row" });
      // Stop takes effect between steps (the answer the model is writing now is awaited first), so say so instead of looking dead
      if (b.status === "running" && (b.owner === TB.me.id || TB.me.admin)) actions.appendChild(stopAsked[id] ? h("button", { class: "ghost", disabled: true }, "Stopping after the current step...") :
        h("button", { class: "ghost", onclick: function () { api("POST", "/api/builds/" + id + "/stop", {}).then(function () { stopAsked[id] = true; vBuild(id); }).catch(function (e) { out.textContent = ""; out.appendChild(err(e)); }); } }, "Stop"));
      if (b.kind === "ai" && b.owner === TB.me.id && !b.applied && /waiting|nothing|rejected|promoted/.test(b.status)) actions.appendChild(h("button", { class: "ghost", onclick: function () {
        api("POST", "/api/builds/" + id + "/apply", {}).then(function () { vBuild(id); }).catch(function (e) { out.textContent = ""; out.appendChild(err(e)); }); } }, "Apply to my project"));
      if (b.can_reject) actions.appendChild(h("button", { class: "ghost", onclick: function () {
        if (!confirm("Reject this build?")) return;
        api("POST", "/api/builds/" + id + "/reject", {}).then(function () { vBuild(id); }).catch(function (e) { out.textContent = ""; out.appendChild(err(e)); }); } }, "Reject"));
      if (b.owner === TB.me.id && !/running|testing|queued/.test(b.status) && TB.shareButton) actions.appendChild(TB.shareButton({ kind: "build", build: id }, "build:" + id));
      if (b.can_promote) actions.appendChild(h("button", { onclick: function () {
        var tp = (b.tripwire_pending || []).map(function (t) { return t.file + " line " + t.line + " ('" + t.word + "')"; });
        if (!confirm("Promote " + (b.files || []).length + " file(s) into the shared team project?\n\n" + (b.files || []).map(function (f) { return f.status + " " + f.path; }).join("\n") + "\n\nFlags: " + ((b.flags || []).join("; ") || "none") +
          (tp.length ? "\n\nWARNING - the safety check blocked these tests (they did NOT run): " + tp.join("; ") : ""))) return;
        api("POST", "/api/builds/" + id + "/promote", { confirm: true, head: b.head }).then(function () { vBuild(id); }).catch(function (e) { out.textContent = ""; out.appendChild(err(e)); }); } }, "Promote (admin)"));
      var trips = (b.tripwire_pending || []).map(function (t) {
        return h("div", { class: "err" }, "Safety check blocked a test: " + t.file + " line " + t.line + " contains '" + t.word + "' (" + t.why + "): " + t.text,
          TB.me.admin && t.sha ? h("div", {}, h("button", { class: "small", onclick: function () { api("POST", "/api/tripwire/" + t.sha + "/letpass", {}).then(function () { vBuild(id); }).catch(function (e) { out.textContent = ""; out.appendChild(err(e)); }); } }, "Let it pass (this exact file)")) : h("div", { class: "muted", text: "The build kept going without it; an admin can let this exact file pass." }));
      });
      show(banner(), h("h1", { text: b.title }),
        h("div", { class: "row" }, statusPill(b.status), b.verdict ? statusPill(b.verdict) : null, pill(b.kind === "push" ? "push from own AI tool" : "AI build: " + (b.model || "")), pill((b.owner_email || b.owner) + " / " + b.sandbox)),
        h("p", { class: "muted", text: (b.last || "") + (b.steps ? " - step " + b.steps : "") + " - " + b.created }),
        actions, out, trips,
        (b.flags && b.flags.length) ? h("div", { class: "err", text: "Flags: " + b.flags.join("; ") }) : null,
        b.promoted_commit ? h("div", { class: "okbox", text: "Promoted into the team project as " + b.promoted_commit.slice(0, 10) + (b.team_main_tests ? " - team project tests after merge: " + (b.team_main_tests.ok ? "PASS" : "FAIL") : "") }) : null,
        h("h2", { text: "Task" }), h("pre", { text: b.task }),
        b.summary ? [h("h2", { text: "Summary" }), h("pre", { text: b.summary })] : null,
        h("h2", { text: "Files it changes in the team project" }),
        (b.files && b.files.length) ? h("ul", {}, b.files.map(function (f) { return h("li", {}, h("code", { text: f.status + " " + f.path })); })) : h("p", { class: "muted", text: "-" }),
        h("h2", { text: "Tests (run in the jail)" }), h("pre", { text: b.tests_text || "(not yet)" }),
        h("h2", { text: "Diff (what a promote would add)" }), fmtDiff(b.diff),
        (b.checkpoints && b.checkpoints.length) ? [h("h2", { text: "Checkpoints" }), h("ol", {}, b.checkpoints.map(function (c) { return h("li", { text: c.at + " - " + c.what + " (" + c.commit.slice(0, 8) + ")" }); }))] : null,
        (b.transcript && b.transcript.length) ? [h("h2", { text: "What the AI did" }), h("pre", { text: b.transcript.map(function (t) {
          return t.at + "  " + t.kind + (t.name ? " " + t.name : "") + (t.result ? " -> " + String(t.result).split("\n")[0].slice(0, 160) : "") + (t.why ? " : " + t.why : "") + (t.error ? " : " + t.error : "");
        }).join("\n") })] : null);
      if (/running|testing/.test(b.status) && location.hash === "#/build/" + id) pollT = setTimeout(function () { vBuild(id); }, 3000);
    }).catch(function (e) { show(banner(), err(e)); });
  }

  // ---- models, versions, chats
  function vModels() {
    Promise.all([api("GET", "/api/models"), api("GET", "/api/chats")]).then(function (r) {
      var m = r[0], chats = r[1].chats;
      var out = h("div");
      var parts = [h("h1", { text: "Models & chat" }), out];
      if (!m.ok) parts.push(h("div", { class: "err", text: "The team gateway said: " + (m.refused || "no answer") }));
      (m.notices || []).forEach(function (n) { parts.push(h("div", { class: "err", text: n.text || JSON.stringify(n) })); });
      if (m.models && m.models.length) {
        parts.push(h("div", { class: "grid" }, m.models.map(function (md) {
          var btns = h("div", { class: "row" });
          btns.appendChild(h("button", { class: "small", onclick: function () { newChat(md.id, verSel.value); } }, "New chat"));
          if (md.startable) {
            btns.appendChild(h("button", { class: "ghost small", onclick: function () { modelAct("start", md.id, out); } }, "Start"));
            btns.appendChild(h("button", { class: "ghost small", onclick: function () { modelAct("stop", md.id, out); } }, "Stop"));
          }
          var verSel = h("select", { "aria-label": "Settings version for " + md.name });
          api("GET", "/api/versions?model=" + encodeURIComponent(md.id)).then(function (v) {
            v.versions.forEach(function (x) { verSel.appendChild(h("option", { value: x.id, text: x.name })); });
          });
          return h("div", { class: "card" }, h("h3", { text: md.name }), h("div", { class: "row" }, pill(md.state), md.started_by_you ? pill("started by you") : null, md.ram_gb ? pill("~" + md.ram_gb + " GB") : null),
            h("label", { text: "Settings version" }), verSel, btns, h("div", {}, h("a", { href: "#/versions/" + encodeURIComponent(md.id), text: "Edit / save versions" })));
        })));
      } else if (m.ok) parts.push(h("p", { class: "muted", text: "No models are shared with your account yet." }));
      parts.push(h("h2", { text: "Your saved chats" }));
      parts.push(chats.length ? h("ul", {}, chats.map(function (c) { return h("li", {}, h("a", { href: "#/chat/" + c.id, text: c.title }), h("span", { class: "muted", text: " - " + c.model + " - " + c.updated })); })) : h("p", { class: "muted", text: "None yet." }));
      show.apply(null, parts);
    }).catch(function (e) { show(err(e)); });
  }
  function modelAct(act, id, out) {
    out.textContent = "";
    api("POST", "/api/models/" + act, { model: id }).then(function (r) {
      out.appendChild(r.ok ? h("div", { class: "okbox", text: act + ": " + JSON.stringify(r.answer) }) : h("div", { class: "err", text: "The team gateway refused: " + (r.refused || ("HTTP " + r.status)) }));
    }).catch(function (e) { out.appendChild(err(e)); });
  }
  function newChat(model, version) {
    api("POST", "/api/chats", { model: model, version: version || "original" }).then(function (r) { location.hash = "#/chat/" + r.chat.id; }).catch(function (e) { show(err(e)); });
  }
  function vChat(id) {
    api("GET", "/api/chats/" + id).then(function (r) {
      var c = r.chat, box = h("div"), input = h("textarea", { id: "chatInput", placeholder: "Message" }), btn;
      function paint() { box.textContent = ""; c.messages.forEach(function (m) {
        box.appendChild(h("div", { class: "msg " + m.role }, m.lumi ? h("div", { class: "msg-head muted", text: "Lumi" }) : null, linkify(m.content)));
      }); }
      paint();
      btn = h("button", { onclick: function () {
        var t = input.value; if (!t.trim()) return; btn.disabled = true; btn.textContent = "Waiting for the model...";
        api("POST", "/api/chats/" + id + "/send", { text: t }).then(function (j) { c = j.chat; input.value = ""; paint(); })
          .catch(function (e) { box.appendChild(err(e)); }).then(function () { btn.disabled = false; btn.textContent = "Send"; });
      } }, "Send");
      show(h("h1", { text: c.title }), h("p", { class: "muted", text: c.lumi ? "Lumi (Marjus's AI assistant) through the team's shared model " + c.model + ". Lumi never asks for your PIN or vault password. This chat is saved under your profile." : "Model: " + c.model + " - settings version: " + (c.version === "original" ? "Original" : c.version) + " - saved under your profile" }), box, h("label", { for: "chatInput", text: "Your message" }), input, h("div", { class: "row" }, btn, h("a", { href: "#/models", text: "Back" })));
    }).catch(function (e) { show(err(e)); });
  }
  // links in a chat message: only this site's own addresses and #/ routes become links; everything else stays text
  function linkify(text) {
    var out = h("div", { class: "mtext" }), rx = /(https?:\/\/[^\s)"'<>]+|#\/[A-Za-z0-9_\/\-.%@]+|\/how-it-works)/g, last = 0, m;
    var origin = location.origin, pub = (TB.me && TB.me.public_url || "").replace(/\/$/, "");
    text = String(text || "");
    while ((m = rx.exec(text))) {
      var u = m[0].replace(/[.,;:!?]+$/, ""), href = null;
      if (u.indexOf("#/") === 0 || u === "/how-it-works") href = u;
      else if (u.indexOf(origin + "/") === 0 || (pub && u.indexOf(pub + "/") === 0)) href = u.slice(u.indexOf("/", 8));
      out.appendChild(document.createTextNode(text.slice(last, m.index)));
      out.appendChild(href ? h("a", { href: href, text: u }) : document.createTextNode(u));
      last = m.index + u.length; rx.lastIndex = last;
    }
    out.appendChild(document.createTextNode(text.slice(last)));
    return out;
  }
  function vVersions(model) {
    api("GET", "/api/versions?model=" + encodeURIComponent(model)).then(function (v) {
      var out = h("div");
      var f = { name: h("input", { id: "vName", placeholder: "e.g. short answers" }), system_prompt: h("textarea", { id: "vSys" }), temperature: h("input", { id: "vTemp", inputmode: "decimal", placeholder: "model default" }),
        top_p: h("input", { id: "vTopP", inputmode: "decimal", placeholder: "model default" }), max_tokens: h("input", { id: "vMax", inputmode: "numeric", placeholder: "model default" }), ctx: h("input", { id: "vCtx", inputmode: "numeric", placeholder: "model default" }) };
      show(h("h1", { text: "Settings versions: " + model }),
        h("p", { class: "muted", text: "'Original' is the model's own settings and is always kept - you can always go back to it. Your versions are saved under your profile. The team gateway may lower values that are over this model's limits (it tells you)." }),
        h("div", { class: "tbl" }, h("table", {}, v.versions.map(function (x) {
          return h("tr", {}, h("td", {}, h("strong", { text: x.name })), h("td", {}, h("code", { text: JSON.stringify(x.settings) })), h("td", { text: x.created || x.note || "" }),
            h("td", {}, x.original ? pill("always kept") : h("button", { class: "ghost small", onclick: function () { api("POST", "/api/versions/delete", { model: model, id: x.id }).then(function () { vVersions(model); }).catch(function (e) { out.textContent = ""; out.appendChild(err(e)); }); } }, "Delete")),
            h("td", {}, h("button", { class: "small", onclick: function () { newChat(model, x.id); } }, "Chat with it")),
            h("td", {}, x.original || !TB.shareButton ? null : TB.shareButton({ kind: "version", model: model, version: x.id }, "version:" + TB.me.id + ":" + model + ":" + x.id)));
        }))),
        h("div", { class: "card" }, h("h3", { text: "Save a new version" }),
          h("label", { for: "vName", text: "Name" }), f.name, h("label", { for: "vSys", text: "System prompt" }), f.system_prompt,
          h("label", { for: "vTemp", text: "Temperature" }), f.temperature, h("label", { for: "vTopP", text: "top_p" }), f.top_p,
          h("label", { for: "vMax", text: "Max answer tokens" }), f.max_tokens, h("label", { for: "vCtx", text: "Context to keep (tokens)" }), f.ctx,
          h("p", { class: "muted", text: "Context: the gateway does not let the team box change a model's loaded context size; this setting keeps your chat history within that many tokens instead." }),
          h("div", { class: "row" }, h("button", { onclick: function () {
            var s = {}; ["system_prompt", "temperature", "top_p", "max_tokens", "ctx"].forEach(function (k) { if (f[k].value.trim() !== "") s[k] = f[k].value.trim(); });
            api("POST", "/api/versions", { model: model, name: f.name.value, settings: s }).then(function () { vVersions(model); }).catch(function (e) { out.textContent = ""; out.appendChild(err(e)); });
          } }, "Save version")), out),
        h("a", { href: "#/models", text: "Back to models" }));
    }).catch(function (e) { show(err(e)); });
  }

  // ---- bring your own AI tool + tokens
  function vByo() {
    api("GET", "/api/tokens").then(function (t) {
      var label = h("input", { id: "tokLabel", placeholder: "e.g. laptop Claude Code" }), out = h("div");
      show(banner(), h("h1", { text: "Use your own AI tool" }),
        h("ol", {}, h("li", { text: "Make a token below. It is shown once - copy it." }),
          h("li", {}, "Clone a project: ", h("code", { text: "git clone " + t.git_base + "<sandbox>.git" }), " (username: anything, password: the token)."),
          h("li", { text: "Open the folder in Claude Code, Codex, Cursor, OpenCode or any tool. The repo already holds CLAUDE.md, AGENTS.md, .cursorrules, opencode.json and others telling the tool it is in a TEAM SANDBOX and must stay inside it." }),
          h("li", { text: "git push - the team box runs your tests in the jail and lists the push under AI builds, where an admin can review and promote it." })),
        h("p", { class: "muted", text: "Those guardrail files are guidance only - the server's jail, the per-member token and admin-only promote are the real protection. A token reaches only your own projects." }),
        h("h2", { text: "Your git tokens" }),
        t.tokens.length ? h("div", { class: "tbl" }, h("table", {}, h("tr", {}, h("th", { text: "Label" }), h("th", { text: "Created" }), h("th", { text: "Last used" }), h("th", { text: "" })),
          t.tokens.map(function (k) { return h("tr", {}, h("td", { text: k.label }), h("td", { text: k.created }), h("td", { text: k.last_used || "never" }),
            h("td", {}, h("button", { class: "danger small", onclick: function () { if (confirm("Revoke '" + k.label + "'? Anything using it stops working.")) api("POST", "/api/tokens/" + k.id + "/revoke", {}).then(vByo).catch(function (e) { out.textContent = ""; out.appendChild(err(e)); }); } }, "Revoke"))); }))) : h("p", { class: "muted", text: "No tokens yet." }),
        h("div", { class: "card" }, h("label", { for: "tokLabel", text: "New token label" }), label, h("div", { class: "row" }, h("button", { onclick: function () {
          api("POST", "/api/tokens", { label: label.value }).then(function (r) {
            out.textContent = ""; var pre = h("pre", { id: "newToken", text: r.token });
            out.appendChild(h("div", { class: "okbox", text: "Your new token - copy it now, it will not be shown again:" })); out.appendChild(pre); out.appendChild(copyBtn(function () { return r.token; }, "Copy token"));
          }).catch(function (e) { out.textContent = ""; out.appendChild(err(e)); });
        } }, "Make token")), out));
    }).catch(function (e) { show(err(e)); });
  }

  function vCaps() {
    api("GET", "/api/models").then(function (m) {
      if (!m.ok) return show(h("h1", { text: "Your caps" }), h("div", { class: "err", text: "The team gateway said: " + (m.refused || "no answer") }));
      var c = m.your_caps || {}, t = m.team || {};
      show(h("h1", { text: "Your caps" }),
        h("p", { class: "muted", text: "Set by Marjus on BESNIK and enforced by the team gateway there. When it says no, you see its reason word for word." }),
        h("div", { class: "card" }, h("table", {},
          h("tr", {}, h("th", { text: "Model memory for your account" }), h("td", { text: (c.ram_gb_cap != null ? c.ram_gb_cap + " GB" : "-") })),
          h("tr", {}, h("th", { text: "Models you may run at once" }), h("td", { text: c.max_models != null ? String(c.max_models) : "-" })),
          h("tr", {}, h("th", { text: "Whole team now" }), h("td", { text: (t.models_running || 0) + " model(s), " + (t.ram_gb || 0) + " GB of " + (t.ram_gb_cap || "?") + " GB, at most " + (t.max_models || "?") + " model(s)" })),
          h("tr", {}, h("th", { text: "AI builds" }), h("td", { text: "one at a time per person" })))));
    }).catch(function (e) { show(err(e)); });
  }

  function profileFields() {
    var p = TB.me.profile || {};
    return { display_name: h("input", { id: "pName", value: TB.me.display_name || "", maxlength: "60" }), language: h("input", { id: "pLang", value: TB.me.language || "en", maxlength: "12" }),
      about: h("textarea", { id: "pAbout", maxlength: "600" }, p.about || ""), contact: h("input", { id: "pContact", value: p.contact || "", maxlength: "200" }),
      timezone: h("input", { id: "pTz", value: p.timezone || (Intl.DateTimeFormat().resolvedOptions().timeZone || ""), maxlength: "60" }) };
  }
  function profileForm(f) {
    return [h("label", { for: "pName", text: "Display name (what the team sees)" }), f.display_name, h("label", { for: "pLang", text: "Language (e.g. en, sq, it)" }), f.language,
      h("label", { for: "pAbout", text: "About you (optional)" }), f.about, h("label", { for: "pContact", text: "How the team can reach you outside the box (optional)" }), f.contact,
      h("label", { for: "pTz", text: "Time zone (optional)" }), f.timezone];
  }
  function vals(f) { var o = {}; Object.keys(f).forEach(function (k) { o[k] = f[k].value; }); return o; }
  function vProfile() {
    var f = profileFields(), out = h("div"), acc = TB.me.account;
    var oldPin = h("input", { type: "password", id: "pinOld", inputmode: "numeric", autocomplete: "current-password" }), newPin = h("input", { type: "password", id: "pinNew", inputmode: "numeric", autocomplete: "new-password" }), pout = h("div");
    show(h("h1", { text: "Profile" }), h("p", { text: (acc ? "Signed in as " + (acc.identifiers || []).join(", ") + " (account " + acc.id + ")" : TB.me.email) + " - " + (TB.me.admin ? "admin" : "team member") }),
      profileForm(f),
      h("div", { class: "row" }, h("button", { onclick: function () { api("POST", "/api/profile", vals(f)).then(function () { out.textContent = ""; out.appendChild(h("div", { class: "okbox", text: "Saved" })); boot(true); }).catch(function (e) { out.textContent = ""; out.appendChild(err(e)); }); } }, "Save"),
        h("a", { class: "btn ghost", href: "#/byo", text: "Git tokens" }), h("a", { class: "btn ghost", href: "#/vault", text: "Vault" }), h("a", { class: "btn ghost", href: "#/tour", text: "Open the tour again" })), out,
      acc ? h("div", { class: "card" }, h("h3", { text: "Signing in" }),
        h("p", { class: "muted", text: "You stay signed in until " + TB.me.session.idle_minutes + " minutes without activity, or " + TB.me.session.absolute_hours + " hours at most. Nobody - not Marjus, not Lumi - will ever ask for your PIN or vault password." }),
        h("div", { class: "row" }, h("button", { class: "ghost", type: "button", onclick: signOut }, "Sign out"),
          h("button", { class: "danger", type: "button", id: "signOutAll", onclick: function () {
            if (!confirm("Sign out on every device and browser?")) return;
            api("POST", "/api/auth/logout-all", {}).then(function () { location.hash = "#/"; location.reload(); });
          } }, "Sign out everywhere")),
        h("h3", { text: "Change PIN" }), h("label", { for: "pinOld", text: "Current PIN" }), oldPin, h("label", { for: "pinNew", text: "New PIN (digits, at least 6)" }), newPin,
        h("div", { class: "row" }, h("button", { class: "small", type: "button", onclick: function () {
          api("POST", "/api/auth/pin", { old: oldPin.value, new: newPin.value }).then(function () { pout.textContent = ""; pout.appendChild(h("div", { class: "okbox", text: "PIN changed" })); oldPin.value = newPin.value = ""; })
            .catch(function (e) { pout.textContent = ""; pout.appendChild(err(e)); });
        } }, "Change PIN")), pout) : null);
  }
  function signOut() { api("POST", "/api/auth/logout", {}).catch(function () {}).then(function () { location.hash = "#/"; location.reload(); }); }

  // ------------------------------------------------------------------ signed out: sign in, or first visit with the invite code
  function bare() { document.getElementById("nav").textContent = ""; document.getElementById("who").textContent = ""; }
  var NEVER = "Marjus and Lumi will never ask for your PIN or your vault password.";
  function vSignIn(why) {
    bare();
    var id = h("input", { id: "signinId", autocomplete: "username" }), pin = h("input", { type: "password", id: "signinPin", inputmode: "numeric", autocomplete: "current-password" }), out = h("div");
    show(h("h1", { text: "Sign in to the team box" }), why && !/not signed in/.test(why) ? h("p", { class: "muted", text: why }) : null,
      h("form", { class: "card", onsubmit: function (ev) {
        ev.preventDefault(); out.textContent = "";
        api("POST", "/api/auth/login", { identifier: id.value, pin: pin.value }).then(function () { location.hash = "#/"; boot(); }).catch(function (e) { out.appendChild(err(e)); pin.value = ""; });
      } }, h("label", { for: "signinId", text: "Your sign-in name (the phone number, email or name you chose)" }), id, h("label", { for: "signinPin", text: "PIN" }), pin,
        h("div", { class: "row" }, h("button", { type: "submit" }, "Sign in")), out),
      h("p", {}, "First time here? ", h("a", { href: "#/join", text: "I have an invite code" })),
      h("p", { class: "muted", text: NEVER + " Forgot your PIN? Ask Marjus for a new invite." }),
      h("p", {}, h("a", { href: "/how-it-works", text: "How this site works" })));
  }
  function vJoin() {
    bare();
    var code = h("input", { id: "joinCode", inputmode: "numeric", autocomplete: "one-time-code", placeholder: "1234 5678 9012", maxlength: "20" }), out = h("div");
    show(h("h1", { text: "Welcome to the team box" }), h("p", { text: "Lumi (Marjus's AI assistant) gave you a 12-digit invite code by email or on the phone. Type it here. It works once and expires." }),
      h("form", { class: "card", onsubmit: function (ev) {
        ev.preventDefault(); out.textContent = "";
        api("POST", "/api/auth/join", { code: code.value }).then(function (r) { vSetup(r.setup); }).catch(function (e) { out.appendChild(err(e)); });
      } }, h("label", { for: "joinCode", text: "Invite code" }), code, h("div", { class: "row" }, h("button", { type: "submit" }, "Continue")), out),
      h("p", { class: "muted", text: NEVER }), h("p", {}, h("a", { href: "#/signin", text: "I already have an account - sign in" })));
  }
  function vSetup(st) {
    bare();
    var sugg = st.suggestions || [];
    var id = h("input", { id: "setupId", value: sugg[0] || "", autocomplete: "username", list: "setupIdList" });
    var dl = h("datalist", { id: "setupIdList" }, sugg.map(function (s) { return h("option", { value: s }); }));
    var p1 = h("input", { type: "password", id: "setupPin", inputmode: "numeric", autocomplete: "new-password" }), p2 = h("input", { type: "password", id: "setupPin2", inputmode: "numeric", autocomplete: "new-password" }), out = h("div");
    show(h("h1", { text: "Hi " + (st.name || "") + "!" }), h("p", { text: "Marjus invited you to: " + (st.purpose || "") }),
      h("form", { class: "card", onsubmit: function (ev) {
        ev.preventDefault(); out.textContent = "";
        if (!/^\d{6,12}$/.test(p1.value)) return out.appendChild(err("your PIN must be 6 to 12 digits"));
        if (p1.value !== p2.value) return out.appendChild(err("the two PINs are not the same"));
        api("POST", "/api/auth/setup", { identifier: id.value, pin: p1.value, pin2: p2.value }).then(function () { location.hash = "#/setup/vault"; boot(); }).catch(function (e) { out.appendChild(err(e)); });
      } }, h("label", { for: "setupId", text: "Your sign-in name - your phone number, email or a name you will remember" }), id, dl,
        h("label", { for: "setupPin", text: "Choose a PIN - your sign-in code (digits, at least 6)" }), p1, h("label", { for: "setupPin2", text: "The PIN again" }), p2,
        h("div", { class: "row" }, h("button", { type: "submit" }, "Next: your vault")), out),
      h("p", { class: "muted", text: NEVER }));
  }
  function vSetupVault() {
    var el = h("div");
    show(h("h1", { text: "Step 2: your vault password" }), el);
    if (!TB.vaultSetup) return el.appendChild(err("the vault page did not load"));
    TB.vaultSetup(el, function () { boot(); });
  }
  function vSetupProfile() {
    var f = profileFields(), out = h("div");
    show(h("h1", { text: "Step 3: about you (optional)" }), h("p", { class: "muted", text: "Fill in what you like - or skip it. You can change it later on your Profile." }),
      profileForm(f), h("div", { class: "row" },
        h("button", { onclick: function () { api("POST", "/api/setup/profile", vals(f)).then(function () { boot(); }).catch(function (e) { out.appendChild(err(e)); }); } }, "Save and continue"),
        h("button", { class: "ghost", id: "profileSkip", onclick: function () { api("POST", "/api/setup/profile", { skip: true }).then(function () { boot(); }).catch(function (e) { out.appendChild(err(e)); }); } }, "Skip")), out);
  }
  function vWelcome() {
    show(h("h1", { text: "Lumi is saying hello..." }), h("p", { class: "muted", text: "Your first project is ready; Lumi is writing your welcome message." }));
    api("POST", "/api/welcome", {}).then(function (r) { TB.me.setup_next = null; location.hash = "#/chat/" + r.chat.id; }).catch(function (e) { show(err(e)); });
  }

  // ------------------------------------------------------------------ admin: invites + accounts
  function vInvites() {
    if (!TB.me.admin) return show(err("Admins only"));
    Promise.all([api("GET", "/api/admin/invites"), api("GET", "/api/admin/accounts")]).then(function (r) {
      var f = { name: h("input", { id: "invName" }), phone: h("input", { id: "invPhone", inputmode: "tel" }), email: h("input", { id: "invEmail", inputmode: "email" }),
        purpose: h("textarea", { id: "invPurpose", placeholder: "what they are here to do - Lumi welcomes them with it and names their first project from it" }), language: h("input", { id: "invLang", value: "en" }) };
      var out = h("div");
      show(h("h1", { text: "Admin: invites & accounts" }),
        h("p", { class: "muted", text: "Lumi can do this for you (\"invite <name>, <phone/email>, they're here to <purpose>\"). The code is shown once; the person types it at /join, then sets their PIN and vault password." }),
        h("form", { class: "card", onsubmit: function (ev) {
          ev.preventDefault(); out.textContent = "";
          api("POST", "/api/admin/invites", vals(f)).then(function (j) {
            out.appendChild(h("div", { class: "okbox" }, "Invite for " + j.name + " - code (shown only now): ", h("strong", { id: "newInviteCode", text: j.code }), " - " + j.link + " - expires " + j.expires));
          }).catch(function (e) { out.appendChild(err(e)); });
        } }, h("h3", { text: "New invite" }), h("label", { for: "invName", text: "Name" }), f.name, h("label", { for: "invPhone", text: "Phone (optional)" }), f.phone,
          h("label", { for: "invEmail", text: "Email (optional)" }), f.email, h("label", { for: "invPurpose", text: "What are they here to do?" }), f.purpose,
          h("label", { for: "invLang", text: "Language" }), f.language, h("div", { class: "row" }, h("button", { type: "submit" }, "Make invite code")), out),
        h("h2", { text: "Invites" }),
        h("div", { class: "tbl" }, h("table", {}, h("tr", {}, ["Name", "Account", "Status", "Expires", "Purpose", ""].map(function (t) { return h("th", { text: t }); })),
          r[0].invites.map(function (i) { return h("tr", {}, h("td", { text: i.name }), h("td", { text: i.account }), h("td", {}, pill(i.status, i.status === "used" ? "ok" : /locked|revoked|expired/.test(i.status) ? "bad" : "")),
            h("td", { text: i.expires }), h("td", { text: i.purpose }),
            h("td", {}, /pending|redeemed/.test(i.status) ? h("button", { class: "danger small", onclick: function () { api("POST", "/api/admin/invites/" + i.id + "/revoke", {}).then(vInvites).catch(function (e) { out.textContent = ""; out.appendChild(err(e)); }); } }, "Revoke") : null)); }))),
        h("h2", { text: "Accounts" }),
        h("div", { class: "tbl" }, h("table", {}, h("tr", {}, ["Account", "Sign-in name", "Role", "Status", "Sessions", ""].map(function (t) { return h("th", { text: t }); })),
          r[1].accounts.map(function (a) {
            function act(x) { return h("button", { class: "ghost small", onclick: function () { api("POST", "/api/admin/accounts/" + encodeURIComponent(a.id) + "/" + x, {}).then(vInvites).catch(function (e) { show(err(e)); }); } }, x); }
            return h("tr", {}, h("td", { text: a.id + " (" + a.name + ")" }), h("td", { text: (a.identifiers || []).join(", ") }), h("td", { text: a.role }),
              h("td", {}, pill(a.locked ? "locked" : a.status, a.status === "active" && !a.locked ? "ok" : "bad")), h("td", { text: String(a.sessions) }),
              h("td", {}, a.status === "active" ? act("disable") : act("enable"), a.locked ? act("unlock") : null, act("signout")));
          }))));
    }).catch(function (e) { show(err(e)); });
  }

  function vAdmin() {
    if (!TB.me.admin) return show(err("Admins only"));
    Promise.all([api("GET", "/api/builds?all=1"), api("GET", "/api/admin/members"), api("GET", "/api/admin/team-main"), api("GET", "/api/tripwire")]).then(function (r) {
      var waiting = r[0].builds.filter(function (b) { return b.status === "waiting for review"; });
      show(h("h1", { text: "Admin: review & promote" }),
        h("h2", { text: "Waiting for review" }), waiting.length ? h("div", { class: "tbl" }, buildsTable(waiting)) : h("p", { class: "muted", text: "Nothing waiting." }),
        h("h2", { text: "Safety check: blocked tests" }), r[3].pending.length ? h("ul", {}, r[3].pending.map(function (p) { return h("li", {}, h("a", { href: "#/build/" + p.build, text: p.owner + ": " + p.file + " line " + p.line + " '" + p.word + "' (" + p.why + ")" })); })) : h("p", { class: "muted", text: "None." }),
        h("h2", { text: "Members" }), h("div", { class: "tbl" }, h("table", {}, r[1].members.map(function (m) { return h("tr", {}, h("td", { text: m.email }), h("td", { text: m.admin ? "admin" : "team" }), h("td", { text: m.sandboxes.join(", ") })); }))),
        h("h2", { text: "Team project (data/team-main)" }), h("p", { class: "muted", text: r[2].files.length + " files, head " + r[2].head.slice(0, 10) }),
        h("div", { class: "tbl" }, h("table", {}, r[2].log.map(function (l) { return h("tr", {}, h("td", {}, h("code", { text: l[0] })), h("td", { text: l[1] }), h("td", { text: l[3] })); }))),
        h("h2", { text: "All builds" }), h("div", { class: "tbl" }, buildsTable(r[0].builds)));
    }).catch(function (e) { show(err(e)); });
  }

  function vModule(id) {
    var m = (TB.modules || []).filter(function (x) { return x.id === id; })[0];
    if (!m) return show(err("No such area"));
    if (TB.views[id] && m.status === "live") return TB.views[id](m);
    show(h("h1", { text: m.title }), pill(m.status), h("p", { text: m.summary }), h("p", { text: m.help }),
      m.notice ? h("div", { class: "card" }, h("h3", { text: "Team notice (you will be asked to accept it before first use)" }), h("p", { class: "notice-text", id: "notice-text", text: m.notice })) : null);
  }

  // ------------------------------------------------------------------ router
  function route() {
    clearTimeout(pollT);
    document.getElementById("nav").classList.remove("open");
    markNav();
    var p = (location.hash || "#/").slice(1).split("/").filter(Boolean).map(decodeURIComponent);
    var v = p[0] || "";
    if (v === "") return vHome();
    if (v === "tour") return vTour();
    if (TB.me.setup_next) {                                         // first visit of an invited account: finish the steps first
      var want = TB.me.setup_next === "welcome" ? "welcome" : "setup/" + TB.me.setup_next;
      if ((v === "setup" ? "setup/" + p[1] : v) !== want) { location.hash = "#/" + want; return; }
    }
    if (v === "setup" && p[1] === "vault") return vSetupVault();
    if (v === "setup" && p[1] === "profile") return vSetupProfile();
    if (v === "welcome") return vWelcome();
    if (v === "join" || v === "signin") { location.hash = "#/"; return; }
    if (v === "projects" || v === "sandboxes") return vSandboxes();          // old #/sandbox... addresses keep working
    if ((v === "project" || v === "sandbox") && p[2]) return vSandbox(p[1], p[2]);
    if (v === "vault") return TB.views.vault ? TB.views.vault() : show(err("the vault page did not load"));
    if (v === "admin" && p[1] === "invites") return vInvites();
    if (v === "builds") return vBuilds();
    if (v === "build" && p[1]) return vBuild(p[1]);
    if (v === "models") return vModels();
    if (v === "versions" && p[1]) return vVersions(p[1]);
    if (v === "chat" && p[1]) return vChat(p[1]);
    if (v === "byo") return vByo();
    if (v === "caps") return vCaps();
    if (v === "profile") return vProfile();
    if (v === "admin") return vAdmin();
    if (v === "m" && p[1]) return vModule(p[1]);
    show(err("Not found"));
  }

  function boot(quiet) {
    return Promise.all([api("GET", "/api/me"), api("GET", "/api/modules")]).then(function (r) {
      TB.me = r[0]; TB.modules = r[1].modules; TB.shares = null;
      document.getElementById("who").textContent = (TB.me.display_name || TB.me.email) + (TB.me.admin ? " (admin)" : "");
      renderNav(TB.modules);
      if (quiet) return;
      if (TB.me.first_visit && !TB.me.setup_next && !TB.me.account && (location.hash || "#/") === "#/") location.hash = "#/tour";
      else route();
    }).catch(function (e) {
      if (e.status !== 401) return show(err(e), h("p", {}, h("a", { href: "/how-it-works", text: "How this site works" })));
      TB.me = null;
      var hp = (location.hash || "").slice(2);
      if (location.pathname === "/join" || hp === "join") return api("GET", "/api/auth/state").then(function (s) { return s.setup ? vSetup(s.setup) : vJoin(); }).catch(function () { vJoin(); });
      api("GET", "/api/auth/state").then(function (s) { if (s.setup) vSetup(s.setup); else vSignIn(e.message); }).catch(function () { vSignIn(e.message); });
    });
  }

  function theme() {
    var t = null; try { t = localStorage.getItem("tb-theme"); } catch (e) {}
    if (t) document.documentElement.setAttribute("data-theme", t);
  }
  document.addEventListener("DOMContentLoaded", function () {
    theme();
    document.getElementById("themeBtn").addEventListener("click", function () {
      var cur = document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      var nx = cur === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", nx);
      try { localStorage.setItem("tb-theme", nx); } catch (e) {}
    });
    var mb = document.getElementById("menuBtn");
    mb.addEventListener("click", function () { var n = document.getElementById("nav"); n.classList.toggle("open"); mb.setAttribute("aria-expanded", n.classList.contains("open")); });
    window.addEventListener("hashchange", function () {
      if (TB.me) return route();
      var hp = (location.hash || "").slice(2);
      if (hp === "join") vJoin(); else if (hp === "signin") vSignIn();
    });
    boot();
  });
})();
