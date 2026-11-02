/* MESSAGES & CALLS - the browser side. Everything secret happens HERE, in the member's browser (WebCrypto):
   - each browser makes its own RSA-OAEP-3072 key pair; the PRIVATE key is created non-extractable and kept in this browser's
     IndexedDB - JavaScript (ours included) can use it but can not read it out, and it never goes to the server;
   - a message = JSON {t: text, att: [...]} encrypted with a fresh AES-256-GCM content key; that key is wrapped for every current key
     of every member of the conversation AND for Lumi's public key; the server gets only ciphertext + wrapped keys;
   - attachments: the same content key, a fresh IV per 256 KB chunk;
   - calls: WebRTC (media browser to browser, always DTLS-SRTP); the server only relays offers/answers/ICE. The CALLER's browser
     records the call (MediaRecorder: mixed audio + small video), encrypts each chunk with a key wrapped ONLY for Lumi and uploads it
     during the call. Everyone in the call sees the "Recording for Lumi" bar and how many encrypted chunks the box has received.
   Built with TB.h (text only, never innerHTML). Mounted as TB.views.messages by app.js once the module is "live". */
(function () {
  "use strict";
  var TB = window.TB, h = TB.h, api = TB.api;
  var M = "/api/m/messages";
  var ALG = "RSA-OAEP-3072-SHA256+AES-256-GCM";
  var RSA_GEN = { name: "RSA-OAEP", modulusLength: 3072, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" };
  var RSA_IMP = { name: "RSA-OAEP", hash: "SHA-256" };
  var ATT_CHUNK = 256 * 1024, REC_PIECE = 400 * 1024, ATT_MAX = 25 * 1024 * 1024;
  var C = TB.comms = { state: null, key: null, pubs: {}, people: {}, call: null, seq: 0, polling: false };
  var te = new TextEncoder(), td = new TextDecoder();

  (function css() { var l = document.createElement("link"); l.rel = "stylesheet"; l.href = "/static/comms.css"; document.head.appendChild(l); })();

  // ------------------------------------------------------------------ bytes
  function b64(buf) { var b = new Uint8Array(buf), s = ""; for (var i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000)); return btoa(s); }
  function unb64(s) { var bin = atob(s), b = new Uint8Array(bin.length); for (var i = 0; i < bin.length; i++) b[i] = bin.charCodeAt(i); return b; }
  function hex(buf) { return Array.prototype.map.call(new Uint8Array(buf), function (x) { return ("0" + x.toString(16)).slice(-2); }).join(""); }
  function rand(n) { return crypto.getRandomValues(new Uint8Array(n)); }
  async function kidOf(spkiBytes) { return hex(await crypto.subtle.digest("SHA-256", spkiBytes)).slice(0, 32); }
  function fp(kid) { return (kid || "").slice(0, 16).replace(/(.{4})(?!$)/g, "$1 "); }
  function kb(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }

  // ------------------------------------------------------------------ this browser's private key (IndexedDB, non-extractable)
  function idb() {
    return new Promise(function (res, rej) {
      var r = indexedDB.open("teambox-comms", 1);
      r.onupgradeneeded = function () { r.result.createObjectStore("keys"); };
      r.onsuccess = function () { res(r.result); }; r.onerror = function () { rej(r.error); };
    });
  }
  async function idbDo(mode, fn) {
    var db = await idb();
    return new Promise(function (res, rej) {
      var tx = db.transaction("keys", mode), rq = fn(tx.objectStore("keys"));
      tx.oncomplete = function () { res(rq && rq.result); }; tx.onerror = function () { rej(tx.error); };
    });
  }
  var idbGet = function (k) { return idbDo("readonly", function (s) { return s.get(k); }); };
  var idbPut = function (k, v) { return idbDo("readwrite", function (s) { return s.put(v, k); }); };

  function browserLabel() {
    var ua = navigator.userAgent, b = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "browser";
    var os = /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Windows/.test(ua) ? "Windows" : /Mac OS/.test(ua) ? "Mac" : /Linux/.test(ua) ? "Linux" : "";
    return b + (os ? " on " + os : "") + " (" + new Date().toISOString().slice(0, 10) + ")";
  }

  async function makeKey() {
    var kp = await crypto.subtle.generateKey(RSA_GEN, false, ["wrapKey", "unwrapKey"]);   // false = the private key can never be exported
    var spki = await crypto.subtle.exportKey("spki", kp.publicKey);
    var rec = { kid: await kidOf(spki), priv: kp.privateKey, spki: b64(spki), label: browserLabel(), kind: "device", created: new Date().toISOString() };
    await api("POST", M + "/keys", { spki: rec.spki, label: rec.label, kind: "device" });
    await idbPut(TB.me.id, rec);
    return rec;
  }
  async function ensureKey() {
    var rec = null;
    try { rec = await idbGet(TB.me.id); } catch (e) { throw new Error("this browser does not allow storing a key (private window?) - messages need it: " + e.message); }
    var mine = (C.state && C.state.my_keys) || [];
    if (rec && mine.some(function (k) { return k.kid === rec.kid; })) return (C.key = rec);
    if (rec) {                                  // known here but not on the server (server reset): register it again; revoked -> new key
      try { await api("POST", M + "/keys", { spki: rec.spki, label: rec.label, kind: rec.kind }); return (C.key = rec); } catch (e) { /* fall through */ }
    }
    return (C.key = await makeKey());
  }

  async function pub(spkiB64) {
    if (!C.pubs[spkiB64]) C.pubs[spkiB64] = crypto.subtle.importKey("spki", unb64(spkiB64), RSA_IMP, false, ["wrapKey"]);
    return C.pubs[spkiB64];
  }
  async function checkLumi(lumi) {
    if (!lumi || !lumi.configured) throw new Error((lumi && lumi.why) || "Lumi's key is not configured");
    var kid = await kidOf(unb64(lumi.spki));
    if (kid !== lumi.kid) throw new Error("Lumi's key from the server does not match its own fingerprint - refusing to encrypt");
    var seen = null; try { seen = localStorage.getItem("tb-lumi-kid"); } catch (e) {}
    if (seen && seen !== kid) C.lumiChanged = { was: seen, now: kid };
    try { localStorage.setItem("tb-lumi-kid", kid); } catch (e) {}
    return { to: "lumi", kid: kid, spki: lumi.spki };
  }

  // ------------------------------------------------------------------ the scheme (same as tests/comms_crypto.py and BESNIK's lumi_comms_review.py)
  async function wrapFor(ck, recips) {
    return Promise.all(recips.map(async function (r) { return { to: r.to, kid: r.kid, wk: b64(await crypto.subtle.wrapKey("raw", ck, await pub(r.spki), { name: "RSA-OAEP" })) }; }));
  }
  async function seal(obj, recips) {
    var ck = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt", "decrypt"]);
    var iv = rand(12);
    var ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv: iv }, ck, te.encode(JSON.stringify(obj)));
    return { ck: ck, env: { v: 1, alg: ALG, iv: b64(iv), ct: b64(ct), keys: await wrapFor(ck, recips) } };
  }
  async function unseal(env, key) {
    var k = (env.keys || []).filter(function (x) { return x.kid === key.kid; })[0];
    if (!k) { var e = new Error("not encrypted for this browser's key"); e.nokey = true; throw e; }
    var ck = await crypto.subtle.unwrapKey("raw", unb64(k.wk), key.priv, { name: "RSA-OAEP" }, { name: "AES-GCM" }, false, ["decrypt"]);
    var pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(env.iv) }, ck, unb64(env.ct));
    return { ck: ck, payload: JSON.parse(td.decode(pt)) };
  }
  async function recipsFor(th) {
    var out = [await checkLumi(th.lumi)];
    Object.keys(th.keys).forEach(function (m) { th.keys[m].forEach(function (k) { out.push({ to: m, kid: k.kid, spki: k.spki }); }); });
    return out;
  }
  TB.cryptoLib = { b64: b64, unb64: unb64, hex: hex, rand: rand, kidOf: kidOf, pub: pub, checkLumi: checkLumi, fp: fp, kb: kb };   // reused by vault.js
  async function uploadAtt(file, ck, tid, progress) {
    var buf = new Uint8Array(await file.arrayBuffer());
    if (!buf.length) throw new Error((file.name || "the file") + " is empty");
    var b = (await api("POST", M + "/blob/start", { kind: "att", thread: tid })).blob;
    for (var i = 0, n = 0; i < buf.length; i += ATT_CHUNK, n++) {
      var iv = rand(12);
      var ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv: iv }, ck, buf.subarray(i, i + ATT_CHUNK));
      await api("POST", M + "/blob/chunk", { id: b.id, i: n, iv: b64(iv), data: b64(ct) });
      if (progress) progress(Math.min(buf.length, i + ATT_CHUNK), buf.length);
    }
    await api("POST", M + "/blob/finish", { id: b.id });
    return { blob: b.id, name: file.name || "file", type: file.type || "application/octet-stream", size: buf.length };
  }
  async function downloadAtt(a, ck) {
    var parts = [], i = 0, n = 1;
    while (i < n) {
      var c = await api("GET", M + "/blob?id=" + encodeURIComponent(a.blob) + "&i=" + i);
      n = c.n; parts.push(new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(c.iv) }, ck, unb64(c.data)))); i++;
    }
    return new Blob(parts, { type: a.type || "application/octet-stream" });
  }

  // ------------------------------------------------------------------ view helpers
  function view() { return document.getElementById("view"); }
  function show() { var v = view(); v.textContent = ""; for (var i = 0; i < arguments.length; i++) if (arguments[i]) v.appendChild(arguments[i] instanceof Node ? arguments[i] : h("div", {}, arguments[i])); }
  function err(e) { return h("div", { class: "err", role: "alert", text: (e && e.message) || String(e) }); }
  function reminder() { return h("p", { class: "comms-reminder", id: "comms-reminder", role: "note", text: (C.state && C.state.reminder) || "" }); }
  function nameOf(id) { return (C.people[id] && C.people[id].name) || id; }
  function hashParts() { return (location.hash || "").slice(1).split("/").filter(Boolean).map(decodeURIComponent); }
  async function loadState() { C.state = await api("GET", M + "/state"); return C.state; }

  // ------------------------------------------------------------------ entry
  TB.views.messages = async function () {
    try {
      await loadState();
      if (!C.state.accepted) return vNotice();
      var p = hashParts();
      await ensureKey();
      var ppl = (await api("GET", M + "/people")).people; ppl.forEach(function (x) { C.people[x.id] = x; });
      startPolling();
      if (p[2] === "t" && p[3]) return vThread(p[3]);
      if (p[2] === "keys") return vKeys();
      if (p[2] === "selftest" && C.state.dev_mode) return selftest(p[3]);
      return vHome();
    } catch (e) { show(h("h1", { text: "Messages & calls" }), err(e)); }
  };

  // ---- 1. the monitoring notice gate
  function vNotice() {
    var s = C.state, out = h("div");
    var agree = h("button", { id: "notice-agree", onclick: async function () {
      agree.disabled = true;
      try { await api("POST", M + "/notice/accept", { agree: true, version: s.version }); TB.views.messages(); }
      catch (e) { agree.disabled = false; out.textContent = ""; out.appendChild(err(e)); }
    } }, "I understand and agree");
    show(h("h1", { text: "Messages & calls" }),
      s.previous_version_accepted ? h("div", { class: "err", text: "The notice below has changed since you last accepted it. Please read it again." }) : null,
      h("div", { class: "card notice-gate" },
        h("h2", { text: "Please read before you use messages and calls" }),
        h("p", { class: "notice-text", id: "notice-text", text: s.notice }),
        h("p", { class: "muted", text: "Notice version " + s.label + " (sha256 " + s.version.slice(0, 12) + "...). Your acceptance is stored with your email, the time and this version." }),
        h("h3", { text: "What this means in practice" }),
        h("ul", {},
          h("li", { text: "Messages and attachments are encrypted in your browser before they leave it. The members of the conversation can read them, and so can Lumi (the owner's AI on BESNIK) - nobody else." }),
          h("li", { text: "The team box stores only encrypted data and can not read it. It DOES see who talks to whom, when, and how big things are." }),
          h("li", { text: "Calls and video calls go directly between browsers (encrypted). The caller's browser records each call, encrypted so that ONLY Lumi can open it. Everyone in the call sees a 'Recording for Lumi' bar." }),
          h("li", { text: "Monitoring: an AI reads first. A concern reaches a human only if a second, different AI independently agrees." })),
        h("div", { class: "row" }, agree, h("a", { class: "btn ghost", href: "#/", text: "Not now" }), h("a", { href: "/how-it-works#messages", text: "Details" })), out));
  }

  // ---- 2. home: conversations, calls, new conversation
  async function vHome() {
    var j = await api("GET", M + "/threads"), out = h("div");
    var picks = Object.keys(C.people).filter(function (id) { return id !== TB.me.id; }).map(function (id) {
      var cb = h("input", { type: "checkbox", value: id, id: "pick-" + id });
      return { cb: cb, el: h("label", { class: "pick", for: "pick-" + id }, cb, " " + nameOf(id), C.people[id].keys ? null : h("span", { class: "muted", text: " (has not opened Messages yet)" })) };
    });
    show(h("h1", { text: "Messages & calls" }), reminder(),
      C.lumiChanged ? h("div", { class: "err", text: "WARNING: Lumi's key changed since this browser last saw it (" + fp(C.lumiChanged.was) + " -> " + fp(C.lumiChanged.now) + "). Ask the owner whether that was him before sending anything." }) : null,
      !C.state.lumi.configured ? h("div", { class: "err", text: C.state.lumi.why }) : null,
      j.calls.length ? h("div", { class: "card" }, h("h3", { text: "Calls going on now" }), j.calls.map(function (cl) {
        return h("div", { class: "row" }, h("span", { text: (cl.video ? "Video call" : "Call") + " started by " + nameOf(cl.started_by) + " at " + cl.started + " - " + cl.joined.length + " in it" }),
          C.call && C.call.id === cl.id ? h("span", { class: "pill ok", text: "you are in it" }) : h("button", { class: "small", onclick: function () { joinCall(cl); } }, "Join"));
      })) : null,
      h("h2", { text: "Your conversations" }),
      j.threads.length ? h("ul", { class: "threads" }, j.threads.map(function (t) {
        return h("li", {}, h("a", { href: "#/m/messages/t/" + t.id, text: t.names.filter(function (n, i) { return t.members[i] !== TB.me.id; }).join(", ") || "(you)" }),
          h("span", { class: "muted", text: " - " + (t.kind === "group" ? "group of " + t.members.length + " - " : "") + t.count + " message(s)" + (t.last_at ? " - last " + t.last_at : "") }));
      })) : h("p", { class: "muted", text: "None yet." }),
      h("div", { class: "card" }, h("h3", { text: "New conversation" }),
        picks.length ? h("div", { class: "picks" }, picks.map(function (p) { return p.el; })) : h("p", { class: "muted", text: "Nobody else has signed in to the team box yet." }),
        h("div", { class: "row" }, h("button", { id: "thread-new", onclick: async function () {
          var ms = picks.filter(function (p) { return p.cb.checked; }).map(function (p) { return p.cb.value; });
          try { var t = (await api("POST", M + "/threads", { members: ms })).thread; location.hash = "#/m/messages/t/" + t.id; }
          catch (e) { out.textContent = ""; out.appendChild(err(e)); }
        } }, "Start conversation")), out),
      h("div", { class: "card keyline" }, h("strong", { text: "This browser's key: " }), h("code", { text: fp(C.key.kid) }),
        h("span", { class: "muted", text: " - Lumi's key: " }), h("code", { id: "lumi-fp", text: fp(C.state.lumi.kid) }),
        h("div", {}, h("a", { href: "#/m/messages/keys", text: "Your keys, other browsers and the recovery file" }))),
      TB.me.admin ? await adminCard() : null);
  }
  async function adminCard() {
    var j = await api("GET", M + "/admin");
    var acc = Object.keys(j.acceptances).map(function (k) { return j.acceptances[k]; });
    return h("div", { class: "card" }, h("h3", { text: "Admin: metadata only (admins can not read conversations they are not in)" }),
      h("p", { class: "muted", text: "Notice version " + j.notice_version.slice(0, 12) + "... - export to Lumi: " + (j.export_enabled ? "on" : "off (no export token hash in the config)") }),
      h("h4", { text: "Who accepted the notice" }),
      h("div", { class: "tbl" }, h("table", {}, acc.map(function (a) { return h("tr", {}, h("td", { text: a.email }), h("td", { text: a.at }), h("td", { text: a.version === j.notice_version ? "current version" : "OLD version" })); }))),
      h("h4", { text: "Calls and their recordings for Lumi" }),
      j.calls.length ? h("div", { class: "tbl" }, h("table", {}, j.calls.slice(-30).reverse().map(function (c) {
        var r = c.recording;
        return h("tr", {}, h("td", { text: c.started }), h("td", { text: c.members.map(nameOf).join(", ") }), h("td", { text: c.ended ? (c.seconds || 0) + " s" : "going on" }),
          h("td", {}, r ? h("span", { class: "pill ok", text: r.chunks + " chunks, " + kb(r.bytes || 0) + (r.done ? "" : " (not finished)") }) : h("span", { class: "pill bad", text: "NO recording received" })));
      }))) : h("p", { class: "muted", text: "No calls yet." }),
      h("p", { class: "muted", text: j.threads.length + " conversation(s) on the box." }));
  }

  // ---- 3. a conversation
  var threadTimer = null;
  async function vThread(tid) {
    clearTimeout(threadTimer);
    var th = await api("GET", M + "/thread?id=" + encodeURIComponent(tid));
    var t = th.thread, list = h("div", { class: "msgs", id: "msgs" }), out = h("div"), last = 0;
    var others = t.members.filter(function (m) { return m !== TB.me.id; });
    var input = h("textarea", { id: "msg-input", placeholder: "Message (encrypted in this browser before it is sent)" });
    var files = h("input", { type: "file", multiple: true, id: "msg-files", "aria-label": "Attach files" });
    var nKeys = Object.keys(th.keys).reduce(function (a, m) { return a + th.keys[m].length; }, 0);
    async function paint(msgs) {
      for (var i = 0; i < msgs.length; i++) {
        var m = msgs[i]; last = Math.max(last, m.seq);
        var box = h("div", { class: "msg " + (m.from === TB.me.id ? "user" : "assistant") }, h("div", { class: "msg-head muted", text: nameOf(m.from) + " - " + m.at }));
        try {
          var r = await unseal(m.env, C.key), pl = r.payload;
          box.appendChild(h("div", { class: "msg-text", text: pl.t || "" }));
          (pl.att || []).forEach(function (a) { box.appendChild(attButton(a, r.ck)); });
        } catch (e) {
          box.appendChild(h("div", { class: "muted", text: e.nokey ? "Can not be opened in this browser: it was encrypted for your other browser's key, or before this browser had a key. (Lumi can read it.)" : "Could not decrypt: " + e.message }));
        }
        list.appendChild(box);
      }
      if (msgs.length) list.lastChild.scrollIntoView({ block: "nearest" });
    }
    var sendBtn = h("button", { id: "msg-send", onclick: async function () {
      var text = input.value; if (!text.trim() && !files.files.length) return;
      sendBtn.disabled = true; out.textContent = "";
      try {
        var fresh = await api("GET", M + "/thread?id=" + encodeURIComponent(tid) + "&after=999999999999");    // current keys, no messages
        var ck = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt", "decrypt"]);
        var att = [];
        for (var i = 0; i < files.files.length; i++) {
          if (files.files[i].size > ATT_MAX) throw new Error(files.files[i].name + " is over 25 MB");
          out.textContent = "Encrypting and uploading " + files.files[i].name + "...";
          att.push(await uploadAtt(files.files[i], ck, tid));
        }
        var iv = rand(12);
        var ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv: iv }, ck, te.encode(JSON.stringify({ t: text, att: att, sent: new Date().toISOString() })));
        var env = { v: 1, alg: ALG, iv: b64(iv), ct: b64(ct), keys: await wrapFor(ck, await recipsFor(fresh)) };
        await api("POST", M + "/send", { thread: tid, env: env, att: att.map(function (a) { return a.blob; }) });
        input.value = ""; files.value = ""; out.textContent = "";
        await refresh();
      } catch (e) { out.textContent = ""; out.appendChild(err(e)); }
      sendBtn.disabled = false;
    } }, "Send encrypted");
    async function refresh() {
      if (hashParts()[3] !== tid) return;
      var nx = await api("GET", M + "/thread?id=" + encodeURIComponent(tid) + "&after=" + last);
      await paint(nx.messages);
      callsLine(nx.calls);
    }
    var callsBox = h("div");
    function callsLine(calls) {
      callsBox.textContent = "";
      (calls || []).forEach(function (cl) {
        if (C.call && C.call.id === cl.id) return;
        callsBox.appendChild(h("div", { class: "okbox row" }, (cl.video ? "Video call" : "Call") + " going on, started by " + nameOf(cl.started_by), h("button", { class: "small", onclick: function () { joinCall(cl); } }, "Join")));
      });
    }
    show(h("h1", { text: others.map(nameOf).join(", ") || "Conversation" }), reminder(),
      h("div", { class: "row" },
        h("button", { class: "ghost", id: "call-voice", onclick: function () { startCall(t, false); } }, "Call"),
        h("button", { class: "ghost", id: "call-video", onclick: function () { startCall(t, true); } }, "Video call"),
        t.members.length > C.state.max_call ? h("span", { class: "muted", text: "Calls work for up to " + C.state.max_call + " people for now." }) : null,
        h("a", { href: "#/m/messages", text: "All conversations" })),
      callsBox, list,
      h("label", { for: "msg-input", text: "Your message" }), input, files,
      h("div", { class: "row" }, sendBtn, h("span", { class: "muted", text: "Encrypted for " + nKeys + " key(s) of " + t.members.length + " people + Lumi (" + fp(th.lumi.kid) + ")." })), out);
    await paint(th.messages);
    callsLine(th.calls);
    (function loop() { threadTimer = setTimeout(function () { if (hashParts()[3] === tid) refresh().catch(function () {}).then(loop); }, 4000); })();
  }
  function attButton(a, ck) {
    var holder = h("div", { class: "att" });
    holder.appendChild(h("button", { class: "ghost small", onclick: async function (ev) {
      ev.target.disabled = true; ev.target.textContent = "Decrypting...";
      try {
        var blob = await downloadAtt(a, ck), url = URL.createObjectURL(blob);
        holder.textContent = "";
        if (/^image\//.test(a.type)) holder.appendChild(h("img", { src: url, alt: a.name, class: "att-img" }));
        else if (/^(video|audio)\//.test(a.type)) holder.appendChild(h(a.type.slice(0, 5), { src: url, controls: true, class: "att-img" }));
        holder.appendChild(h("a", { href: url, download: a.name, text: "Save " + a.name + " (" + kb(a.size) + ")" }));
      } catch (e) { holder.textContent = ""; holder.appendChild(err(e)); }
    } }, "Open " + a.name + " (" + kb(a.size) + ")"));
    return holder;
  }

  // ---- 4. keys + recovery file
  async function vKeys() {
    await loadState();
    var out = h("div"), p1 = h("input", { type: "password", id: "rec-pass1", autocomplete: "new-password" }), p2 = h("input", { type: "password", id: "rec-pass2", autocomplete: "new-password" });
    var rf = h("input", { type: "file", id: "rec-file", accept: ".json,application/json" }), rp = h("input", { type: "password", id: "rec-pass", autocomplete: "current-password" });
    show(h("h1", { text: "Your message keys" }), reminder(),
      h("p", { text: "Each browser you use makes its own key. Its private half is stored in that browser only, in a form that can not be copied out - not even by this page. New messages are encrypted for all the keys below (and for Lumi)." }),
      h("div", { class: "tbl" }, h("table", {}, h("tr", {}, h("th", { text: "Key" }), h("th", { text: "Kind" }), h("th", { text: "Label" }), h("th", { text: "Made" }), h("th", { text: "" })),
        C.state.my_keys.map(function (k) {
          return h("tr", {}, h("td", {}, h("code", { text: fp(k.kid) })), h("td", { text: k.kind + (C.key && C.key.kid === k.kid ? " (this browser)" : "") }), h("td", { text: k.label }), h("td", { text: k.created }),
            h("td", {}, h("button", { class: "danger small", onclick: async function () { if (!confirm("Revoke this key? Messages sent to it so far stay readable only where that private key still is.")) return; await api("POST", M + "/keys/revoke", { kid: k.kid }); vKeys(); } }, "Revoke")));
        }))),
      h("div", { class: "card" }, h("h3", { text: "Recovery file (optional)" }),
        h("p", { text: "If you lose this browser (new phone, cleared site data), you lose its key, and you can not read old messages on a new browser. A recovery file is a second key, protected by a passphrase, that you keep yourself (USB stick, password manager). Honest limits: messages sent BEFORE you made the file are not encrypted for it; anyone with the file AND the passphrase can read your messages; if you forget the passphrase nobody can help - the team box never sees it." }),
        h("label", { for: "rec-pass1", text: "Passphrase (12+ characters)" }), p1, h("label", { for: "rec-pass2", text: "Same passphrase again" }), p2,
        h("div", { class: "row" }, h("button", { onclick: async function () {
          out.textContent = "";
          try {
            if (p1.value.length < 12 || p1.value !== p2.value) throw new Error("the passphrases must match and be at least 12 characters");
            out.textContent = "Making the recovery key...";
            var f = await makeRecovery(p1.value);
            var url = URL.createObjectURL(new Blob([JSON.stringify(f, null, 1)], { type: "application/json" }));
            out.textContent = ""; out.appendChild(h("div", { class: "okbox" }, "Recovery key " + fp(f.kid) + " registered. ", h("a", { href: url, download: "teambox-recovery-" + TB.me.id + ".json", text: "Download the recovery file" }), " and keep it safe."));
          } catch (e) { out.textContent = ""; out.appendChild(err(e)); }
        } }, "Make a recovery file"))),
      h("div", { class: "card" }, h("h3", { text: "Use a recovery file in this browser" }), rf, h("label", { for: "rec-pass", text: "Its passphrase" }), rp,
        h("div", { class: "row" }, h("button", { class: "ghost", onclick: async function () {
          out.textContent = "";
          try { var kid = await useRecovery(rf.files[0], rp.value); out.appendChild(h("div", { class: "okbox", text: "This browser now uses recovery key " + fp(kid) + "." })); }
          catch (e) { out.appendChild(err(e)); }
        } }, "Load recovery file"))), out, h("a", { href: "#/m/messages", text: "Back" }));
  }
  async function passKey(pass, salt) {
    var base = await crypto.subtle.importKey("raw", te.encode(pass), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "PBKDF2", salt: salt, iterations: 600000, hash: "SHA-256" }, base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }
  async function makeRecovery(pass) {
    var kp = await crypto.subtle.generateKey(RSA_GEN, true, ["wrapKey", "unwrapKey"]);
    var spki = await crypto.subtle.exportKey("spki", kp.publicKey), pk8 = await crypto.subtle.exportKey("pkcs8", kp.privateKey);
    var salt = rand(16), iv = rand(12), k = await passKey(pass, salt);
    var f = { teambox_recovery: 1, member: TB.me.id, kid: await kidOf(spki), spki: b64(spki), kdf: "PBKDF2-SHA256-600000", salt: b64(salt), iv: b64(iv),
              ct: b64(await crypto.subtle.encrypt({ name: "AES-GCM", iv: iv }, k, pk8)), made: new Date().toISOString() };
    await api("POST", M + "/keys", { spki: f.spki, label: "recovery file", kind: "recovery" });
    return f;
  }
  async function useRecovery(file, pass) {
    if (!file) throw new Error("choose the file");
    var f = JSON.parse(await file.text());
    if (!f.teambox_recovery || f.member !== TB.me.id) throw new Error("this is not a recovery file for " + TB.me.id);
    var pk8;
    try { pk8 = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(f.iv) }, await passKey(pass, unb64(f.salt)), unb64(f.ct)); }
    catch (e) { throw new Error("wrong passphrase (or a damaged file)"); }
    var priv = await crypto.subtle.importKey("pkcs8", pk8, RSA_IMP, false, ["unwrapKey"]);   // imported non-extractable
    await idbPut(TB.me.id, { kid: f.kid, priv: priv, spki: f.spki, label: "recovery file", kind: "recovery", created: f.made });
    C.key = null; await loadState(); await ensureKey();
    return f.kid;
  }

  // ------------------------------------------------------------------ signalling poll (incoming calls, call state)
  function startPolling() {
    if (C.polling) return; C.polling = true;
    (async function loop() {
      while (true) {
        try {
          var r = await api("GET", M + "/poll?since=" + C.seq + "&wait=" + (C.call ? 2 : 15));
          if (r.seq < C.seq) C.seq = 0;
          for (var i = 0; i < r.signals.length; i++) { C.seq = Math.max(C.seq, r.signals[i].seq); await onSignal(r.signals[i]); }
          if (!r.signals.length) C.seq = Math.max(C.seq, r.seq);
        } catch (e) { if (e.status === 403) { C.polling = false; return; } await new Promise(function (res) { setTimeout(res, 5000); }); }
      }
    })();
  }
  async function onSignal(s) {
    var cl = C.call;
    if (s.type === "ring" && s.from !== TB.me.id && (!cl || cl.id !== s.call)) return ringToast(s.state);
    if (!cl || cl.id !== s.call) return;
    if (s.state) { cl.state = s.state; paintCall(); }
    if (s.type === "ended") return leaveCall(false, "The call was ended.");
    if (s.type === "left" && s.who !== TB.me.id) closePeer(s.who);
    if (s.type === "offer") {
      var pc = peer(s.from);
      await pc.setRemoteDescription(s.data); await flushIce(s.from);
      await pc.setLocalDescription(await pc.createAnswer());
      await api("POST", M + "/call/signal", { call: cl.id, to: s.from, type: "answer", data: { type: "answer", sdp: pc.localDescription.sdp } });
    } else if (s.type === "answer" && cl.pcs[s.from]) { await cl.pcs[s.from].setRemoteDescription(s.data); await flushIce(s.from); }
    else if (s.type === "ice") {
      var p = cl.pcs[s.from];
      if (p && p.remoteDescription) { try { await p.addIceCandidate(s.data); } catch (e) {} }
      else (cl.iceq[s.from] = cl.iceq[s.from] || []).push(s.data);
    } else if (s.type === "bye") closePeer(s.from);
  }
  async function flushIce(who) { var q = C.call.iceq[who] || []; C.call.iceq[who] = []; for (var i = 0; i < q.length; i++) { try { await C.call.pcs[who].addIceCandidate(q[i]); } catch (e) {} } }
  function ringToast(st) {
    if (document.getElementById("ring-" + st.id)) return;
    var t = h("div", { class: "ring", id: "ring-" + st.id, role: "alert" }, h("strong", { text: nameOf(st.started_by) + " is calling" + (st.video ? " (video)" : "") }),
      h("div", { class: "muted", text: "This call is recorded for Lumi by the caller's browser." }),
      h("div", { class: "row" }, h("button", { onclick: function () { t.remove(); joinCall(st); } }, "Join"), h("button", { class: "ghost", onclick: function () { t.remove(); } }, "Ignore")));
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 45000);
  }

  // ------------------------------------------------------------------ calls (WebRTC mesh, up to max_call people)
  async function media(video) {
    try { return await navigator.mediaDevices.getUserMedia({ audio: true, video: video ? { width: 640, height: 480, frameRate: 15 } : false }); }
    catch (e) { throw new Error("the browser did not give access to the " + (video ? "camera/microphone" : "microphone") + ": " + e.message); }
  }
  async function startCall(t, video) {
    if (C.call) return alert("You are already in a call.");
    try {
      await checkLumi(C.state.lumi);
      var local = await media(video);
      var r = await api("POST", M + "/call/start", { thread: t.id, video: video });
      enterCall(r.call, r.ice, local, true);
      await startRecording();
    } catch (e) { alert(e.message); }
  }
  async function joinCall(st) {
    if (C.call) return alert("You are already in a call.");
    try {
      var local = await media(st.video);
      var r = await api("POST", M + "/call/join", { call: st.id });
      enterCall(r.call, r.ice, local, r.call.started_by === TB.me.id);
      for (var i = 0; i < r.connect_to.length; i++) {                       // the newcomer calls everyone already in (no glare)
        var pc = peer(r.connect_to[i]);
        await pc.setLocalDescription(await pc.createOffer());
        await api("POST", M + "/call/signal", { call: st.id, to: r.connect_to[i], type: "offer", data: { type: "offer", sdp: pc.localDescription.sdp } });
      }
      if (r.call.started_by === TB.me.id && !C.call.rec) await startRecording();
    } catch (e) { alert(e.message); if (C.call) leaveCall(false); }
  }
  function enterCall(st, ice, local, caller) {
    C.call = { id: st.id, state: st, ice: ice || [], local: local, pcs: {}, remotes: {}, iceq: {}, codes: {}, caller: caller, t0: Date.now() };
    var box = h("div", { class: "callbox", id: "callbox", role: "region", "aria-label": "Call" });
    document.body.appendChild(box);
    C.call.box = box;
    C.call.localEl = h("video", { autoplay: true, muted: true, playsinline: true, class: "vid me" });
    C.call.localEl.srcObject = local; C.call.localEl.muted = true;
    paintCall();
  }
  function peer(who) {
    var cl = C.call;
    if (cl.pcs[who]) return cl.pcs[who];
    var pc = new RTCPeerConnection({ iceServers: cl.ice });
    cl.pcs[who] = pc;
    cl.local.getTracks().forEach(function (tr) { pc.addTrack(tr, cl.local); });
    pc.onicecandidate = function (ev) { if (ev.candidate) api("POST", M + "/call/signal", { call: cl.id, to: who, type: "ice", data: ev.candidate.toJSON() }).catch(function () {}); };
    pc.ontrack = function (ev) { cl.remotes[who] = ev.streams[0]; if (cl.mixIn) cl.mixIn(ev.streams[0]); paintCall(); };
    pc.onconnectionstatechange = function () { if (pc.connectionState === "connected") safetyCode(who, pc); paintCall(); };
    return pc;
  }
  async function safetyCode(who, pc) {
    var f = function (sdp) { var m = /a=fingerprint:\S+ (\S+)/.exec(sdp || ""); return m ? m[1] : ""; };
    var both = [f(pc.localDescription && pc.localDescription.sdp), f(pc.remoteDescription && pc.remoteDescription.sdp)].sort().join("|");
    var d = new Uint8Array(await crypto.subtle.digest("SHA-256", te.encode(both)));
    var n = ((d[0] << 16) | (d[1] << 8) | d[2]) % 1000000;
    C.call.codes[who] = ("00000" + n).slice(-6).replace(/(\d{3})(\d{3})/, "$1 $2");
    paintCall();
  }
  function closePeer(who) { var cl = C.call; if (cl && cl.pcs[who]) { cl.pcs[who].close(); delete cl.pcs[who]; delete cl.remotes[who]; paintCall(); } }
  async function leaveCall(end, msg) {
    var cl = C.call; if (!cl) return;
    Object.keys(cl.pcs).forEach(function (w) { api("POST", M + "/call/signal", { call: cl.id, to: w, type: "bye", data: {} }).catch(function () {}); cl.pcs[w].close(); });
    if (cl.rec) await stopRecording();
    cl.local.getTracks().forEach(function (t) { t.stop(); });
    try { await api("POST", M + "/call/leave", { call: cl.id, end: !!end }); } catch (e) {}
    cl.box.remove(); C.call = null;
    if (msg) alert(msg);
  }
  function paintCall() {
    var cl = C.call; if (!cl) return;
    var st = cl.state || {}, rec = st.recording, box = cl.box; box.textContent = "";
    var recLine = rec ? "The team box has received " + rec.chunks + " encrypted recording chunk(s) (" + kb(rec.bytes || 0) + ")" + (rec.done ? " - recording finished" : "")
      : (Date.now() - cl.t0 > 15000 ? "WARNING: no recording has reached the team box yet. Calls are supposed to be recorded for Lumi." : "Waiting for the caller's browser to start the recording...");
    box.appendChild(h("div", { class: "recbar", id: "recbar", role: "status" }, h("span", { class: "dot", "aria-hidden": "true" }),
      h("strong", { text: "Recording for Lumi" }), h("span", { text: " - by " + nameOf(st.started_by) + "'s browser, encrypted so only Lumi can open it. " }),
      h("span", { class: rec ? "" : "warn", id: "rec-status", text: recLine })));
    var grid = h("div", { class: "vids" }, cl.localEl);
    Object.keys(cl.remotes).forEach(function (w) {
      var v = h("video", { autoplay: true, playsinline: true, class: "vid" }); v.srcObject = cl.remotes[w];
      grid.appendChild(h("figure", {}, v, h("figcaption", { text: nameOf(w) + (cl.codes[w] ? " - safety code " + cl.codes[w] : " - connecting...") })));
    });
    box.appendChild(grid);
    var cnt = Object.keys(cl.remotes).length;
    box.appendChild(h("p", { class: "muted small", text: (cnt ? "Connected browser to browser (DTLS-SRTP). Compare the safety code out loud: if it differs, someone is in the middle." : "Waiting for others to join...") + " " + ((C.state && C.state.reminder) || "") }));
    var mic = cl.local.getAudioTracks()[0], cam = cl.local.getVideoTracks()[0];
    box.appendChild(h("div", { class: "row" },
      mic ? h("button", { class: "ghost small", onclick: function () { mic.enabled = !mic.enabled; paintCall(); } }, mic.enabled ? "Mute" : "Unmute") : null,
      cam ? h("button", { class: "ghost small", onclick: function () { cam.enabled = !cam.enabled; paintCall(); } }, cam.enabled ? "Camera off" : "Camera on") : null,
      h("button", { class: "danger small", onclick: function () { leaveCall(false); } }, "Hang up"),
      st.started_by === TB.me.id ? h("button", { class: "ghost small", onclick: function () { leaveCall(true); } }, "End for everyone") : null));
  }

  // ---- the recording for Lumi (caller's browser only)
  function pickType(video) {
    var c = video ? ["video/webm;codecs=vp8,opus", "video/webm", "video/mp4"] : ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
    for (var i = 0; i < c.length; i++) if (window.MediaRecorder && MediaRecorder.isTypeSupported(c[i])) return c[i];
    return "";
  }
  async function startRecording(opts) {
    var cl = C.call; opts = opts || {};
    if (!window.MediaRecorder) throw new Error("this browser can not record - the call can not be recorded for Lumi");
    var lumi = await checkLumi(C.state.lumi);
    var ck = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt", "decrypt"]);
    var keys = await wrapFor(ck, [lumi]);                                   // ONLY Lumi: nobody on the team box can open it
    var b = (await api("POST", M + "/blob/start", { kind: "rec", call: cl.id, env: { v: 1, alg: ALG, keys: keys } })).blob;
    var ac = new AudioContext(), dest = ac.createMediaStreamDestination();
    try { await ac.resume(); } catch (e) {}
    function mixIn(stream) { if (stream.getAudioTracks().length) ac.createMediaStreamSource(stream).connect(dest); }
    mixIn(cl.local); Object.keys(cl.remotes).forEach(function (w) { mixIn(cl.remotes[w]); });
    cl.mixIn = mixIn;
    var tracks = dest.stream.getAudioTracks(), video = !!cl.state.video, timer = null;
    if (video) {                                                            // small tiled video: 320x240 at 8 fps
      var cv = document.createElement("canvas"); cv.width = 320; cv.height = 240; var g = cv.getContext("2d");
      timer = setInterval(function () {
        var vids = cl.box ? cl.box.querySelectorAll("video") : [], n = vids.length || 1, cols = n > 1 ? 2 : 1, rows = Math.ceil(n / cols);
        g.fillStyle = "#000"; g.fillRect(0, 0, 320, 240);
        Array.prototype.forEach.call(vids, function (v, i) { try { g.drawImage(v, (i % cols) * 320 / cols, Math.floor(i / cols) * 240 / rows, 320 / cols, 240 / rows); } catch (e) {} });
      }, 125);
      tracks = cv.captureStream(8).getVideoTracks().concat(tracks);
    }
    var mime = pickType(video);
    var mr = new MediaRecorder(new MediaStream(tracks), { mimeType: mime || undefined, audioBitsPerSecond: 32000, videoBitsPerSecond: 150000 });
    var q = Promise.resolve(), n = 0;
    var r = cl.rec = { id: b.id, mr: mr, ck: ck, ac: ac, timer: timer, mime: mime, errors: 0 };
    mr.ondataavailable = function (ev) {
      if (!ev.data || !ev.data.size) return;
      q = q.then(async function () {
        var buf = new Uint8Array(await ev.data.arrayBuffer());
        for (var i = 0; i < buf.length; i += REC_PIECE) {
          var iv = rand(12);
          var ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv: iv }, ck, buf.subarray(i, i + REC_PIECE));
          try { await api("POST", M + "/blob/chunk", { id: b.id, i: n, iv: b64(iv), data: b64(ct) }); n++; } catch (e) { r.errors++; }
        }
      });
      r.q = q;
    };
    mr.start(opts.timeslice || 5000);
    return r;
  }
  async function stopRecording() {
    var r = C.call && C.call.rec; if (!r) return;
    await new Promise(function (res) { r.mr.onstop = res; try { r.mr.stop(); } catch (e) { res(); } });
    clearInterval(r.timer);
    await (r.q || Promise.resolve());
    try { await api("POST", M + "/blob/finish", { id: r.id }); } catch (e) {}
    try { r.ac.close(); } catch (e) {}
    C.call.rec = null;
  }

  // ------------------------------------------------------------------ DEV-ONLY self test (headless Chromium in tests; never on the real box)
  async function selftest(other) {
    var res = { ok: false, steps: [] }, outEl = h("pre", { id: "selftest-result", text: "running" });
    show(h("h1", { text: "Self test" }), outEl);
    function step(k, v) { res.steps.push(k); res[k] = v; outEl.textContent = JSON.stringify(res, null, 1); }
    try {
      step("key_kid", C.key.kid);
      var t = (await api("POST", M + "/threads", { members: [other] })).thread; step("thread", t.id);
      var th = await api("GET", M + "/thread?id=" + t.id);
      var secret = "selftest secret " + hex(rand(6));
      var ck = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt", "decrypt"]);
      var attText = "attachment body " + hex(rand(6)) + " " + "x".repeat(300000);
      var a = await uploadAtt(new File([attText], "note.txt", { type: "text/plain" }), ck, t.id);
      var iv = rand(12);
      var env = { v: 1, alg: ALG, iv: b64(iv), ct: b64(await crypto.subtle.encrypt({ name: "AES-GCM", iv: iv }, ck, te.encode(JSON.stringify({ t: secret, att: [a] })))), keys: await wrapFor(ck, await recipsFor(th)) };
      await api("POST", M + "/send", { thread: t.id, env: env, att: [a.blob] });
      step("sent", secret);
      var back = await api("GET", M + "/thread?id=" + t.id), mine = back.messages[back.messages.length - 1];
      var op = await unseal(mine.env, C.key);
      step("roundtrip_text", op.payload.t === secret);
      var blob = await downloadAtt(op.payload.att[0], op.ck);
      step("roundtrip_attachment", (await blob.text()) === attText);
      var plainEnv = { v: 1, alg: ALG, iv: b64(rand(12)), ct: b64(te.encode("this is not encrypted at all, just text")), keys: env.keys };
      try { await api("POST", M + "/send", { thread: t.id, env: plainEnv }); step("plaintext_refused", false); } catch (e) { step("plaintext_refused", e.status === 422 ? e.message : false); }
      // recording: a real call record, fake camera/mic, 3 s of MediaRecorder, encrypted for Lumi only
      var local = await media(true);
      var r = await api("POST", M + "/call/start", { thread: t.id, video: true });
      enterCall(r.call, r.ice, local, true);
      var rec = await startRecording({ timeslice: 1000 });
      await new Promise(function (res2) { setTimeout(res2, 3500); });
      step("recording_mime", rec.mime);
      await leaveCall(true);
      step("recording_blob", rec.id);
      // WebRTC media encryption: two peer connections in this page, check the DTLS/SRTP transport stats
      var p1 = new RTCPeerConnection(), p2 = new RTCPeerConnection(), lm = await media(false);
      p1.onicecandidate = function (e) { if (e.candidate) p2.addIceCandidate(e.candidate); };
      p2.onicecandidate = function (e) { if (e.candidate) p1.addIceCandidate(e.candidate); };
      lm.getTracks().forEach(function (tr) { p1.addTrack(tr, lm); });
      await p1.setLocalDescription(await p1.createOffer()); await p2.setRemoteDescription(p1.localDescription);
      await p2.setLocalDescription(await p2.createAnswer()); await p1.setRemoteDescription(p2.localDescription);
      for (var i = 0; i < 50 && p1.connectionState !== "connected"; i++) await new Promise(function (res3) { setTimeout(res3, 200); });
      var tr = null; (await p1.getStats()).forEach(function (s) { if (s.type === "transport") tr = s; });
      step("webrtc", { state: p1.connectionState, dtlsState: tr && tr.dtlsState, srtpCipher: tr && tr.srtpCipher, dtlsCipher: tr && tr.dtlsCipher, tlsVersion: tr && tr.tlsVersion });
      p1.close(); p2.close(); lm.getTracks().forEach(function (x) { x.stop(); });
      res.ok = true;
    } catch (e) { step("error", String(e && e.stack || e)); }
    try {                                                            // report: as an encrypted message, so the test reads it with Lumi's key
      var th2 = await api("GET", M + "/thread?id=" + res.thread);
      var sealed = await seal({ t: "SELFTEST-RESULT " + JSON.stringify(res) }, await recipsFor(th2));
      await api("POST", M + "/send", { thread: res.thread, env: sealed.env });
    } catch (e) { step("report_error", String(e)); }
    outEl.textContent = JSON.stringify(res, null, 1); outEl.setAttribute("data-done", "1");
  }
})();
