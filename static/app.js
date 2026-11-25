/* DocuRecon AI — Precision Split-Pane Workbench Controller */

const state = {
  activeAudit: null,
  activeFilename: null,
};

const fmtNum = (n) => Number(n || 0).toLocaleString("en-US");
const fmtUsd = (n) => `$${Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;

function showToast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(window._t);
  window._t = setTimeout(() => el.classList.remove("show"), 3000);
}

function switchView(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  document.getElementById(`view-${name}`).classList.add("active");
  document.querySelectorAll(".nav-tab").forEach((t) => t.classList.toggle("active", t.dataset.view === name));

  if (name === "classifier") { searchHs(""); searchPorts(""); }
  if (name === "pos") loadPos();
  if (name === "history") loadLedger();
}

async function refreshMetricsStrip() {
  const res = await fetch("/api/overview");
  const data = await res.json();
  const s = data.summary;
  document.getElementById("mAudited").textContent = fmtNum(s.total_audited);
  document.getElementById("mLeakage").textContent = fmtUsd(s.overcharges_prevented_usd);
  document.getElementById("mGreen").textContent = `${s.green_channel_rate_pct}%`;
  document.getElementById("mHsCodes").textContent = fmtNum(s.wco_hs_codes);
  document.getElementById("mPorts").textContent = fmtNum(s.un_seaports);
}

async function loadSamplesAndBoot() {
  await refreshMetricsStrip();
  const res = await fetch("/api/samples");
  const data = await res.json();
  renderSampleList(data.samples);
  if (data.samples.length && !state.activeAudit) {
    reconcileSample(data.samples[0].filename);
  }
}

function renderSampleList(samples) {
  document.getElementById("sampleList").innerHTML = samples
    .map(
      (s) => `
      <div class="doc-item ${s.filename === state.activeFilename ? "active" : ""}" onclick="reconcileSample('${s.filename}')">
        <div>
          <div class="doc-name">${s.filename}</div>
          <div class="doc-meta">${s.ext} · ${(s.size_bytes / 1024).toFixed(1)} KB</div>
        </div>
        <div style="display:flex;gap:6px;align-items:center;">
          <a class="btn btn-outline btn-sm" href="/api/samples/${encodeURIComponent(s.filename)}/download" onclick="event.stopPropagation()" title="Download PDF/TXT">↓</a>
          <span class="tag tag-neutral">Audit</span>
        </div>
      </div>`
    )
    .join("");
}

async function reconcileSample(filename) {
  state.activeFilename = filename;
  const res = await fetch(`/api/reconcile/sample/${encodeURIComponent(filename)}`, { method: "POST" });
  const data = await res.json();
  renderWorkbench(data);
  const sRes = await fetch("/api/samples");
  renderSampleList((await sRes.json()).samples);
  refreshMetricsStrip();
}

async function inspectAudit(auditRef) {
  switchView("workbench");
  const res = await fetch(`/api/audits/${encodeURIComponent(auditRef)}`);
  const data = await res.json();
  state.activeFilename = data.filename;
  renderWorkbench(data);
}

function highlightRawDocument(d) {
  let raw = (d.extracted.raw_text || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const statusMap = {};
  (d.comparison_rows || []).forEach((r) => { statusMap[r.field] = r.status; });

  const mark = (val, cls) => {
    if (!val || String(val).length < 3) return;
    const esc = String(val).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    raw = raw.replace(new RegExp(esc, "gi"), (m) => `<span class="hl-${cls}">${m}</span>`);
  };

  mark(d.po_matched, "pass");
  mark(d.extracted.container_number, "pass");
  if (d.extracted.hs_code && d.extracted.hs_code.length === 6) {
    const hsDot = `${d.extracted.hs_code.slice(0, 4)}.${d.extracted.hs_code.slice(4, 6)}`;
    mark(hsDot, statusMap["WCO HS Tariff Code"] || "pass");
  }
  mark(d.extracted.origin_locode, statusMap["UN/LOCODE Port Corridor"] || "pass");
  mark(d.extracted.destination_locode, statusMap["UN/LOCODE Port Corridor"] || "pass");
  if (d.extracted.invoiced_freight_usd) {
    mark(Number(d.extracted.invoiced_freight_usd).toLocaleString("en-US", { minimumFractionDigits: 2 }), statusMap["Ocean Freight Charge (USD)"] || "pass");
  }
  if (d.extracted.reefer_temp_celsius !== null && d.extracted.reefer_temp_celsius !== undefined) {
    mark(`${d.extracted.reefer_temp_celsius}`, statusMap["Reefer Temperature Telemetry"] || "pass");
  }
  return raw;
}

function renderWorkbench(d) {
  state.activeAudit = d;
  document.getElementById("wbTitle").textContent = `${d.filename} — ${d.audit_ref}`;
  document.getElementById("wbSub").textContent =
    `${d.extracted.doc_type} · PO ${d.po_matched || "UNMATCHED"} · Container ${d.extracted.container_number} · ${d.extracted.carrier}`;
  document.getElementById("docLangTag").textContent = d.extracted.language || "EN";

  const tag = document.getElementById("wbActionTag");
  tag.textContent = d.action_code;
  tag.className = `tag tag-${d.severity}`;

  // Visual Document Sheet with inline highlighted entities
  document.getElementById("visualDocSheet").innerHTML = highlightRawDocument(d);

  // Verdict Callout
  const v = document.getElementById("wbVerdict");
  v.style.display = "block";
  v.className = `verdict ${d.severity}`;
  v.innerHTML = `<strong>${d.badge_text}</strong><div style="margin-top:3px;">${d.assessment}</div>`;

  // AI Fraud Risk Score Card
  const risk = d.fraud_risk || { score: 0, tier: "Low Risk", factors: [] };
  const riskColor = risk.score >= 55 ? "var(--crimson)" : risk.score >= 25 ? "var(--amber)" : "var(--emerald)";
  document.getElementById("riskScoreNum").textContent = `${risk.score}/100`;
  document.getElementById("riskScoreNum").style.color = riskColor;
  document.getElementById("riskTierLabel").textContent = risk.tier;
  document.getElementById("riskBarFill").style.width = `${Math.max(4, risk.score)}%`;
  document.getElementById("riskBarFill").style.background = riskColor;

  document.getElementById("riskFactorsList").innerHTML = (risk.factors || [])
    .map(
      (f) => `
      <div class="factor-row">
        <span style="color:var(--ink-secondary)">${f.factor}</span>
        <span class="mono" style="font-weight:700;color:${f.points > 0 ? (f.level === "high" ? "var(--crimson)" : "var(--amber)") : "var(--emerald)"}">
          +${f.points}/${f.max_points}
        </span>
      </div>`
    )
    .join("");

  // AI WCO NLP Tariff Classifier Card
  const agreeTag = document.getElementById("nlpAgreeTag");
  agreeTag.textContent = d.ai_tariff_agrees ? "NLP CONFIRMED" : "NLP MISCLASSIFICATION";
  agreeTag.className = `tag ${d.ai_tariff_agrees ? "tag-pass" : "tag-fail"}`;

  const preds = d.ai_tariff_predictions || [];
  document.getElementById("nlpPredictionsList").innerHTML = preds
    .map(
      (p) => `
      <div class="nlp-item">
        <div class="nlp-top">
          <span class="mono" style="font-weight:700;font-size:12px;">HS ${p.hscode_formatted}</span>
          <span class="mono" style="font-size:11.5px;font-weight:700;color:var(--emerald)">${p.confidence_pct}%</span>
        </div>
        <div style="font-size:11px;color:var(--ink-secondary)">${p.description.slice(0, 78)}</div>
        <div class="nlp-bar"><div class="nlp-bar-fill" style="width:${p.confidence_pct}%"></div></div>
      </div>`
    )
    .join("") || `<div style="font-size:12px;color:var(--ink-muted)">No commodity text extracted.</div>`;

  // Comparison Table
  document.getElementById("wbComparisonBody").innerHTML = (d.comparison_rows || [])
    .map(
      (r) => `
      <tr>
        <td style="font-weight:600">${r.field}</td>
        <td>${r.extracted}</td>
        <td style="color:var(--ink-secondary)">${r.po_authorized}</td>
        <td class="mono" style="font-weight:600;font-size:11.5px">${r.variance}</td>
        <td><span class="tag tag-${r.status}">${r.status.toUpperCase()}</span></td>
      </tr>`
    )
    .join("") || `<tr><td colspan="5" style="text-align:center;color:var(--crimson);padding:20px;">${(d.discrepancies || []).join(" ")}</td></tr>`;

  // Automated Dispute / Debit Note
  const disp = d.dispute_notice;
  const dispSec = document.getElementById("disputeSection");
  if (disp && disp.required === "yes") {
    dispSec.style.display = "block";
    document.getElementById("disputeSubject").textContent = disp.subject;
    document.getElementById("disputeBody").textContent = disp.body;
  } else {
    dispSec.style.display = "none";
  }

  document.getElementById("sapPanel").style.display = "none";
}

/* ==================== VIEW 2: STANDALONE AI CLASSIFIER & REGISTRIES ==================== */
document.getElementById("hsClassifyForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = document.getElementById("hsClassifyInput").value.trim();
  if (!q) return;
  const res = await fetch("/api/classify-hs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ commodity_description: q, top_k: 5 }),
  });
  const data = await res.json();
  document.getElementById("hsClassifyOutput").innerHTML = data.predictions
    .map(
      (p) => `
      <div class="nlp-item" style="display:grid;grid-template-columns:110px 1fr 90px;gap:12px;align-items:center;">
        <span class="tag tag-pass">HS ${p.hscode_formatted}</span>
        <div>
          <div style="font-weight:600;font-size:12.5px;">${p.description}</div>
          <div class="nlp-bar"><div class="nlp-bar-fill" style="width:${p.confidence_pct}%"></div></div>
        </div>
        <span class="mono" style="text-align:right;font-weight:700;color:var(--emerald)">${p.confidence_pct}%</span>
      </div>`
    )
    .join("");
});

async function searchHs(q) {
  const res = await fetch(`/api/reference/hs?q=${encodeURIComponent(q)}&limit=40`);
  const data = await res.json();
  document.getElementById("hsRegistryBody").innerHTML = data.results
    .map((r) => `<tr><td class="mono" style="font-weight:700">${r.hscode}</td><td>${r.section}</td><td>${r.description}</td></tr>`)
    .join("");
}

async function searchPorts(q) {
  const res = await fetch(`/api/reference/ports?q=${encodeURIComponent(q)}&limit=40`);
  const data = await res.json();
  document.getElementById("portRegistryBody").innerHTML = data.results
    .map((r) => `<tr><td class="mono" style="font-weight:700">${r.locode}</td><td>${r.country}</td><td>${r.name}</td><td class="mono" style="color:var(--ink-muted)">${r.coordinates || "—"}</td></tr>`)
    .join("");
}

/* ==================== VIEW 3: SAP POS ==================== */
async function loadPos() {
  const res = await fetch("/api/purchase-orders");
  const data = await res.json();
  document.getElementById("poListBody").innerHTML = data.purchase_orders
    .map(
      (p) => `
      <tr>
        <td class="mono" style="font-weight:700">${p.po_number}</td>
        <td>${p.vendor_name}</td>
        <td>${p.commodity}</td>
        <td><span class="tag tag-neutral">${p.hs_code}</span></td>
        <td class="mono">${p.origin_locode}→${p.destination_locode}</td>
        <td class="mono">${p.approved_weight_mt} MT</td>
        <td class="mono">${fmtUsd(p.approved_freight_usd)}</td>
        <td class="mono">${p.max_temp_celsius}°C</td>
        <td><button class="btn btn-danger btn-sm" onclick="deletePo('${p.po_number}')">Delete</button></td>
      </tr>`
    )
    .join("");
}

async function deletePo(num) {
  await fetch(`/api/purchase-orders/${encodeURIComponent(num)}`, { method: "DELETE" });
  showToast(`Removed ${num}`);
  loadPos();
}

/* ==================== VIEW 4: AUDIT LEDGER ==================== */
async function loadLedger() {
  const res = await fetch("/api/audits?limit=100");
  const data = await res.json();
  document.getElementById("ledgerBody").innerHTML = data.audits
    .map(
      (a) => `
      <tr>
        <td class="mono" style="font-weight:700">${a.audit_ref}</td>
        <td class="mono" style="font-size:11.5px">${a.filename}</td>
        <td>${a.language_detected}</td>
        <td class="mono">${a.po_number || "—"}</td>
        <td class="mono">${a.origin_locode || "?"}→${a.destination_locode || "?"}</td>
        <td class="mono">${a.hs_code || "—"}</td>
        <td class="mono" style="color:${a.freight_variance_usd > 50 ? "var(--crimson)" : "inherit"};font-weight:700">${a.freight_variance_usd > 0 ? `+${fmtUsd(a.freight_variance_usd)}` : "$0"}</td>
        <td><span class="tag tag-${a.severity}">${a.action_code}</span></td>
        <td><button class="btn btn-outline btn-sm" onclick="inspectAudit('${a.audit_ref}')">Open</button></td>
      </tr>`
    )
    .join("");
}

/* ==================== COPILOT & WIRING ==================== */
function toggleCopilot(force) {
  const d = document.getElementById("copilotDrawer");
  if (typeof force === "boolean") d.classList.toggle("open", force);
  else d.classList.toggle("open");
}

function addCopilotMsg(text, role, src = "") {
  const box = document.getElementById("copilotMessages");
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = text.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>") + (src ? `<div class="msg-src">${src}</div>` : "");
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

async function askCopilot(q) {
  toggleCopilot(true);
  addCopilotMsg(q, "user");
  const res = await fetch("/api/copilot", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query: q }),
  });
  const out = await res.json();
  addCopilotMsg(out.answer || "No answer.", "bot", out.mode || "DocuRecon AI");
}

document.querySelectorAll(".nav-tab").forEach((t) => t.addEventListener("click", () => switchView(t.dataset.view)));

document.getElementById("fileUpload").addEventListener("change", async (e) => {
  const files = Array.from(e.target.files || []);
  if (!files.length) return;
  document.getElementById("uploadStatus").textContent = `Auditing ${files.length} document(s)...`;
  try {
    if (files.length === 1) {
      const fd = new FormData();
      fd.append("file", files[0]);
      const res = await fetch("/api/reconcile/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Audit failed");
      renderWorkbench(data);
      showToast(`Audited ${files[0].name} → Risk ${data.fraud_risk.score}/100`);
    } else {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      const res = await fetch("/api/reconcile/batch", { method: "POST", body: fd });
      const batch = await res.json();
      if (batch.results.length) renderWorkbench(batch.results[0]);
      showToast(`Batch audited ${batch.count} documents! View all in Audit Ledger.`);
    }
    refreshMetricsStrip();
  } catch (err) {
    showToast(`Error: ${err.message}`);
  } finally {
    document.getElementById("uploadStatus").textContent = "Drop or click to upload PDF / TXT / CSV manifests";
    e.target.value = "";
  }
});

document.getElementById("btnCopyDispute").addEventListener("click", () => {
  const subj = document.getElementById("disputeSubject").textContent;
  const body = document.getElementById("disputeBody").textContent;
  navigator.clipboard.writeText(`Subject: ${subj}\n\n${body}`);
  showToast("Copied Carrier Debit Note to clipboard");
});

document.getElementById("btnShowSapJson").addEventListener("click", async () => {
  if (!state.activeAudit) return;
  const panel = document.getElementById("sapPanel");
  if (panel.style.display === "block") { panel.style.display = "none"; return; }
  const res = await fetch(`/api/audits/${encodeURIComponent(state.activeAudit.audit_ref)}/sap-json`);
  document.getElementById("sapJsonBox").textContent = JSON.stringify(await res.json(), null, 2);
  panel.style.display = "block";
});

document.getElementById("btnCopySap").addEventListener("click", () => {
  navigator.clipboard.writeText(document.getElementById("sapJsonBox").textContent);
  showToast("Copied SAP IDoc JSON");
});

document.getElementById("poCreateForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target).entries());
  body.approved_weight_mt = Number(body.approved_weight_mt);
  body.approved_freight_usd = Number(body.approved_freight_usd);
  body.max_temp_celsius = Number(body.max_temp_celsius);
  const res = await fetch("/api/purchase-orders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.ok) {
    showToast(`Saved SAP PO ${body.po_number.toUpperCase()}`);
    e.target.reset();
    loadPos();
    refreshMetricsStrip();
  }
});

document.getElementById("copilotForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const inp = document.getElementById("copilotInput");
  const q = inp.value.trim();
  if (!q) return;
  inp.value = "";
  askCopilot(q);
});

let t1, t2;
document.getElementById("hsSearchBox").addEventListener("input", (e) => { clearTimeout(t1); t1 = setTimeout(() => searchHs(e.target.value), 180); });
document.getElementById("portSearchBox").addEventListener("input", (e) => { clearTimeout(t2); t2 = setTimeout(() => searchPorts(e.target.value), 180); });

loadSamplesAndBoot();
