import { PinRenderer } from "./pin_renderer.js";

const surface = document.getElementById("displaySurface");
const status = document.getElementById("displayStatus");
const grid = document.getElementById("displayGrid");
const renderer = new PinRenderer(surface);

let revision = -1;
let frameIdentity = "";

function applyRuntime(data) {
  if (!data || data.revision === revision) return;
  revision = data.revision;
  const state = data.motion_state || "IDLE";
  if (state === "RETRACTING" || state === "LOWERING_OLD_FRAME") {
    renderer.retract();
  } else if (data.frame) {
    const identity = `${data.frame.frame_id}:${data.frame.checksum || ""}`;
    if (identity !== frameIdentity || state === "RISING") {
      renderer.updateFrame(data.frame);
      renderer.setTopView();
      frameIdentity = identity;
    }
  }
  const checksum = data.frame?.checksum?.slice(7, 15) || "--------";
  status.textContent = `${state} · Frame #${data.frame?.frame_id ?? 0} · ${checksum}`;
  grid.textContent = `${data.frame?.cols ?? 80} × ${data.frame?.rows ?? 48} · UInt8`;
}

async function initialSnapshot() {
  try {
    const response = await fetch("/api/runtime/frame", { cache: "no-store" });
    if (!response.ok) throw new Error(await response.text());
    applyRuntime(await response.json());
  } catch (error) {
    console.error(error);
    status.textContent = "连接中断，正在重连…";
  }
}

function connect() {
  const source = new EventSource("/api/runtime/events");
  source.addEventListener("runtime", (event) => {
    try { applyRuntime(JSON.parse(event.data)); } catch (error) { console.error(error); }
  });
  source.onopen = () => document.body.classList.add("connected");
  source.onerror = () => {
    document.body.classList.remove("connected");
    status.textContent = "连接中断，正在自动重连…";
  };
}

initialSnapshot();
connect();
