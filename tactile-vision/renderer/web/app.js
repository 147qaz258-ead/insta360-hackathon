import { PinRenderer } from "./pin_renderer.js";

const $ = (id) => document.getElementById(id);
const renderer = new PinRenderer($("surface"));
const auxRenderer = new PinRenderer($("surfaceAux"));

let runtimeInfo = null;
let providerStatus = null;
let imageDataUrl = null;
let imageObject = null;
let currentRunId = null;
let currentRunSource = null;
let runtimeSource = null;
let currentFrame = null;
let currentRuntimeRevision = -1;
let lastTouch = null;
let lastSpokenFrame = -1;
let runtimeImageFrameId = -1;

function setApiState(ok, text) {
  $("apiPill").classList.toggle("ok", ok);
  $("apiText").textContent = text;
}

function drawInput(image) {
  const canvas = $("inputCanvas");
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#ecece8";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const scale = Math.min(canvas.width / image.width, canvas.height / image.height);
  const width = image.width * scale;
  const height = image.height * scale;
  ctx.drawImage(image, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
  $("inputSize").textContent = `${image.width} × ${image.height}`;
  $("emptyInput").hidden = true;
}

function selectState(state) {
  const order = ["UNDERSTANDING", "DESIGNING", "VALIDATING", "RENDERING", "BLIND_READING", "CRITIQUING", "READY"];
  const aliases = {
    ACCEPTED: "READY",
    PUBLISHING: "READY",
    LOWERING_OLD_FRAME: "READY",
    RISING: "READY",
    REVISING: "DESIGNING",
    ANALYZING: "UNDERSTANDING",
    SELF_REVIEW: "CRITIQUING",
    QUEUED: "UNDERSTANDING",
  };
  const effective = aliases[state] || state;
  const current = order.indexOf(effective);
  document.querySelectorAll(".state-step").forEach((element, index) => {
    element.classList.toggle("active", index === current);
    element.classList.toggle("done", current >= 0 && index < current);
    element.classList.toggle("error", (state === "ERROR" || state === "CANCELLED") && index === Math.max(0, current));
  });
}

function appendChat(role, text) {
  const row = document.createElement("div");
  row.className = `chat-row ${role}`;
  row.textContent = text;
  $("chatLog").appendChild(row);
  $("chatLog").scrollTop = $("chatLog").scrollHeight;
}

function resetChat() {
  $("chatLog").replaceChildren();
  appendChat("system", "触觉表面准备完成后，点击凸点开始探索。");
}

function speak(text, audioUrl = null) {
  if (audioUrl) {
    new Audio(audioUrl).play().catch(() => speak(text));
    return;
  }
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "zh-CN";
  utterance.rate = 0.96;
  window.speechSynthesis.speak(utterance);
}

function renderRegions(frame) {
  const container = $("regionList");
  container.replaceChildren();
  for (const region of frame?.regions || []) {
    const item = document.createElement("button");
    item.className = "region-item";
    item.type = "button";
    item.innerHTML = `<b>${region.id}. ${escapeHtml(region.name)}</b><span>${escapeHtml(region.description)}</span>`;
    item.addEventListener("click", () => {
      appendChat("assistant", region.speech || region.description);
      speak(region.speech || region.description);
    });
    container.appendChild(item);
  }
}

function renderMatrix(frame) {
  const canvas = $("heightMap");
  if (!canvas || !frame?.height_rows) return;
  const ctx = canvas.getContext("2d");
  const cols = Number(frame.cols || 80);
  const rows = Number(frame.rows || 48);
  const cellW = canvas.width / cols;
  const cellH = canvas.height / rows;
  const heights = frame.height_rows;
  const regions = frame.region_rows || [];
  let raised = 0;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#f6f7f4";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      const height = Number(heights[row]?.[col] || 0);
      if (height > 0) raised++;
      const tone = Math.round(224 - Math.min(1, height / 255) * 145);
      ctx.fillStyle = `rgb(${tone},${tone + 2},${tone + 3})`;
      ctx.fillRect(col * cellW, row * cellH, cellW + 0.35, cellH + 0.35);
    }
  }
  // The outline is a direct visualization of the model's region_rows. It is
  // never used to alter height_rows or to create another hardware frame.
  ctx.strokeStyle = "rgba(42, 49, 51, .72)";
  ctx.lineWidth = Math.max(0.6, Math.min(cellW, cellH) * 0.12);
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      const id = Number(regions[row]?.[col] || 0);
      if (col + 1 < cols && id !== Number(regions[row]?.[col + 1] || 0)) {
        const x = (col + 1) * cellW;
        ctx.beginPath(); ctx.moveTo(x, row * cellH); ctx.lineTo(x, (row + 1) * cellH); ctx.stroke();
      }
      if (row + 1 < rows && id !== Number(regions[row + 1]?.[col] || 0)) {
        const y = (row + 1) * cellH;
        ctx.beginPath(); ctx.moveTo(col * cellW, y); ctx.lineTo((col + 1) * cellW, y); ctx.stroke();
      }
    }
  }
  $("matrixStats").textContent = `${raised} / ${cols * rows} 个点升起`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

const SCORE_LABELS = {
  composition: "构图可辨",
  completeness: "部件完整",
  spatial: "空间关系",
  raised_meaning: "升起合理",
  separation: "分离留白",
  line_quality: "线条质量",
  height_layers: "高度层次",
  complexity_fit: "复杂度适配",
  blind_read_match: "盲读匹配",
};

function stageSeconds(stages) {
  if (!stages) return null;
  const total = Object.values(stages).reduce((sum, value) => sum + (Number(value) || 0), 0);
  return total > 0 ? total.toFixed(1) : null;
}

function renderCandidateReview(snapshot) {
  const container = $("candidateReview");
  if (!container) return;
  container.replaceChildren();
  const trace = Array.isArray(snapshot?.trace) ? snapshot.trace : [];
  if (!trace.length) return;
  for (const entry of trace) {
    const card = document.createElement("section");
    card.className = "candidate-card";
    const decision = entry.decision || "—";
    const seconds = stageSeconds(entry.stages);
    const runsCount = entry.sparse_candidate?.raised_runs?.length;
    const metrics = entry.metrics || {};
    const header = document.createElement("header");
    header.innerHTML =
      `<b>候选 ${entry.candidate}</b>` +
      `<span>${runsCount != null ? `点段 ${runsCount} 段 · ` : ""}` +
      `升起率 ${((metrics.active_ratio ?? 0) * 100).toFixed(1)}%` +
      `${seconds ? ` · 耗时 ${seconds}s` : ""}` +
      `${entry.frame_checksum ? ` · ${escapeHtml(entry.frame_checksum.slice(0, 19))}` : ""}</span>` +
      `<span class="decision-pill ${escapeHtml(decision)}">${escapeHtml(decision)}</span>`;
    card.appendChild(header);

    if (entry.review_token) {
      const shots = document.createElement("div");
      shots.className = "candidate-shots";
      shots.innerHTML =
        `<figure><img loading="lazy" src="/api/internal/review-shots/${entry.review_token}/full.png" alt="候选 ${entry.candidate} 审查页截图" />` +
        `<figcaption>正式渲染器审查页：原图 + 俯视主点阵 + 2.5D 辅图 + 机械指标</figcaption></figure>` +
        `<figure><img loading="lazy" src="/api/internal/review-shots/${entry.review_token}/blind.png" alt="候选 ${entry.candidate} 盲读点阵" />` +
        `<figcaption>无原图盲读所用的严格俯视黑白点阵</figcaption></figure>`;
      card.appendChild(shots);
    }

    const critique = entry.critique;
    if (critique?.scores) {
      const grid = document.createElement("div");
      grid.className = "score-grid";
      for (const [key, label] of Object.entries(SCORE_LABELS)) {
        const value = critique.scores[key];
        if (value == null) continue;
        const threshold = ["composition", "completeness", "raised_meaning", "separation", "line_quality"].includes(key) ? 4 : 3;
        const cell = document.createElement("div");
        cell.className = `score-cell${Number(value) < threshold ? " low" : ""}`;
        cell.innerHTML = `<b>${escapeHtml(value)}</b>${label}（≥${threshold}）`;
        grid.appendChild(cell);
      }
      card.appendChild(grid);
    }

    const notes = document.createElement("div");
    notes.className = "candidate-notes";
    const addNote = (label, value) => {
      if (!value) return;
      const row = document.createElement("div");
      row.innerHTML = `<span class="note-label">${label}</span>${escapeHtml(value)}`;
      notes.appendChild(row);
    };
    if (entry.validation && entry.validation.valid === false) {
      const errors = (entry.validation.errors || []).slice(0, 6)
        .map((issue) => `${issue.path}: ${issue.message}`).join("；");
      addNote("协议错误", errors + ((entry.validation.errors || []).length > 6 ? " …" : ""));
    }
    if (entry.blind_read) {
      addNote("盲读", `${entry.blind_read.interpretation || "—"}（可读性 ${entry.blind_read.overall_readability ?? "?"}）`);
    }
    if (critique) {
      addNote("Critic 总评", critique.rationale);
      addNote("密度判定", critique.density_verdict);
      if (Array.isArray(critique.critical_defects) && critique.critical_defects.length) {
        addNote("关键缺陷", critique.critical_defects.join("；"));
      }
      if (Array.isArray(critique.defects) && critique.defects.length) {
        const list = document.createElement("ul");
        list.className = "defect-list";
        for (const defect of critique.defects.slice(0, 8)) {
          const item = document.createElement("li");
          item.textContent = `${defect.area || "?"}（${defect.location || "?"}）→ ${defect.fix_direction || "?"}`;
          list.appendChild(item);
        }
        const labelRow = document.createElement("div");
        labelRow.innerHTML = '<span class="note-label">修订意见</span>';
        labelRow.appendChild(list);
        notes.appendChild(labelRow);
      }
    }
    if (entry.design_notes) addNote("设计说明", entry.design_notes);
    if (notes.childNodes.length) card.appendChild(notes);
    container.appendChild(card);
  }
}

function trimmedSnapshotForDump(snapshot) {
  if (!snapshot) return snapshot;
  const clone = JSON.parse(JSON.stringify(snapshot));
  if (Array.isArray(clone.trace)) {
    for (const entry of clone.trace) {
      if (entry.sparse_candidate) {
        const runs = entry.sparse_candidate.raised_runs?.length ?? 0;
        entry.sparse_candidate = `[sparse-runs-v1 候选：${runs} 段，见上方候选卡片]`;
      }
    }
  }
  return clone;
}

function chooseBoardUrl() {
  const base = runtimeInfo?.network_urls?.[0] || window.location.origin;
  return `${base}${runtimeInfo?.display_path || "/display.html"}`;
}

async function loadRuntimeImage(frameId) {
  if (!frameId || frameId === runtimeImageFrameId) return;
  runtimeImageFrameId = frameId;
  try {
    const response = await fetch(`/api/runtime/image?frame_id=${frameId}`, { cache: "no-store" });
    if (!response.ok) throw new Error("当前原图不可用");
    const blob = await response.blob();
    const reader = new FileReader();
    reader.onload = () => {
      imageDataUrl = String(reader.result || "");
      const image = new Image();
      image.onload = () => {
        imageObject = image;
        drawInput(image);
        $("generateButton").disabled = !providerStatus?.configured;
      };
      image.src = imageDataUrl;
    };
    reader.readAsDataURL(blob);
  } catch (error) {
    runtimeImageFrameId = -1;
    console.error(error);
  }
}

async function recoverCurrentRun(runId) {
  if (!runId || runId === currentRunId) return;
  currentRunId = runId;
  try {
    const response = await fetch(`/api/agent/runs/${runId}`, { cache: "no-store" });
    if (!response.ok) {
      $("candidateBadge").textContent = "已发布帧";
      return;
    }
    const run = await response.json();
    applyRun(run);
    if (!["READY", "ERROR"].includes(run.state)) connectRun(runId);
  } catch (error) {
    console.error(error);
  }
}

function applyRuntime(snapshot) {
  if (!snapshot || snapshot.revision === currentRuntimeRevision) return;
  currentRuntimeRevision = snapshot.revision;
  const state = snapshot.motion_state || "IDLE";
  selectState(state);
  $("surfaceStatus").textContent = `${state} · ${snapshot.source || "runtime"}`;

  if (state === "RETRACTING" || state === "LOWERING_OLD_FRAME") {
    renderer.retract();
    auxRenderer.retract();
  } else if (snapshot.frame) {
    const changed = !currentFrame || snapshot.frame.frame_id !== currentFrame.frame_id || snapshot.frame.checksum !== currentFrame.checksum;
    currentFrame = snapshot.frame;
    if (state === "RISING" || changed) {
      renderer.updateFrame(currentFrame);
      renderer.setTopView();
      auxRenderer.updateFrame(currentFrame);
      auxRenderer.setPerspectiveView();
    }
    $("frameIdentity").textContent = `Frame #${currentFrame.frame_id} · ${currentFrame.cols} × ${currentFrame.rows}`;
    $("checksum").textContent = currentFrame.checksum?.slice(0, 20) || "—";
    $("sceneSummary").textContent = currentFrame.scene_summary || "—";
    renderRegions(currentFrame);
    renderMatrix(currentFrame);
    if (currentFrame.frame_id > 0) loadRuntimeImage(currentFrame.frame_id);
    if (changed && currentFrame.frame_id > 0 && currentFrame.frame_id !== lastSpokenFrame) {
      lastSpokenFrame = currentFrame.frame_id;
      appendChat("assistant", currentFrame.audio_overview);
      speak(currentFrame.audio_overview);
    }
  }
  if (snapshot.current_run_id && snapshot.current_run_available) {
    recoverCurrentRun(snapshot.current_run_id);
  } else if (snapshot.current_run_id && !snapshot.current_run_available) {
    currentRunId = snapshot.current_run_id;
    $("candidateBadge").textContent = "已发布帧";
  }
  $("developerTrace").textContent = JSON.stringify({ runtime: trimmedSnapshotForDump(snapshot), run_id: currentRunId }, null, 2);
}

function connectRuntime() {
  runtimeSource?.close();
  runtimeSource = new EventSource("/api/runtime/events");
  runtimeSource.addEventListener("runtime", (event) => {
    try { applyRuntime(JSON.parse(event.data)); } catch (error) { console.error(error); }
  });
  runtimeSource.onerror = () => { $("surfaceStatus").textContent = "连接中断，正在重连…"; };
}

function applyRun(snapshot) {
  if (!snapshot) return;
  const state = snapshot.state || "QUEUED";
  selectState(state);
  $("candidateBadge").textContent = `候选 ${snapshot.candidate || snapshot.candidate_count || 0} / 3`;
  $("surfaceStatus").textContent = `${state} · ${snapshot.message || ""}`;
  $("developerTrace").textContent = JSON.stringify(trimmedSnapshotForDump(snapshot), null, 2);
  renderCandidateReview(snapshot);
  const latestValidation = snapshot.validation;
  if (latestValidation && !latestValidation.valid) {
    const first = latestValidation.errors?.[0];
    if (first) $("surfaceStatus").textContent = `协议反馈给智能体：${first.path} · ${first.message}`;
  }
  if (state === "ERROR" || state === "CANCELLED") {
    appendChat("system", `生成失败：${snapshot.error || snapshot.message}`);
    $("generateButton").disabled = false;
    currentRunSource?.close();
  }
  if (state === "READY") {
    $("generateButton").disabled = false;
    currentRunSource?.close();
  }
}

function connectRun(runId, eventsUrl) {
  currentRunSource?.close();
  currentRunSource = new EventSource(eventsUrl || `/api/agent/runs/${runId}/events`);
  currentRunSource.addEventListener("run", (event) => {
    try { applyRun(JSON.parse(event.data)); } catch (error) { console.error(error); }
  });
}

async function startRun() {
  if (!imageDataUrl || !providerStatus?.configured) return;
  $("generateButton").disabled = true;
  lastTouch = null;
  resetChat();
  $("touchPosition").textContent = "尚未触摸";
  selectState("UNDERSTANDING");
  $("surfaceStatus").textContent = "UNDERSTANDING · 正在创建智能体运行";
  try {
    const response = await fetch("/api/agent/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_data_url: imageDataUrl,
        user_instruction: $("userInstruction").value.trim(),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "创建智能体运行失败");
    currentRunId = payload.run_id;
    connectRun(payload.run_id, payload.events_url);
  } catch (error) {
    $("generateButton").disabled = false;
    selectState("ERROR");
    appendChat("system", `无法启动：${error.message}`);
  }
}

function loadImageFile(file) {
  if (!file || !file.type.startsWith("image/")) return;
  if (file.size > 20 * 1024 * 1024) {
    appendChat("system", "图片超过 20MB，请选择较小文件。");
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    currentRunSource?.close();
    currentRunSource = null;
    currentRunId = null;
    runtimeImageFrameId = -1;
    lastTouch = null;
    resetChat();
    $("touchPosition").textContent = "尚未触摸";
    imageDataUrl = String(reader.result || "");
    const image = new Image();
    image.onload = () => {
      imageObject = image;
      drawInput(image);
      $("generateButton").disabled = !providerStatus?.configured;
    };
    image.src = imageDataUrl;
  };
  reader.readAsDataURL(file);
}

async function surfaceAction(action) {
  const response = await fetch("/api/surface/actions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  const payload = await response.json();
  if (!response.ok) appendChat("system", payload.error || "表面控制失败");
}

async function requestTouch(row, col, question = "") {
  if (!currentFrame) return;
  if (question) appendChat("user", question);
  try {
    const response = await fetch("/api/touch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ frame_id: currentFrame.frame_id, row, col, question }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "触摸解释失败");
    const regionName = payload.region?.name || "底面";
    $("touchPosition").textContent = `r${row} c${col} · h${payload.height} · ${regionName}`;
    appendChat("assistant", payload.speech);
    speak(payload.speech, payload.audio_url);
  } catch (error) {
    appendChat("system", `触摸解释失败：${error.message}`);
  }
}

async function boot() {
  try {
    const [infoResponse, providerResponse, frameResponse] = await Promise.all([
      fetch("/api/info", { cache: "no-store" }),
      fetch("/api/multimodal/status", { cache: "no-store" }),
      fetch("/api/runtime/frame", { cache: "no-store" }),
    ]);
    runtimeInfo = await infoResponse.json();
    providerStatus = await providerResponse.json();
    const initialRuntime = await frameResponse.json();
    $("providerModel").textContent = providerStatus.model || "—";
    $("device").textContent = runtimeInfo.hardware_contract?.device_id || "rdk_hdmi";
    $("boardUrl").textContent = chooseBoardUrl();
    setApiState(providerStatus.configured, providerStatus.configured ? `${providerStatus.model} 已连接` : "模型 API 未配置");
    applyRuntime(initialRuntime);
    connectRuntime();
  } catch (error) {
    setApiState(false, "本地服务连接失败");
    appendChat("system", error.message);
  }
}

$("fileInput").addEventListener("change", (event) => loadImageFile(event.target.files?.[0]));
$("dropZone").addEventListener("click", () => $("fileInput").click());
for (const name of ["dragenter", "dragover"]) $("dropZone").addEventListener(name, (event) => { event.preventDefault(); $("dropZone").classList.add("dragging"); });
for (const name of ["dragleave", "drop"]) $("dropZone").addEventListener(name, (event) => { event.preventDefault(); $("dropZone").classList.remove("dragging"); });
$("dropZone").addEventListener("drop", (event) => loadImageFile(event.dataTransfer?.files?.[0]));
$("generateButton").addEventListener("click", startRun);
$("changeButton").addEventListener("click", () => $("fileInput").click());
$("lowerButton").addEventListener("click", () => surfaceAction("lower"));
$("replayButton").addEventListener("click", () => surfaceAction("replay"));
$("openDisplay").addEventListener("click", () => window.open("/display.html", "_blank", "noopener"));
renderer.container.addEventListener("pinselect", (event) => {
  const { x, y } = event.detail;
  lastTouch = { row: y, col: x };
  requestTouch(y, x);
});
$("chatSend").addEventListener("click", () => {
  const question = $("chatInput").value.trim();
  if (!question || !currentFrame) return;
  $("chatInput").value = "";
  const target = lastTouch || { row: 24, col: 40 };
  requestTouch(target.row, target.col, question);
});
$("chatInput").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); $("chatSend").click(); } });
$("copyBoardUrl").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("boardUrl").textContent); } catch { window.prompt("复制 RDK 地址", $("boardUrl").textContent); }
});

boot();
