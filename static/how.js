/* how-it-works: fills in the live verification prompt and the monitoring notice from the server (no inline script: CSP). */
(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    var ta = document.getElementById("prompt");
    fetch("/api/public/tour").then(function (r) { return r.json(); }).then(function (t) { ta.value = t.prompt; }).catch(function () { ta.value = "(could not load)"; });
    fetch("/api/public/notice").then(function (r) { return r.json(); }).then(function (n) { document.getElementById("notice").textContent = n.notice; }).catch(function () {});
    document.getElementById("copyPrompt").addEventListener("click", function (ev) {
      ta.select();
      var done = function () { ev.target.textContent = "Copied"; };
      if (navigator.clipboard) navigator.clipboard.writeText(ta.value).then(done, function () { document.execCommand("copy"); done(); });
      else { document.execCommand("copy"); done(); }
    });
  });
})();
