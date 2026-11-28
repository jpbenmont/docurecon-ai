/* DocuRecon AI v4.0 — Split-Pane Trade Compliance Workbench Controller */

const state = {
  activeAudit: null,
  activeFilename: null,
  lastSapPayload: null,
  poCache: [],
  ledgerUniqueOnly: true,
};

const escHtml = (s) =>
  String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

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

  if (name === "classifier") {
    searchHs("");
    searchPorts("");
  }
  if (name === "pos") loadPos();
  if (name === "history") loadLedger();
}

async function refreshMetricsStrip() {
  const res = await fetch("/api/overview");
  const data = await res.json();
  const s = data.summary;
  document.getElementById("mAudited").textContent = fmtNum(s.total_audited);
  document.getElementById("mSplit").textContent = `${s.green_channel_count} green · ${s.hold_amendment_count} hold · ${s.quarantine_count} quarantine`;
  document.getElementById("mLeakage").textContent = fmtUsd(s.overcharges_prevented_usd);
  document.getElementById("mLeakSub").textContent = `Freight ${fmtUsd(s.freight_overcharges_usd)} + Cargo ${fmtUsd(s.cargo_overinvoicing_usd)}`;
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
    .map((s) => {
      const langShort = s.language === "Portuguese" ? "PT" : s.language === "French" ? "FR" : "EN";
      const dotCls = s.severity || "healthy";
      return `
      <div class="doc-item ${s.filename === state.activeFilename ? "active" : ""}" onclick="reconcileSample('${escHtml(s.filename)}')">
        <div style="min-width:0;">
          <div style="display:flex;align-items:center;gap:6px;">
            <span class="status-dot ${escHtml(dotCls)}"></span>
            <span class="doc-name">${escHtml(s.filename)}</span>
          </div>
          <div class="doc-meta">
            <span class="mono">${escHtml(s.corridor || "—")}</span>
            <span>·</span>
            <span>${langShort}</span>
            <span>·</span>
            <span>${(s.size_bytes / 1024).toFixed(1)} KB</span>
            ${s.risk_score !== null && s.risk_score !== undefined ? `<span>· Risk ${s.risk_score}</span>` : ""}
          </div>
        </div>
        <div style="display:flex;gap:5px;align-items:center;flex-shrink:0;">
          <a class="btn btn-outline btn-sm" href="/api/samples/${encodeURIComponent(s.filename)}/download" onclick="event.stopPropagation()" title="Download original file">↓</a>
        </div>
      </div>`;
    })
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
  let raw = escHtml(d.extracted.raw_text || "");
  const statusMap = {};
  (d.comparison_rows || []).forEach((r) => {
    statusMap[r.field] = r.status;
  });

  const mark = (val, cls) => {
    if (!val || String(val).length < 3) return;
    const esc = String(val).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    raw = raw.replace(new RegExp(esc, "gi"), (m) => `<span class="hl-${cls}">${m}</span>`);
  };

  mark(d.po_matched, "pass");
  mark(d.extracted.container_number, statusMap["Container ID (ISO 6346)"] || "pass");
  if (d.extracted.hs_code && d.extracted.hs_code.length === 6) {
    const hsDot = `${d.extracted.hs_code.slice(0, 4)}.${d.extracted.hs_code.slice(4, 6)}`;
    mark(hsDot, statusMap["WCO HS Tariff Code"] || "pass");
  }
  mark(d.extracted.origin_locode, statusMap["UN/LOCODE Port Corridor"] || "pass");
  mark(d.extracted.destination_locode, statusMap["UN/LOCODE Port Corridor"] || "pass");
  if (d.extracted.invoiced_freight_usd) {
    const usFmt = Number(d.extracted.invoiced_freight_usd).toLocaleString("en-US", { minimumFractionDigits: 2 });
    const euFmt = usFmt.replace(/,/g, "#").replace(/\./g, ",").replace(/#/g, ".");
    mark(usFmt, statusMap["Ocean Freight Charge (USD)"] || "pass");
    mark(euFmt, statusMap["Ocean Freight Charge (USD)"] || "pass");
  }
  if (d.extracted.cargo_value_usd) {
    const usVal = Number(d.extracted.cargo_value_usd).toLocaleString("en-US", { minimumFractionDigits: 2 });
    const euVal = usVal.replace(/,/g, "#").replace(/\./g, ",").replace(/#/g, ".");
    mark(usVal, statusMap["Cargo Value FOB/CIF (USD)"] || "pass");
    mark(euVal, statusMap["Cargo Value FOB/CIF (USD)"] || "pass");
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

  document.getElementById("visualDocSheet").innerHTML = highlightRawDocument(d);

  const v = document.getElementById("wbVerdict");
  v.style.display = "block";
  v.className = `verdict ${d.severity}`;
  v.innerHTML = `<strong>${escHtml(d.badge_text)}</strong><div style="margin-top:3px;">${escHtml(d.assessment)}</div>`;

  // Multi-Factor Risk Card
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
        <span style="color:var(--ink-secondary)">${escHtml(f.factor)}</span>
        <span class="mono" style="font-weight:600;color:${f.points > 0 ? (f.level === "high" ? "var(--crimson)" : "var(--amber)") : "var(--emerald)"}">
          +${f.points}/${f.max_points}
        </span>
      </div>`
    )
    .join("");

  // WCO Hierarchy NLP Card
  const agreeTag = document.getElementById("nlpAgreeTag");
  agreeTag.textContent = d.ai_tariff_agrees ? "NLP CONFIRMED" : "NLP REVIEW";
  agreeTag.className = `tag ${d.ai_tariff_agrees ? "tag-pass" : "tag-fail"}`;

  const preds = d.ai_tariff_predictions || [];
  document.getElementById("nlpPredictionsList").innerHTML =
    preds
      .map(
        (p) => `
      <div class="nlp-item">
        <div class="nlp-top">
          <span class="mono" style="font-weight:700;font-size:12px;">HS ${escHtml(p.hscode_formatted)}</span>
          <span class="mono" style="font-size:11.5px;font-weight:700;color:var(--emerald)">${p.confidence_pct}%</span>
        </div>
        <div style="font-size:11.5px;color:var(--ink)">${escHtml(p.description.slice(0, 74))}</div>
        ${p.heading_description ? `<div class="nlp-heading">Heading: ${escHtml(p.heading_description.slice(0, 68))}</div>` : ""}
        <div class="nlp-bar"><div class="nlp-bar-fill" style="width:${p.confidence_pct}%"></div></div>
      </div>`
      )
      .join("") || `<div style="font-size:12px;color:var(--ink-muted)">No commodity text extracted.</div>`;

  // 8-Dimension ERP Comparison Table
  document.getElementById("wbComparisonBody").innerHTML =
    (d.comparison_rows || [])
      .map(
        (r) => `
      <tr>
        <td style="font-weight:600">${escHtml(r.field)}</td>
        <td>${escHtml(r.extracted)}</td>
        <td style="color:var(--ink-secondary)">${escHtml(r.po_authorized)}</td>
        <td class="mono" style="font-weight:600;font-size:11.5px">${escHtml(r.variance)}</td>
        <td><span class="tag tag-${escHtml(r.status)}">${escHtml(r.status.toUpperCase())}</span></td>
      </tr>`
      )
      .join("") || `<tr><td colspan="5" style="text-align:center;color:var(--crimson);padding:20px;">${escHtml((d.discrepancies || []).join(" "))}</td></tr>`;

  // Automated Multilingual Debit Note
  const disp = d.dispute_notice;
  const dispSec = document.getElementById("disputeSection");
  if (disp && disp.required === "yes") {
    dispSec.style.display = "block";
    document.getElementById("disputeSubject").textContent = disp.subject;
    document.getElementById("disputeBody").textContent = disp.body;
    const mailto = `mailto:?subject=${encodeURIComponent(disp.subject)}&body=${encodeURIComponent(disp.body)}`;
    document.getElementById("btnMailtoDispute").setAttribute("href", mailto);
  } else {
    dispSec.style.display = "none";
  }

  document.getElementById("sapPanel").style.display = "none";
}

/* ==================== VIEW 2: STANDALONE CLASSIFIER & REGISTRIES ==================== */
async function runClassifyQuery(q) {
  const res = await fetch("/api/classify-hs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ commodity_description: q, top_k: 5 }),
  });
  const data = await res.json();
  document.getElementById("hsClassifyOutput").innerHTML = data.predictions
    .map(
      (p) => `
      <div class="nlp-item" style="display:grid;grid-template-columns:108px 1fr 80px;gap:12px;align-items:center;">
        <span class="tag tag-pass">HS ${escHtml(p.hscode_formatted)}</span>
        <div>
          <div style="font-weight:600;font-size:12.5px;">${escHtml(p.description)}</div>
          ${p.heading_description ? `<div class="nlp-heading">Section ${escHtml(p.section)} · Heading: ${escHtml(p.heading_description)}</div>` : ""}
          <div class="nlp-bar"><div class="nlp-bar-fill" style="width:${p.confidence_pct}%"></div></div>
        </div>
        <span class="mono num" style="font-weight:700;color:var(--emerald)">${p.confidence_pct}%</span>
      </div>`
    )
    .join("");
}

function runQuickClassify(sampleText) {
  document.getElementById("hsClassifyInput").value = sampleText;
  runClassifyQuery(sampleText);
}

document.getElementById("hsClassifyForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = document.getElementById("hsClassifyInput").value.trim();
  if (!q) return;
  await runClassifyQuery(q);
});

async function searchHs(q) {
  const res = await fetch(`/api/reference/hs?q=${encodeURIComponent(q)}&limit=40`);
  const data = await res.json();
  document.getElementById("hsRegistryBody").innerHTML = data.results
    .map((r) => `<tr><td class="mono" style="font-weight:700">${escHtml(r.hscode)}</td><td>${escHtml(r.section)}</td><td>${escHtml(r.description)}</td></tr>`)
    .join("");
}

async function searchPorts(q) {
  const res = await fetch(`/api/reference/ports?q=${encodeURIComponent(q)}&limit=40`);
  const data = await res.json();
  document.getElementById("portRegistryBody").innerHTML = data.results
    .map((r) => `<tr><td class="mono" style="font-weight:700">${escHtml(r.locode)}</td><td>${escHtml(r.country)}</td><td>${escHtml(r.name)}</td><td class="mono" style="color:var(--ink-muted)">${escHtml(r.coordinates || "—")}</td></tr>`)
    .join("");
}

/* ==================== VIEW 3: SAP POS (CREATE / EDIT / DELETE) ==================== */
async function loadPos() {
  const res = await fetch("/api/purchase-orders");
  const data = await res.json();
  state.poCache = data.purchase_orders || [];
  document.getElementById("poListBody").innerHTML = state.poCache
    .map(
      (p) => `
      <tr>
        <td class="mono" style="font-weight:700">${escHtml(p.po_number)}</td>
        <td>${escHtml(p.vendor_name)}</td>
        <td>${escHtml(p.commodity)}</td>
        <td><span class="tag tag-neutral">${escHtml(p.hs_code)}</span></td>
        <td class="mono">${escHtml(p.origin_locode)}→${escHtml(p.destination_locode)}</td>
        <td class="num mono">${p.approved_weight_mt} MT</td>
        <td class="num mono">${fmtUsd(p.approved_freight_usd)}</td>
        <td class="num mono">${fmtUsd(p.approved_cargo_usd)}</td>
        <td class="num mono">${p.max_temp_celsius}°C</td>
        <td style="white-space:nowrap;">
          <button class="btn btn-outline btn-sm" onclick="editPo('${escHtml(p.po_number)}')">Edit</button>
          <button class="btn btn-danger btn-sm" onclick="deletePo('${escHtml(p.po_number)}')">Delete</button>
        </td>
      </tr>`
    )
    .join("");
}

function editPo(poNumber) {
  const p = state.poCache.find((item) => item.po_number === poNumber);
  if (!p) return;
  const form = document.getElementById("poCreateForm");
  form.elements["po_number"].value = p.po_number;
  form.elements["vendor_name"].value = p.vendor_name;
  form.elements["commodity"].value = p.commodity;
  form.elements["hs_code"].value = p.hs_code;
  form.elements["origin_locode"].value = p.origin_locode;
  form.elements["destination_locode"].value = p.destination_locode;
  form.elements["approved_weight_mt"].value = p.approved_weight_mt;
  form.elements["approved_freight_usd"].value = p.approved_freight_usd;
  form.elements["approved_cargo_usd"].value = p.approved_cargo_usd;
  form.elements["max_temp_celsius"].value = p.max_temp_celsius;
  form.elements["incoterms"].value = p.incoterms || "CIF";
  document.getElementById("poFormTitle").textContent = `Editing SAP PO: ${p.po_number}`;
  document.getElementById("btnSavePo").textContent = "Update SAP PO";
  document.getElementById("btnResetPoForm").style.display = "inline-flex";
  form.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function resetPoForm() {
  document.getElementById("poCreateForm").reset();
  document.getElementById("poFormTitle").textContent = "Register or Edit Authorized SAP Purchase Order";
  document.getElementById("btnSavePo").textContent = "Save SAP PO";
  document.getElementById("btnResetPoForm").style.display = "none";
}

async function deletePo(num) {
  await fetch(`/api/purchase-orders/${encodeURIComponent(num)}`, { method: "DELETE" });
  showToast(`Removed ${num}`);
  loadPos();
  refreshMetricsStrip();
}

/* ==================== VIEW 4: AUDIT LEDGER ==================== */
async function loadLedger() {
  const res = await fetch(`/api/audits?limit=100&unique_only=${state.ledgerUniqueOnly}`);
  const data = await res.json();
  document.getElementById("ledgerBody").innerHTML = data.audits
    .map(
      (a) => `
      <tr>
        <td class="mono" style="font-weight:700">${escHtml(a.audit_ref)}</td>
        <td class="mono" style="font-size:11.5px">${escHtml(a.filename)}</td>
        <td>${escHtml(a.language_detected)}</td>
        <td class="mono">${escHtml(a.po_number || "—")}</td>
        <td class="mono">${escHtml(a.origin_locode || "?")}→${escHtml(a.destination_locode || "?")}</td>
        <td class="mono">${escHtml(a.hs_code || "—")}</td>
        <td class="num mono" style="font-weight:600">${a.risk_score !== undefined ? `${a.risk_score}` : "—"}</td>
        <td class="num mono" style="color:${a.freight_variance_usd > 50 ? "var(--crimson)" : "inherit"};font-weight:600">${a.freight_variance_usd > 0 ? `+${fmtUsd(a.freight_variance_usd)}` : "$0"}</td>
        <td class="num mono" style="color:${Math.abs(a.cargo_variance_usd || 0) > 100 ? "var(--crimson)" : "inherit"};font-weight:600">${a.cargo_variance_usd ? `${a.cargo_variance_usd > 0 ? "+" : ""}${fmtUsd(a.cargo_variance_usd)}` : "$0"}</td>
        <td><span class="tag tag-${escHtml(a.severity)}">${escHtml(a.action_code)}</span></td>
        <td><button class="btn btn-outline btn-sm" onclick="inspectAudit('${escHtml(a.audit_ref)}')">Open</button></td>
      </tr>`
    )
    .join("");
}

/* ==================== FILE UPLOAD & DRAG-AND-DROP ==================== */
async function handleFilesUpload(fileList) {
  const files = Array.from(fileList || []);
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
      showToast(`Batch audited ${batch.count} documents. View all in Audit Ledger.`);
    }
    refreshMetricsStrip();
  } catch (err) {
    showToast(`Error: ${err.message}`);
  } finally {
    document.getElementById("uploadStatus").textContent = "Drop shipping manifests here or click to browse";
  }
}

const dropzone = document.getElementById("docDropzone");
["dragenter", "dragover"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  })
);
dropzone.addEventListener("drop", (e) => {
  if (e.dataTransfer?.files?.length) handleFilesUpload(e.dataTransfer.files);
});

document.getElementById("fileUpload").addEventListener("change", (e) => {
  handleFilesUpload(e.target.files);
  e.target.value = "";
});

/* ==================== COPILOT (XSS-SAFE) & WIRING ==================== */
function toggleCopilot(force) {
  const d = document.getElementById("copilotDrawer");
  if (typeof force === "boolean") d.classList.toggle("open", force);
  else d.classList.toggle("open");
}

function formatSafeMd(raw) {
  return escHtml(raw)
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.*?)\*/g, "<em>$1</em>");
}

function addCopilotMsg(text, role, src = "") {
  const box = document.getElementById("copilotMessages");
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = formatSafeMd(text) + (src ? `<div class="msg-src">${escHtml(src)}</div>` : "");
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

document.getElementById("ledgerModeToggle").addEventListener("click", (e) => {
  if (!e.target.classList.contains("seg-btn")) return;
  document.querySelectorAll("#ledgerModeToggle .seg-btn").forEach((b) => b.classList.remove("active"));
  e.target.classList.add("active");
  state.ledgerUniqueOnly = e.target.dataset.unique === "true";
  loadLedger();
});

document.getElementById("btnCopyDispute").addEventListener("click", () => {
  const subj = document.getElementById("disputeSubject").textContent;
  const body = document.getElementById("disputeBody").textContent;
  navigator.clipboard.writeText(`Subject: ${subj}\n\n${body}`);
  showToast("Copied Carrier Debit Note to clipboard");
});

document.getElementById("btnDownloadDispute").addEventListener("click", () => {
  const subj = document.getElementById("disputeSubject").textContent;
  const body = document.getElementById("disputeBody").textContent;
  const blob = new Blob([`Subject: ${subj}\n\n${body}\n`], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${state.activeAudit?.audit_ref || "debit_note"}_dispute.txt`;
  a.click();
});

document.getElementById("btnShowSapJson").addEventListener("click", async () => {
  if (!state.activeAudit) return;
  const panel = document.getElementById("sapPanel");
  if (panel.style.display === "block") {
    panel.style.display = "none";
    return;
  }
  const res = await fetch(`/api/audits/${encodeURIComponent(state.activeAudit.audit_ref)}/sap-json`);
  const payload = await res.json();
  state.lastSapPayload = payload;
  document.getElementById("sapJsonBox").textContent = JSON.stringify(payload, null, 2);
  panel.style.display = "block";
});

document.getElementById("btnCopySap").addEventListener("click", () => {
  navigator.clipboard.writeText(document.getElementById("sapJsonBox").textContent);
  showToast("Copied SAP IDoc JSON");
});

document.getElementById("btnDownloadSap").addEventListener("click", () => {
  if (!state.lastSapPayload) return;
  const blob = new Blob([JSON.stringify(state.lastSapPayload, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${state.activeAudit?.audit_ref || "SAP_IDOC"}_INVOIC02.json`;
  a.click();
});

document.getElementById("poCreateForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target).entries());
  body.approved_weight_mt = Number(body.approved_weight_mt);
  body.approved_freight_usd = Number(body.approved_freight_usd);
  body.approved_cargo_usd = Number(body.approved_cargo_usd || 0);
  body.max_temp_celsius = Number(body.max_temp_celsius);
  const res = await fetch("/api/purchase-orders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.ok) {
    showToast(`Saved SAP PO ${body.po_number.toUpperCase()}`);
    resetPoForm();
    loadPos();
    refreshMetricsStrip();
  } else {
    const err = await res.json();
    showToast(`Error: ${err.detail || "Save failed"}`);
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
document.getElementById("hsSearchBox").addEventListener("input", (e) => {
  clearTimeout(t1);
  t1 = setTimeout(() => searchHs(e.target.value), 180);
});
document.getElementById("portSearchBox").addEventListener("input", (e) => {
  clearTimeout(t2);
  t2 = setTimeout(() => searchPorts(e.target.value), 180);
});

loadSamplesAndBoot();
