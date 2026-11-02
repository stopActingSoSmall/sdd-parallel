"""Deterministic Playwright + Pillow terminal screencast renderer for sdd-parallel.

Renders a pixel-perfect terminal screencast with Catppuccin Mocha styling,
Callout-First (Inverted Pyramid) layout with a docked bottom status bar across 4 paged scenes:
  Scene 1: sdd-parallel demo --stage 1 (6.5s)
  Scene 2: sdd-parallel plan (6.5s)
  Scene 3: sdd-parallel prepare --wave 1 (6.5s)
  Scene 4: sdd-parallel merge --wave 1 (6.5s)

Output:
  docs/assets/demo.gif (<= 5 MB, ~26-28s duration)
  assets/demo.gif
"""

from __future__ import annotations

# ruff: noqa: E501
import io
import shutil
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #11111b;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 1000px;
    height: 600px;
    font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
    overflow: hidden;
  }
  .window {
    width: 960px;
    height: 560px;
    background: #1e1e2e;
    border-radius: 12px;
    border: 1px solid #313244;
    box-shadow: 0 20px 40px rgba(0, 0, 0, 0.6);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .titlebar {
    height: 38px;
    background: #181825;
    display: flex;
    align-items: center;
    padding: 0 16px;
    border-bottom: 1px solid #313244;
    position: relative;
    flex-shrink: 0;
  }
  .buttons {
    display: flex;
    gap: 8px;
  }
  .btn {
    width: 12px;
    height: 12px;
    border-radius: 50%;
  }
  .btn-red { background: #f38ba8; }
  .btn-yellow { background: #f9e2af; }
  .btn-green { background: #a6e3a1; }
  .title {
    position: absolute;
    left: 0;
    right: 0;
    text-align: center;
    color: #a6adc8;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.3px;
  }
  .terminal-body {
    flex: 1;
    padding: 16px 20px;
    color: #cdd6f4;
    font-size: 13px;
    line-height: 1.45;
    white-space: pre;
    overflow: hidden;
    font-variant-ligatures: normal;
  }
  .prompt { color: #89b4fa; font-weight: 600; }
  .prompt-path { color: #a6e3a1; }
  .cmd { color: #89dceb; font-weight: 600; }
  .dim { color: #6c7086; }
  .bold { font-weight: 700; }
  .green { color: #a6e3a1; }
  .yellow { color: #f9e2af; }
  .red { color: #f38ba8; }
  .cyan { color: #89dceb; }
  .blue { color: #89b4fa; }
  .magenta { color: #cba6f7; }
  .cursor {
    display: inline-block;
    width: 8px;
    height: 15px;
    background: #cdd6f4;
    vertical-align: text-bottom;
    margin-left: 2px;
  }

  /* Docked Bottom Status Bar */
  .statusbar {
    height: 42px;
    background: #181825;
    border-top: 1px solid #313244;
    display: flex;
    align-items: center;
    padding: 0 20px;
    font-size: 13px;
    gap: 16px;
    flex-shrink: 0;
  }
  .badge {
    padding: 3px 9px;
    border-radius: 4px;
    font-weight: 700;
    font-size: 11.5px;
    letter-spacing: 0.5px;
    color: #11111b;
  }
  .badge-step1 { background: #f9e2af; }
  .badge-step2 { background: #fab387; }
  .badge-step3 { background: #89b4fa; }
  .badge-step4 { background: #a6e3a1; }
  .progress-bar {
    font-family: monospace;
    font-size: 13px;
    letter-spacing: 1px;
    display: flex;
    align-items: center;
  }
  .bar-fill { color: #a6e3a1; font-weight: 700; }
  .bar-empty { color: #45475a; }
  .next-info {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-left: auto;
    font-size: 12.5px;
  }
  .next-label { color: #6c7086; font-weight: 600; }
  .next-cmd { color: #89dceb; font-weight: 700; }
  .done-badge { color: #a6e3a1; font-weight: 700; }
  .try-label { color: #cdd6f4; }
</style>
</head>
<body>
<div class="window">
  <div class="titlebar">
    <div class="buttons">
      <div class="btn btn-red"></div>
      <div class="btn btn-yellow"></div>
      <div class="btn btn-green"></div>
    </div>
    <div class="title">sdd-parallel — bash — 1000×600</div>
  </div>
  <div class="terminal-body" id="term"></div>
  <div class="statusbar" id="statusbar"></div>
</div>
</body>
</html>
"""


def render_screencast() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    docs_assets_dir = repo_root / "docs" / "assets"
    docs_assets_dir.mkdir(parents=True, exist_ok=True)
    root_assets_dir = repo_root / "assets"
    root_assets_dir.mkdir(parents=True, exist_ok=True)

    gif_dest_docs = docs_assets_dir / "demo.gif"
    gif_dest_root = root_assets_dir / "demo.gif"

    # Status bars across 4 stages
    status_s1 = (
        '<span class="badge badge-step1">STEP 1/4 ●○○○</span>'
        '<span class="progress-bar"><span class="bar-fill">████</span><span class="bar-empty">░░░░░░░░░░░░</span></span>'
        '<div class="next-info"><span class="next-label">Next ➔</span> <span class="next-cmd">sdd-parallel plan</span></div>'
    )
    status_s2 = (
        '<span class="badge badge-step2">STEP 2/4 ●●○○</span>'
        '<span class="progress-bar"><span class="bar-fill">████████</span><span class="bar-empty">░░░░░░░░</span></span>'
        '<div class="next-info"><span class="next-label">Next ➔</span> <span class="next-cmd">sdd-parallel prepare --wave 1</span></div>'
    )
    status_s3 = (
        '<span class="badge badge-step3">STEP 3/4 ●●●○</span>'
        '<span class="progress-bar"><span class="bar-fill">████████████</span><span class="bar-empty">░░░░</span></span>'
        '<div class="next-info"><span class="next-label">Next ➔</span> <span class="next-cmd">sdd-parallel merge --wave 1</span></div>'
    )
    status_s4 = (
        '<span class="badge badge-step4">STEP 4/4 ●●●●</span>'
        '<span class="progress-bar"><span class="bar-fill">████████████████</span></span>'
        '<div class="next-info"><span class="done-badge">Complete!</span> <span class="try-label">Try:</span> <span class="next-cmd">uvx sdd-parallel demo</span></div>'
    )

    # Define keyframe script: (term_html, status_html, duration_ms)
    keyframes: list[tuple[str, str, int]] = []

    prompt_demo = '<span class="prompt">user@host</span>:<span class="prompt-path">/tmp/sdd-demo</span>$ '

    # =========================================================================
    # SCENE 1: Setup & Hidden Dependency Trap (6.0s reading pause)
    # =========================================================================
    cmd1 = "sdd-parallel demo --stage 1"
    for i in range(1, len(cmd1) + 1, 2):
        text = prompt_demo + f'<span class="cmd">{cmd1[:i]}</span><span class="cursor"></span>'
        keyframes.append((text, status_s1, 50))
    text = prompt_demo + f'<span class="cmd">{cmd1}</span>'
    keyframes.append((text, status_s1, 300))

    s1_output = (
        text + "\n"
        '<span class="yellow">┌────────────────────────────────────────────────────────────────────┐</span>\n'
        '<span class="yellow">│</span> <span class="yellow bold">⚠️  HIDDEN LINK FOUND: api.py (T004) imports normalizer.py (T002)</span>   <span class="yellow">│</span>\n'
        '<span class="yellow">│</span>    Standard worktrees run them in parallel and crash on merge.     <span class="yellow">│</span>\n'
        '<span class="yellow">│</span>    SDD-Parallel AST scanner catches semantic imports upfront.      <span class="yellow">│</span>\n'
        '<span class="yellow">└────────────────────────────────────────────────────────────────────┘</span>\n\n'
        '<span class="bold">[Step 1/4] AST &amp; Architectural Role Classification</span>\n'
        '<span class="bold">File Path                              | Detected Role      | Importers</span>\n'
        '<span class="dim">----------------------------------------------------------------------</span>\n'
        'src/demo_app/api.py                    | standard           | -\n'
        'src/demo_app/cli.py                    | <span class="blue">additive_registry</span>  | -\n'
        'src/demo_app/exporter.py               | standard           | -\n'
        'src/demo_app/normalizer.py             | standard           | <span class="yellow">src/demo_app/api.py</span>\n'
        'src/demo_app/schema.py                 | <span class="yellow">spine</span>              | -'
    )
    # Reading pause: 6.0s
    keyframes.append((s1_output, status_s1, 6000))

    # =========================================================================
    # SCENE 2: sdd-parallel plan — Naive Crash vs Wave Split (6.0s reading pause)
    # =========================================================================
    cmd2 = "sdd-parallel plan"
    for i in range(1, len(cmd2) + 1, 2):
        text = prompt_demo + f'<span class="cmd">{cmd2[:i]}</span><span class="cursor"></span>'
        keyframes.append((text, status_s2, 50))
    text = prompt_demo + f'<span class="cmd">{cmd2}</span>'
    keyframes.append((text, status_s2, 300))

    s2_output = (
        text + "\n"
        '<span class="red">┌────────────────────────────────────────────────────────────────────┐</span>\n'
        '<span class="red">│</span> <span class="red bold">❌ Standard Worktrees: T002 + T004 in parallel -> CRASH ON MERGE</span>   <span class="red">│</span>\n'
        '<span class="green">│</span> <span class="green bold">✅ SDD-Parallel Plan : Split into Wave 1 -> Wave 2</span>                 <span class="green">│</span>\n'
        '<span class="green">│</span>    Hidden import resolved sequentially, cli.py merged safely!      <span class="green">│</span>\n'
        '<span class="green">└────────────────────────────────────────────────────────────────────┘</span>\n\n'
        '<span class="bold">[Step 2/4] Type-2 Semantic Collision Detection &amp; BSP Wave Schedule</span>\n'
        '<span class="bold">Collision Analysis:</span>\n'
        '  - <span class="yellow bold">Hidden Link Found:</span> api.py (T004) imports normalizer.py (T002)\n'
        '  - <span class="blue bold">Additive Registry:</span> T002 and T003 extend cli.py (<span class="green">Wave 1 Safe</span>)\n'
        '<span class="bold">Wave Schedule:</span>\n'
        '  * <span class="magenta bold">Wave 1</span>: [<span class="green">track-a: T002</span>] [<span class="green">track-b: T003</span>] (parallel independent)\n'
        '  * <span class="magenta bold">Wave 2</span>: [<span class="yellow">track-a: T001</span>] [<span class="yellow">track-b: T004</span>] (runs after Wave 1 normalizer)\n'
        '  * <span class="magenta bold">Wave 3</span>: [<span class="blue">track-a: T005</span>] (schema migration barrier)'
    )
    # Reading pause: 6.0s
    keyframes.append((s2_output, status_s2, 6000))

    # =========================================================================
    # SCENE 3: sdd-parallel prepare --wave 1 — Worktrees & Guard DENY (6.0s)
    # =========================================================================
    cmd3 = "sdd-parallel prepare --wave 1"
    for i in range(1, len(cmd3) + 1, 2):
        text = prompt_demo + f'<span class="cmd">{cmd3[:i]}</span><span class="cursor"></span>'
        keyframes.append((text, status_s3, 50))
    text = prompt_demo + f'<span class="cmd">{cmd3}</span>'
    keyframes.append((text, status_s3, 300))

    s3_output = (
        text + "\n"
        '<span class="yellow">┌────────────────────────────────────────────────────────────────────┐</span>\n'
        '<span class="yellow">│</span> <span class="yellow bold">🔒 Fixed-slot worktrees (.worktrees/track-a, track-b)</span>              <span class="yellow">│</span>\n'
        '<span class="yellow">│</span>    PreToolUse Scope Guard prevents rogue writes across slots       <span class="yellow">│</span>\n'
        '<span class="yellow">│</span>    track-a attempt to edit schema.py -> <span class="red bold">DENY (Spine-Lock)!</span>         <span class="yellow">│</span>\n'
        '<span class="yellow">└────────────────────────────────────────────────────────────────────┘</span>\n\n'
        '<span class="bold">[Step 3/4] Fixed-Slot Worktree Provisioning &amp; PreToolUse Scope Guard</span>\n'
        '<span class="dim">----------------------------------------------------------------------</span>\n'
        'Provisioned Fixed Slots (<span class="green bold">100% Clean git status: True</span>):\n'
        '  - <span class="green">track-a</span> (.worktrees/track-a)\n'
        '  - <span class="green">track-b</span> (.worktrees/track-b)\n'
        'PreToolUse Scope Guard Check (track-a -> \'src/demo_app/schema.py\'):\n'
        '  - Decision : <span class="red bold">DENY</span>\n'
        '  - Reason   : Locked by Spine Chain invariant.'
    )
    # Reading pause: 6.0s
    keyframes.append((s3_output, status_s3, 6000))

    # =========================================================================
    # SCENE 4: sdd-parallel merge --wave 1 — Conflict-Free Merge & 5-Kit (6.0s)
    # =========================================================================
    cmd4 = "sdd-parallel merge --wave 1"
    for i in range(1, len(cmd4) + 1, 2):
        text = prompt_demo + f'<span class="cmd">{cmd4[:i]}</span><span class="cursor"></span>'
        keyframes.append((text, status_s4, 50))
    text = prompt_demo + f'<span class="cmd">{cmd4}</span>'
    keyframes.append((text, status_s4, 300))

    s4_output = (
        text + "\n"
        '<span class="green">┌────────────────────────────────────────────────────────────────────┐</span>\n'
        '<span class="green">│</span> <span class="green bold">✨ Wave 1 merged with 0 conflict markers in tasks.md</span>               <span class="green">│</span>\n'
        '<span class="green">│</span>    Verified across all 5 SDD frameworks (BMAD, Spec Kit, GSD...)   <span class="green">│</span>\n'
        '<span class="green">│</span>    Worktrees cleaned up. Ready for Wave 2 execution!               <span class="green">│</span>\n'
        '<span class="green">└────────────────────────────────────────────────────────────────────┘</span>\n\n'
        '<span class="bold">[Step 4/4] Barrier Checkpoint &amp; Monotonic 3-Way Merge</span>\n'
        '<span class="dim">----------------------------------------------------------------------</span>\n'
        'Merged Branches: <span class="green">parallel/track-a</span>, <span class="green">parallel/track-b</span>\n'
        'Resolved tasks.md (<span class="green bold">0 conflict markers</span>):\n'
        '  <span class="green">✓</span> - [x] T002 [P] [US1] Normalize `src/demo_app/normalizer.py`, `src/demo_app/cli.py`\n'
        '  <span class="green">✓</span> - [x] T003 [P] [US1] Export `src/demo_app/exporter.py`, `src/demo_app/cli.py`\n'
        '<span class="cyan">======================================================================</span>\n'
        '<span class="green bold">ALL 5 CORE INVARIANTS VERIFIED IN 0.38s (5/5 SDD KITS PASSED)</span>\n'
        '<span class="cyan">======================================================================</span>'
    )
    # Reading pause: 6.0s
    keyframes.append((s4_output, status_s4, 6000))

    total_duration_ms = sum(dur for _, _, dur in keyframes)
    print(f"🎬 Total keyframes: {len(keyframes)}, Total duration: {total_duration_ms / 1000:.1f}s")

    # Render frames via Playwright
    rendered_images: list[Image.Image] = []
    durations: list[int] = []

    print("🎨 Rendering terminal frames via Playwright Chromium...")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            try:
                browser = p.chromium.launch(channel="chrome")
            except Exception:
                browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(
            viewport={"width": 1000, "height": 600},
            device_scale_factor=1,
        )

        page.set_content(HTML_TEMPLATE, wait_until="domcontentloaded")

        for idx, (content, status, dur) in enumerate(keyframes):
            page.evaluate(
                """(args) => {
                    document.getElementById('term').innerHTML = args.term;
                    document.getElementById('statusbar').innerHTML = args.status;
                }""",
                {"term": content, "status": status},
            )
            png_bytes = page.locator(".window").screenshot(type="png", omit_background=False)
            img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            # Quantize image to 128 colors palette for crisp terminal text & small GIF size
            quantized = img.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
            rendered_images.append(quantized)
            durations.append(dur)
            if (idx + 1) % 15 == 0 or idx == len(keyframes) - 1:
                print(f"  Frame {idx + 1:2d}/{len(keyframes)} captured...")

        browser.close()

    print("💾 Compiling optimized animated GIF...")
    rendered_images[0].save(
        str(gif_dest_docs),
        save_all=True,
        append_images=rendered_images[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )

    size_docs = gif_dest_docs.stat().st_size / (1024 * 1024)
    print(f"  ✓ {gif_dest_docs} ({size_docs:.2f} MB)")
    assert size_docs <= 5.0, f"GIF size {size_docs:.2f} MB exceeds 5 MB limit!"

    # Copy to assets/demo.gif as well
    shutil.copy2(gif_dest_docs, gif_dest_root)
    size_root = gif_dest_root.stat().st_size / (1024 * 1024)
    print(f"  ✓ {gif_dest_root} ({size_root:.2f} MB)")

    print("✅ Terminal screencast generated successfully.")
    return gif_dest_docs


if __name__ == "__main__":
    render_screencast()
