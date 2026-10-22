/* Home feed view (module "feed") + "Share to feed" (a read-only copy of a project, build or settings version, posted by its owner).
   Loaded after app.js; uses TB.h / TB.api. Everything a member wrote is put on the page with textContent (TB.h "text"), previews of
   notes/spreadsheets are strings in <pre> or table cells - never HTML. Pictures/videos come from /api/m/feed/file (served inline only
   for those types). There is no scheduled publishing: nothing reaches the feed unless its owner presses "Share to feed". */
(function () {
  "use strict";
  var TB = window.TB, h = TB.h, api = TB.api;

  function err(e) { return h("div", { class: "err", role: "alert", text: (e && e.message) || String(e) }); }
  function pill(t, k) { return h("span", { class: "pill " + (k || ""), text: t }); }
  function mb(n) { return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }
  function diffPre(text) {
    var pre = h("pre", { class: "diff" });
    (text || "").split("\n").slice(0, 4000).forEach(function (ln) {
      var c = ln.indexOf("+") === 0 && ln.indexOf("+++") !== 0 ? "add" : ln.indexOf("-") === 0 && ln.indexOf("---") !== 0 ? "del" : ln.indexOf("@@") === 0 ? "hunk" : "";
      pre.appendChild(h("span", { class: c, text: ln + "\n" }));
    });
    return pre;
  }
  function replace(el, node) { el.textContent = ""; if (node) el.appendChild(node); }

  // ---------------------------------------------------------------- comments + reactions
  function social(base, idKey, id, data) {
    var box = h("div", { class: "social" });
    function paint(d) {
      box.textContent = "";
      var row = h("div", { class: "reactions", role: "group", "aria-label": "Reactions" });
      (d.reactions || []).forEach(function (r) {
        row.appendChild(h("button", { class: "react" + (r.mine ? " mine" : ""), type: "button", "aria-pressed": r.mine ? "true" : "false",
          "aria-label": "React " + r.emoji + " (" + r.count + ")", onclick: function () {
            var b = {}; b[idKey] = id; b.emoji = r.emoji;
            api("POST", base + "/react", b).then(paint).catch(function (e) { box.appendChild(err(e)); });
          } }, r.emoji, r.count ? h("span", { class: "n", text: String(r.count) }) : null));
      });
      box.appendChild(row);
      var list = h("div", { class: "comments" });
      (d.comments || []).forEach(function (c) {
        list.appendChild(h("div", { class: "comment" }, h("strong", { text: c.author_name }), h("span", { class: "muted", text: " " + c.at }),
          (c.mine || TB.me.admin) ? h("button", { class: "linkish", type: "button", onclick: function () {
            if (!confirm("Delete this comment?")) return;
            var b = {}; b[idKey] = id; b.comment = c.id;
            api("POST", base + "/comment/delete", b).then(paint).catch(function (e) { box.appendChild(err(e)); });
          } }, "delete") : null,
          h("div", { class: "ctext", text: c.text })));
      });
      box.appendChild(list);
      var inp = h("input", { placeholder: "Write a comment", "aria-label": "Write a comment", maxlength: "4000" });
      box.appendChild(h("form", { class: "row cform", onsubmit: function (ev) {
        ev.preventDefault(); if (!inp.value.trim()) return;
        var b = {}; b[idKey] = id; b.text = inp.value;
        api("POST", base + "/comment", b).then(paint).catch(function (e) { box.appendChild(err(e)); });
      } }, inp, h("button", { class: "small", type: "submit" }, "Comment")));
    }
    paint(data);
    return box;
  }

  // ---------------------------------------------------------------- attachments
  function attachment(a) {
    var wrap = h("div", { class: "att" });
    if (a.kind === "picture") wrap.appendChild(h("a", { href: a.url, target: "_blank", rel: "noopener" }, h("img", { src: a.url, alt: a.name, loading: "lazy" })));
    else if (a.kind === "video") wrap.appendChild(h("video", { src: a.url, controls: true, preload: "metadata" }));
    var pv = h("div");
    wrap.appendChild(h("div", { class: "row attrow" }, pill(a.kind), h("span", { class: "fname", text: a.name }), h("span", { class: "muted", text: mb(a.size) }),
      a.has_preview ? h("button", { class: "ghost small", type: "button", onclick: function () {
        if (pv.firstChild) { pv.textContent = ""; return; }
        api("GET", "/api/m/feed/preview?id=" + a.id).then(function (j) { replace(pv, previewNode(j.preview)); }).catch(function (e) { replace(pv, err(e)); });
      } }, "Preview") : null,
      h("a", { class: "btn ghost small", href: a.download, download: a.name, text: "Download" })));
    wrap.appendChild(pv);
    return wrap;
  }
  function previewNode(p) {
    if (!p || p.type === "none") return h("p", { class: "muted", text: (p && p.why) || "no preview" });
    if (p.type === "text") return h("pre", { class: "preview", text: p.text + (p.truncated ? "\n... (first part only - download for all of it)" : "") });
    var t = h("table", { class: "sheet" });
    (p.rows || []).forEach(function (r, i) { t.appendChild(h("tr", {}, r.map(function (c) { return h(i === 0 ? "th" : "td", { text: c }); }))); });
    return h("div", { class: "tbl" }, t, h("p", { class: "muted", text: "First " + (p.rows || []).length + " rows (download for the whole file)." }));
  }

  // ---------------------------------------------------------------- shared copies (Share to feed)
  var KIND_LABEL = { project: "shared project", build: "shared build", version: "shared settings version" };
  function sharedData(kind, d) {
    var box = h("div", { class: "shared-copy" });
    box.appendChild(h("p", { class: "muted", text: "Read-only copy, taken when it was shared. It does not give anyone access to the project or its git remote." }));
    if (kind === "build") {
      box.appendChild(h("h3", { text: "Task" })); box.appendChild(h("pre", { text: d.task || "" }));
      if (d.summary) { box.appendChild(h("h3", { text: "Summary" })); box.appendChild(h("pre", { text: d.summary })); }
      box.appendChild(h("h3", { text: "Files changed" }));
      box.appendChild((d.files || []).length ? h("ul", {}, d.files.map(function (f) { return h("li", {}, h("code", { text: f.status + " " + f.path })); })) : h("p", { class: "muted", text: "-" }));
      box.appendChild(h("h3", { text: "Tests (run in the jail)" })); box.appendChild(h("pre", { text: d.tests_text || "(none)" }));
      box.appendChild(h("h3", { text: "Diff" })); box.appendChild(diffPre(d.diff));
    } else if (kind === "project") {
      box.appendChild(h("p", { text: (d.files || []).length + " files - created " + (d.created || "") }));
      if ((d.commits || []).length) box.appendChild(h("div", { class: "tbl" }, h("table", {}, d.commits.map(function (c) { return h("tr", {}, h("td", {}, h("code", { text: c.sha })), h("td", { text: c.at }), h("td", { text: c.subject })); }))));
      box.appendChild(h("h3", { text: "Changes since the starter template" })); box.appendChild(h("pre", { text: d.stat || "(none)" })); box.appendChild(diffPre(d.diff));
      box.appendChild(h("h3", { text: "Files" }));
      box.appendChild(h("ul", {}, (d.files || []).slice(0, 200).map(function (f) { return h("li", {}, h("code", { text: f })); })));
    } else if (kind === "version") {
      box.appendChild(h("p", { text: "Model: " + d.model + " - version '" + d.name + "' - saved " + (d.created || "") }));
      box.appendChild(h("pre", { text: JSON.stringify(d.settings, null, 2) }));
    }
    return box;
  }
  function sharedBlock(p) {
    var s = p.shared, more = h("div"), sm = s.summary || {};
    return h("div", { class: "card shared" },
      h("div", { class: "row" }, pill(KIND_LABEL[s.kind] || s.kind, "ok"), h("strong", { text: s.title }),
        sm.verdict ? pill(sm.verdict, /pass/.test(sm.verdict) ? "ok" : /FAIL/.test(sm.verdict) ? "bad" : "") : null),
      sm.summary ? h("p", { text: sm.summary }) : sm.task ? h("p", { class: "muted", text: sm.task }) : null,
      h("button", { class: "ghost small", type: "button", onclick: function () {
        if (more.firstChild) { more.textContent = ""; return; }
        api("GET", "/api/m/feed/shared?post=" + encodeURIComponent(p.id)).then(function (j) { replace(more, sharedData(s.kind, j.post.data || {})); }).catch(function (e) { replace(more, err(e)); });
      } }, "Open the read-only copy"), more);
  }

  function postNode(p, emoji, reload) {
    var body = h("div", { class: "ptext", text: p.text });
    var card = h("article", { class: "card post", id: "post-" + p.id });
    var tools = h("div", { class: "row ptools" });
    if (p.can_edit) tools.appendChild(h("button", { class: "linkish", type: "button", onclick: function () {
      var ta = h("textarea", { "aria-label": "Edit post" }); ta.value = p.text;
      var out = h("div");
      replace(body, h("div", {}, ta, h("div", { class: "row" }, h("button", { class: "small", type: "button", onclick: function () {
        api("POST", "/api/m/feed/post/edit", { id: p.id, text: ta.value }).then(reload).catch(function (e) { replace(out, err(e)); });
      } }, "Save"), h("button", { class: "ghost small", type: "button", onclick: reload }, "Cancel")), out));
    } }, "edit"));
    if (p.shared && p.can_edit) tools.appendChild(h("button", { class: "linkish", type: "button", onclick: function () {
      if (!confirm("Unshare? The post and its copy are removed from the feed.")) return;
      api("POST", "/api/m/feed/unshare", { key: p.shared.key }).then(function () { TB.shares = null; reload(); }).catch(function (e) { card.appendChild(err(e)); });
    } }, "unshare"));
    else if (p.can_delete) tools.appendChild(h("button", { class: "linkish", type: "button", onclick: function () {
      if (!confirm(p.can_edit ? "Delete your post and its files?" : "Delete this post (admin)?")) return;
      api("POST", "/api/m/feed/post/delete", { id: p.id }).then(reload).catch(function (e) { card.appendChild(err(e)); });
    } }, "delete"));
    card.appendChild(h("div", { class: "row phead" }, h("strong", { text: p.author_name }), h("span", { class: "muted", text: p.created + (p.edited ? " (edited)" : "") }), tools));
    if (p.text) card.appendChild(body);
    if (p.shared) card.appendChild(sharedBlock(p));
    p.attachments.forEach(function (a) { card.appendChild(attachment(a)); });
    card.appendChild(social("/api/m/feed", "post", p.id, p));
    return card;
  }

  // ---------------------------------------------------------------- the "Share to feed" / "Unshare" button (used by app.js)
  function loadShares() {
    if (!TB.shares) TB.shares = api("GET", "/api/m/feed/shares").then(function (j) { return j.shares || {}; }).catch(function () { return {}; });
    return TB.shares;
  }
  TB.shareButton = function (body, key) {
    var wrap = h("span", { class: "share-box" }), out = h("span");
    function paint(shared) {
      wrap.textContent = "";
      wrap.appendChild(shared
        ? h("button", { class: "ghost small", type: "button", "data-share": key, onclick: function () {
            if (!confirm("Unshare? The read-only copy is removed from the home feed.")) return;
            api("POST", "/api/m/feed/unshare", { key: key }).then(function () { TB.shares = null; paint(false); }).catch(function (e) { replace(out, err(e)); });
          } }, "Unshare (on the feed now)")
        : h("button", { class: "small", type: "button", "data-share": key, onclick: function () {
            var note = prompt("Share a read-only copy to the home feed - the whole team will see it. Add a note (optional):", "");
            if (note === null) return;
            api("POST", "/api/m/feed/share", Object.assign({ text: note }, body)).then(function () { TB.shares = null; paint(true); }).catch(function (e) { replace(out, err(e)); });
          } }, "Share to feed"));
      wrap.appendChild(out);
    }
    loadShares().then(function (s) { paint(!!s[key]); });
    return wrap;
  };

  function upload(file, onProgress) {
    return new Promise(function (resolve, reject) {
      var x = new XMLHttpRequest();
      x.open("POST", "/api/m/feed/upload?name=" + encodeURIComponent(file.name));
      x.setRequestHeader("X-Teambox", "1");
      x.upload.onprogress = function (e) { if (e.lengthComputable) onProgress(e.loaded / e.total); };
      x.onload = function () {
        var j = {}; try { j = JSON.parse(x.responseText); } catch (e) {}
        if (x.status === 200) resolve(j.file); else reject(new Error(file.name + ": " + (j.error || "HTTP " + x.status)));
      };
      x.onerror = function () { reject(new Error(file.name + ": the upload failed")); };
      x.send(file);
    });
  }

  function composer(limits, reload) {
    var ta = h("textarea", { id: "postText", placeholder: "Share something with the team", maxlength: "10000" });
    var fi = h("input", { type: "file", id: "postFiles", multiple: true });
    var status = h("div");
    var btn = h("button", { type: "submit" }, "Post");
    return h("form", { class: "card composer", onsubmit: function (ev) {
      ev.preventDefault();
      var files = Array.prototype.slice.call(fi.files || []);
      if (!ta.value.trim() && !files.length) return;
      btn.disabled = true;
      var ids = [], chain = Promise.resolve();
      files.forEach(function (f) {
        chain = chain.then(function () {
          var bar = h("div"); replace(status, h("div", {}, h("span", { class: "muted", text: "Uploading " + f.name }), h("div", { class: "progress" }, bar)));
          return upload(f, function (r) { bar.style.width = Math.round(r * 100) + "%"; }).then(function (x) { ids.push(x.id); });
        });
      });
      chain.then(function () { return api("POST", "/api/m/feed/posts", { text: ta.value, attachments: ids }); })
        .then(function () { ta.value = ""; fi.value = ""; status.textContent = ""; reload(); })
        .catch(function (e) {
          replace(status, err(e));
          ids.forEach(function (id) { api("POST", "/api/m/feed/upload/discard", { id: id }).catch(function () {}); });   // free the quota
        })
        .then(function () { btn.disabled = false; });
    } }, h("label", { for: "postText", text: "New post" }), ta,
      h("label", { for: "postFiles", text: "Attach files (pictures, videos, notes, spreadsheets, PDFs, documents)" }), fi,
      h("p", { class: "muted small", text: "Up to " + limits.max_files_per_post + " files, " + limits.max_file_mb + " MB each. You have used " + limits.used_mb + " of your " + limits.member_quota_mb + " MB. Everyone on the team sees the home feed." }),
      h("div", { class: "row" }, btn), status);
  }

  function homeFeed(el, noTitle) {
    function load() {
      api("GET", "/api/m/feed/posts?limit=30").then(function (j) {
        el.textContent = "";
        if (!noTitle) el.appendChild(h("h2", { text: "Home feed" }));
        el.appendChild(composer(j.limits, load));
        el.appendChild(h("p", { class: "muted small", text: "Your projects, builds and settings versions stay private until you press 'Share to feed' on one of them." }));
        if (!j.posts.length) el.appendChild(h("p", { class: "muted", text: "No posts yet - be the first." }));
        j.posts.forEach(function (p) { el.appendChild(postNode(p, j.emoji, load)); });
      }).catch(function (e) { replace(el, err(e)); });
    }
    load();
  }
  TB.homeFeed = homeFeed;
  TB.views.feed = function (m) {
    var v = document.getElementById("view"); v.textContent = "";
    v.appendChild(h("h1", { text: m.title })); v.appendChild(h("p", { class: "muted", text: m.help }));
    var el = h("div", { id: "home-feed" }); v.appendChild(el); homeFeed(el, true);
  };
})();
