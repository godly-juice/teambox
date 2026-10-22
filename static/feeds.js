/* Home feed + weekly team feed views (modules "feed" and "team-feed"). Loaded after app.js; uses TB.h / TB.api.
   Everything a member wrote is put on the page with textContent (TB.h "text"), previews of notes/spreadsheets are strings in <pre>
   or table cells - never HTML. Pictures/videos come from /api/m/feed/file (served inline only for those types). */
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

  // ---------------------------------------------------------------- comments + reactions (both feeds)
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

  // ---------------------------------------------------------------- home feed
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
    if (p.can_delete) tools.appendChild(h("button", { class: "linkish", type: "button", onclick: function () {
      if (!confirm(p.can_edit ? "Delete your post and its files?" : "Delete this post (admin)?")) return;
      api("POST", "/api/m/feed/post/delete", { id: p.id }).then(reload).catch(function (e) { card.appendChild(err(e)); });
    } }, "delete"));
    card.appendChild(h("div", { class: "row phead" }, h("strong", { text: p.author_name }), h("span", { class: "muted", text: p.created + (p.edited ? " (edited)" : "") }), tools));
    card.appendChild(body);
    p.attachments.forEach(function (a) { card.appendChild(attachment(a)); });
    card.appendChild(social("/api/m/feed", "post", p.id, p));
    return card;
  }

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

  // ---------------------------------------------------------------- weekly team feed
  var TYPE_LABEL = { build: "build", commits: "commits", sandbox: "new sandbox", version: "settings version" };
  function tfView(m) {
    var v = document.getElementById("view"); v.textContent = "";
    var p = (location.hash || "").split("/").slice(3).map(decodeURIComponent);    // #/m/team-feed/<week|item>/<id>
    if (p[0] === "item" && p[1]) return tfItem(v, p[1]);
    api("GET", "/api/m/team-feed/weeks").then(function (w) {
      v.appendChild(h("h1", { text: m.title }));
      v.appendChild(h("p", { text: "Published " + w.schedule + ". Next: " + w.next_publish + ". Until then, everyone's new work stays private (owner + admins). Published work is a read-only copy - nobody gets access to anyone's sandbox." }));
      var pend = h("div", { class: "card" }, h("h3", { text: TB.me.admin ? "Waiting for the next publish (all members)" : "Your work that goes out next time" }));
      v.appendChild(pend);
      api("GET", "/api/m/team-feed/pending").then(function (j) {
        pend.appendChild(j.pending.length ? h("ul", {}, j.pending.map(function (x) { return h("li", { text: (TB.me.admin ? x.owner_name + ": " : "") + (TYPE_LABEL[x.type] || x.type) + ": " + x.title }); }))
          : h("p", { class: "muted", text: "Nothing yet." }));
        pend.appendChild(h("p", { class: "muted small", text: j.note }));
      }).catch(function (e) { pend.appendChild(err(e)); });
      if (w.admin) {
        var out = h("div");
        v.appendChild(h("div", { class: "card" }, h("h3", { text: "Admin: publish now (for testing)" }),
          h("p", { class: "muted", text: "Makes everything that exists right now visible to the whole team. Tuesday then publishes only what is new." }),
          h("button", { class: "ghost", type: "button", onclick: function () {
            if (!confirm("Publish everyone's work now, to the whole team?")) return;
            api("POST", "/api/m/team-feed/publish-now", { confirm: true }).then(function () { tfView(m); }).catch(function (e) { replace(out, err(e)); });
          } }, "Publish now"), out));
      }
      if (!w.weeks.length) v.appendChild(h("p", { class: "muted", text: "Nothing has been published yet." }));
      var sel = p[0] === "week" && p[1] ? p[1] : (w.weeks[0] && w.weeks[0].key);
      if (w.weeks.length) {
        v.appendChild(h("div", { class: "row weeks" }, w.weeks.map(function (x) {
          return h("a", { class: "btn small" + (x.key === sel ? "" : " ghost"), href: "#/m/team-feed/week/" + encodeURIComponent(x.key),
            text: (x.manual ? "Early publish " + x.published_text : "Tuesday " + x.key) + " (" + x.count + ")" });
        })));
        var list = h("div"); v.appendChild(list);
        api("GET", "/api/m/team-feed/items?week=" + encodeURIComponent(sel)).then(function (j) {
          if (j.week.late) list.appendChild(h("p", { class: "muted", text: "Published late (" + j.week.published_text + ") - the team box was off at 14:00 and caught up." }));
          if (!j.items.length) list.appendChild(h("p", { class: "muted", text: "Nothing new that week." }));
          j.items.forEach(function (it) { list.appendChild(tfCard(it)); });
        }).catch(function (e) { list.appendChild(err(e)); });
      }
    }).catch(function (e) { v.appendChild(err(e)); });
  }
  function tfCard(it) {
    return h("article", { class: "card post" },
      h("div", { class: "row phead" }, pill(TYPE_LABEL[it.type] || it.type), h("strong", { text: it.owner_name }), h("span", { class: "muted", text: it.created || "" }),
        it.verdict ? pill(it.verdict, /pass/.test(it.verdict) ? "ok" : /FAIL/.test(it.verdict) ? "bad" : "") : null),
      h("h3", {}, h("a", { href: "#/m/team-feed/item/" + it.id, text: it.title })),
      h("div", { class: "row" }, it.reactions.filter(function (r) { return r.count; }).map(function (r) { return pill(r.emoji + " " + r.count); }),
        h("a", { href: "#/m/team-feed/item/" + it.id, text: it.comment_count + " comment(s) - open" })));
  }
  function tfItem(v, id) {
    api("GET", "/api/m/team-feed/item?id=" + encodeURIComponent(id)).then(function (j) {
      var it = j.item, d = it.data || {};
      v.appendChild(h("p", {}, h("a", { href: "#/m/team-feed/week/" + encodeURIComponent(it.week), text: "Back to the team feed" })));
      v.appendChild(h("h1", { text: it.title }));
      v.appendChild(h("div", { class: "row" }, pill(TYPE_LABEL[it.type] || it.type), pill(it.owner_name), d.status ? pill(d.status) : null,
        d.verdict ? pill(d.verdict, /pass/.test(d.verdict) ? "ok" : /FAIL/.test(d.verdict) ? "bad" : "") : null));
      v.appendChild(h("p", { class: "muted", text: "Read-only copy, taken when it was published. It does not give anyone access to the sandbox or its git remote." }));
      if (it.type === "build") {
        v.appendChild(h("h2", { text: "Task" })); v.appendChild(h("pre", { text: d.task || "" }));
        if (d.summary) { v.appendChild(h("h2", { text: "Summary" })); v.appendChild(h("pre", { text: d.summary })); }
        v.appendChild(h("h2", { text: "Files changed" }));
        v.appendChild(d.files.length ? h("ul", {}, d.files.map(function (f) { return h("li", {}, h("code", { text: f.status + " " + f.path })); })) : h("p", { class: "muted", text: "-" }));
        v.appendChild(h("h2", { text: "Tests (run in the jail)" })); v.appendChild(h("pre", { text: d.tests_text || "(none)" }));
        v.appendChild(h("h2", { text: "Diff" })); v.appendChild(diffPre(d.diff));
      } else if (it.type === "commits") {
        v.appendChild(h("div", { class: "tbl" }, h("table", {}, d.commits.map(function (c) { return h("tr", {}, h("td", {}, h("code", { text: c.sha })), h("td", { text: c.at }), h("td", { text: c.author }), h("td", { text: c.subject })); }))));
        v.appendChild(h("h2", { text: "Changes" })); v.appendChild(h("pre", { text: d.stat || "" })); v.appendChild(diffPre(d.diff));
      } else if (it.type === "version") {
        v.appendChild(h("p", { text: "Model: " + d.model + " - version '" + d.name + "' - saved " + (d.created || "") }));
        v.appendChild(h("pre", { text: JSON.stringify(d.settings, null, 2) }));
      } else if (it.type === "sandbox") {
        v.appendChild(h("p", { text: "Created " + (d.created || "") + " - " + d.files.length + " files" }));
        v.appendChild(h("ul", {}, d.files.slice(0, 200).map(function (f) { return h("li", {}, h("code", { text: f })); })));
      }
      v.appendChild(h("h2", { text: "Comments & reactions" }));
      v.appendChild(social("/api/m/team-feed", "item", it.id, it));
    }).catch(function (e) { v.appendChild(err(e)); });
  }
  TB.views["team-feed"] = tfView;
})();
