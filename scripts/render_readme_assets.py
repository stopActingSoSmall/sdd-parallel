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
        ("05_social_preview.html", "social-preview.png"),
    ]

    print("🎨 Rendering Bento Section Screens & Social Preview at 2x Retina...")

    with sync_playwright() as p:
        browser = None
        for launch_kwargs in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
            try:
                browser = p.chromium.launch(**launch_kwargs)
                break
            except Exception:
                continue

        if browser is None:
            print("ERROR: Failed to launch Chromium/Chrome/Edge via Playwright.", file=sys.stderr)
            sys.exit(1)

        for template_name, out_filename in targets:
            html_path = templates_dir / template_name
            out_path = output_dir / out_filename

            if not html_path.exists():
                print(f"❌ Template not found: {html_path}", file=sys.stderr)
                sys.exit(1)

            page = browser.new_page(
                viewport={"width": 1360, "height": 900},
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

            size_kb = out_path.stat().st_size / 1024
            print(f"  ✓ {out_filename:<28} -> {size_kb:6.1f} KB ({out_path})")

            # For social preview, also export optimized JPEG for GitHub & Telegram cards
            if out_filename == "social-preview.png":
                jpg_path = output_dir / "social-preview.jpg"
                canvas.screenshot(
                    path=str(jpg_path),
                    type="jpeg",
                    quality=92,
                )
                jpg_size_kb = jpg_path.stat().st_size / 1024
                print(f"  ✓ {'social-preview.jpg':<28} -> {jpg_size_kb:6.1f} KB ({jpg_path})")

            page.close()

        browser.close()

    print("✅ All banners and social preview rendered successfully.")


if __name__ == "__main__":
    render_all_assets()
