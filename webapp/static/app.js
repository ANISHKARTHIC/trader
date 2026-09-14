// --- Navigation (shared by rail clicks, "-> tab" links, and the symbol back button) ---

let lastNonSymbolTab = "home";

function goToTab(tabName) {
  document.querySelectorAll(".rail-btn[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tabName));
  document.querySelectorAll(".tab-content").forEach((c) => (c.style.display = "none"));
  const target = document.getElementById(`tab-${tabName}`);
  if (target) target.style.display = "";
  if (tabName !== "symbol") lastNonSymbolTab = tabName;

  if (tabName === "home") refreshHome();
  if (tabName === "portfolio") refreshHoldings();
  if (tabName === "journal") refreshJournalTab();
  if (tabName === "settings") refreshSettingsTab();
  if (tabName === "chat") loadChatHistory();
}

document.querySelectorAll(".rail-btn[data-tab]").forEach((btn) => {
  btn.addEventListener("click", () => goToTab(btn.dataset.tab));
});

document.getElementById("rail-home-mark").addEventListener("click", () => goToTab("home"));

document.addEventListener("click", (e) => {
  const gotoBtn = e.target.closest("[data-goto]");
  if (gotoBtn) goToTab(gotoBtn.dataset.goto);
});

document.getElementById("symbol-back-btn").addEventListener("click", () => goToTab(lastNonSymbolTab));

// --- Mode toggles (Quick/Deep) ---

const modeState = {}; // id -> "quick" | "deep"

document.querySelectorAll(".mode-toggle").forEach((toggle) => {
  modeState[toggle.id] = "quick";
  toggle.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      toggle.querySelectorAll(".mode-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      modeState[toggle.id] = btn.dataset.mode;
    });
  });
});

// --- Model status dot ---

async function checkModelStatus() {
  const dot = document.getElementById("status-dot");
  try {
    const res = await fetch("http://localhost:11434/api/version").catch(() => null);
    dot.className = "status-dot online";
    dot.parentElement.title = "Ollama daemon reachable";
  } catch {
    dot.className = "status-dot offline";
  }
}
checkModelStatus();

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : s;
  return div.innerHTML;
}

function fmtMoney(n) {
  if (n == null || isNaN(n)) return "—";
  return "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function fmtPrice(n) {
  if (n == null || isNaN(n)) return "—";
  return "₹" + Number(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function section(title, body) {
  if (!body) return "";
  return `
    <div class="report-section">
      <h3>${title}</h3>
      <div class="report-body">${escapeHtml(body)}</div>
    </div>
  `;
}

function bindReportToggles(root) {
  root.querySelectorAll(".report-section h3").forEach((h) => {
    h.addEventListener("click", () => h.parentElement.classList.toggle("open"));
  });
}

// =====================================================================
// Single analysis
// =====================================================================

const jobListEl = document.getElementById("job-list");
const detailEl = document.getElementById("detail-panel");
const form = document.getElementById("analyze-form");
const submitBtn = document.getElementById("submit-btn");

let selectedJobId = null;
let pollTimer = null;

document.getElementById("analysis_date").valueAsDate = new Date();

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  submitBtn.disabled = true;
  submitBtn.textContent = "Starting…";

  const body = {
    symbol: document.getElementById("symbol").value,
    analysis_date: document.getElementById("analysis_date").value,
    account_equity: parseFloat(document.getElementById("account_equity").value),
    mode: modeState["single_mode"] || "quick",
  };

  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    selectedJobId = data.job_id;
    await refreshJobList();
    renderDetail(await fetchJob(selectedJobId));
  } catch (err) {
    alert("Failed to start analysis: " + err.message);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Run analysis";
  }
});

async function fetchJob(jobId) {
  const res = await fetch(`/api/jobs/${jobId}`);
  return res.json();
}

async function refreshJobList() {
  const res = await fetch("/api/jobs");
  const jobs = await res.json();
  jobListEl.innerHTML = "";
  for (const job of jobs) {
    const li = document.createElement("li");
    li.className = "job-item" + (job.job_id === selectedJobId ? " selected" : "");
    li.innerHTML = `
      <span class="job-symbol">${job.symbol}</span>
      <span class="job-date">${job.analysis_date}</span>
      <span class="badge badge-${job.status}">${job.status}</span>
      <span class="mode-pill ${job.mode || "quick"}">${job.mode || "quick"}</span>
      ${job.rating ? `<span class="tag rating-${job.rating}" style="width:fit-content">${job.rating}</span>` : ""}
    `;
    li.addEventListener("click", async () => {
      selectedJobId = job.job_id;
      await refreshJobList();
      renderDetail(await fetchJob(job.job_id));
    });
    jobListEl.appendChild(li);
  }
}

function renderDetail(job) {
  if (job.status === "queued" || job.status === "running") {
    detailEl.innerHTML = `
      <div class="detail-header"><h2>${job.symbol}</h2></div>
      <div class="detail-meta">${job.analysis_date}</div>
      <p style="color:var(--ink-dim);font-size:13px;display:flex;align-items:center;gap:8px;">
        <span class="spinner"></span> Running TradingAgents (${job.mode || "quick"} mode). ${job.mode === "deep" ? "This takes several minutes." : "Usually under a minute or two."}
      </p>
    `;
    schedulePoll(job.job_id);
    return;
  }

  if (job.status === "error") {
    detailEl.innerHTML = `
      <div class="detail-header"><h2>${job.symbol}</h2></div>
      <div class="detail-meta">${job.analysis_date} — failed</div>
      <div class="error-box">${escapeHtml(job.error || "Unknown error")}</div>
    `;
    return;
  }

  const r = job.result;
  const plan = r.trade_plan;
  const isFlat = plan.side === "FLAT";

  const baseSymbol = job.symbol.replace(/\.(NS|BO)$/, "");
  detailEl.innerHTML = `
    <div class="detail-header">
      <h2>${job.symbol}</h2>
      <span class="rating-pill rating-${plan.source_rating}">${plan.source_rating}</span>
      <span class="mode-pill ${r.mode || "quick"}">${r.mode || "quick"}</span>
      <button type="button" class="link-btn" data-symbol="${baseSymbol}" style="margin-left:auto;">Full symbol history &rarr;</button>
    </div>
    <div class="detail-meta">${job.analysis_date}</div>

    <div class="order-status ${isFlat ? "noop" : (r.order_filled ? "filled" : "")}">
      ${isFlat ? "No trade submitted — Hold, or no valid setup" : (r.order_filled ? `Order filled: ${plan.side} ${plan.suggested_qty} shares` : `Order submitted, not yet filled: ${plan.side} ${plan.suggested_qty} shares`)}
    </div>

    ${!isFlat ? `
    <div class="trade-plan-grid">
      <div class="tp-cell"><div class="tp-label">Entry</div><div class="tp-value">${fmtPrice(plan.entry)}</div></div>
      <div class="tp-cell"><div class="tp-label">Stop</div><div class="tp-value">${fmtPrice(plan.stop)}</div></div>
      <div class="tp-cell"><div class="tp-label">Target</div><div class="tp-value">${fmtPrice(plan.target)}</div></div>
      <div class="tp-cell"><div class="tp-label">Qty</div><div class="tp-value">${plan.suggested_qty}</div></div>
      <div class="tp-cell"><div class="tp-label">Risk</div><div class="tp-value">${fmtMoney(plan.risk_amount)}</div></div>
    </div>
    ` : ""}

    ${section("Portfolio Manager decision", r.pm_decision_markdown)}
    ${section("Trader proposal", r.trader_proposal_markdown)}
    ${section("Market / technical report", r.market_report)}
    ${section("Fundamentals report", r.fundamentals_report)}
    ${section("Account report (paper)", r.account_report)}
    ${section("Fills report (paper)", r.fills_report)}
    ${section("Positions report (paper)", r.positions_report)}
  `;

  bindReportToggles(detailEl);
}

function schedulePoll(jobId) {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    if (selectedJobId !== jobId) return;
    const job = await fetchJob(jobId);
    await refreshJobList();
    renderDetail(job);
  }, 4000);
}

refreshJobList();

// =====================================================================
// Today scan
// =====================================================================

const todayForm = document.getElementById("today-form");
const todayResultsEl = document.getElementById("today-results-panel");
document.getElementById("today_analysis_date").valueAsDate = new Date();

let todayJobId = null;
let todayPollTimer = null;

todayForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = document.getElementById("today-submit-btn");
  btn.disabled = true;
  btn.textContent = "Starting…";

  const body = {
    analysis_date: document.getElementById("today_analysis_date").value,
    screen_top_n: parseInt(document.getElementById("today_screen_top_n").value, 10),
    deep_analyze_top_n: parseInt(document.getElementById("today_deep_top_n").value, 10),
    account_equity: parseFloat(document.getElementById("today_account_equity").value),
    mode: modeState["today_mode"] || "quick",
  };

  try {
    const res = await fetch("/api/today", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    todayJobId = data.job_id;
    pollToday();
  } catch (err) {
    alert("Failed to start scan: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Run scan";
  }
});

async function pollToday() {
  if (todayPollTimer) clearTimeout(todayPollTimer);
  const res = await fetch(`/api/today/${todayJobId}`);
  const job = await res.json();
  renderToday(job);
  if (job.status === "running" || job.status === "queued") {
    todayPollTimer = setTimeout(pollToday, 3000);
  }
}

function renderToday(job) {
  if (job.status === "queued" || job.status === "running") {
    const pct = job.progress_total ? Math.round((100 * job.progress_done) / job.progress_total) : 0;
    const stageLabel = job.stage === "screening" ? "Screening the Nifty 500…" : "Running the full analyst debate…";
    todayResultsEl.innerHTML = `
      <div class="today-progress">
        <div class="stage"><span class="spinner"></span> ${stageLabel}</div>
        <div class="count">${job.progress_done} / ${job.progress_total || "?"}</div>
        <div class="progress-bar-track"><div class="progress-bar-fill" style="width:${pct}%"></div></div>
      </div>
    `;
    return;
  }

  if (job.status === "error") {
    todayResultsEl.innerHTML = `<div class="error-box">${escapeHtml(job.error || "Unknown error")}</div>`;
    return;
  }

  const actions = job.result || [];
  updateHeaderStats(actions);

  if (actions.length === 0) {
    todayResultsEl.innerHTML = `<div class="empty-state"><p>No actionable results — try a wider screen or check your Ollama model setup.</p></div>`;
    return;
  }

  const groups = [
    { label: "Needs attention", filter: (a) => a.label.startsWith("Exit") || a.label.startsWith("Trim") },
    { label: "New opportunities", filter: (a) => !a.is_existing_holding && a.label.startsWith("Buy") },
    { label: "Everything else", filter: (a) => true },
  ];
  const seen = new Set();
  let html = "";
  for (const g of groups) {
    const items = actions.filter((a) => !seen.has(a.symbol) && g.filter(a));
    items.forEach((a) => seen.add(a.symbol));
    if (items.length === 0) continue;
    html += `<div class="results-group-label">${g.label}</div>`;
    html += `<div class="today-results-list">${items.map(renderActionCard).join("")}</div>`;
  }
  todayResultsEl.innerHTML = html;

  todayResultsEl.querySelectorAll(".action-card-head").forEach((h) => {
    h.addEventListener("click", () => h.parentElement.classList.toggle("open"));
  });
  bindReportToggles(todayResultsEl);
}

function updateHeaderStats(actions) {
  const held = actions.filter((a) => a.is_existing_holding);
  document.getElementById("stat-holdings").textContent = held.length;
  const totalPnl = held.reduce((sum, a) => sum + (a.holding_eval ? a.holding_eval.unrealized_pnl : 0), 0);
  const pnlEl = document.getElementById("stat-pnl");
  if (held.length === 0) {
    pnlEl.textContent = "—";
    pnlEl.style.color = "";
  } else {
    pnlEl.textContent = (totalPnl >= 0 ? "+" : "") + fmtMoney(totalPnl);
    pnlEl.style.color = totalPnl >= 0 ? "var(--gain)" : "var(--loss)";
  }
}

function verdictClass(label) {
  const l = label.toLowerCase();
  if (l.startsWith("exit")) return "exit";
  if (l.startsWith("trim")) return "trim";
  if (l.startsWith("buy")) return "buy";
  if (l.startsWith("add")) return "add";
  if (l.startsWith("hold")) return "hold";
  return "watch";
}

function renderActionCard(action) {
  const plan = action.result.trade_plan;
  const vClass = verdictClass(action.label);
  const isFlat = plan.side === "FLAT";
  const he = action.holding_eval;

  const metricsHtml = he ? `
      <div class="action-tp-row">
        <div class="metric"><span class="metric-label">Qty</span><span class="metric-value">${he.quantity}</span></div>
        <div class="metric"><span class="metric-label">Avg cost</span><span class="metric-value">${fmtPrice(he.avg_price)}</span></div>
        <div class="metric"><span class="metric-label">Last</span><span class="metric-value">${fmtPrice(he.last_price)}</span></div>
        <div class="metric"><span class="metric-label">P&amp;L</span><span class="metric-value ${he.unrealized_pnl >= 0 ? "gain" : "loss"}">${he.unrealized_pnl >= 0 ? "+" : ""}${fmtMoney(he.unrealized_pnl)} (${he.unrealized_pnl_pct.toFixed(1)}%)</span></div>
        <div class="metric"><span class="metric-label">Hard stop</span><span class="metric-value">${fmtPrice(he.hard_stop)}</span></div>
        <div class="metric"><span class="metric-label">Hard target</span><span class="metric-value">${fmtPrice(he.hard_target)}</span></div>
      </div>
  ` : (!isFlat ? `
      <div class="action-tp-row">
        <div class="metric"><span class="metric-label">Entry</span><span class="metric-value">${fmtPrice(plan.entry)}</span></div>
        <div class="metric"><span class="metric-label">Stop</span><span class="metric-value">${fmtPrice(plan.stop)}</span></div>
        <div class="metric"><span class="metric-label">Target</span><span class="metric-value">${fmtPrice(plan.target)}</span></div>
        <div class="metric"><span class="metric-label">Qty</span><span class="metric-value">${plan.suggested_qty}</span></div>
        <div class="metric"><span class="metric-label">Risk</span><span class="metric-value">${fmtMoney(plan.risk_amount)}</span></div>
      </div>
  ` : "");

  return `
    <div class="action-card verdict-${vClass}">
      <div class="action-card-head">
        <span class="action-symbol clickable-symbol" data-symbol="${action.symbol}" onclick="event.stopPropagation()">${action.symbol}</span>
        <span class="action-label-pill ${vClass}">${action.label}</span>
        <div class="action-tags">
          ${action.is_existing_holding ? '<span class="tag">held</span>' : '<span class="tag">new idea</span>'}
          ${action.screen_score != null ? `<span class="tag">score ${action.screen_score}</span>` : ""}
          <span class="mode-pill ${action.result.mode || "quick"}">${action.result.mode || "quick"}</span>
        </div>
        <svg class="chevron" viewBox="0 0 24 24" width="16" height="16" fill="none"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </div>
      ${metricsHtml}
      <div class="action-detail">
        ${section("Portfolio Manager decision", action.result.pm_decision_markdown)}
        ${section("Trader proposal", action.result.trader_proposal_markdown)}
        ${section("Market / technical report", action.result.market_report)}
        ${section("Fundamentals report", action.result.fundamentals_report)}
      </div>
    </div>
  `;
}

// =====================================================================
// Portfolio
// =====================================================================

const holdingForm = document.getElementById("holding-form");
const holdingsTableBody = document.querySelector("#holdings-table tbody");

holdingForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    symbol: document.getElementById("h_symbol").value,
    quantity: parseInt(document.getElementById("h_quantity").value, 10),
    avg_price: parseFloat(document.getElementById("h_avg_price").value),
  };
  await fetch("/api/portfolio", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  holdingForm.reset();
  refreshHoldings();
});

async function refreshHoldings() {
  const res = await fetch("/api/portfolio");
  const holdings = await res.json();
  document.getElementById("stat-holdings").textContent = holdings.length;
  holdingsTableBody.innerHTML = holdings
    .map(
      (h) => `
      <tr>
        <td><span class="clickable-symbol" data-symbol="${h.symbol}">${h.symbol}</span></td>
        <td>${h.quantity}</td>
        <td>${fmtPrice(h.avg_price)}</td>
        <td><button data-remove-symbol="${h.symbol}">Remove</button></td>
      </tr>
    `
    )
    .join("");
  holdingsTableBody.querySelectorAll("button[data-remove-symbol]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetch(`/api/portfolio/${btn.dataset.removeSymbol}`, { method: "DELETE" });
      refreshHoldings();
    });
  });
}

refreshHoldings();

// =====================================================================
// Journal / learning
// =====================================================================

let selectedScanId = null;
let journalLoaded = false;

async function refreshJournalTab() {
  document.getElementById("j_date").valueAsDate ||= new Date();
  await Promise.all([
    refreshLearningSummary(),
    refreshScanHistory(),
    refreshReflections(),
    refreshJournalEntries(),
  ]);
}

document.getElementById("journal-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    symbol: document.getElementById("j_symbol").value,
    entry_date: document.getElementById("j_date").value,
    action_taken: document.getElementById("j_action").value,
    actual_qty: document.getElementById("j_qty").value ? parseInt(document.getElementById("j_qty").value, 10) : null,
    actual_price: document.getElementById("j_price").value ? parseFloat(document.getElementById("j_price").value) : null,
    notes: document.getElementById("j_notes").value || null,
  };
  await fetch("/api/journal", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  document.getElementById("journal-form").reset();
  document.getElementById("j_notes").value = "";
  document.getElementById("j_date").valueAsDate = new Date();
  refreshJournalEntries();
});

async function refreshJournalEntries() {
  const res = await fetch("/api/journal?limit=50");
  const entries = await res.json();
  const listEl = document.getElementById("journal-entries-list");
  if (entries.length === 0) {
    listEl.innerHTML = `<p style="color:var(--ink-faint);font-size:13px;">No journal entries yet.</p>`;
    return;
  }
  const actionLabel = { followed: "Followed", ignored: "Ignored", modified: "Modified", other: "Other" };
  listEl.innerHTML = entries
    .map((e) => `
      <div class="reflection-card">
        <div class="reflection-head">
          <span class="rf-symbol clickable-symbol" data-symbol="${e.symbol}">${e.symbol}</span>
          <span class="tag">${actionLabel[e.action_taken] || e.action_taken}</span>
          ${e.actual_qty ? `<span style="color:var(--ink-dim);font-family:var(--font-mono);font-size:12px;">${e.actual_qty} @ ${fmtPrice(e.actual_price)}</span>` : ""}
          <span style="color:var(--ink-faint);margin-left:auto;">${e.entry_date}</span>
        </div>
        ${e.notes ? `<div class="reflection-body">${escapeHtml(e.notes)}</div>` : ""}
        ${e.ai_reflection ? `<div class="reflection-body" style="margin-top:6px;padding-top:6px;border-top:1px solid var(--border);"><strong>AI's own reflection:</strong> ${escapeHtml(e.ai_reflection)}</div>` : ""}
      </div>
    `)
    .join("");
}

// =====================================================================
// Settings
// =====================================================================

const llmForm = document.getElementById("llm-settings-form");
const telegramForm = document.getElementById("telegram-settings-form");
const providerSelect = document.getElementById("s_provider");
const ollamaUrlField = document.getElementById("ollama-url-field");
const apiKeyField = document.getElementById("api-key-field");

function updateProviderFieldVisibility() {
  const isOllama = providerSelect.value === "ollama";
  ollamaUrlField.style.display = isOllama ? "" : "none";
  apiKeyField.style.display = isOllama ? "none" : "";
}
providerSelect.addEventListener("change", updateProviderFieldVisibility);

async function refreshSettingsTab() {
  const res = await fetch("/api/settings");
  const s = await res.json();

  providerSelect.value = s.llm_provider;
  document.getElementById("s_ollama_url").value = s.ollama_base_url;
  document.getElementById("s_deep_model").value = s.deep_think_model;
  document.getElementById("s_quick_model").value = s.quick_think_model;
  document.getElementById("s_concurrency").value = s.max_concurrent_analyses;

  const knownModelsList = document.getElementById("known-models");
  knownModelsList.innerHTML = s.known_ollama_models.map((m) => `<option value="${m}">`).join("");

  document.getElementById("api-key-current").textContent = s.llm_api_key_set
    ? `Current: ${s.llm_api_key_masked}`
    : "No key set";

  document.getElementById("s_tg_chat").value = s.telegram_chat_id || "";
  document.getElementById("tg-token-current").textContent = s.telegram_configured
    ? `Current: ${s.telegram_bot_token_masked}`
    : "No bot token set";
  document.getElementById("telegram-status").innerHTML = s.telegram_configured
    ? '<span style="color:var(--gain);">&#9679;</span> Notifications are active.'
    : '<span style="color:var(--ink-faint);">&#9679;</span> Not configured — daily reports won\'t be sent.';

  updateProviderFieldVisibility();
}

llmForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = llmForm.querySelector("button");
  btn.disabled = true;
  const apiKey = document.getElementById("s_api_key").value;
  const body = {
    llm_provider: providerSelect.value,
    ollama_base_url: document.getElementById("s_ollama_url").value,
    deep_think_model: document.getElementById("s_deep_model").value,
    quick_think_model: document.getElementById("s_quick_model").value,
    max_concurrent_analyses: parseInt(document.getElementById("s_concurrency").value, 10),
  };
  if (apiKey) body.llm_api_key = apiKey;
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  document.getElementById("s_api_key").value = "";
  btn.disabled = false;
  await refreshSettingsTab();
});

telegramForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = telegramForm.querySelector("button");
  btn.disabled = true;
  const token = document.getElementById("s_tg_token").value;
  const body = { telegram_chat_id: document.getElementById("s_tg_chat").value };
  if (token) body.telegram_bot_token = token;
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  document.getElementById("s_tg_token").value = "";
  btn.disabled = false;
  await refreshSettingsTab();
});

async function refreshLearningSummary() {
  const res = await fetch("/api/learning/summary");
  const s = await res.json();
  document.getElementById("sum-total").textContent = s.total_resolved ?? "0";
  document.getElementById("sum-accuracy").textContent =
    s.directional_accuracy != null ? `${Math.round(s.directional_accuracy * 100)}%` : "—";
  const alphaEl = document.getElementById("sum-alpha");
  if (s.avg_alpha_vs_benchmark != null) {
    const pct = (s.avg_alpha_vs_benchmark * 100).toFixed(2);
    alphaEl.textContent = (s.avg_alpha_vs_benchmark >= 0 ? "+" : "") + pct + "%";
    alphaEl.style.color = s.avg_alpha_vs_benchmark >= 0 ? "var(--gain)" : "var(--loss)";
  } else {
    alphaEl.textContent = "—";
  }
}

async function refreshScanHistory() {
  const res = await fetch("/api/history/scans?limit=30");
  const scans = await res.json();
  const listEl = document.getElementById("scan-history-list");
  listEl.innerHTML = scans
    .map(
      (s) => `
      <li class="scan-item${s.id === selectedScanId ? " selected" : ""}" data-id="${s.id}">
        <span class="scan-date">${s.analysis_date}</span>
        <span class="scan-meta">${s.kind} · <span class="badge badge-${s.status}">${s.status}</span></span>
      </li>
    `
    )
    .join("");
  listEl.querySelectorAll(".scan-item").forEach((li) => {
    li.addEventListener("click", () => loadScanReport(parseInt(li.dataset.id, 10)));
  });
}

async function loadScanReport(scanId) {
  selectedScanId = scanId;
  await refreshScanHistory();
  const res = await fetch(`/api/history/scans/${scanId}`);
  const data = await res.json();
  const col = document.getElementById("scan-report-col");
  if (!data.decisions || data.decisions.length === 0) {
    col.innerHTML = `<h2>Report — ${data.scan.analysis_date}</h2><p style="color:var(--ink-faint);font-size:13px;">No decisions recorded for this scan.</p>`;
    return;
  }
  col.innerHTML = `
    <h2>Report — ${data.scan.analysis_date}</h2>
    ${data.decisions.map(reportRow).join("")}
  `;
}

function reportRow(d) {
  const rating = d.rating || "Hold";
  const pnl = d.holding_unrealized_pnl;
  return `
    <div class="report-decision-row">
      <span class="rd-symbol clickable-symbol" data-symbol="${d.symbol}">${d.symbol}</span>
      <span class="rating-pill rating-${rating}" style="font-size:11px;">${rating}</span>
      <span style="color:var(--ink-dim);flex:1;">${escapeHtml(d.action_label)}</span>
      ${pnl != null ? `<span class="metric-value ${pnl >= 0 ? "gain" : "loss"}" style="font-family:var(--font-mono);font-size:12.5px;">${pnl >= 0 ? "+" : ""}${fmtMoney(pnl)}</span>` : ""}
    </div>
  `;
}

async function refreshReflections() {
  const res = await fetch("/api/learning/reflections?limit=30");
  const reflections = await res.json();
  const listEl = document.getElementById("reflections-list");
  if (reflections.length === 0) {
    listEl.innerHTML = `<p style="color:var(--ink-faint);font-size:13px;">No resolved decisions yet — reflections appear once TradingAgents can compare a past call to what actually happened.</p>`;
    return;
  }
  listEl.innerHTML = reflections
    .map((r) => {
      const raw = parseFloat((r.raw_return || "0").replace("%", ""));
      const cls = raw >= 0 ? "gain" : "loss";
      return `
        <div class="reflection-card">
          <div class="reflection-head">
            <span class="rf-symbol clickable-symbol" data-symbol="${r.ticker}">${r.ticker}</span>
            <span class="tag">${r.rating}</span>
            <span class="rf-return ${cls}">${r.raw_return || "—"} raw · ${r.alpha_return || "—"} alpha</span>
            <span style="color:var(--ink-faint);margin-left:auto;">${r.resolved_date || r.date}</span>
          </div>
          <div class="reflection-body">${escapeHtml(r.reflection)}</div>
        </div>
      `;
    })
    .join("");
}

// =====================================================================
// Chat
// =====================================================================

const chatMessagesEl = document.getElementById("chat-messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
let chatHistoryLoaded = false;

// Minimal markdown -> HTML: bold, inline code, markdown tables, line breaks.
// Not a general markdown renderer — just enough for how the model actually
// formats answers (tables of holdings/prices, **bold** labels).
function renderChatMarkdown(text) {
  const escaped = escapeHtml(text);
  const lines = escaped.split("\n");
  let html = "";
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim().startsWith("|") && lines[i + 1] && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])) {
      const headerCells = line.split("|").map((c) => c.trim()).filter((c) => c !== "");
      let tableHtml = "<table><thead><tr>" + headerCells.map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>";
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const cells = lines[i].split("|").map((c) => c.trim()).filter((c) => c !== "");
        tableHtml += "<tr>" + cells.map((c) => `<td>${c}</td>`).join("") + "</tr>";
        i++;
      }
      tableHtml += "</tbody></table>";
      html += tableHtml;
      continue;
    }
    html += line + "\n";
    i++;
  }
  return html
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function appendChatBubble(role, content) {
  const row = document.createElement("div");
  row.className = `chat-bubble-row ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  bubble.innerHTML = role === "assistant" ? renderChatMarkdown(content) : escapeHtml(content);
  row.appendChild(bubble);
  chatMessagesEl.appendChild(row);
  chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
  return row;
}

async function loadChatHistory() {
  if (chatHistoryLoaded) return;
  chatHistoryLoaded = true;
  const res = await fetch("/api/chat/messages?limit=50");
  const messages = await res.json();
  if (messages.length === 0) return; // keep the empty-state/suggestions visible
  chatMessagesEl.innerHTML = "";
  for (const m of messages) appendChatBubble(m.role, m.content);
}

async function sendChatMessage(text) {
  if (!text.trim()) return;
  if (chatMessagesEl.querySelector(".empty-state")) chatMessagesEl.innerHTML = "";

  appendChatBubble("user", text);
  chatInput.value = "";
  document.getElementById("chat-send-btn").disabled = true;

  const typingRow = document.createElement("div");
  typingRow.className = "chat-bubble-row assistant";
  typingRow.innerHTML = `<div class="chat-typing"><span class="spinner"></span> thinking…</div>`;
  chatMessagesEl.appendChild(typingRow);
  chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;

  try {
    const res = await fetch("/api/chat/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    typingRow.remove();
    if (!res.ok) {
      appendChatBubble("assistant", "Something went wrong reaching the model — check Settings that Ollama is configured correctly.");
      return;
    }
    const data = await res.json();
    appendChatBubble("assistant", data.reply);
  } catch (err) {
    typingRow.remove();
    appendChatBubble("assistant", "Couldn't reach the server. Is it still running?");
  } finally {
    document.getElementById("chat-send-btn").disabled = false;
    chatInput.focus();
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  sendChatMessage(chatInput.value);
});

document.querySelectorAll(".suggestion-chip").forEach((chip) => {
  chip.addEventListener("click", () => sendChatMessage(chip.dataset.q));
});

// =====================================================================
// Symbol view — the cross-linking hub. Every clickable symbol anywhere in
// the app (portfolio rows, action cards, journal entries, chat) routes here.
// =====================================================================

function clickableSymbol(sym) {
  return `<span class="clickable-symbol" data-symbol="${sym}">${sym}</span>`;
}

// Event delegation: any element anywhere with a data-symbol attribute opens
// that symbol's view, including content injected after this script loads
// (action cards, chat replies, etc.) — no per-render rebinding needed.
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-symbol]");
  if (el) openSymbol(el.dataset.symbol);
});

async function openSymbol(symbol) {
  goToTab("symbol");
  document.getElementById("symbol-title").textContent = symbol;
  const bodyEl = document.getElementById("symbol-body");
  bodyEl.innerHTML = `<div class="empty-state"><p><span class="spinner"></span> Loading ${escapeHtml(symbol)}&hellip;</p></div>`;

  const res = await fetch(`/api/symbol/${encodeURIComponent(symbol)}`);
  if (!res.ok) {
    bodyEl.innerHTML = `<div class="error-box">Could not load data for ${escapeHtml(symbol)}.</div>`;
    return;
  }
  const data = await res.json();
  renderSymbolView(data);
}

function renderSymbolView(data) {
  const bodyEl = document.getElementById("symbol-body");
  const holdingPill = document.getElementById("symbol-holding-pill");
  holdingPill.style.display = data.holding ? "" : "none";

  const q = data.quote || {};
  const quoteHtml = q.error
    ? `<p style="color:var(--ink-faint);font-size:13px;">${escapeHtml(q.error)}</p>`
    : `
      <div class="trade-plan-grid">
        <div class="tp-cell"><div class="tp-label">Last</div><div class="tp-value">${fmtPrice(q.last_price)}</div></div>
        <div class="tp-cell"><div class="tp-label">Prev close</div><div class="tp-value">${fmtPrice(q.previous_close)}</div></div>
        <div class="tp-cell"><div class="tp-label">52w high</div><div class="tp-value">${fmtPrice(q.year_high)}</div></div>
        <div class="tp-cell"><div class="tp-label">52w low</div><div class="tp-value">${fmtPrice(q.year_low)}</div></div>
      </div>
    `;

  let holdingHtml = "";
  if (data.holding) {
    const pnl = q.last_price != null ? (q.last_price - data.holding.avg_price) * data.holding.quantity : null;
    holdingHtml = `
      <div class="symbol-section">
        <h3>Your position</h3>
        <div class="trade-plan-grid">
          <div class="tp-cell"><div class="tp-label">Qty</div><div class="tp-value">${data.holding.quantity}</div></div>
          <div class="tp-cell"><div class="tp-label">Avg cost</div><div class="tp-value">${fmtPrice(data.holding.avg_price)}</div></div>
          ${pnl != null ? `<div class="tp-cell"><div class="tp-label">P&amp;L</div><div class="tp-value ${pnl >= 0 ? "gain" : "loss"}">${pnl >= 0 ? "+" : ""}${fmtMoney(pnl)}</div></div>` : ""}
        </div>
      </div>
    `;
  }

  const decisionsHtml = data.decisions.length
    ? data.decisions
        .map(
          (d) => `
          <div class="action-card" style="margin-bottom:8px;">
            <div class="action-card-head">
              <span class="action-symbol">${d.analysis_date}</span>
              <span class="rating-pill rating-${d.rating}">${d.rating}</span>
              <span class="tag">${escapeHtml(d.action_label)}</span>
              <svg class="chevron" viewBox="0 0 24 24" width="16" height="16" fill="none"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </div>
            <div class="action-detail">
              ${section("Portfolio Manager decision", d.pm_decision_markdown)}
              ${section("Trader proposal", d.trader_proposal_markdown)}
            </div>
          </div>
        `
        )
        .join("")
    : `<p style="color:var(--ink-faint);font-size:13px;">No past analysis for this symbol yet.</p>`;

  const reflectionsHtml = data.reflections.length
    ? data.reflections
        .map(
          (r) => `
          <div class="reflection-card">
            <div class="reflection-head">
              <span class="tag">${r.rating}</span>
              <span class="rf-return ${(parseFloat(r.raw_return) || 0) >= 0 ? "gain" : "loss"}">${r.raw_return || "—"} raw · ${r.alpha_return || "—"} alpha</span>
              <span style="color:var(--ink-faint);margin-left:auto;">${r.date}</span>
            </div>
            <div class="reflection-body">${escapeHtml(r.reflection)}</div>
          </div>
        `
        )
        .join("")
    : `<p style="color:var(--ink-faint);font-size:13px;">No resolved outcomes yet.</p>`;

  const journalHtml = data.journal_entries.length
    ? data.journal_entries
        .map(
          (j) => `
          <div class="reflection-card">
            <div class="reflection-head">
              <span class="tag">${j.action_taken}</span>
              <span style="color:var(--ink-faint);margin-left:auto;">${j.entry_date}</span>
            </div>
            ${j.notes ? `<div class="reflection-body">${escapeHtml(j.notes)}</div>` : ""}
          </div>
        `
        )
        .join("")
    : `<p style="color:var(--ink-faint);font-size:13px;">No journal entries for this symbol.</p>`;

  bodyEl.innerHTML = `
    <div class="symbol-section">
      <h3>Live quote</h3>
      ${quoteHtml}
    </div>
    ${holdingHtml}
    <div class="symbol-section">
      <h3>Past decisions</h3>
      ${decisionsHtml}
    </div>
    <div class="symbol-section">
      <h3>What the AI learned</h3>
      ${reflectionsHtml}
    </div>
    <div class="symbol-section">
      <h3>Your journal</h3>
      ${journalHtml}
    </div>
  `;

  bodyEl.querySelectorAll(".action-card-head").forEach((h) => {
    h.addEventListener("click", () => h.parentElement.classList.toggle("open"));
  });
  bindReportToggles(bodyEl);
}

// =====================================================================
// Home overview
// =====================================================================

async function refreshHome() {
  const res = await fetch("/api/home");
  const data = await res.json();
  renderHomePortfolio(data);
  renderHomeActions(data);
  renderHomePerformance(data);
  renderHomeChat(data);
}

function renderHomePortfolio(data) {
  const el = document.getElementById("home-portfolio-body");
  if (!data.holdings.length) {
    el.innerHTML = `<div class="empty-state small"><p>No holdings yet. Add one in Portfolio.</p></div>`;
    return;
  }
  const rows = data.holdings
    .map((h) => {
      const pnl = h.unrealized_pnl;
      return `
        <div class="home-portfolio-row" data-symbol="${h.symbol}">
          <span class="hp-symbol clickable-symbol" data-symbol="${h.symbol}">${h.symbol}</span>
          <span class="hp-qty">${h.quantity} sh</span>
          <span class="hp-price">${fmtPrice(h.last_price)}</span>
          ${pnl != null ? `<span class="metric-value ${pnl >= 0 ? "gain" : "loss"}" style="font-family:var(--font-mono);font-size:12.5px;">${pnl >= 0 ? "+" : ""}${fmtMoney(pnl)}</span>` : ""}
        </div>
      `;
    })
    .join("");
  const total = data.total_unrealized_pnl;
  el.innerHTML = `
    ${rows}
    ${total != null ? `
    <div class="home-total-pnl">
      <span>Total unrealized P&amp;L</span>
      <span class="value ${total >= 0 ? "gain" : "loss"}">${total >= 0 ? "+" : ""}${fmtMoney(total)}</span>
    </div>` : ""}
  `;
}

function renderHomeActions(data) {
  const el = document.getElementById("home-actions-body");
  const scan = data.latest_scan;
  if (!scan) {
    el.innerHTML = `<div class="empty-state small"><p>No scan run yet. Run one from Today.</p></div>`;
    return;
  }
  if (scan.status === "running") {
    el.innerHTML = `<div class="empty-state small"><p><span class="spinner"></span> A scan from ${scan.analysis_date} is running&hellip;</p></div>`;
    return;
  }
  if (scan.status === "error") {
    el.innerHTML = `<div class="empty-state small"><p>The last scan (${scan.analysis_date}) failed. Try again from Today.</p></div>`;
    return;
  }
  const actions = data.latest_scan_top_actions;
  if (!actions.length) {
    el.innerHTML = `<div class="empty-state small"><p>Nothing needs attention from the ${scan.analysis_date} scan.</p></div>`;
    return;
  }
  el.innerHTML = actions
    .map(
      (d) => `
      <div class="home-action-row" data-symbol="${d.symbol}">
        <span class="clickable-symbol">${d.symbol}</span>
        <span class="tag">${escapeHtml(d.action_label)}</span>
        <span class="rating-pill rating-${d.rating}" style="margin-left:auto;font-size:11px;">${d.rating}</span>
      </div>
    `
    )
    .join("");
}

function renderHomePerformance(data) {
  const el = document.getElementById("home-performance-body");
  const p = data.performance;
  if (!p.total_resolved) {
    el.innerHTML = `<div class="empty-state small"><p>No resolved decisions yet — check back after a few days of scans.</p></div>`;
    return;
  }
  const accuracy = p.directional_accuracy != null ? `${Math.round(p.directional_accuracy * 100)}%` : "—";
  const alpha = p.avg_alpha_vs_benchmark;
  el.innerHTML = `
    <div class="home-stat-row"><span>Resolved decisions</span><span class="value">${p.total_resolved}</span></div>
    <div class="home-stat-row"><span>Directional accuracy</span><span class="value">${accuracy}</span></div>
    <div class="home-stat-row"><span>Avg. alpha vs. benchmark</span><span class="value" style="color:${alpha >= 0 ? "var(--gain)" : "var(--loss)"}">${alpha != null ? (alpha >= 0 ? "+" : "") + (alpha * 100).toFixed(2) + "%" : "—"}</span></div>
  `;
}

function renderHomeChat(data) {
  const el = document.getElementById("home-chat-body");
  if (!data.recent_chat.length) {
    el.innerHTML = `<div class="empty-state small"><p>No conversations yet. Ask something in Chat.</p></div>`;
    return;
  }
  el.innerHTML = data.recent_chat
    .slice(-4)
    .map(
      (m) => `
      <div class="home-chat-msg">
        <span class="role-label">${m.role === "user" ? "You" : "Verdict"}:</span>
        ${escapeHtml(m.content.slice(0, 140))}${m.content.length > 140 ? "…" : ""}
      </div>
    `
    )
    .join("");
}

// Home is the default tab, so load it now rather than waiting for a rail click.
refreshHome();
