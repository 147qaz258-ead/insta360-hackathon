import { PinRenderer } from "./pin_renderer.js";

const params = new URLSearchParams(window.location.search);
const token = params.get("token") || "";
const mode = params.get("mode") === "blind" ? "blind" : "full";

function metricEntries(metrics) {
  if (!metrics) return [];
  const height = metrics.height_stats || {};
  return [
    ["升起点数", `${metrics.active_pins ?? "—"} / ${metrics.pin_count ?? 3840}`],
    ["升起率", `${((metrics.active_ratio ?? 0) * 100).toFixed(1)}%（目标 ${metrics.target_active_ratio || "18-30%"}）`],
    ["高度 min/mean/max", `${height.min ?? 0} / ${height.mean ?? 0} / ${height.max ?? 0}`],
    ["高度层数", height.distinct_levels ?? "—"],
    ["最长水平连续段", metrics.longest_horizontal_run ?? "—"],
    ["最长垂直连续段", metrics.longest_vertical_run ?? "—"],
    ["最大连通块占比", `${(((metrics.largest_component_ratio_of_active ?? 0)) * 100).toFixed(1)}%（${metrics.largest_component_pins ?? 0} 点）`],
    ["实心内部点占比", `${((metrics.solid_interior_ratio_of_active ?? 0) * 100).toFixed(1)}%`],
    ["content_rect", JSON.stringify(metrics.content_rect || {})],
  ];
}

function renderMetrics(metrics, regions) {
  const list = document.getElementById("metricsList");
  list.replaceChildren();
  for (const [key, value] of metricEntries(metrics)) {
    const dt = document.createElement("dt");
    dt.textContent = key;
    const dd = document.createElement("dd");
    dd.textContent = String(value);
    list.append(dt, dd);
  }
  const counts = (metrics && metrics.region_pin_counts) || {};
  for (const [regionKey, count] of Object.entries(counts)) {
    const dt = document.createElement("dt");
    dt.textContent = `区域 ${regionKey}`;
    const dd = document.createElement("dd");
    dd.textContent = `${count} 点`;
    list.append(dt, dd);
  }
  if (Array.isArray(regions) && regions.length) {
    const dt = document.createElement("dt");
    dt.textContent = "区域数";
    const dd = document.createElement("dd");
    dd.textContent = `${regions.length} 个`;
    list.append(dt, dd);
  }
}

function loadImage(dataUrl) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("原图加载失败"));
    image.src = dataUrl;
  });
}

function fail(message) {
  document.body.innerHTML = `<div class="load-error">审查页数据加载失败：${message}</div>`;
  document.title = "review-error";
}

async function boot() {
  if (!token) {
    fail("缺少 token");
    return;
  }
  let payload;
  try {
    const response = await fetch(`/api/internal/review-candidates/${encodeURIComponent(token)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    payload = await response.json();
  } catch (error) {
    fail(error.message || String(error));
    return;
  }

  const frame = payload.frame;
  if (mode === "blind") {
    document.getElementById("blindShell").hidden = false;
    const renderer = new PinRenderer(document.getElementById("blindSurface"));
    renderer.updateFrame(frame, { animate: false });
    renderer.setTopView();
    window.requestAnimationFrame(() => { document.title = "review-ready"; });
    return;
  }

  document.getElementById("fullShell").hidden = false;
  document.getElementById("candidateLabel").textContent =
    `候选 ${payload.candidate_number ?? "?"} · ${frame.cols} × ${frame.rows} · UInt8`;
  try {
    await loadImage(payload.image_data_url);
    document.getElementById("originalImage").src = payload.image_data_url;
  } catch (_error) {
    document.getElementById("originalImage").alt = "原图不可用";
  }

  const mainRenderer = new PinRenderer(document.getElementById("mainSurface"));
  mainRenderer.updateFrame(frame, { animate: false });
  mainRenderer.setTopView();

  const auxRenderer = new PinRenderer(document.getElementById("auxSurface"));
  auxRenderer.updateFrame(frame, { animate: false });
  auxRenderer.setPerspectiveView();

  renderMetrics(payload.metrics, frame.regions);
  window.requestAnimationFrame(() => { document.title = "review-ready"; });
}

boot();
