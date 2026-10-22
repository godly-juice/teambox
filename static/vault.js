/* THE VAULT - browser side. Everything secret happens HERE (WebCrypto); the team box only ever gets ciphertext.
   - vault key VK: random AES-256-GCM key, made here. Stored on the box only wrapped (AES-GCM) under a key derived from the member's
     VAULT PASSWORD: PBKDF2-SHA256, 600,000 iterations, random 16-byte salt. Unlocking = typing the password (any device).
   - each item: a fresh AES-256-GCM content key CK encrypts {t:"note", title, text} or {t:"file", name, type, size}; CK is wrapped
     for VK (AES-GCM) AND for Lumi's public key (RSA-OAEP-3072, the messages key) - so Lumi can read the vault (monitoring notice).
   - files: encrypted 256 KB chunks under CK, a fresh IV each.
   - change password: unwrap VK with the old password, wrap it under the new one (new salt + IV); the PIN is asked as well.
   VK lives only in this page's memory while unlocked; "Lock" or leaving the page forgets it. Uses TB.cryptoLib from comms.js. */
(function () {
  "use strict";
  var TB = window.TB, h = TB.h, api = TB.api;
  var ALG = "AES-256-GCM(content) + AES-256-GCM(vault key) + RSA-OAEP-3072-SHA256(Lumi)";
  var ITER = 600000, CHUNK = 256 * 1024, MIN_PW = 10;
  var te = new TextEncoder(), td = new TextDecoder();
  var V = TB.vault = { vk: null, state: null };
  function L() { return TB.cryptoLib; }
  function err(e) { return h("div", { class: "err", role: "alert", text: (e && e.message) || String(e) }); }
  function show() { var v = document.getElementById("view"); v.textContent = ""; for (var i = 0; i < arguments.length; i++) if (arguments[i]) v.appendChild(arguments[i]); }

  // ------------------------------------------------------------------ password strength (hint only - the server never sees it)
  function strength(pw) {
    var s = 0, tips = [];
    if (pw.length >= MIN_PW) s++; else tips.push("at least " + MIN_PW + " characters");
    if (pw.length >= 16) s++;
    var classes = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter(function (r) { return r.test(pw); }).length;
    if (classes >= 3) s++; else tips.push("mix lower case, capitals, digits or symbols");
    if (/^(.)\1+$/.test(pw) || /password|123456|qwerty|xseed|teambox/i.test(pw)) { s = 0; tips.push("avoid common words and repeats"); }
    if (pw.split(/\s+/).length >= 4) s++;              // a passphrase of 4+ words is good too
    return { score: Math.min(s, 3), label: ["too weak", "weak", "good", "strong"][Math.min(s, 3)], tips: tips };
  }
  function pwFields(id) {
    var p1 = h("input", { type: "password", id: id + "1", autocomplete: "new-password", minlength: String(MIN_PW) });
    var p2 = h("input", { type: "password", id: id + "2", autocomplete: "new-password" });
    var meter = h("p", { class: "muted small", "aria-live": "polite", text: "Use at least " + MIN_PW + " characters - a few unrelated words work well." });
    p1.addEventListener("input", function () { var s = strength(p1.value); meter.textContent = "Strength: " + s.label + (s.tips.length ? " - " + s.tips.join("; ") : ""); });
    return { p1: p1, p2: p2, meter: meter, check: function (pin) {
      if (p1.value.length < MIN_PW) throw new Error("the vault password needs at least " + MIN_PW + " characters");
      if (p1.value !== p2.value) throw new Error("the two vault passwords are not the same");
      if (strength(p1.value).score < 1) throw new Error("that vault password is too easy to guess");
      if (pin && p1.value === pin) throw new Error("use something different from your PIN");
      return p1.value;
    } };
  }

  // ------------------------------------------------------------------ crypto
  async function kek(pass, salt, iter) {
    var base = await crypto.subtle.importKey("raw", te.encode(pass), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "PBKDF2", salt: salt, iterations: iter, hash: "SHA-256" }, base, { name: "AES-GCM", length: 256 }, false, ["wrapKey", "unwrapKey"]);
  }
  async function wrapVK(vk, pass) {
    var salt = L().rand(16), iv = L().rand(12);
    var wk = await crypto.subtle.wrapKey("raw", vk, await kek(pass, salt, ITER), { name: "AES-GCM", iv: iv });
    return { kdf: { name: "PBKDF2-SHA256", iter: ITER, salt: L().b64(salt) }, pw: { iv: L().b64(iv), wk: L().b64(wk) } };
  }
  async function unlock(pass) {
    var rec = V.state.vault;
    try {
      V.vk = await crypto.subtle.unwrapKey("raw", L().unb64(rec.pw.wk), await kek(pass, L().unb64(rec.kdf.salt), rec.kdf.iter),
        { name: "AES-GCM", iv: L().unb64(rec.pw.iv) }, { name: "AES-GCM" }, true, ["wrapKey", "unwrapKey"]);
    } catch (e) { V.vk = null; throw new Error("wrong vault password"); }
  }
  async function lumiRecip() { return L().checkLumi(V.state.lumi); }
  async function seal(obj, ck) {
    ck = ck || await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt", "decrypt"]);
    var iv = L().rand(12), kiv = L().rand(12), lu = await lumiRecip();
    var ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv: iv }, ck, te.encode(JSON.stringify(obj)));
    var wv = await crypto.subtle.wrapKey("raw", ck, V.vk, { name: "AES-GCM", iv: kiv });
    var wl = await crypto.subtle.wrapKey("raw", ck, await L().pub(lu.spki), { name: "RSA-OAEP" });
    return { ck: ck, env: { v: 1, alg: ALG, iv: L().b64(iv), ct: L().b64(ct),
      keys: [{ to: "vault", iv: L().b64(kiv), wk: L().b64(wv) }, { to: "lumi", kid: lu.kid, wk: L().b64(wl) }] } };
  }
  async function open(env) {
    var k = env.keys.filter(function (x) { return x.to === "vault"; })[0];
    var ck = await crypto.subtle.unwrapKey("raw", L().unb64(k.wk), V.vk, { name: "AES-GCM", iv: L().unb64(k.iv) }, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
    var pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: L().unb64(env.iv) }, ck, L().unb64(env.ct));
    return { ck: ck, payload: JSON.parse(td.decode(pt)) };
  }
  async function putFile(file, progress) {
    var buf = new Uint8Array(await file.arrayBuffer());
    if (!buf.length) throw new Error("the file is empty");
    var s = await seal({ t: "file", name: file.name || "file", type: file.type || "application/octet-stream", size: buf.length });
    var it = (await api("POST", "/api/vault/items", { kind: "file", env: s.env, size: buf.length })).item;
    for (var i = 0, n = 0; i < buf.length; i += CHUNK, n++) {
      var iv = L().rand(12);
      var ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv: iv }, s.ck, buf.subarray(i, i + CHUNK));
      await api("POST", "/api/vault/chunk", { id: it.id, i: n, iv: L().b64(iv), data: L().b64(ct) });
      if (progress) progress(Math.min(buf.length, i + CHUNK) / buf.length);
    }
    return (await api("POST", "/api/vault/finish", { id: it.id })).item;
  }
  async function getFile(id, ck, type) {
    var parts = [], i = 0, n = 1;
    while (i < n) {
      var c = await api("GET", "/api/vault/chunk?id=" + encodeURIComponent(id) + "&i=" + i);
      n = c.n; parts.push(new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: L().unb64(c.iv) }, ck, L().unb64(c.data)))); i++;
    }
    return new Blob(parts, { type: type || "application/octet-stream" });
  }

  // ------------------------------------------------------------------ first-visit step: create the vault (app.js calls this)
  TB.vaultSetup = function (el, done) {
    api("GET", "/api/vault").then(function (st) {
      V.state = st; el.textContent = "";
      if (st.vault) return done();
      if (!st.lumi.configured) {
        el.appendChild(h("p", { text: "Your vault switches on as soon as Marjus has set Lumi's key on the team box (" + (st.lumi.why || "not set yet") + "). You will be asked for a vault password then." }));
        el.appendChild(h("button", { type: "button", onclick: function () { api("POST", "/api/setup/vault-later", {}).then(done).catch(function (e) { el.appendChild(err(e)); }); } }, "Continue"));
        return;
      }
      var f = pwFields("vpw"), out = h("div"), btn;
      btn = h("button", { type: "submit" }, "Create my vault");
      el.appendChild(h("form", { class: "card", onsubmit: async function (ev) {
        ev.preventDefault(); out.textContent = "";
        try {
          var pass = f.check(); btn.disabled = true; btn.textContent = "Making your vault key...";
          var vk = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["wrapKey", "unwrapKey"]);
          await lumiRecip();
          var w = await wrapVK(vk, pass);
          await api("POST", "/api/vault/init", w);
          V.vk = vk; done();
        } catch (e) { out.appendChild(err(e)); btn.disabled = false; btn.textContent = "Create my vault"; }
      } }, h("p", { text: "Your vault keeps personal notes and files, encrypted in this browser. Choose a VAULT PASSWORD - different from your PIN. It opens your vault on any device; the team box never sees it, and nobody can reset it for you." }),
        h("p", { class: "muted", text: st.monitoring }),
        h("label", { for: "vpw1", text: "Vault password" }), f.p1, f.meter, h("label", { for: "vpw2", text: "Vault password again" }), f.p2,
        h("div", { class: "row" }, btn), out));
    }).catch(function (e) { el.appendChild(err(e)); });
  };

  // ------------------------------------------------------------------ the Vault page
  TB.views.vault = async function () {
    try { V.state = await api("GET", "/api/vault"); } catch (e) { return show(err(e)); }
    var st = V.state;
    var head = [h("h1", { text: "Your vault" }), h("p", { class: "muted", text: "Encrypted in your browser with your vault password. " + st.monitoring })];
    if (!st.vault) { var el = h("div"); show.apply(null, head.concat([el])); return TB.vaultSetup(el, function () { TB.views.vault(); }); }
    if (!V.vk) {
      var pw = h("input", { type: "password", id: "vaultPass", autocomplete: "current-password" }), out = h("div");
      return show.apply(null, head.concat([h("form", { class: "card", onsubmit: async function (ev) {
        ev.preventDefault(); out.textContent = "Opening...";
        try { await unlock(pw.value); TB.views.vault(); } catch (e) { out.textContent = ""; out.appendChild(err(e)); }
      } }, h("label", { for: "vaultPass", text: "Vault password" }), pw, h("div", { class: "row" }, h("button", { type: "submit" }, "Open my vault")), out)]));
    }
    var list = h("div"), msg = h("div");
    var title = h("input", { id: "noteTitle", placeholder: "Title" }), text = h("textarea", { id: "noteText", placeholder: "Your note" });
    var fi = h("input", { type: "file", id: "vaultFile" });
    show.apply(null, head.concat([
      h("div", { class: "row" }, h("button", { class: "ghost small", type: "button", onclick: function () { V.vk = null; TB.views.vault(); } }, "Lock"),
        h("span", { class: "muted", text: st.items.length + " item(s), " + L().kb(st.used_bytes || 0) })),
      h("form", { class: "card", onsubmit: async function (ev) {
        ev.preventDefault(); msg.textContent = "";
        try { var s = await seal({ t: "note", title: title.value, text: text.value }); await api("POST", "/api/vault/items", { kind: "note", env: s.env }); TB.views.vault(); }
        catch (e) { msg.appendChild(err(e)); }
      } }, h("h3", { text: "New note" }), h("label", { for: "noteTitle", text: "Title" }), title, h("label", { for: "noteText", text: "Note" }), text, h("div", { class: "row" }, h("button", { type: "submit" }, "Save note"))),
      h("form", { class: "card", onsubmit: async function (ev) {
        ev.preventDefault(); msg.textContent = "";
        if (!fi.files[0]) return msg.appendChild(err("choose a file first"));
        try { await putFile(fi.files[0], function (r) { msg.textContent = "Encrypting and uploading " + Math.round(r * 100) + "%"; }); TB.views.vault(); }
        catch (e) { msg.textContent = ""; msg.appendChild(err(e)); }
      } }, h("h3", { text: "Add a file" }), h("label", { for: "vaultFile", text: "File (at most " + st.limits.file_max_mb + " MB)" }), fi, h("div", { class: "row" }, h("button", { type: "submit" }, "Encrypt and upload"))),
      msg, h("h2", { text: "Items" }), list, changeCard()]));
    for (var i = 0; i < st.items.length; i++) list.appendChild(await itemNode(st.items[i]));
    if (!st.items.length) list.appendChild(h("p", { class: "muted", text: "Nothing in your vault yet." }));
  };
  async function itemNode(m) {
    var box = h("div", { class: "card" });
    try {
      var it = (await api("GET", "/api/vault/item?id=" + m.id)).item, o = await open(it.env), p = o.payload;
      var del = h("button", { class: "linkish", type: "button", onclick: function () { if (confirm("Delete this from your vault?")) api("POST", "/api/vault/delete", { id: m.id }).then(function () { TB.views.vault(); }).catch(function (e) { box.appendChild(err(e)); }); } }, "delete");
      if (p.t === "note") { box.appendChild(h("div", { class: "row" }, h("strong", { text: p.title || "(no title)" }), h("span", { class: "muted", text: m.updated }), del)); box.appendChild(h("pre", { class: "preview", text: p.text })); }
      else {
        box.appendChild(h("div", { class: "row" }, h("strong", { text: p.name }), h("span", { class: "muted", text: L().kb(p.size) + " - " + m.updated }),
          m.status === "ok" ? h("button", { class: "ghost small", type: "button", onclick: async function () {
            try { var b = await getFile(m.id, o.ck, p.type), a = h("a", { href: URL.createObjectURL(b), download: p.name }); document.body.appendChild(a); a.click(); a.remove(); }
            catch (e) { box.appendChild(err(e)); }
          } }, "Download") : h("span", { class: "pill", text: m.status }), del));
      }
    } catch (e) { box.appendChild(err(e)); }
    return box;
  }
  function changeCard() {
    var old = h("input", { type: "password", id: "vOld", autocomplete: "current-password" }), pin = h("input", { type: "password", id: "vPin", inputmode: "numeric", autocomplete: "off" });
    var f = pwFields("vNew"), out = h("div");
    return h("details", { class: "card" }, h("summary", { text: "Change vault password" }),
      h("form", { onsubmit: async function (ev) {
        ev.preventDefault(); out.textContent = "";
        try {
          var pass = f.check(pin.value);
          var keep = V.vk; await unlock(old.value); var w = await wrapVK(V.vk, pass); V.vk = keep || V.vk;
          await api("POST", "/api/vault/password", Object.assign({ pin: pin.value }, w));
          out.appendChild(h("div", { class: "okbox", text: "Vault password changed. Use the new one from now on." }));
        } catch (e) { out.appendChild(err(e)); }
      } }, h("label", { for: "vOld", text: "Current vault password" }), old, h("label", { for: "vPin", text: "Your PIN" }), pin,
        h("label", { for: "vNew1", text: "New vault password" }), f.p1, f.meter, h("label", { for: "vNew2", text: "New vault password again" }), f.p2,
        h("div", { class: "row" }, h("button", { type: "submit" }, "Change")), out));
  }
})();
