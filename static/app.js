/* Team box UI - plain JS, no framework. Hash routes: #/ #/tour #/sandboxes #/sandbox/<owner>/<name> #/builds #/build/<id>
   #/models #/chat/<id> #/byo #/caps #/profile #/admin #/m/<module>.  Every DOM node is built with h() (text, never innerHTML),
   so nothing a member or a model writes can run as code. Other agents add views for modules via TB.views[<module id>]. */
(function () {
  "use strict";
  var TB = window.TB = { views: {}, me: null };
  var BANNER = "TEAM SANDBOX - this is the team's shared workspace on the team computer. Not your computer, not Marjus's computer.";

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
  function banner() { return h("div", { class: "banner", role: "note" }, BANNER, h("small", { text: "Only you and the team's admins can see your sandboxes. Tests run in a locked jail with no network." })); }
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
    ["#/", "Home"], ["#/tour", "Tour"], ["#/sandboxes", "Sandboxes"], ["#/builds", "AI builds"], ["#/models", "Models & chat"],
    ["#/byo", "Your own AI tool"], ["#/caps", "Your caps"], ["#/profile", "Profile"]
  ];
  function renderNav(mods) {
    var nav = document.getElementById("nav"); nav.textContent = "";
    NAV.forEach(function (n) { nav.appendChild(h("a", { href: n[0], text: n[1] })); });
    nav.appendChild(h("h4", { text: "Team" }));
    (mods || []).forEach(function (m) {
      nav.appendChild(h("a", { href: "#/m/" + m.id }, m.nav, m.status !== "live" ? h("span", { class: "soon", text: "coming soon" }) : null));
    });
    if (TB.me && TB.me.admin) { nav.appendChild(h("h4", { text: "Admin" })); nav.appendChild(h("a", { href: "#/admin", text: "Admin: review & promote" })); }
    nav.appendChild(h("h4", { text: "Help" }));
    nav.appendChild(h("a", { href: "/how-it-works", text: "How it works" }));
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
        tile("Sandboxes", "Your private project folders (git repos).", "#/sandboxes"),
        tile("AI builds", "Let a shared AI build something in a sandbox.", "#/builds"),
        tile("Models & chat", "Chat, start/stop models, save your own settings versions.", "#/models"),
        tile("Your own AI tool", "Connect Claude Code, Codex, Cursor or OpenCode.", "#/byo")),
      h("div", { class: "card" }, h("h3", { text: "Home feed" }), h("p", { class: "muted", text: "Posts with attachments (videos, pictures, notes, spreadsheets) - coming soon." })),
      TB.me.dev_mode ? h("div", { class: "err", text: "DEV MODE is on: identity comes from a test header/cookie, not Cloudflare Access. Never on the real box." }) : null);
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
      show(h("h1", { text: "What this site is" }), h("p", { text: t.intro }),
        h("div", { class: "progress", "aria-label": done.length + " of " + t.checkpoints.length + " checkpoints visited" }, bar),
        h("p", { class: "muted", text: done.length + " of " + t.checkpoints.length + " checkpoints visited - click each to see that area." }),
        list,
        h("p", { class: "bottom-text", id: "tour-bottom-text", text: t.bottom_text }),
        h("div", { class: "copybox card" }, h("div", { class: "row" }, h("strong", { text: "Text for your trusted AI" }), copyBtn(function () { return t.prompt; }, "Copy text")), ta),
        h("div", { class: "row" }, h("button", { onclick: function () { api("POST", "/api/tour", { seen: true }).then(function () { location.hash = "#/"; }); } }, "Got it - go to Home"),
          h("button", { class: "ghost", onclick: function () { api("POST", "/api/tour", { reset: true }).then(vTour); } }, "Reset progress")));
      api("POST", "/api/tour", { seen: true });
    }).catch(function (e) { show(err(e)); });
  }

  function vSandboxes() {
    var q = TB.me.admin ? "?all=1" : "";
    api("GET", "/api/sandboxes" + q).then(function (j) {
      var name = h("input", { placeholder: "e.g. weather-bot", maxlength: "40", id: "newSandboxName" });
      var out = h("div");
      show(banner(), h("h1", { text: TB.me.admin ? "All sandboxes (admin view)" : "Your sandboxes" }),
        h("div", { class: "tbl" }, h("table", {}, h("tr", {}, h("th", { text: "Sandbox" }), TB.me.admin ? h("th", { text: "Owner" }) : null, h("th", { text: "Created" }), h("th", { text: "" })),
          j.sandboxes.map(function (s) {
            return h("tr", {}, h("td", {}, h("a", { href: "#/sandbox/" + s.owner + "/" + s.name, text: s.label || s.name })),
              TB.me.admin ? h("td", { text: s.owner_email || s.owner }) : null, h("td", { text: s.created || "" }), h("td", {}, s.busy ? pill(s.busy) : null));
          }))),
        h("div", { class: "card" }, h("h3", { text: "New sandbox" }), h("label", { for: "newSandboxName", text: "Name" }), name,
          h("div", { class: "row" }, h("button", { onclick: function () {
            api("POST", "/api/sandboxes", { name: name.value }).then(function (r) { location.hash = "#/sandbox/" + r.sandbox.owner + "/" + r.sandbox.name; })
              .catch(function (e) { out.textContent = ""; out.appendChild(err(e)); });
          } }, "Create")), out,
          h("p", { class: "muted", text: "Every new sandbox starts from the clean starter template (README, guardrail files, an example) - never anyone's own files." })));
    }).catch(function (e) { show(err(e)); });
  }

  function vSandbox(owner, name) {
    Promise.all([api("GET", "/api/sandboxes/" + owner + "/" + name), api("GET", "/api/models").catch(function () { return { models: [] }; })]).then(function (r) {
      var s = r[0], models = r[1].models || [];
      var task = h("textarea", { id: "buildTask", placeholder: "What should the AI build? e.g. 'add a function that converts Celsius to Fahrenheit, with a test'" });
      var sel = h("select", { id: "buildModel" }, models.map(function (m) { return h("option", { value: m.id, text: m.name + " (" + m.state + ")" }); }));
      var out = h("div"), fileBox = h("div"), testOut = h("div");
      var mine = owner === TB.me.id;
      show(banner(), h("h1", { text: "Sandbox: " + (s.label || s.name) }),
        h("p", { class: "muted", text: "Owner: " + (s.owner_email || s.owner) + (s.busy ? " - busy: " + s.busy : "") + " - " + s.size_mb + " MB" }),
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
    return h("table", {}, h("tr", {}, h("th", { text: "Build" }), h("th", { text: "Kind" }), h("th", { text: "Sandbox" }), h("th", { text: "Status" }), h("th", { text: "Verdict" }), h("th", { text: "When" })),
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
        j.builds.length ? h("div", { class: "tbl" }, buildsTable(j.builds)) : h("p", {}, "No builds yet. Open a ", h("a", { href: "#/sandboxes", text: "sandbox" }), " and start one."));
    }).catch(function (e) { show(err(e)); });
  }

  var pollT = null;
  function vBuild(id) {
    clearTimeout(pollT);
    api("GET", "/api/builds/" + id).then(function (b) {
      var out = h("div");
      var actions = h("div", { class: "row" });
      if (b.status === "running" && (b.owner === TB.me.id || TB.me.admin)) actions.appendChild(h("button", { class: "ghost", onclick: function () { api("POST", "/api/builds/" + id + "/stop", {}).then(function () { vBuild(id); }); } }, "Stop"));
      if (b.kind === "ai" && b.owner === TB.me.id && !b.applied && /waiting|nothing|rejected|promoted/.test(b.status)) actions.appendChild(h("button", { class: "ghost", onclick: function () {
        api("POST", "/api/builds/" + id + "/apply", {}).then(function () { vBuild(id); }).catch(function (e) { out.textContent = ""; out.appendChild(err(e)); }); } }, "Apply to my sandbox"));
      if (b.can_reject) actions.appendChild(h("button", { class: "ghost", onclick: function () {
        if (!confirm("Reject this build?")) return;
        api("POST", "/api/builds/" + id + "/reject", {}).then(function () { vBuild(id); }).catch(function (e) { out.textContent = ""; out.appendChild(err(e)); }); } }, "Reject"));
      if (b.can_promote) actions.appendChild(h("button", { onclick: function () {
        if (!confirm("Promote " + (b.files || []).length + " file(s) into the shared team project?\n\n" + (b.files || []).map(function (f) { return f.status + " " + f.path; }).join("\n") + "\n\nFlags: " + ((b.flags || []).join("; ") || "none"))) return;
        api("POST", "/api/builds/" + id + "/promote", { confirm: true, head: b.head }).then(function () { vBuild(id); }).catch(function (e) { out.textContent = ""; out.appendChild(err(e)); }); } }, "Promote (admin)"));
      var trips = (b.tripwire_pending || []).map(function (t) {
        return h("div", { class: "err" }, "Safety check blocked a test: " + t.file + " line " + t.line + " contains '" + t.word + "' (" + t.why + "): " + t.text,
          TB.me.admin && t.sha ? h("div", {}, h("button", { class: "small", onclick: function () { api("POST", "/api/tripwire/" + t.sha + "/letpass", {}).then(function () { vBuild(id); }); } }, "Let it pass (this exact file)")) : h("div", { class: "muted", text: "The build kept going without it; an admin can let this exact file pass." }));
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
      function paint() { box.textContent = ""; c.messages.forEach(function (m) { box.appendChild(h("div", { class: "msg " + m.role, text: m.content })); }); }
      paint();
      btn = h("button", { onclick: function () {
        var t = input.value; if (!t.trim()) return; btn.disabled = true; btn.textContent = "Waiting for the model...";
        api("POST", "/api/chats/" + id + "/send", { text: t }).then(function (j) { c = j.chat; input.value = ""; paint(); })
          .catch(function (e) { box.appendChild(err(e)); }).then(function () { btn.disabled = false; btn.textContent = "Send"; });
      } }, "Send");
      show(h("h1", { text: c.title }), h("p", { class: "muted", text: "Model: " + c.model + " - settings version: " + (c.version === "original" ? "Original" : c.version) + " - saved under your profile" }), box, h("label", { for: "chatInput", text: "Your message" }), input, h("div", { class: "row" }, btn, h("a", { href: "#/models", text: "Back" })));
    }).catch(function (e) { show(err(e)); });
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
            h("td", {}, x.original ? pill("always kept") : h("button", { class: "ghost small", onclick: function () { api("POST", "/api/versions/delete", { model: model, id: x.id }).then(function () { vVersions(model); }); } }, "Delete")),
            h("td", {}, h("button", { class: "small", onclick: function () { newChat(model, x.id); } }, "Chat with it")));
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
          h("li", {}, "Clone a sandbox: ", h("code", { text: "git clone " + t.git_base + "<sandbox>.git" }), " (username: anything, password: the token)."),
          h("li", { text: "Open the folder in Claude Code, Codex, Cursor, OpenCode or any tool. The repo already holds CLAUDE.md, AGENTS.md, .cursorrules, opencode.json and others telling the tool it is in a TEAM SANDBOX and must stay inside it." }),
          h("li", { text: "git push - the team box runs your tests in the jail and lists the push under AI builds, where an admin can review and promote it." })),
        h("p", { class: "muted", text: "Those guardrail files are guidance only - the server's jail, the per-member token and admin-only promote are the real protection. A token reaches only your own sandboxes." }),
        h("p", { class: "muted", text: "If Cloudflare Access asks for a browser login on git, ask an admin: the /git/ path is meant to use your token instead." }),
        h("h2", { text: "Your git tokens" }),
        t.tokens.length ? h("div", { class: "tbl" }, h("table", {}, h("tr", {}, h("th", { text: "Label" }), h("th", { text: "Created" }), h("th", { text: "Last used" }), h("th", { text: "" })),
          t.tokens.map(function (k) { return h("tr", {}, h("td", { text: k.label }), h("td", { text: k.created }), h("td", { text: k.last_used || "never" }),
            h("td", {}, h("button", { class: "danger small", onclick: function () { if (confirm("Revoke '" + k.label + "'? Anything using it stops working.")) api("POST", "/api/tokens/" + k.id + "/revoke", {}).then(vByo); } }, "Revoke"))); }))) : h("p", { class: "muted", text: "No tokens yet." }),
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

  function vProfile() {
    var dn = h("input", { id: "pName", value: TB.me.display_name || "" }), lang = h("input", { id: "pLang", value: TB.me.language || "en" }), out = h("div");
    show(h("h1", { text: "Profile" }), h("p", { text: TB.me.email + " - " + (TB.me.admin ? "admin" : "team member") }),
      h("label", { for: "pName", text: "Display name" }), dn, h("label", { for: "pLang", text: "Language (e.g. en, sq, it)" }), lang,
      h("div", { class: "row" }, h("button", { onclick: function () { api("POST", "/api/profile", { display_name: dn.value, language: lang.value }).then(function () { out.textContent = ""; out.appendChild(h("div", { class: "okbox", text: "Saved" })); boot(true); }).catch(function (e) { out.textContent = ""; out.appendChild(err(e)); }); } }, "Save"),
        h("a", { class: "btn ghost", href: "#/byo", text: "Git tokens" }), h("a", { class: "btn ghost", href: "#/tour", text: "Open the tour again" })), out);
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
    if (v === "sandboxes") return vSandboxes();
    if (v === "sandbox" && p[2]) return vSandbox(p[1], p[2]);
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
      TB.me = r[0]; TB.modules = r[1].modules;
      document.getElementById("who").textContent = (TB.me.display_name || TB.me.email) + (TB.me.admin ? " (admin)" : "");
      renderNav(TB.modules);
      if (quiet) return;
      if (TB.me.first_visit && (location.hash || "#/") === "#/") location.hash = "#/tour";
      else route();
    }).catch(function (e) { show(err(e), h("p", {}, h("a", { href: "/how-it-works", text: "How this site works" }))); });
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
    window.addEventListener("hashchange", route);
    boot();
  });
})();
