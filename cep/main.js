/* LUT Match — CEP panel shell.
 *
 * Responsibilities:
 *  - ensure the Python engine is running (spawn PROJECT/.venv/bin/uvicorn if not)
 *  - embed the web UI (http://127.0.0.1:8765/?panel=1) in the iframe
 *  - native bridges via ExtendScript: grab playhead frame, apply LUT to clip
 *
 * The installed panel is a copy in ~/Library/.../CEP/extensions/LUTMatch, so
 * the project location comes from config.json written by install.sh.
 */

/* global CSInterface */

const BASE = "http://127.0.0.1:8765";
const cs = new CSInterface();

const nodeRequire =
  (window.cep_node && window.cep_node.require) || window.require || null;
const fs = nodeRequire ? nodeRequire("fs") : null;
const cp = nodeRequire ? nodeRequire("child_process") : null;
const os = nodeRequire ? nodeRequire("os") : null;

const $ = (id) => document.getElementById(id);

function setStatus(cls, text) {
  $("status-dot").className = cls;
  $("status-text").textContent = text;
}
function setMsg(text, isError) {
  $("msg").textContent = text || "";
  $("msg").className = isError ? "error" : "";
}

function readConfig() {
  try {
    // "extension" = Adobe's SystemPath.EXTENSION constant (shim doesn't define it)
    const dir = cs.getSystemPath("extension");
    return JSON.parse(fs.readFileSync(dir + "/config.json", "utf8"));
  } catch (e) {
    return null;
  }
}

function serverUp() {
  return fetch(BASE + "/status", { signal: AbortSignal.timeout(1500) })
    .then((r) => r.ok)
    .catch(() => false);
}

async function ensureServer() {
  setStatus("wait", "checking engine…");
  if (await serverUp()) return true;

  const config = readConfig();
  if (!nodeRequire || !config || !config.projectPath) {
    showOffline(
      "Engine isn't running and the panel can't start it (missing config.json). " +
        "Run install.sh again, or start LUT Match manually (run.command)."
    );
    return false;
  }
  const project = config.projectPath;
  const uvicorn = project + "/.venv/bin/uvicorn";
  if (!fs.existsSync(uvicorn)) {
    showOffline(
      "No Python environment at " + project + "/.venv — double-click run.command " +
        "once to set it up, then Retry."
    );
    return false;
  }

  setStatus("wait", "starting engine…");
  try {
    const child = cp.spawn(
      uvicorn,
      ["app.server:app", "--host", "127.0.0.1", "--port", "8765"],
      { cwd: project, detached: true, stdio: "ignore" }
    );
    child.unref();
  } catch (e) {
    showOffline("Could not start the engine: " + e.message);
    return false;
  }

  for (let i = 0; i < 40; i++) {
    await new Promise((r) => setTimeout(r, 500));
    if (await serverUp()) return true;
  }
  showOffline("Engine didn't come up after 20s — try run.command manually.");
  return false;
}

function showOffline(text) {
  setStatus("", "engine offline");
  $("frame").style.display = "none";
  $("offline").style.display = "flex";
  $("offline-text").textContent = text;
  $("grab").disabled = true;
  $("apply").disabled = true;
}

function showApp() {
  setStatus("ok", "engine running");
  $("offline").style.display = "none";
  const frame = $("frame");
  frame.src = BASE + "/?panel=1";
  frame.style.display = "";
  $("grab").disabled = false;
  $("apply").disabled = false;
}

function evalScript(script) {
  return new Promise((resolve) => cs.evalScript(script, resolve));
}

// --- fail-fast ExtendScript check (the one thing not verifiable from disk) ---
async function pingHost() {
  const res = await evalScript("lmPing()");
  if (res !== "pong") {
    setMsg(
      "ExtendScript bridge failed (" + String(res).slice(0, 80) + ") — " +
        "grab/apply disabled; the embedded app still works.",
      true
    );
    $("grab").disabled = true;
    $("apply").disabled = true;
    return false;
  }
  return true;
}

// --- grab the frame under the playhead ---
async function grabFrame() {
  setMsg("Grabbing frame…");
  $("grab").disabled = true;
  try {
    const path = os.tmpdir() + "/lutmatch_grab_" + Date.now() + ".png";
    const res = await evalScript('lmGrabFrame("' + path.replace(/"/g, '\\"') + '")');
    if (res !== "ok") throw new Error(res || "no response from Premiere");

    // exportFramePNG writes asynchronously; wait for the file.
    let exists = false;
    for (let i = 0; i < 50 && !exists; i++) {
      await new Promise((r) => setTimeout(r, 100));
      exists = fs.existsSync(path) && fs.statSync(path).size > 0;
    }
    if (!exists) throw new Error("Premiere exported no frame (file never appeared)");

    const resp = await fetch(BASE + "/frame-from-path", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);

    $("frame").contentWindow.postMessage({ type: "frame-updated" }, "*");
    setMsg("Frame grabbed. Note: grabs the rendered timeline — disable existing grades first.");
  } catch (e) {
    setMsg("Grab failed: " + e.message, true);
  } finally {
    $("grab").disabled = false;
  }
}

// --- export the LUT and apply it to the selected clip ---
function getIframeSettings() {
  return new Promise((resolve) => {
    const onMsg = (e) => {
      if (e.data && e.data.type === "settings") {
        window.removeEventListener("message", onMsg);
        resolve(e.data);
      }
    };
    window.addEventListener("message", onMsg);
    $("frame").contentWindow.postMessage({ type: "get-settings" }, "*");
    setTimeout(() => {
      window.removeEventListener("message", onMsg);
      resolve(null);
    }, 1500);
  });
}

async function applyLut() {
  setMsg("Exporting LUT…");
  $("apply").disabled = true;
  try {
    const settings = await getIframeSettings();
    if (!settings || !settings.ready) {
      throw new Error("Match colors first — nothing to export yet.");
    }
    const resp = await fetch(BASE + "/export-file?strength=" + (settings.strength ?? 1));
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    const { path } = await resp.json();

    setMsg("Applying to selected clip…");
    const res = await evalScript('lmApplyLut("' + path.replace(/"/g, '\\"') + '")');
    if (res === "ok") {
      setMsg("LUT applied to the selected clip's Lumetri Look.");
    } else {
      // Graceful degrade: the .cube exists on disk either way.
      setMsg(
        "Couldn't auto-apply (" + res + "). LUT saved at " + path +
          " — in Lumetri: Creative → Look → Browse.",
        true
      );
    }
  } catch (e) {
    setMsg("Apply failed: " + e.message, true);
  } finally {
    $("apply").disabled = false;
  }
}

// --- wiring ---
$("grab").onclick = grabFrame;
$("apply").onclick = applyLut;
$("retry").onclick = init;
$("quit-server").onclick = async () => {
  try { await fetch(BASE + "/shutdown", { method: "POST" }); } catch (e) {}
  showOffline("Engine stopped. Retry to start it again.");
};

async function init() {
  setMsg("");
  if (await ensureServer()) {
    showApp();
    await pingHost();
  }
}
init();
