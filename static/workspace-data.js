/* Workspace reads and Lumi chat over the existing Team box API. No DOM, setup,
   model starts, or background jobs are created here. Resource status describes
   the request, not the health of a model or worker. */
(function () {
  "use strict";
  var TB = window.TB;
  if (!TB || typeof TB.api !== "function") throw new Error("Load workspace-data.js after app.js");

  var account = null, generation = 0, pendingSend = null, listeners = [];
  function resource() { return { status: "idle", data: null, fetchedAt: null, attemptedAt: null, error: null, errorStatus: null, stale: false }; }
  function emptyState() { return { builds: resource(), sandboxes: resource(), models: resource(), backgroundQueueConnected: false }; }
  var state = emptyState();
  function copy(value) { return JSON.parse(JSON.stringify(value)); }
  function text(value, limit) { return value == null ? "" : String(value).replace(/\u0000/g, "").slice(0, limit || 2000); }
  function count(value) { return typeof value === "number" && isFinite(value) && value >= 0 ? value : null; }
  function identity() { return TB.me && TB.me.id ? String(TB.me.id) : null; }
  function error(message, status) { var e = new Error(message); if (status != null) e.status = status; return e; }
  function problem(e) { return text(e && e.message ? e.message : e || "The request failed.", 1000); }
  function snapshot() { return copy(state); }
  function notify() { listeners.slice().forEach(function (fn) { try { fn(snapshot()); } catch (_) { /* A view cannot break a request. */ } }); }
  function ensureAccount() {
    var next = identity();
    if (account !== next) { account = next; generation++; state = emptyState(); }
    return next;
  }
  function current(owner, token) { return identity() === owner && account === owner && generation === token; }
  function requireList(payload, key) {
    if (!payload || !Array.isArray(payload[key])) throw error("The server returned an invalid " + key + " response.");
    return payload[key];
  }
  function statusGroup(value) {
    var s = text(value).trim().toLowerCase();
    if (/^(running|testing|queued)$/.test(s)) return "active";
    if (s === "waiting for review") return "review";
    if (/^(promoted|nothing to promote)$/.test(s)) return "done";
    if (/^(error|interrupted|rejected|failed)$/.test(s)) return "attention";
    return "unknown";
  }
  function builds(payload) {
    return { builds: requireList(payload, "builds").map(function (b) {
      b = b || {};
      return { id: text(b.id, 80), kind: text(b.kind, 40), owner: text(b.owner, 120), owner_email: text(b.owner_email, 160),
        sandbox: text(b.sandbox, 80), title: text(b.title, 300), status: text(b.status, 100), statusGroup: statusGroup(b.status),
        verdict: text(b.verdict, 300), created: text(b.created, 80), updated: text(b.updated, 80), model: text(b.model, 120),
        steps: count(b.steps), files_changed: count(b.files_changed), last: text(b.last, 2000) };
    }), running: count(payload.running), max_total: count(payload.max_total) };
  }
  function sandboxes(payload) {
    return { sandboxes: requireList(payload, "sandboxes").map(function (s) {
      s = s || {};
      return { owner: text(s.owner, 120), owner_email: text(s.owner_email, 160), name: text(s.name, 80), label: text(s.label, 120),
        created: text(s.created, 80), busy: text(s.busy, 300), git_path: text(s.git_path, 300) };
    }) };
  }
  function modelState(value) {
    var s = text(value).trim().toLowerCase();
    if (/^(running|ready|on|up)$/.test(s)) return "running";
    if (/^(loading|starting)$/.test(s)) return "loading";
    if (/^(off|stopped|down)$/.test(s)) return "stopped";
    if (/^paused(?:\s|$)/.test(s)) return "paused";
    return "unknown";
  }
  function models(payload) {
    if (payload && payload.ok === false) throw error(text(payload.refused, 1000) || "The shared-model gateway did not answer.", payload.status);
    var list = requireList(payload, "models"), caps = payload.your_caps || {}, team = payload.team || {};
    return { ok: true, models: list.map(function (m) {
      m = m || {};
      return { id: text(m.id, 120), name: text(m.name, 200), state: text(m.state, 200), stateGroup: modelState(m.state),
        startable: m.startable === true, ram_gb: count(m.ram_gb), started_by_team: m.started_by_team === true, started_by_you: m.started_by_you === true };
    }), notices: (Array.isArray(payload.notices) ? payload.notices : []).map(function (n) { return { text: text(n && n.text != null ? n.text : n, 2000) }; }),
      your_caps: { ram_gb_cap: count(caps.ram_gb_cap), max_models: count(caps.max_models) },
      team: { models_running: count(team.models_running), ram_gb: count(team.ram_gb), max_models: count(team.max_models), ram_gb_cap: count(team.ram_gb_cap) } };
  }

  /* Each source succeeds or fails independently. Cached results keep their last
     successful fetchedAt and become stale while refreshing or after failure.
     The shared running count is global; rows are private unless all:true was
     explicitly requested by an admin. There is no queue ETA or progress %. */
  function load(options) {
    var owner = ensureAccount(), token = ++generation;
    if (!owner) return Promise.reject(error("Sign in to load your workspace.", 401));
    var all = !!(options && options.all && TB.me.admin), suffix = all ? "?all=1" : "";
    var specs = [["builds", "/api/builds" + suffix, builds], ["sandboxes", "/api/sandboxes" + suffix, sandboxes], ["models", "/api/models", models]];
    specs.forEach(function (spec) {
      var r = state[spec[0]];
      r.status = "loading"; r.error = null; r.errorStatus = null; r.attemptedAt = new Date().toISOString(); r.stale = r.data !== null;
    });
    notify();
    return Promise.allSettled(specs.map(function (spec) {
      return Promise.resolve().then(function () { return TB.api("GET", spec[1]); }).then(function (payload) {
        var data = spec[2](payload);
        if (spec[0] !== "models") data.scope = all ? "all" : "mine";
        if (current(owner, token)) {
          var r = state[spec[0]];
          r.status = "ready"; r.data = data; r.fetchedAt = new Date().toISOString(); r.error = null; r.errorStatus = null; r.stale = false;
          notify();
        }
        return data;
      }).catch(function (e) {
        if (current(owner, token)) {
          var r = state[spec[0]];
          r.status = "error"; r.error = problem(e); r.errorStatus = e && typeof e.status === "number" ? e.status : null; r.stale = r.data !== null;
          notify();
        }
        throw e;
      });
    })).then(function () {
      if (!current(owner, token)) throw error("The workspace changed while it was loading.");
      return snapshot();
    });
  }

  function safeChat(chat, expectedId) {
    if (!chat || chat.id !== expectedId || !Array.isArray(chat.messages)) throw error("The server returned an invalid chat response.");
    return { id: text(chat.id, 80), title: text(chat.title, 300), model: text(chat.model, 120), version: text(chat.version, 120),
      lumi: chat.lumi === true, created: text(chat.created, 80), updated: text(chat.updated, 80), messages: chat.messages.filter(function (m) {
        return m && /^(user|assistant|notice)$/.test(m.role);
      }).map(function (m) { return { role: m.role, content: text(m.content, 300000), at: text(m.at, 80), lumi: m.lumi === true, fallback: m.fallback === true }; }) };
  }
  function chatResult(payload, cid) {
    var chat = safeChat(payload && payload.chat, cid), reason = "";
    if (!chat.lumi) reason = "This saved conversation is not a Lumi welcome chat.";
    else if (!chat.model || /^\(\s*no\s*model\s*\)$/i.test(chat.model)) reason = "No shared model is connected to this Lumi chat.";
    var assistant = chat.messages.filter(function (m) { return m.role === "assistant"; }).slice(-1)[0];
    return { status: reason ? "unavailable" : "ready", chat: chat, canSend: !reason, unavailableReason: reason || null,
      fallback: !!(assistant && assistant.fallback), refused: null, error: null, responseStatus: null,
      version_name: text(payload.version_name, 200), version_missing: payload.version_missing === true };
  }
  function chatError(e) { return { status: "error", chat: null, canSend: false, unavailableReason: null, fallback: false, refused: null, error: problem(e), responseStatus: e && typeof e.status === "number" ? e.status : null }; }
  function getLumiChat() {
    var owner = ensureAccount(), cid = TB.me && TB.me.welcome_chat;
    if (!owner) return Promise.resolve(chatError(error("Sign in to open your Lumi chat.", 401)));
    if (!cid) return Promise.resolve({ status: "empty", chat: null, canSend: false, unavailableReason: "Your account has no Lumi welcome chat yet.", fallback: false, refused: null, error: null, responseStatus: null });
    return Promise.resolve().then(function () { return TB.api("GET", "/api/chats/" + encodeURIComponent(cid)); }).then(function (payload) {
      if (identity() !== owner || !TB.me || TB.me.welcome_chat !== cid) throw error("The account changed while the chat was loading.");
      return chatResult(payload, cid);
    }).catch(chatError);
  }
  function sendLumi(value) {
    var message = text(value, 20001).trim(), owner = ensureAccount();
    if (!message) return Promise.resolve(chatError(error("Write a message first.")));
    if (message.length > 20000) return Promise.resolve(chatError(error("Keep the message within 20,000 characters.")));
    if (pendingSend && pendingSend.owner === owner) return Promise.resolve(chatError(error("Lumi is still answering your previous message.")));
    var request = { owner: owner }; pendingSend = request;
    return getLumiChat().then(function (before) {
      if (!before.canSend) return before;
      var cid = before.chat.id;
      if (identity() !== owner) throw error("The account changed before the message was sent.");
      return TB.api("POST", "/api/chats/" + encodeURIComponent(cid) + "/send", { text: message }).then(function (payload) {
        if (identity() !== owner || !TB.me || TB.me.welcome_chat !== cid) throw error("The account changed while Lumi was answering.");
        var result = chatResult(payload, cid);
        if (payload.refused || (typeof payload.status === "number" && payload.status >= 400)) {
          result.status = "refused"; result.refused = text(payload.refused, 2000) || "The shared-model gateway did not accept this message.";
          result.responseStatus = typeof payload.status === "number" ? payload.status : null;
        }
        result.version_fallback = payload.version_fallback === true;
        return result;
      });
    }).catch(chatError).then(function (result) { if (pendingSend === request) pendingSend = null; return result; });
  }

  TB.workspaceData = {
    load: load,
    getState: function () { ensureAccount(); return snapshot(); },
    subscribe: function (fn) {
      if (typeof fn !== "function") throw new TypeError("A workspace subscriber must be a function.");
      listeners.push(fn);
      return function () { listeners = listeners.filter(function (item) { return item !== fn; }); };
    },
    getLumiChat: getLumiChat,
    sendLumi: sendLumi
  };
})();
