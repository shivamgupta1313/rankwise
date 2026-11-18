const API_BASE = "";
(function () {
  const STEPS = [
    "Validating the URL",
    "Fetching the webpage",
    "Reading the HTML",
    "Checking on-page factors",
    "Checking robots.txt and sitemap",
    "Scoring and recommendations",
  ];

  const cache = {};

  function esc(value) {
    return String(value ?? "")
      .replace(/&/g, "\u0026amp;")
      .replace(/</g, "\u0026lt;")
      .replace(/>/g, "\u0026gt;")
      .replace(/"/g, "\u0026quot;")
      .replace(/'/g, "\u0026#39;");
  }

  function markup(name) {
    if (cache[name]) return cache[name];
    cache[name] = fetch("/markup/" + name + ".html").then((res) => {
      if (!res.ok) throw new Error("missing markup");
      return res.text();
    });
    return cache[name];
  }

  async function shell(main) {
    const [header, footer] = await Promise.all([markup("header"), markup("footer")]);
    return header + main + footer;
  }

  function setNav(root, page) {
    root.querySelectorAll("[data-nav]").forEach((link) => {
      if (link.getAttribute("data-nav") === page) {
        link.setAttribute("aria-current", "page");
      } else {
        link.removeAttribute("aria-current");
      }
    });
  }

  function when(iso) {
    const then = new Date(iso).getTime();
    if (!Number.isFinite(then)) return "";
    const mins = Math.round((Date.now() - then) / 60000);
    if (mins < 1) return "Just now";
    if (mins < 60) return mins + " min ago";
    const hours = Math.round(mins / 60);
    if (hours < 24) return hours + " hr ago";
    const days = Math.round(hours / 24);
    return days + " day" + (days === 1 ? "" : "s") + " ago";
  }

  function hostOf(url) {
    try {
      return new URL(url).hostname;
    } catch {
      return url;
    }
  }

  async function renderHome(root) {
    root.innerHTML = await shell(await markup("home"));
    setNav(root, "home");
    const form = root.querySelector("#audit-form");
    const input = root.querySelector("#audit-url");
    const error = root.querySelector("#audit-error");
    const hint = root.querySelector("#audit-hint");
    const status = root.querySelector("#audit-status");
    const submit = root.querySelector("#audit-submit");
    let timer = 0;

    function setBusy(busy, label) {
      submit.disabled = busy;
      input.disabled = busy;
      root.querySelectorAll(".rw-chip").forEach((chip) => {
        chip.disabled = busy;
      });
      submit.innerHTML = busy ? '<span class="rw-spin" aria-hidden="true"></span> Auditing' : "Run audit";
      status.textContent = busy ? label : "";
      hint.hidden = busy || !error.hidden;
    }

    function showError(message) {
      error.hidden = !message;
      error.textContent = message || "";
      hint.hidden = Boolean(message);
    }

    async function run(body) {
      showError("");
      let step = 0;
      setBusy(true, STEPS[0] + "…");
      timer = window.setInterval(() => {
        step = Math.min(step + 1, STEPS.length - 1);
        status.textContent = STEPS[step] + "…";
      }, 700);
      try {
        const res = await fetch(API_BASE + "/api/seo/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!data.ok) {
          showError(data.message || "The audit could not be completed.");
          return;
        }
        window.location.assign("/audit/" + data.audit.id);
      } catch {
        showError("The audit could not be completed. Please try again.");
      } finally {
        window.clearInterval(timer);
        setBusy(false, "");
      }
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const url = input.value.trim();
      if (!url) {
        showError("Enter a website URL to start the audit.");
        input.focus();
        return;
      }
      void run({ url });
    });

    root.querySelectorAll("[data-url]").forEach((chip) => {
      chip.addEventListener("click", () => {
        input.value = chip.getAttribute("data-url") || "";
        input.focus();
      });
    });

    root.querySelector("#sample-audit").addEventListener("click", () => {
      void run({ sample: true });
    });
  }

  async function renderHistory(root) {
    root.innerHTML = await shell(await markup("history"));
    setNav(root, "history");
    const slot = root.querySelector("#history-root");
    try {
      const res = await fetch(API_BASE + "/api/seo/history");
      const data = await res.json();
      const audits = Array.isArray(data.audits) ? data.audits : [];
      if (!audits.length) {
        slot.innerHTML =
          '<div class="rw-empty"><p><strong>No audits yet</strong></p><p>Run a check from the home page and the report will appear here.</p><p><a class="rw-btn" href="/">Start an audit</a></p></div>';
        return;
      }
      slot.innerHTML =
        '<ul class="rw-history-list">' +
        audits
          .map((audit) => {
            const tone = esc(audit.status || "warning");
            return (
              '<li><a class="rw-history-item" href="/audit/' +
              esc(audit.id) +
              '"><span class="rw-score-pill">' +
              esc(audit.score) +
              "</span><span><strong>" +
              esc(hostOf(audit.url)) +
              "</strong><small>" +
              esc(audit.url) +
              "</small></span><span class=\"rw-tag rw-tag-" +
              tone +
              '">' +
              esc(when(audit.createdAt)) +
              "</span></a></li>"
            );
          })
          .join("") +
        "</ul>";
    } catch {
      slot.innerHTML =
        '<p class="rw-error">Saved audits could not be loaded. Refresh and try again.</p>';
    }
  }

  function gauge(score, grade, status) {
    const radius = 52;
    const circ = 2 * Math.PI * radius;
    const dash = Math.max(0, Math.min(100, Number(score) || 0)) / 100 * circ;
    return (
      '<div class="rw-gauge-wrap"><svg class="rw-gauge rw-gauge-' +
      esc(status || "warning") +
      '" viewBox="0 0 120 120" role="img" aria-label="Score ' +
      esc(score) +
      " out of 100, " +
      esc(grade) +
      '"><circle class="rw-gauge-track" cx="60" cy="60" r="52"></circle><circle class="rw-gauge-value" cx="60" cy="60" r="52" stroke-dasharray="' +
      dash.toFixed(1) +
      " " +
      circ.toFixed(1) +
      '"></circle><text x="60" y="68">' +
      esc(score) +
      "</text></svg></div>"
    );
  }

  function reportHtml(audit) {
    const host = hostOf(audit.fetchedUrl || audit.url);
    const recs = audit.recommendations || [];
    const recBlock = recs.length
      ? '<section class="rw-block"><h2>Recommendations</h2><ol class="rw-rec-list">' +
        recs
          .map(
            (item, index) =>
              '<li class="rw-rec rw-rec-' +
              esc(item.priority) +
              '"><p class="rw-kicker">' +
              String(index + 1).padStart(2, "0") +
              " · " +
              esc(item.priority) +
              "</p><strong>" +
              esc(item.title) +
              "</strong><p>" +
              esc(item.body) +
              "</p></li>",
          )
          .join("") +
        "</ol></section>"
      : '<p class="rw-pass-note">This page passed the selected checks. Keep titles unique as the site grows.</p>';

    const page = audit.page || {};
    const checks = (audit.checks || [])
      .map((check) => {
        const items = (check.items || [])
          .map(
            (item) =>
              "<li><span>" + esc(item.label) + "</span><b>" + esc(item.value) + "</b></li>",
          )
          .join("");
        return (
          '<details class="rw-check rw-check-' +
          esc(check.status) +
          '"><summary><span>' +
          esc(check.title) +
          '</span><span class="rw-tag rw-tag-' +
          esc(check.status) +
          '">' +
          esc(check.status) +
          " · " +
          esc(check.score) +
          "/" +
          esc(check.maxScore) +
          "</span></summary><p>" +
          esc(check.summary) +
          "</p><p>" +
          esc(check.why) +
          "</p>" +
          (check.recommendation ? "<p><strong>Next step. </strong>" + esc(check.recommendation) + "</p>" : "") +
          (items ? '<ul class="rw-items">' + items + "</ul>" : "") +
          "</details>"
        );
      })
      .join("");

    return (
      '<main class="rw-wrap rw-report"><div><a class="rw-back" href="/">← New audit</a>' +
      (audit.sample ? '<span class="rw-sample">Sample report</span>' : "") +
      '</div><section class="rw-hero">' +
      gauge(audit.score, audit.grade, audit.status) +
      '<div><p class="rw-kicker">Audited site</p><h1>' +
      esc(host) +
      '</h1><a class="rw-host" href="' +
      esc(audit.fetchedUrl) +
      '" target="_blank" rel="noreferrer">' +
      esc(audit.fetchedUrl) +
      '</a><p class="rw-grade">' +
      esc(audit.grade) +
      '</p><dl class="rw-stats"><div class="rw-stat"><span>Passed</span><strong class="rw-tag-pass">' +
      esc(audit.passed) +
      '</strong></div><div class="rw-stat"><span>Warnings</span><strong class="rw-tag-warning">' +
      esc(audit.warnings) +
      '</strong></div><div class="rw-stat"><span>Errors</span><strong class="rw-tag-error">' +
      esc(audit.errors) +
      '</strong></div><div class="rw-stat"><span>HTTP</span><strong>' +
      esc(audit.statusCode) +
      '</strong></div></dl><p class="rw-meta-line">' +
      esc(when(audit.analyzedAt)) +
      " · Fetched in " +
      esc(audit.fetchMs) +
      " ms</p></div></section>" +
      recBlock +
      '<section class="rw-block"><h2>Page snapshot</h2><div class="rw-snap-grid"><article class="rw-snap"><p class="rw-kicker">Title</p><p>' +
      esc(page.title || "Missing") +
      '</p></article><article class="rw-snap"><p class="rw-kicker">Headings</p><p>H1 ' +
      esc(page.h1Count) +
      " · H2 " +
      esc(page.h2Count) +
      " · H3 " +
      esc(page.h3Count) +
      '</p></article><article class="rw-snap"><p class="rw-kicker">Images</p><p>' +
      esc(page.imageCount) +
      " total · " +
      esc(page.missingAltCount) +
      ' missing alt</p></article><article class="rw-snap"><p class="rw-kicker">Links</p><p>' +
      esc(page.internalLinks) +
      " internal · " +
      esc(page.externalLinks) +
      ' external</p></article><article class="rw-snap"><p class="rw-kicker">Technical files</p><p>robots.txt ' +
      (page.robotsFound ? "found" : "missing") +
      " · sitemap " +
      (page.sitemapFound ? "found" : "missing") +
      "</p></article></div></section>" +
      '<section class="rw-block"><h2>Individual checks</h2><div class="rw-check-list">' +
      checks +
      "</div></section></main>"
    );
  }

  async function renderAudit(root, id) {
    root.innerHTML = await shell(
      '<main class="rw-wrap"><p class="rw-hint">Opening the report…</p></main>',
    );
    setNav(root, "home");
    try {
      const res = await fetch(API_BASE + "/api/seo/" + encodeURIComponent(id));
      if (res.status === 404) {
        root.innerHTML = await shell(
          '<main class="rw-wrap"><div class="rw-empty"><h1>Audit not found</h1><p>That report is no longer available. Start a new audit from the home page.</p><p><a class="rw-btn" href="/">New audit</a></p></div></main>',
        );
        setNav(root, "home");
        return;
      }
      const audit = await res.json();
      root.innerHTML = await shell(reportHtml(audit));
      setNav(root, "home");
    } catch {
      root.innerHTML = await shell(
        '<main class="rw-wrap"><p class="rw-error">The report could not be loaded.</p></main>',
      );
    }
  }

  let token = 0;

  window.RankWise = {
    mount(el, page, auditId) {
      const current = ++token;
      el.dataset.rw = String(current);
      const job =
        page === "history"
          ? renderHistory(el)
          : page === "audit"
            ? renderAudit(el, auditId || "")
            : renderHome(el);
      job.catch(() => {
        if (el.dataset.rw !== String(current)) return;
        el.innerHTML =
          '<main class="rw-wrap"><p class="rw-error">The page could not be drawn. Refresh and try again.</p></main>';
      });
    },
    unmount(el) {
      token += 1;
      el.innerHTML = "";
    },
  };
})();
