# LUT Match

Turn a reference image's color "vibe" into a `.cube` LUT you can drop straight into Premiere Pro's Lumetri Color panel.

Give it a reference photo (the look you want) and a still frame from your own footage. It automatically fixes any lighting problems in your footage first, then statistically matches its colors to the reference — shadows, midtones, and highlights independently, so it can reproduce looks like "warm highlights, cool shadows" that a simple color match can't. You fine-tune the result live, then export a LUT.

It's a small local web app: everything runs on your own machine, nothing is uploaded anywhere, no API keys or internet connection required.

## Requirements

- macOS
- Python 3.10 or newer ([python.org](https://www.python.org/downloads/) if you don't have it — check with `python3 --version` in Terminal)

## Running it

**Easiest: double-click `LUT Match.app`.**

The first launch takes about a minute to set itself up (you'll see a notification), then it opens in your browser. After that, opening it again is instant.

> If macOS says it can't be opened ("Apple could not verify..."), that's expected for an app not distributed through the App Store. Right-click (or Control-click) `LUT Match.app` and choose **Open**, then click **Open** again in the dialog that appears. You only need to do this once — normal double-clicking works after that. If right-click doesn't offer an "Open" option, go to **System Settings → Privacy & Security**, scroll down, and click **Open Anyway** next to the LUT Match message.

**Alternative: double-click `run.command`.** This opens a Terminal window and runs the app from there — a bit more visible, but sidesteps the Gatekeeper dialog above entirely since it runs through Terminal instead of as a standalone app.

Either way, the app opens at `http://127.0.0.1:8765` in your browser. Click the **✕ Quit** button in the app (top right) to shut it down when you're done.

## How to use it

1. **Reference (the vibe)** — drop in the image whose color look you want to copy. It doesn't need to show similar content to your footage — a sunset photo, a movie still, anything with a look you like works.
2. **Footage frame** — drop in a still frame exported from your actual footage. Tell it what kind of footage it is (normal Rec.709, or a log profile like S-Log3/V-Log/C-Log3) so it decodes the colors correctly.
3. **Match** — click **Match colors**. It automatically corrects any lighting issues in your frame first (you'll see a summary of what it fixed, if anything), then matches your footage's colors to the reference.
4. **Fine-tune** — hover over the preview to compare before/after with a sliding wipe. Adjust Strength (how much of the match to apply) and the six fine-tune sliders (temperature, tint, contrast, saturation, shadows, highlights) to taste. Use **Save look** to pin a version you like so you can compare a few variations, then **Export .cube**.
5. Load the exported `.cube` into Premiere: **Lumetri Color → Creative → Look**, and browse to the file (it lands in this project's `output/` folder).

## Project layout

```
lut-match/
  app/
    server.py       — the local web server (FastAPI)
    engine/          — the actual color math: corrections, matching, LUT export
    static/          — the browser UI (single HTML file)
  tests/             — automated tests (run with: .venv/bin/python -m pytest tests/ -q)
  docs/              — design notes and revision history
  LUT Match.app/     — double-clickable launcher
  run.command        — Terminal-based launcher (fallback)
```

## Premiere Pro panel (experimental — `feature-extension` branch)

LUT Match can also run as a docked panel inside Premiere Pro 2026, with two native buttons:

- **🎬 Grab frame** — exports the frame under the playhead straight into LUT Match (no manual still exports). Note: it grabs the *rendered* timeline frame, so disable any existing grade on the clip first.
- **🎯 Apply to clip** — exports the LUT and sets it as the selected clip's Lumetri Creative Look. If Premiere's scripting API refuses (it varies by version), you'll get the saved `.cube` path to browse to manually instead.

Install:

```bash
./cep/install.sh        # copies the panel into ~/Library/.../Adobe/CEP/extensions
```

Then restart Premiere → **Window → Extensions → LUT Match**. The panel starts the Python engine automatically (it needs the `.venv` to exist — double-click `run.command` once on a fresh machine first).

Unsigned panels require CEP debug mode once per machine: `defaults write com.adobe.CSXS.12 PlayerDebugMode 1`

## Notes for developers

- No external services, API keys, or AI models are used — the color matching is pure statistics (per-tone-range distribution transfer), not AI-generated.
- `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` sets up the environment manually if you'd rather not use the launchers.
- The app holds one session in memory (single user, no accounts) — it's meant to be run locally by one editor at a time, not deployed as a shared server.
