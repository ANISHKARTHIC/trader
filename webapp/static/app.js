// --- Rail navigation ---

document.querySelectorAll(".rail-btn[data-tab]").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".rail-btn[data-tab]").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach((c) => (c.style.display = "none"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).style.display = "";
    if (btn.dataset.tab === "portfolio") refreshHoldings();
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
        <span class="spinner"></span> Running the full TradingAgents analyst debate. This takes several minutes.
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

  detailEl.innerHTML = `
    <div class="detail-header">
      <h2>${job.symbol}</h2>
      <span class="rating-pill rating-${plan.source_rating}">${plan.source_rating}</span>
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
        <span class="action-symbol">${action.symbol}</span>
        <span class="action-label-pill ${vClass}">${action.label}</span>
        <div class="action-tags">
          ${action.is_existing_holding ? '<span class="tag">held</span>' : '<span class="tag">new idea</span>'}
          ${action.screen_score != null ? `<span class="tag">score ${action.screen_score}</span>` : ""}
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
        <td>${h.symbol}</td>
        <td>${h.quantity}</td>
        <td>${fmtPrice(h.avg_price)}</td>
        <td><button data-symbol="${h.symbol}">Remove</button></td>
      </tr>
    `
    )
    .join("");
  holdingsTableBody.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await fetch(`/api/portfolio/${btn.dataset.symbol}`, { method: "DELETE" });
      refreshHoldings();
    });
  });
}

refreshHoldings();
