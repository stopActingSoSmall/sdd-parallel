"""Deterministic Code-to-Image renderer for SDD-Parallel README assets.

Renders HTML/CSS bento banner templates via Playwright Headless Chromium
at 2x Retina resolution (2400px width) with transparent border-radius corners.
"""

from __future__ import annotations

import sys
from pathlib import Path


def render_all_assets() -> None:
    """Render all 4 marketing and documentation banners to docs/assets/."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "ERROR: playwright is required to render documentation assets.\n"
            "Run with: uv run --with playwright python -X utf8 scripts/render_readme_assets.py",
            file=sys.stderr,
        )
        sys.exit(1)

    repo_root = Path(__file__).resolve().parent.parent
    templates_dir = repo_root / "docs" / "marketing" / "banner_templates"
    output_dir = repo_root / "docs" / "assets"
    output_dir.mkdir(parents=True, exist_ok=True)

    targets = [
        ("01_hero_banner.html", "hero-banner.png"),
        ("02_merge_bomb_anatomy.html", "merge-bomb-anatomy.png"),
        ("03_whats_inside_worktrees.html", "whats-inside-worktrees.png"),
        ("04_how_it_works_pipeline.html", "how-it-works-pipeline.png"),
    ]

    print("🎨 Rendering 4 Bento Section Screens at 2x Retina (2400px)...")

    with sync_playwright() as p:
        browser = p.chromium.launch()

        for template_name, out_filename in targets:
            html_path = templates_dir / template_name
            out_path = output_dir / out_filename

            if not html_path.exists():
                print(f"❌ Template not found: {html_path}", file=sys.stderr)
                sys.exit(1)

            page = browser.new_page(
                viewport={"width": 1200, "height": 800},
                device_scale_factor=2,
            )

            file_url = html_path.as_uri()
            page.goto(file_url, wait_until="networkidle")

            # Screenshot the banner-canvas element to preserve 16px border-radius with transparency
            canvas = page.locator(".banner-canvas")
            canvas.screenshot(
                path=str(out_path),
                omit_background=True,
            )
            page.close()

            size_kb = out_path.stat().st_size / 1024
            print(f"  ✓ {out_filename:<28} -> {size_kb:6.1f} KB ({out_path})")

        browser.close()

    print("✅ All 4 banners rendered successfully.")


if __name__ == "__main__":
    render_all_assets()
