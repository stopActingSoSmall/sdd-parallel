"""Deterministic Playwright + Pillow terminal screencast renderer for sdd-parallel.

Renders a pixel-perfect terminal screencast with Catppuccin Mocha styling,
realistic typing and command output across 3 key scenes:
  Scene 1: sdd-parallel demo --kit all (5/5 SDD kits verified)
  Scene 2: sdd-parallel bench --demo (100% recall, 0 FN)
  Scene 3: sdd-parallel demo --kit spec-kit --scaffold-only -> plan -> prepare --wave 1

Output:
  docs/assets/demo.gif (<= 5 MB, ~40-45s duration)
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

    # Define keyframe script: (html_content, duration_ms)
    keyframes: list[tuple[str, int]] = []

    prompt_main = '<span class="prompt">user@host</span>:<span class="prompt-path">~/sdd-parallel</span>$ '
    prompt_demo = '<span class="prompt">user@host</span>:<span class="prompt-path">/tmp/sdd-demo</span>$ '

    # =========================================================================
    # SCENE 1: sdd-parallel demo --kit all (~12-14 sec)
    # =========================================================================
    cmd1 = "sdd-parallel demo --kit all"
    # Typing cmd1
    for i in range(1, len(cmd1) + 1, 2):
        text = prompt_main + f'<span class="cmd">{cmd1[:i]}</span><span class="cursor"></span>'
        keyframes.append((text, 70))
    # Enter pressed
    text = prompt_main + f'<span class="cmd">{cmd1}</span>'
    keyframes.append((text, 350))

    # Output start
    s1_step1 = (
        text + "\n"
        '<span class="cyan">============================================================================</span>\n'
        '<span class="bold">SDD-PARALLEL STANDALONE 5-KIT VERIFICATION (ZERO API KEYS / OFFLINE)</span>\n'
        '<span class="cyan">============================================================================</span>\n'
        '[Step 1/4] Scanning AST & Call Graph across 5 SDD Frameworks...\n'
        '  <span class="green">✓</span> <span class="dim">src/demo_app/schema.py</span>     -> <span class="yellow">spine</span> (locked)\n'
        '  <span class="green">✓</span> <span class="dim">src/demo_app/cli.py</span>        -> <span class="blue">additive_registry</span> (safe shared)\n'
        '  <span class="green">✓</span> <span class="dim">src/demo_app/normalizer.py</span> -> standard (imported by api.py)\n'
    )
    keyframes.append((s1_step1, 1200))

    s1_step2 = (
        s1_step1 +
        '\n[Step 2/4] Type-2 Semantic Collision Detection (BSP Wave Schedule)\n'
        '  <span class="red">✖ Naive Scheduler</span> : W(1-2)={normalizer.py} vs W(2-1)={api.py} -> <span class="red">DISJOINT (Merge-Bomb!)</span>\n'
        '  <span class="green">✓ BSPWaveScheduler</span>: Detected W(1-2) ∩ I(2-1) ≠ ∅ -> Split into <span class="magenta">Wave 1 -> Wave 2</span>\n'
        '  <span class="blue">✓ Additive Registry</span>: Concurrent registration in cli.py safe in Wave 1\n'
    )
    keyframes.append((s1_step2, 1600))

    s1_step3 = (
        s1_step2 +
        '\n[Step 3/4] Worktree Provisioning & PreToolUse Scope Guard\n'
        '  <span class="green">✓ Provisioned slots</span>: .worktrees/track-a, .worktrees/track-b (<span class="green">Clean: True</span>)\n'
        '  <span class="yellow">🔒 PreToolUse Guard</span>: track-a edit \'src/demo_app/schema.py\' -> <span class="red bold">DENY</span> (Spine-Lock)\n'
        '\n[Step 4/4] Barrier Quality Gate & Monotonic 3-Way Status Merger\n'
        '  <span class="green">✓ Merged branches</span>: parallel/track-a + parallel/track-b (0 conflict markers)\n'
    )
    keyframes.append((s1_step3, 1800))

    s1_summary = (
        s1_step3 +
        '\n<span class="green">============================================================================</span>\n'
        '<span class="bold green">5/5 SDD KITS VERIFIED (BMAD, SPEC KIT, GSD, OPENSPEC, BEADS)</span>\n'
        '<span class="green">============================================================================</span>\n'
        '<span class="bold">Kit          | Merge-Bomb   | Worktrees    | Scope Guard  | 3-Way Merge </span>\n'
        '<span class="dim">----------------------------------------------------------------------------</span>\n'
        'bmad         | <span class="green">PASSED</span>       | <span class="green">PASSED</span>       | <span class="green">PASSED</span>       | <span class="green">PASSED</span>      \n'
        'spec-kit     | <span class="green">PASSED</span>       | <span class="green">PASSED</span>       | <span class="green">PASSED</span>       | <span class="green">PASSED</span>      \n'
        'gsd          | <span class="green">PASSED</span>       | <span class="green">PASSED</span>       | <span class="green">PASSED</span>       | <span class="green">PASSED</span>      \n'
        'openspec     | <span class="green">PASSED</span>       | <span class="green">PASSED</span>       | <span class="green">PASSED</span>       | <span class="green">PASSED</span>      \n'
        'beads        | <span class="green">PASSED</span>       | <span class="green">PASSED</span>       | <span class="green">PASSED</span>       | <span class="green">PASSED</span>      \n'
        '<span class="green">============================================================================</span>'
    )
    # Hold the grand summary for 7.5 seconds so user can absorb it!
    keyframes.append((s1_summary, 7500))

    # =========================================================================
    # SCENE 2: sdd-parallel bench --demo (~10-11 sec)
    # =========================================================================
    cmd2 = "sdd-parallel bench --demo"
    # Transition to new clear screen with cmd2 typing
    for i in range(1, len(cmd2) + 1, 2):
        text = prompt_main + f'<span class="cmd">{cmd2[:i]}</span><span class="cursor"></span>'
        keyframes.append((text, 70))
    text = prompt_main + f'<span class="cmd">{cmd2}</span>'
    keyframes.append((text, 350))

    s2_header = (
        text + "\n"
        '<span class="cyan">================================================================================</span>\n'
        '<span class="bold">SDD-PARALLEL HISTORICAL BENCHMARK & RETROSPECTIVE EVALUATION</span>\n'
        '<span class="cyan">================================================================================</span>\n'
        'Target Repository: <span class="dim">/tmp/sdd-bench-demo</span>\n'
        'SDD Kit:           <span class="magenta bold">BMAD</span> (5 tasks, 10 pairs) | Scheduled Waves: <span class="blue bold">3 waves</span>\n'
        'Execution Time:    <span class="green bold">0.369s</span>\n'
    )
    keyframes.append((s2_header, 1000))

    s2_full = (
        s2_header +
        '\n<span class="bold">CONFUSION MATRIX & ACCURACY METRICS</span>\n'
        '<span class="dim">--------------------------------------------------------------------------------</span>\n'
        '  True Positives  (TP) : <span class="green bold">3</span>    (Predicted semantic conflict)\n'
        '  False Positives (FP) : <span class="blue bold">0</span>    (Conservative separation)\n'
        '  <span class="green bold">False Negatives (FN) : 0    (MISSED MERGE-BOMB / SILENT BREAKAGE)</span>\n'
        '  True Negatives  (TN) : <span class="dim">7</span>    (Clean parallel)\n\n'
        '  Precision : <span class="green bold">100.0%</span>\n'
        '  Recall    : <span class="green bold">100.0% (Zero missed Merge-Bombs)</span>\n'
        '  F1-Score  : <span class="green bold">1.0000</span>\n'
        '\n<span class="bold">PREVENTED MERGE DISASTERS (BLAST-RADIUS SHIELD)</span>\n'
        '<span class="dim">--------------------------------------------------------------------------------</span>\n'
        '  1. 1-1 <-> 1-2: Explicit dependency (1-1 blocks 1-2)\n'
        '  2. 1-1 <-> 2-2: Spine-Lock shared contract: <span class="yellow">src/demo_app/schema.py</span>\n'
        '  3. 1-2 <-> 2-1: <span class="red bold">Type-2 Semantic Collision (W ∩ I ≠ ∅):</span> <span class="yellow">src/demo_app/api.py</span>\n'
        '<span class="cyan">================================================================================</span>'
    )
    # Hold bench matrix for 8 seconds!
    keyframes.append((s2_full, 8000))

    # =========================================================================
    # SCENE 3: Scaffold demo -> plan -> prepare --wave 1 (~18 sec)
    # =========================================================================
    cmd3_1 = "sdd-parallel demo --kit spec-kit --scaffold-only --output-dir /tmp/sdd-demo && cd /tmp/sdd-demo"
    for i in range(1, len(cmd3_1) + 1, 4):
        text = prompt_main + f'<span class="cmd">{cmd3_1[:i]}</span><span class="cursor"></span>'
        keyframes.append((text, 50))
    text = prompt_main + f'<span class="cmd">{cmd3_1}</span>'
    keyframes.append((text, 300))

    s3_scaffold = (
        text + "\n"
        '<span class="cyan">============================================================================</span>\n'
        '<span class="bold green">SDD-PARALLEL DEMO SANDBOX CREATED (SPEC-KIT)</span>\n'
        '<span class="cyan">============================================================================</span>\n'
        'Location: <span class="dim">/tmp/sdd-demo</span> (synthetic repo with hidden merge-bomb)\n'
        'Next steps:\n'
        '  1. cd /tmp/sdd-demo\n'
        '  2. sdd-parallel plan\n'
        '  3. sdd-parallel prepare --wave 1\n'
        '<span class="cyan">============================================================================</span>\n'
    )
    keyframes.append((s3_scaffold, 1500))

    # Type sdd-parallel plan
    cmd3_2 = "sdd-parallel plan"
    prompt3 = s3_scaffold + "\n" + prompt_demo
    for i in range(1, len(cmd3_2) + 1, 2):
        text = prompt3 + f'<span class="cmd">{cmd3_2[:i]}</span><span class="cursor"></span>'
        keyframes.append((text, 60))
    text = prompt3 + f'<span class="cmd">{cmd3_2}</span>'
    keyframes.append((text, 300))

    s3_plan = (
        text + "\n"
        '<span class="bold"># Parallel Execution Plan (`spec-kit`)</span>\n'
        '- <span class="bold">Total Scheduled Tasks:</span> 5 | <span class="bold">Total Waves:</span> 3\n'
        '- <span class="bold">Detected Spine Locks:</span> <span class="yellow">src/demo_app/schema.py</span>\n'
        '- <span class="bold">Additive Registries:</span> <span class="blue">src/demo_app/cli.py</span>\n\n'
        '<span class="bold">Wave Schedule:</span>\n'
        '  * <span class="magenta bold">Wave 1</span>: [<span class="green">track-a: T002</span> (normalizer.py, cli.py)] [<span class="green">track-b: T003</span> (exporter.py, cli.py)]\n'
        '  * <span class="magenta bold">Wave 2</span>: [<span class="yellow">track-a: T001</span> (schema.py)]             [<span class="yellow">track-b: T004</span> (api.py)]\n'
        '  * <span class="magenta bold">Wave 3</span>: [<span class="blue">track-a: T005</span> (schema.py)]\n'
    )
    keyframes.append((s3_plan, 3500))

    # Type sdd-parallel prepare --wave 1
    cmd3_3 = "sdd-parallel prepare --wave 1"
    prompt4 = s3_plan + "\n" + prompt_demo
    for i in range(1, len(cmd3_3) + 1, 2):
        text = prompt4 + f'<span class="cmd">{cmd3_3[:i]}</span><span class="cursor"></span>'
        keyframes.append((text, 60))
    text = prompt4 + f'<span class="cmd">{cmd3_3}</span>'
    keyframes.append((text, 300))

    s3_prepare = (
        text + "\n"
        '{\n'
        '  <span class="blue">"wave"</span>: <span class="green">1</span>,\n'
        '  <span class="blue">"dry_run"</span>: <span class="yellow">false</span>,\n'
        '  <span class="blue">"layout"</span>: <span class="magenta">"local"</span>,\n'
        '  <span class="blue">"worktrees"</span>: [\n'
        '    <span class="green">"/tmp/sdd-demo/.worktrees/track-a"</span>,\n'
        '    <span class="green">"/tmp/sdd-demo/.worktrees/track-b"</span>\n'
        '  ],\n'
        '  <span class="blue">"clean_status"</span>: <span class="green bold">true</span>,\n'
        '  <span class="blue">"scope_guard"</span>: <span class="green bold">"ENABLED"</span>\n'
        '}\n'
        '\n<span class="green bold">✨ Wave 1 isolated slots ready. Agents can now work concurrently with ZERO merge-bombs!</span>'
    )
    # Hold final state for 8 seconds!
    keyframes.append((s3_prepare, 8000))

    total_duration_ms = sum(dur for _, dur in keyframes)
    print(f"🎬 Total keyframes: {len(keyframes)}, Total duration: {total_duration_ms / 1000:.1f}s")

    # Render frames via Playwright
    rendered_images: list[Image.Image] = []
    durations: list[int] = []

    print("🎨 Rendering terminal frames via Playwright Chromium...")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1000, "height": 600},
            device_scale_factor=1,
        )

        page.set_content(HTML_TEMPLATE, wait_until="domcontentloaded")

        for idx, (content, dur) in enumerate(keyframes):
            page.evaluate(
                "(html) => { document.getElementById('term').innerHTML = html; }",
                content,
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
