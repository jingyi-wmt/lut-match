# LUT Match — AI "color DNA" LUT generator with Premiere integration

## Context

JZ is a video editor who wants to speed up color grading: provide a reference image that carries a *vibe/style* (not necessarily similar content), have AI extract the **color-grading DNA** of that reference, and bake it into a `.cube` LUT for Premiere Pro (Lumetri → Creative). The footage frame comes straight from Premiere: the app grabs the frame at the playhead via JZ's existing **premiere-pro-mcp** server — no manual screenshots.

Decisions from brainstorming:
- **Personal local tool** on macOS; local web app (Python engine + single-page UI at `localhost`).
- **Vision-AI-led grading**: the model extracts a structured grading recipe from the reference and it is rendered into the LUT. A literal statistical color match is kept only as a secondary "match exactly" mode / no-key fallback.
- **Model access via CLI agents, no API keys**: JZ cannot provide raw API keys but has authenticated CLI coding agents (Claude Code `claude`, code puppy, etc.). The vision layer shells out to a configured CLI in headless mode (e.g. `claude -p "<prompt>" --output-format json`, prompt instructs the agent to read the two image files from disk and return strict JSON). Adapters are pluggable so any CLI agent or a local Ollama vision model can be used. App degrades gracefully to literal-match mode when no CLI is configured/working.
- **Mixed footage**: user marks the frame Rec.709 or log (S-Log3 / V-Log / C-Log3 / generic); log frames are linearized before grading so the LUT includes the log handling.

Project dir: `/Users/j0z08ai/Documents/Local AI folder/lut-match/` (new, `git init`).

## Verified facts about the Premiere MCP (explored this session)

- Lives at `/Volumes/WMT_FY27/Library/AI/assistant-video-editor/vendor/premiere-pro-mcp/` (volume currently mounted). Launch command per its `.mcp.json`: `tools/node/bin/node vendor/premiere-pro-mcp/dist/index.js` (stdio transport).
- Tool `capture_frame` (src/tools/export.ts:280): exports the frame at the playhead (or a given `time_seconds`) as PNG to `$TMPDIR/mcp_frame_capture_<ts>.png` via `seq.exportFramePNG`, executed by the CEP "MCP Bridge" panel through a file-based bridge. Also useful: `get_premiere_state` (playhead, sequence, selection).
- ⚠️ Prior session notes (`~/.claude/plans/i-have-transfer-this-encapsulated-peacock.md`) say the Bridge panel install on this machine was never completed (`install-cep.sh --copy`, then Premiere → Window → Extensions → MCP Bridge → Start Bridge). Plan step 0 verifies/completes this.

## Architecture

```
lut-match/
  app/
    server.py            # FastAPI: serves UI; /grab-frame, /analyze, /preview, /export, /settings
    premiere.py          # MCP stdio client (python `mcp` pkg): spawn premiere MCP, call capture_frame,
                         #   read PNG from returned temp path; clear errors if volume unmounted /
                         #   bridge not started / no active sequence
    engine/
      io.py              # load JPEG/PNG/TIFF (8/16-bit), validation warnings (clipped/dark/small)
      logspace.py        # log→linear transfer functions via colour-science
      recipe.py          # GradingRecipe (pydantic): temp/tint, lift/gamma/gain, tone-curve points,
                         #   global + per-hue saturation, split-tone shadow/highlight colors, contrast
      render.py          # apply a GradingRecipe to an image (numpy) — single source of truth,
                         #   used by both preview and LUT baking; strength = interpolate recipe→identity
      match.py           # secondary literal statistical match mode (distribution transfer)
      lut.py             # sample 33³ lattice through render pipeline → .cube; clamp [0,1], no NaNs
    vision/
      provider.py        # abstract VisionProvider.extract_dna(ref_path, frame_path) -> GradingRecipe + look_description
      cli_agent.py       # shell out to an authenticated CLI agent (subprocess, 120s timeout):
                         #   claude:     claude -p "<prompt referencing the two image paths>" --output-format json
                         #   generic:    command template from config (covers code puppy, gemini-cli, etc.)
                         #   parses/validates strict-JSON GradingRecipe from agent output
      ollama_p.py        # optional keyless local vision model via Ollama HTTP (llava, qwen-vl)
      registry.py        # provider from config.toml; None/failing → literal-match-only mode
    static/index.html    # single-page UI (vanilla JS)
  config.toml            # provider, model, base_url, premiere MCP path; keys via env
  tests/
  run.command            # double-clickable launcher (venv, deps, open browser)
```

Python 3.12+, deps: `fastapi uvicorn numpy pillow colour-science httpx pydantic mcp`.

## Data flow

1. **Reference in**: drag-drop reference image (drop zone).
2. **Frame in**: "Grab frame from Premiere" button → `/grab-frame` → `premiere.py` spawns the MCP over stdio, calls `capture_frame`, loads the PNG. **Fallback**: if the MCP path fails for any reason (volume unmounted, bridge not running, no active sequence, timeout), the UI shows why and highlights the manual drop zone — dropping a still frame of the footage works identically from that point on. User marks Rec.709 vs log type either way.
3. **DNA extraction**: `/analyze` writes both images (≤1024px) to a temp dir and invokes the configured CLI agent with a fixed prompt naming those file paths: extract the reference's grading DNA as strict-JSON `GradingRecipe` + plain-English look description, *taking the footage frame into account* (e.g. don't double-warm an already-warm frame). Prompt design can borrow category structure from the local `visual-dna-extractor` skill (`~/.claude/skills/visual-dna-extractor/`) — its Colour/Lighting/Mood taxonomy.
4. **Preview**: `render.py` applies the recipe to the (linearized) frame; UI shows before/after; **strength slider** re-renders live; look description + editable recipe values shown so JZ can hand-tune (temp, tint, LGG, saturation).
5. **Export**: `/export` bakes log-handling + recipe (at chosen strength, with hand-tweaks) through a 33³ lattice → `<reference-name>-dna.cube`, ready for Lumetri Creative.
6. **Fallback / alt mode**: "Literal match" toggle uses `match.py` distribution transfer instead of the DNA recipe (also the automatic path when no vision provider configured or the call fails — with a UI notice).

## Error handling

- Premiere grab: distinct messages for volume not mounted, MCP spawn failure, Bridge panel not running, no active sequence; falls back to manual upload.
- Vision: 120s subprocess timeout (CLI agents are slower than raw APIs), JSON schema validation of the recipe (retry once with repair prompt, then fall back to literal match); surface the CLI's stderr in the UI notice when it fails.
- Images: reject non-images; warn on clipped/very dark/small frames.
- LUT: clamp to [0,1], NaN check before write.

## Testing / verification

- **Unit**: identity recipe → near-identity LUT; each recipe field produces the expected pixel-level change; .cube writer round-trips; log curves match colour-science reference values; recipe JSON schema validation with malformed model output.
- **Contract**: vision adapters mocked; premiere.py tested against a fake MCP server.
- **Integration (real)**: with Premiere open + Bridge started, click "Grab frame" and confirm the playhead frame appears; run a DNA extraction with the configured provider; export .cube; apply the .cube to the frame in Python and confirm it matches the preview within tolerance.
- **Final real-world check**: JZ loads the LUT in Premiere's Lumetri Creative tab on the actual sequence.

## Implementation order

0. Verify Premiere MCP works on this machine: spawn it, call `get_premiere_state` with Premiere running. If the Bridge panel isn't installed, run `vendor/premiere-pro-mcp/... install-cep.sh --copy` and have JZ start Bridge (Window → Extensions → MCP Bridge).
1. Scaffold project, `git init`, save design spec to `lut-match/docs/superpowers/specs/2026-07-07-lut-match-design.md`, commit.
2. Engine (TDD): io → logspace → recipe/render → lut; then match.py.
3. FastAPI server + UI: upload, preview, strength slider, export.
4. `premiere.py` + Grab-frame button.
5. Vision layer: provider abstraction, `claude -p` CLI adapter first (already installed and authenticated on this machine), then the generic CLI-template adapter (code puppy etc.) and optional Ollama; settings panel.
6. Polish (warnings, naming, run.command) and full end-to-end verification per above.

## Revisions — 2026-07-08 (user feedback round 1)

1. **Premiere MCP integration removed entirely** (JZ prefers dropping still frames manually). `app/premiere.py`, its tests, the `/grab-frame` endpoint, the UI button, the `mcp` dependency, and the `[premiere]` config section are gone.
2. **Reference/footage panels are 16:9** (`aspect-ratio: 16/9` drop zones), matching video framing.
3. **Full-screen button** on the preview card (native Fullscreen API on the wipe-compare wrap).
4. **Correction first, match second**: new `app/engine/correct.py` auto-fixes footage lighting before any look transfer — levels stretch (1st–99th percentile → 0.02–0.95), gray-world white balance, neutral exposure gamma toward middle gray. Global transform, so it bakes into the LUT. UI checkbox "Auto-correct lighting first" (default on).
5. **Shadow/Highlight fine-tune sliders**, plus the existing temp/tint/contrast/saturation, now form a separate *tweaks layer* (`/tweaks` endpoint, `GradingRecipe` with `shadows`/`highlights` fields) applied after the match in **both** DNA and literal-match modes. The AI recipe is no longer edited by the sliders.

Pipeline is now: footage → log-to-display → auto-correction → DNA recipe or literal match (strength) → user tweaks → .cube.

## Revisions — 2026-07-08 (colorist review round)

Reviewed as a colorist; implemented the core color-science fixes plus polish:

1. **Skin-tone protection** (`skin_protection` on `GradingRecipe`, default 0.7): per-hue saturation moves and split-toning are attenuated for colors near the skin-tone line (hue ≈25° ± 22°, skin-typical chroma/luma). Being hue-based, it bakes into the LUT. DNA prompt now also instructs the model to keep skin natural.
2. **Near-neutral white balance**: WB is measured from the scene's actual near-neutral pixels (chroma < 0.10) and applied FIRST — before the levels stretch, which would amplify chroma and hide the neutrals. Gray-world is only a tightly-capped (0.93–1.08) fallback when a scene has no neutrals, so sunsets/neon keep their intended cast.
3. **Correction deadband + strength**: levels only act when blacks/whites are clearly off (p1 > 0.06 or p99 < 0.80); exposure gamma only when mean luma leaves 0.32–0.58. New "Correction" strength slider (0–100%, `/options` endpoint) blends the whole correction.
4. **Soft-knee clip** (`soft_clip` in render.py): final pipeline stage; identity inside [0.03, 0.97], smooth tanh rolloff outside — pushed highlights compress instead of clipping flat.
5. **Separated S/H bands**: shadows die out by mid-gray, highlights start there — the two sliders no longer fight over midtones.
6. **LUT resolution option**: 33-pt or 65-pt export (`/export?size=`), selector in the UI.

Deferred by JZ for later: live scopes (waveform/vectorscope), correction-only A/B compare.

## Revisions — 2026-07-08 (round 3: pure-math engine)

1. **AI/vision layer removed entirely** (JZ's decision after trade-off discussion): `app/vision/`, `config.toml`, CLI-agent and Ollama support deleted. The app is now 100% self-contained math — no keys, no CLIs, no network.
2. **Band-wise match** replaces both the DNA mode and the global literal match: shadows/mids/highlights each get their own Monge-Kantorovich transform, blended smoothly by pixel luma (LUT-bakeable). Captures tonally split looks (warm highlights/cool shadows) that global statistics cannot. Plus a "Keep original brightness" option that transfers the palette but preserves the footage's luma structure.
3. **Correction is no longer silent** (root cause of "does nothing" report: the deadband). `Correction.describe()` produces e.g. "fixing: levels ×1.26 · WB R+3% B−7% · exposure γ0.70 (brighter)" or "frame is technically clean — nothing to fix"; shown in the UI. The auto-correct checkbox and strength slider are now live (`/options` recomputes) — no re-analyze needed.
4. **Hover wipe**: the before/after divider follows the mouse; no click-drag (which was grabbing the image itself).
5. **Fine-tune card moved below the preview** (2-column slider grid) — adjust while watching.
6. **A/B/C compare slots** (sessionStorage): save/load full slider state for quick look comparison.
7. **33/65 selector removed** from UI (server still accepts `size=`).
8. **`LUT Match.app`** double-clickable bundle (plain Info.plist + shell launcher; reuses a running server, first-run venv setup with notification) + in-app **Quit** button (`/shutdown`).
