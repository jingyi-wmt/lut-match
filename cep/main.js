/* LUT Match — CEP panel shell.
 *
 * Responsibilities:
 *  - ensure the Python engine is running (spawn PROJECT/.venv/bin/uvicorn if not)
 *  - embed the web UI (http://127.0.0.1:8765/?panel=1) in the iframe
 *
 * No ExtendScript bridge: both native integrations that used it were tried
 * and removed.
 *  - Grab-frame depended on the undocumented QE method exportFramePNG(),
 *    which is dead in Premiere 26 — every call returns true but never
 *    writes a file (confirmed by exhaustive live testing).
 *  - Apply-to-clip could set the Lumetri Look, but even with the correct
 *    properties (LookAsset + the Look enum, reverse-engineered from what
 *    Premiere itself writes when browsing a LUT through the real UI) it
 *    still didn't render visibly — dropped in favor of applying LUTs
 *    manually in Lumetri, which always worked.
 *
 * The installed panel is a copy in ~/Library/.../CEP/extensions/LUTMatch, so
 * the project location comes from config.json written by install.sh.
 */

const BASE = "http://127.0.0.1:8765";

const nodeRequire =
  (window.cep_node && window.cep_node.require) || window.require || null;
const fs = nodeRequire ? nodeRequire("fs") : null;
const cp = nodeRequire ? nodeRequire("child_process") : null;
const os = nodeRequire ? nodeRequire("os") : null;
const Buffer = nodeRequire ? nodeRequire("buffer").Buffer : null;

const $ = (id) => document.getElementById(id);

function setStatus(cls, text) {
  $("status-dot").className = cls;
  $("status-text").textContent = text;
}

function readConfig() {
  try {
    // Loaded via file://.../LUTMatch/index.html — resolve config.json
    // relative to this page's own directory (no CSInterface needed for this).
    const dir = decodeURIComponent(new URL(".", document.location.href).pathname);
    return JSON.parse(fs.readFileSync(dir + "config.json", "utf8"));
  } catch (e) {
    return null;
  }
}

// Manual timeout instead of AbortSignal.timeout(): CEP's bundled Chromium is
// version 99, and that static method didn't exist until Chrome 103 — calling
// it throws synchronously and silently wedges the whole init chain.
function fetchWithTimeout(url, opts, ms) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  return fetch(url, Object.assign({}, opts, { signal: controller.signal })).finally(() =>
    clearTimeout(timer)
  );
}

function serverUp() {
  return fetchWithTimeout(BASE + "/status", {}, 1500)
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
  // Log to a file instead of discarding output: if the spawned process ever
  // fails to start or crashes, "stdio: ignore" would hide that completely —
  // this is the only way to see why, since CEP gives no other console access.
  const logPath = os.tmpdir() + "/lutmatch_engine.log";
  let child;
  try {
    const logFd = fs.openSync(logPath, "a");
    child = cp.spawn(
      uvicorn,
      ["app.server:app", "--host", "127.0.0.1", "--port", "8765"],
      { cwd: project, detached: true, stdio: ["ignore", logFd, logFd] }
    );
    child.unref();
  } catch (e) {
    showOffline("Could not start the engine: " + e.message);
    return false;
  }

  let exitedEarly = null;
  child.on("exit", (code, signal) => { exitedEarly = { code, signal }; });

  for (let i = 0; i < 40; i++) {
    await new Promise((r) => setTimeout(r, 500));
    if (await serverUp()) return true;
    if (exitedEarly) {
      showOffline(
        "Engine process exited immediately (code " + exitedEarly.code + "). " +
          "Log: " + logPath
      );
      return false;
    }
  }
  showOffline(
    "Engine didn't come up after 20s. Log: " + logPath +
      " — or try run.command manually."
  );
  return false;
}

function showOffline(text) {
  setStatus("", "engine offline");
  $("frame").style.display = "none";
  $("offline").style.display = "flex";
  $("offline-text").textContent = text;
}

function showApp() {
  setStatus("ok", "engine running");
  $("offline").style.display = "none";
  const frame = $("frame");
  // CEP's HTTP cache lives in a persistent on-disk profile that survives
  // across panel/Premiere restarts — Cache-Control: no-store only prevents
  // *new* caching, it doesn't invalidate anything already stored under this
  // exact URL from before that header existed. A unique query string every
  // launch guarantees there's nothing to ever reuse.
  frame.src = BASE + "/?panel=1&_v=" + Date.now();
  frame.style.display = "";
}

// --- Export .cube: let the user pick a save location via a native macOS
// dialog. The embedded app has no such thing available (it's just a web
// page), so it asks the shell to do this and reports back over postMessage.
function parseSuggestedName(contentDisposition) {
  const star = /filename\*=utf-8''([^;]+)/i.exec(contentDisposition || "");
  if (star) return decodeURIComponent(star[1]);
  const plain = /filename="?([^";]+)"?/i.exec(contentDisposition || "");
  return plain ? plain[1] : "look.cube";
}

window.addEventListener("message", async (e) => {
  if (!e.data || e.data.type !== "request-save") return;
  const reply = (payload) =>
    e.source.postMessage(Object.assign({ type: "save-result" }, payload), "*");

  if (!nodeRequire) {
    reply({ ok: false, error: "Node integration unavailable in this panel." });
    return;
  }
  try {
    const resp = await fetch(BASE + "/export?strength=" + (e.data.strength ?? 1));
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    const suggestedName = parseSuggestedName(resp.headers.get("content-disposition"));
    const bytes = Buffer.from(await resp.arrayBuffer());

    const config = readConfig();
    const defaultDir =
      config && config.projectPath ? config.projectPath + "/output" : os.tmpdir();

    const script =
      'POSIX path of (choose file name with prompt "Save LUT as:" default name "' +
      suggestedName.replace(/"/g, '\\"') +
      '" default location (POSIX file "' +
      defaultDir.replace(/"/g, '\\"') +
      '"))';

    let chosenPath;
    try {
      chosenPath = cp.execFileSync("osascript", ["-e", script], { encoding: "utf8" }).trim();
    } catch (cancelErr) {
      reply({ ok: false, cancelled: true });
      return;
    }
    fs.writeFileSync(chosenPath, bytes);
    reply({ ok: true, path: chosenPath });
  } catch (err) {
    reply({ ok: false, error: err.message });
  }
});

// --- wiring ---
$("retry").onclick = init;
$("quit-server").onclick = async () => {
  try { await fetch(BASE + "/shutdown", { method: "POST" }); } catch (e) {}
  showOffline("Engine stopped. Retry to start it again.");
};

async function init() {
  try {
    if (await ensureServer()) showApp();
  } catch (e) {
    // Safety net: any unexpected error (e.g. a browser API missing in CEP's
    // bundled Chromium) surfaces here instead of leaving the yellow
    // "checking engine…" dot stuck forever with a silent console error.
    showOffline("Unexpected panel error: " + (e && e.message ? e.message : e));
  }
}
init();
