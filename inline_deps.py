#!/usr/bin/env python3
"""Inline all external CSS/JS in an HTML file for offline/file:// compatibility."""

import re
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
except ImportError:
    print("Installing requests...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

TAILWIND_CDN = "https://cdn.tailwindcss.com"
TAILWIND_PREFLIGHT = "https://cdn.tailwindcss.com/docs/preflight.css"

EXTERNAL_RESOURCES = [
    # KaTeX CSS
    ("css", "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.css"),
    # KaTeX JS (core)
    ("js", "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.js"),
    # KaTeX auto-render
    ("js", "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/contrib/auto-render.min.js"),
    # Mermaid
    ("js", "https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.6.1/mermaid.min.js"),
]


def fetch_url(url, timeout=15):
    """Fetch a URL, return (url, content) or (url, None) on failure."""
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return (url, resp.text)
    except Exception as e:
        print(f"  [WARN] Failed to fetch {url}: {e}", file=sys.stderr)
        return (url, None)


def is_tailwind_inline(tag):
    """Check if this is the Tailwind CDN script tag we want to keep."""
    return 'src' in tag and TAILWIND_CDN in tag.get('src', '')


def process_file(html_path, output_path=None):
    if output_path is None:
        output_path = html_path

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # ── 1. Fetch all external resources in parallel ──────────────────────────
    print("Fetching external resources in parallel...")
    resources = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_url, url): (kind, url) for kind, url in EXTERNAL_RESOURCES}
        for future in as_completed(futures):
            kind, url = futures[future]
            url_fetched, content = future.result()
            if content:
                resources[url] = (kind, content)
                print(f"  [OK] {url[:70]}")

    # Also fetch Tailwind preflight (standalone CSS)
    print("Fetching Tailwind preflight CSS...")
    _, tw_preflight = fetch_url(TAILWIND_PREFLIGHT)
    if tw_preflight:
        resources[TAILWIND_PREFLIGHT] = ("css", tw_preflight)

    # ── 2. Build replacement map ─────────────────────────────────────────────
    css_map = {}  # url -> inlined CSS content
    js_map = {}   # url -> inlined JS content

    for url, (kind, content) in resources.items():
        if kind == "css":
            css_map[url] = content
        else:
            js_map[url] = content

    # ── 3. Process <link> tags (CSS) ─────────────────────────────────────────
    def inline_css(match):
        tag = match.group(0)
        href = match.group(1)
        for css_url, css_content in css_map.items():
            if css_url.endswith(href) or href in css_url:
                # Wrap in <style> tag
                return f"<style>\n{css_content}\n</style>"
        return tag  # keep original if not found

    html = re.sub(r'<link([^>]+)>', inline_css, html)

    # Remove duplicate/tailwind-specific link tags that reference our inlined CSS
    # ── 4. Process <script> tags (JS) ────────────────────────────────────────
    def inline_js(match):
        tag_content = match.group(0)
        src = re.search(r'src=["\']([^"\']+)["\']', tag_content)

        # Skip Tailwind CDN — it must stay external (runtime JIT)
        if src and TAILWIND_CDN in src.group(1):
            return f"<!-- Tailwind CDN kept for JIT -->\n{tag_content}"

        if src:
            src_url = src.group(1)
            for js_url, js_content in js_map.items():
                if js_url.endswith(src_url) or src_url in js_url:
                    return f"<script>\n{js_content}\n</script>"
        return tag_content

    html = re.sub(r'<script[^>]*>.*?</script>', inline_js, html, flags=re.DOTALL)

    # ── 5. Inject Tailwind preflight CSS if fetched ───────────────────────────
    if tw_preflight:
        inject_style = f"<style>\n/* Tailwind Preflight */\n{tw_preflight}\n</style>"
        html = html.replace("</head>", f"{inject_style}\n</head>")

    # ── 6. Write output ───────────────────────────────────────────────────────
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    size_before = os.path.getsize(html_path)
    size_after = os.path.getsize(output_path)
    print(f"\nDone! Inlined {len(resources)} resources.")
    print(f"  Before: {size_before:,} bytes")
    print(f"  After:  {size_after:,} bytes")
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    input_file = sys.argv[1] if len(sys.argv) > 1 else "obsidian_web.html"
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found")
        sys.exit(1)

    process_file(input_file, output_file)