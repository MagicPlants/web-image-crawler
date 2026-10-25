#!/usr/bin/env python3
"""
fetch_images.py - Fetch images from a web page and archive them into a zip file.
Handles lazy-loading, srcset, <picture>, <noscript>, background images,
and optional headless browser rendering for JavaScript-heavy pages.

Author: Joe Montgomery (Magic Plants)
Website: https://magicplants.net
"""

__author__ = "Joe Montgomery (Magic Plants)"
__copyright__ = "Copyright 2026, Joe Montgomery (Magic Plants) - https://magicplants.net"

import argparse
import base64
import io
import os
import re
import sys
from urllib.parse import urljoin, urlparse, unquote
import zipfile

import requests
from bs4 import BeautifulSoup
from PIL import Image

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

COMMON_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico", ".tiff", ".avif"}

MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "image/bmp": ".bmp",
}

# Attributes commonly used for lazy-loaded image sources
LAZY_SRC_ATTRS = [
    "data-src",
    "data-original",
    "data-lazy-src",
    "data-orig-file",
    "data-large-file",
    "data-hi-res-src",
    "data-zoom-image",
    "data-full-url",
    "data-url",
    "data-fallback-src",
]

LAZY_SRCSET_ATTRS = [
    "data-srcset",
    "data-lazy-srcset",
    "srcset",
]


def sanitize_filename(name: str) -> str:
    """Removes invalid filename characters for cross-platform compatibility."""
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


def is_webp(data: bytes, filename: str, content_type: str = "") -> bool:
    """Detects whether image data or filename indicates a WebP image."""
    if filename.lower().endswith(".webp") or "image/webp" in content_type.lower():
        return True
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def convert_webp_to_png(webp_bytes: bytes, quality: str = "Lossless (100)") -> bytes:
    """
    Converts WebP image bytes to PNG format based on the selected quality level:
    - Lossless (100): Full 24/32-bit truecolor, max compression (compress_level=9)
    - High (90): Full truecolor, fast compression (compress_level=6)
    - Good (75): 256-color palette quantization with Floyd-Steinberg dithering
    - Low (50): 128-color palette quantization with Floyd-Steinberg dithering
    """
    img = Image.open(io.BytesIO(webp_bytes))
    buf = io.BytesIO()

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        converted = img.convert("RGBA")
    else:
        converted = img.convert("RGB")

    q_lower = quality.lower()
    if "100" in q_lower or "lossless" in q_lower:
        converted.save(buf, format="PNG", optimize=True, compress_level=9)
    elif "90" in q_lower or "high" in q_lower:
        converted.save(buf, format="PNG", optimize=True, compress_level=6)
    elif "75" in q_lower or "good" in q_lower:
        try:
            q_img = converted.quantize(colors=256, dither=Image.Dither.FLOYDSTEINBERG)
            q_img.save(buf, format="PNG", optimize=True)
        except Exception:
            converted.save(buf, format="PNG", optimize=True, compress_level=6)
    elif "50" in q_lower or "low" in q_lower:
        try:
            q_img = converted.quantize(colors=128, dither=Image.Dither.FLOYDSTEINBERG)
            q_img.save(buf, format="PNG", optimize=True)
        except Exception:
            converted.save(buf, format="PNG", optimize=True, compress_level=4)
    else:
        converted.save(buf, format="PNG", optimize=True)

    return buf.getvalue()


def is_placeholder(url_or_data: str) -> bool:
    """
    Detects if an image URL or data URI is an empty lazy-loading placeholder,
    spacer pixel, or blank SVG container.
    """
    if not url_or_data:
        return True

    clean = url_or_data.strip()
    lower = clean.lower()

    # Common dummy, spacer, or lazy-loading file patterns
    dummy_patterns = [
        "blank.svg", "placeholder.svg", "spacer.gif", "spacer.png",
        "pixel.gif", "pixel.png", "lazy.svg", "lazyload.svg",
        "trans.gif", "transparent.png", "empty.png", "1x1.png"
    ]
    if any(p in lower for p in dummy_patterns):
        return True

    # Check inline data URIs
    if lower.startswith("data:image/"):
        # 1x1 transparent GIFs / PNGs
        if "base64" in lower and len(clean) < 150:
            return True

        # SVG data URIs
        if "svg" in lower:
            try:
                if ";base64," in clean:
                    _, b64_data = clean.split(";base64,", 1)
                    decoded = base64.b64decode(b64_data).decode("utf-8", errors="ignore")
                else:
                    _, raw = clean.split(",", 1)
                    decoded = unquote(raw)

                # Check if it contains actual visual shapes/text/images
                visual_tags = ["<path", "<circle", "<ellipse", "<line", "<polygon", "<polyline", "<text", "<image"]
                has_visual = any(tag in decoded.lower() for tag in visual_tags)

                if "<rect" in decoded.lower() and not has_visual:
                    # Ignore rects that are purely transparent wrappers
                    if any(attr in decoded.lower() for attr in ['fill="none"', "fill='none'", 'opacity="0"', "opacity='0'"]):
                        return True

                if not has_visual:
                    return True
            except Exception:
                return True

    return False


def parse_best_from_srcset(srcset: str) -> str | None:
    """Parses a srcset attribute and returns the highest resolution candidate URL."""
    if not srcset:
        return None

    candidates = []
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        pieces = part.split()
        candidate_url = pieces[0].strip()
        if not candidate_url or is_placeholder(candidate_url):
            continue

        score = 1.0
        if len(pieces) > 1:
            desc = pieces[1].lower()
            if desc.endswith("w"):
                try:
                    score = float(desc[:-1])
                except ValueError:
                    score = 1.0
            elif desc.endswith("x"):
                try:
                    score = float(desc[:-1]) * 1000.0
                except ValueError:
                    score = 1.0
        candidates.append((score, candidate_url))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None


def extract_image_sources_static(html: str, page_url: str) -> list[str]:
    """
    Parses HTML to extract real image URLs, prioritizing lazy-load attributes,
    <picture> elements, <noscript> fallbacks, and CSS background images.
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates = []

    # 1. Inspect <picture> elements and <source> tags
    picture_handled_imgs = set()
    for pic in soup.find_all("picture"):
        chosen_pic_url = None
        for source in pic.find_all("source"):
            for attr in LAZY_SRCSET_ATTRS:
                val = source.get(attr)
                if val:
                    best = parse_best_from_srcset(val)
                    if best:
                        chosen_pic_url = best
                        break
            if chosen_pic_url:
                break

            for attr in LAZY_SRC_ATTRS + ["src"]:
                val = source.get(attr)
                if val and not is_placeholder(val):
                    chosen_pic_url = val
                    break
            if chosen_pic_url:
                break

        if chosen_pic_url:
            candidates.append(chosen_pic_url)
            for img in pic.find_all("img"):
                picture_handled_imgs.add(id(img))

    # 2. Inspect <img> tags
    for img in soup.find_all("img"):
        if id(img) in picture_handled_imgs:
            continue

        chosen_img_url = None

        # Priority A: Lazy load src attributes (data-src, data-original, etc.)
        for attr in LAZY_SRC_ATTRS:
            val = img.get(attr)
            if val and not is_placeholder(val):
                chosen_img_url = val.strip()
                break

        # Priority B: Lazy srcset or standard srcset (highest resolution)
        if not chosen_img_url:
            for attr in LAZY_SRCSET_ATTRS:
                val = img.get(attr)
                if val:
                    best = parse_best_from_srcset(val)
                    if best:
                        chosen_img_url = best
                        break

        # Priority C: Standard src attribute (only if not a placeholder)
        if not chosen_img_url:
            val = img.get("src")
            if val and not is_placeholder(val):
                chosen_img_url = val.strip()

        if chosen_img_url:
            candidates.append(chosen_img_url)

    # 3. Inspect <noscript> blocks (WordPress, Medium, etc. put real <img> here for non-JS clients)
    for noscript in soup.find_all("noscript"):
        try:
            ns_soup = BeautifulSoup(noscript.text, "html.parser")
            for img in ns_soup.find_all("img"):
                src = img.get("src")
                if src and not is_placeholder(src):
                    candidates.append(src.strip())
                for attr in LAZY_SRCSET_ATTRS:
                    best = parse_best_from_srcset(img.get(attr))
                    if best:
                        candidates.append(best)
        except Exception:
            pass

    # 4. Inspect CSS background-images in inline styles
    for el in soup.find_all(style=True):
        style = el.get("style", "")
        matches = re.findall(r'url\s*\(\s*[\'"]?([^\'")\s]+)[\'"]?\s*\)', style, flags=re.IGNORECASE)
        for m in matches:
            m = m.strip()
            if not m.startswith("data:") and not is_placeholder(m):
                _, ext = os.path.splitext(urlparse(m).path)
                if ext.lower() in COMMON_IMAGE_EXTS or "/image" in m or "/img" in m or "/upload" in m:
                    candidates.append(m)

    # 5. Resolve relative URLs and deduplicate
    resolved = []
    seen = set()

    for item in candidates:
        if not item or item.startswith("javascript:"):
            continue

        if item.startswith("data:image/"):
            resolved_url = item
        else:
            resolved_url = urljoin(page_url, item)

        if resolved_url not in seen and not is_placeholder(resolved_url):
            seen.add(resolved_url)
            resolved.append(resolved_url)

    return resolved


def extract_image_sources_browser(page_url: str) -> list[str]:
    """
    Renders the page in a headless Chromium browser and scrolls down to trigger
    JavaScript IntersectionObserver lazy loading, then extracts active image sources.
    """
    if not PLAYWRIGHT_AVAILABLE:
        print("[!] Playwright is not installed. Falling back to static parsing.")
        return []

    print("[*] Launching headless browser to execute JavaScript and trigger lazy loaders...")
    raw_urls = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=DEFAULT_USER_AGENT,
                viewport={"width": 1280, "height": 900}
            )
            page = context.new_page()
            page.goto(page_url, wait_until="load", timeout=30000)

            # Smoothly scroll down the page to trigger lazy loading
            page.evaluate("""async () => {
                await new Promise((resolve) => {
                    let totalHeight = 0;
                    const distance = 400;
                    const timer = setInterval(() => {
                        const scrollHeight = document.body.scrollHeight;
                        window.scrollBy(0, distance);
                        totalHeight += distance;
                        if (totalHeight >= scrollHeight || totalHeight > 15000) {
                            clearInterval(timer);
                            resolve();
                        }
                    }, 120);
                });
            }""")
            page.wait_for_timeout(1000)

            # Query DOM for rendered image sources
            raw_urls = page.evaluate("""() => {
                const results = new Set();

                // 1. Image elements (currentSrc is the active rendered asset)
                document.querySelectorAll('img').forEach(img => {
                    const src = img.currentSrc || img.src || img.getAttribute('data-src') || img.getAttribute('data-original');
                    if (src) results.add(src);
                });

                // 2. Picture and source elements, highest resolution first
                const bestFromSrcset = (srcset) => {
                    let best = null;
                    let bestScore = -1;
                    srcset.split(',').forEach(part => {
                        const pieces = part.trim().split(' ').filter(Boolean);
                        if (!pieces[0]) return;
                        let score = 1;
                        const desc = (pieces[1] || '').toLowerCase();
                        if (desc.endsWith('w')) score = parseFloat(desc) || 1;
                        else if (desc.endsWith('x')) score = (parseFloat(desc) || 1) * 1000;
                        if (score > bestScore) { bestScore = score; best = pieces[0]; }
                    });
                    return best;
                };
                document.querySelectorAll('source').forEach(source => {
                    const srcset = source.srcset || source.getAttribute('data-srcset');
                    if (srcset) {
                        const best = bestFromSrcset(srcset);
                        if (best) results.add(best);
                    }
                });

                // 3. Computed background images
                document.querySelectorAll('*').forEach(el => {
                    const bg = window.getComputedStyle(el).backgroundImage;
                    if (bg && bg.startsWith('url(')) {
                        const match = bg.match(/url\\(['"]?(.*?)['"]?\\)/);
                        if (match && match[1]) results.add(match[1]);
                    }
                });

                return Array.from(results);
            }""")

            browser.close()
    except Exception as e:
        print(f"[!] Browser extraction encountered an error: {e}")
        return []

    resolved = []
    seen = set()
    for u in raw_urls:
        if u and not u.startswith("javascript:") and not is_placeholder(u):
            full_url = urljoin(page_url, u)
            if full_url not in seen and not is_placeholder(full_url):
                seen.add(full_url)
                resolved.append(full_url)

    return resolved


def get_filename_from_url(url: str, default_ext: str, index: int) -> str:
    """Derives a clean filename from a URL or index."""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    base_name = os.path.basename(path)

    if base_name:
        root, ext = os.path.splitext(base_name)
        if ext.lower() in COMMON_IMAGE_EXTS:
            return sanitize_filename(f"{index:03d}_{base_name}")
        if root:
            return sanitize_filename(f"{index:03d}_{root}{default_ext}")

    return f"{index:03d}_image{default_ext}"


def download_and_archive(
    page_url: str,
    output_zip: str = "images.zip",
    use_browser: bool = False,
    min_bytes: int = 200,
    convert_webp: bool = True,
    keep_webp: bool = False,
    png_quality: str = "Lossless (100)",
) -> int:
    """
    Fetches real images from page_url and archives them into output_zip.
    Filters out blank SVG placeholders and transparent pixel files.
    """
    print(f"[*] Target page: {page_url}")
    session = requests.Session()
    session.headers.update({
        "User-Agent": DEFAULT_USER_AGENT,
        "Referer": page_url,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    })

    img_urls = []

    if use_browser:
        img_urls = extract_image_sources_browser(page_url)
    else:
        try:
            print("[*] Fetching page HTML...")
            response = session.get(page_url, timeout=15)
            response.raise_for_status()
            img_urls = extract_image_sources_static(response.text, page_url)
        except Exception as e:
            print(f"[!] Error fetching page HTML: {e}")

        # Fallback to browser if no real images were found statically
        if not img_urls and PLAYWRIGHT_AVAILABLE:
            print("[*] No non-placeholder images found via static HTML.")
            print("[*] Attempting browser rendering to process dynamic JavaScript content...")
            img_urls = extract_image_sources_browser(page_url)

    if not img_urls:
        print("[!] No images found on the page.")
        return 0

    print(f"[*] Found {len(img_urls)} candidate image(s). Starting download...")

    saved_count = 0
    used_filenames = set()

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for idx, img_url in enumerate(img_urls, start=1):
            try:
                content_type = ""
                # Handle inline base64 / URL-encoded data URI
                if img_url.startswith("data:image/"):
                    if ";base64," in img_url:
                        header, encoded_data = img_url.split(";base64,", 1)
                        img_data = base64.b64decode(encoded_data)
                    else:
                        header, raw_data = img_url.split(",", 1)
                        img_data = unquote(raw_data).encode("utf-8")

                    mime_match = re.search(r"data:(image/[^;]+)", header)
                    mime = mime_match.group(1).lower() if mime_match else "image/jpeg"
                    content_type = mime
                    ext = MIME_TO_EXT.get(mime, ".jpg")

                    if len(img_data) < min_bytes or is_placeholder(img_url):
                        print(f"[-] [{idx}/{len(img_urls)}] Skipped blank placeholder data URI")
                        continue

                    filename = f"{idx:03d}_inline_image{ext}"
                else:
                    img_resp = session.get(img_url, timeout=15)
                    if img_resp.status_code != 200:
                        print(f"[-] [{idx}/{len(img_urls)}] Failed (HTTP {img_resp.status_code}): {img_url}")
                        continue

                    img_data = img_resp.content

                    # Check for blank / empty responses
                    if len(img_data) < min_bytes:
                        # Inspect text if it's a tiny SVG or 1x1 image
                        text_sample = img_data[:500].decode("utf-8", errors="ignore")
                        if "<svg" in text_sample and is_placeholder(text_sample):
                            print(f"[-] [{idx}/{len(img_urls)}] Skipped blank SVG placeholder ({len(img_data)} bytes)")
                            continue

                    content_type = img_resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
                    default_ext = MIME_TO_EXT.get(content_type, ".jpg")

                    filename = get_filename_from_url(img_url, default_ext, idx)

                # Check and process WebP to PNG conversion
                is_img_webp = is_webp(img_data, filename, content_type)
                orig_webp_saved = False

                if convert_webp and is_img_webp:
                    try:
                        orig_webp_data = img_data
                        orig_name = filename
                        png_bytes = convert_webp_to_png(orig_webp_data, png_quality)

                        # Change extension to .png
                        base_root, _ = os.path.splitext(filename)
                        filename = f"{base_root}.png"
                        img_data = png_bytes

                        # If keep_webp is True, keep original in webp_originals/
                        if keep_webp:
                            webp_entry = f"webp_originals/{orig_name}"
                            if webp_entry in used_filenames:
                                w_root, w_ext = os.path.splitext(webp_entry)
                                webp_entry = f"{w_root}_{idx}{w_ext}"
                            used_filenames.add(webp_entry)
                            archive.writestr(webp_entry, orig_webp_data)
                            orig_webp_saved = True
                    except Exception as conv_err:
                        print(f"[!] Warning: WebP conversion failed ({conv_err}); saving original.")

                # Avoid duplicate names in zip
                if filename in used_filenames:
                    root, ext = os.path.splitext(filename)
                    filename = f"{root}_{idx}{ext}"
                used_filenames.add(filename)

                # Write directly into ZIP
                archive.writestr(filename, img_data)
                saved_count += 1
                size_kb = len(img_data) / 1024

                log_extra = f" [Converted WebP -> PNG, Quality: {png_quality}]" if (convert_webp and is_img_webp) else ""
                if orig_webp_saved:
                    log_extra += " (Original kept in webp_originals/)"
                print(f"[+] [{idx}/{len(img_urls)}] Saved {filename} ({size_kb:.1f} KB){log_extra}")

            except Exception as e:
                print(f"[-] [{idx}/{len(img_urls)}] Error downloading {img_url}: {e}")

    if saved_count > 0:
        zip_path = os.path.abspath(output_zip)
        print(f"\n[OK] Successfully archived {saved_count} image(s) into: {zip_path}")
    else:
        if os.path.exists(output_zip):
            os.remove(output_zip)
        print("\n[!] No valid images were downloaded.")

    return saved_count


def main():
    parser = argparse.ArgumentParser(
        description="Download real images from a webpage (filtering out blank placeholders) into a zip archive."
    )
    parser.add_argument("url", nargs="?", help="URL of the web page to scrape images from.")
    parser.add_argument(
        "-o", "--output", default="images.zip", help="Output zip filename (default: images.zip)"
    )
    parser.add_argument(
        "-b", "--browser", action="store_true", help="Use headless Chromium browser to render JavaScript & lazy loaders."
    )
    parser.add_argument(
        "--min-size", type=int, default=200, help="Minimum file size in bytes to keep (filters out 1x1 spacer pixels)."
    )
    parser.add_argument("--no-convert-webp", action="store_true", help="Do not convert WebP images to PNG")
    parser.add_argument("--keep-webp", action="store_true", help="Keep original WebP files in a webp_originals subfolder (default: trash them)")
    parser.add_argument(
        "--png-quality",
        default="Lossless (100)",
        choices=["Lossless (100)", "High (90)", "Good (75)", "Low (50)"],
        help="PNG conversion quality (default: Lossless (100))",
    )

    args = parser.parse_args()

    url = args.url
    if not url:
        try:
            url = input("Enter web page URL: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            sys.exit(0)

    if not url:
        print("Error: No URL provided.")
        sys.exit(1)

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    download_and_archive(
        url,
        args.output,
        use_browser=args.browser,
        min_bytes=args.min_size,
        convert_webp=not args.no_convert_webp,
        keep_webp=args.keep_webp,
        png_quality=args.png_quality,
    )


if __name__ == "__main__":
    main()
