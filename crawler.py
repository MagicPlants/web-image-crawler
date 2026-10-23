#!/usr/bin/env python3
"""
crawler.py - Recursive web image crawler and archiver.
Crawls web pages up to X levels deep and archives all discovered images into a ZIP.
"""

import argparse
import base64
from collections import deque
import io
import os
import re
import sys
import threading
import time
from urllib.parse import urljoin, urlparse, urldefrag, unquote
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

IGNORE_LINK_EXTS = {
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar", ".exe", ".dmg", ".msi",
    ".mp3", ".mp4", ".avi", ".mkv", ".mov", ".wav", ".flac",
    ".docx", ".xlsx", ".pptx", ".csv", ".json", ".xml", ".css", ".js"
}

LAZY_SRC_ATTRS = [
    "data-src", "data-original", "data-lazy-src", "data-orig-file",
    "data-large-file", "data-hi-res-src", "data-zoom-image",
    "data-full-url", "data-url", "data-fallback-src"
]

LAZY_SRCSET_ATTRS = ["data-srcset", "data-lazy-srcset", "srcset"]


def sanitize_filename(name: str) -> str:
    """Removes invalid filename characters for cross-platform compatibility."""
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


def slugify_url(url: str) -> str:
    """Generates a clean directory/page name from a URL."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        return "index"
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", path)
    return slug[:40].strip("_") or "page"


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

    # Preserve alpha transparency if present
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
    """Detects if an image URL or data URI is an empty lazy-loading placeholder or spacer."""
    if not url_or_data:
        return True

    clean = url_or_data.strip()
    lower = clean.lower()

    dummy_patterns = [
        "blank.svg", "placeholder.svg", "spacer.gif", "spacer.png",
        "pixel.gif", "pixel.png", "lazy.svg", "lazyload.svg",
        "trans.gif", "transparent.png", "empty.png", "1x1.png"
    ]
    if any(p in lower for p in dummy_patterns):
        return True

    if lower.startswith("data:image/"):
        if "base64" in lower and len(clean) < 150:
            return True

        if "svg" in lower:
            try:
                if ";base64," in clean:
                    _, b64_data = clean.split(";base64,", 1)
                    decoded = base64.b64decode(b64_data).decode("utf-8", errors="ignore")
                else:
                    _, raw = clean.split(",", 1)
                    decoded = unquote(raw)

                visual_tags = ["<path", "<circle", "<ellipse", "<line", "<polygon", "<polyline", "<text", "<image"]
                has_visual = any(tag in decoded.lower() for tag in visual_tags)
                if "<rect" in decoded.lower() and not has_visual:
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


def extract_links_from_html(html: str, current_url: str, base_domain: str, same_domain: bool = True) -> list[str]:
    """Extracts internal or external page links from HTML for recursive crawling."""
    soup = BeautifulSoup(html, "html.parser")
    found_links = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue

        full_url = urljoin(current_url, href)
        clean_url, _ = urldefrag(full_url)
        parsed = urlparse(clean_url)

        if parsed.scheme not in ("http", "https"):
            continue

        path_lower = parsed.path.lower()
        if any(path_lower.endswith(ext) for ext in IGNORE_LINK_EXTS):
            continue

        if same_domain and parsed.netloc.lower() != base_domain.lower():
            continue

        found_links.append(clean_url)

    return list(dict.fromkeys(found_links))


def extract_images_from_html(html: str, page_url: str) -> list[str]:
    """Extracts real images from static HTML, handling lazy loading, picture, and noscript."""
    soup = BeautifulSoup(html, "html.parser")
    candidates = []

    # 1. <picture> elements
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

    # 2. <img> tags
    for img in soup.find_all("img"):
        if id(img) in picture_handled_imgs:
            continue

        chosen_img_url = None
        for attr in LAZY_SRC_ATTRS:
            val = img.get(attr)
            if val and not is_placeholder(val):
                chosen_img_url = val.strip()
                break

        if not chosen_img_url:
            for attr in LAZY_SRCSET_ATTRS:
                val = img.get(attr)
                if val:
                    best = parse_best_from_srcset(val)
                    if best:
                        chosen_img_url = best
                        break

        if not chosen_img_url:
            val = img.get("src")
            if val and not is_placeholder(val):
                chosen_img_url = val.strip()

        if chosen_img_url:
            candidates.append(chosen_img_url)

    # 3. <noscript> fallbacks
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

    # 4. Background images
    for el in soup.find_all(style=True):
        style = el.get("style", "")
        matches = re.findall(r'url\s*\(\s*[\'"]?([^\'")\s]+)[\'"]?\s*\)', style, flags=re.IGNORECASE)
        for m in matches:
            m = m.strip()
            if not m.startswith("data:") and not is_placeholder(m):
                _, ext = os.path.splitext(urlparse(m).path)
                if ext.lower() in COMMON_IMAGE_EXTS or "/image" in m or "/img" in m or "/upload" in m:
                    candidates.append(m)

    # Deduplicate & resolve URLs
    resolved = []
    seen = set()
    for item in candidates:
        if not item or item.startswith("javascript:"):
            continue
        resolved_url = item if item.startswith("data:image/") else urljoin(page_url, item)
        if resolved_url not in seen and not is_placeholder(resolved_url):
            seen.add(resolved_url)
            resolved.append(resolved_url)

    return resolved


def extract_images_and_links_browser(page_url: str, base_domain: str, same_domain: bool = True) -> tuple[list[str], list[str]]:
    """Uses Playwright to render JavaScript, scroll, and extract hydrated images and links."""
    if not PLAYWRIGHT_AVAILABLE:
        return [], []

    raw_images = []
    raw_links = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=DEFAULT_USER_AGENT, viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.goto(page_url, wait_until="load", timeout=30000)

            # Scroll to trigger lazy loaders
            page.evaluate("""async () => {
                await new Promise((resolve) => {
                    let totalHeight = 0;
                    const distance = 400;
                    const timer = setInterval(() => {
                        const scrollHeight = document.body.scrollHeight;
                        window.scrollBy(0, distance);
                        totalHeight += distance;
                        if (totalHeight >= scrollHeight || totalHeight > 12000) {
                            clearInterval(timer);
                            resolve();
                        }
                    }, 120);
                });
            }""")
            page.wait_for_timeout(1000)

            result = page.evaluate("""() => {
                const images = new Set();
                const links = new Set();

                document.querySelectorAll('img').forEach(img => {
                    const src = img.currentSrc || img.src || img.getAttribute('data-src') || img.getAttribute('data-original');
                    if (src) images.add(src);
                });

                document.querySelectorAll('source').forEach(source => {
                    const srcset = source.srcset || source.getAttribute('data-srcset');
                    if (srcset) {
                        const first = srcset.split(',')[0].trim().split(' ')[0];
                        if (first) images.add(first);
                    }
                });

                document.querySelectorAll('a[href]').forEach(a => {
                    links.add(a.href);
                });

                return { images: Array.from(images), links: Array.from(links) };
            }""")

            browser.close()
            raw_images = result.get("images", [])
            raw_links = result.get("links", [])
    except Exception:
        pass

    # Clean images
    cleaned_images = []
    seen_img = set()
    for u in raw_images:
        if u and not u.startswith("javascript:") and not is_placeholder(u):
            full = urljoin(page_url, u)
            if full not in seen_img and not is_placeholder(full):
                seen_img.add(full)
                cleaned_images.append(full)

    # Clean links
    cleaned_links = []
    for lk in raw_links:
        clean_url, _ = urldefrag(urljoin(page_url, lk))
        parsed = urlparse(clean_url)
        if parsed.scheme in ("http", "https"):
            if not same_domain or parsed.netloc.lower() == base_domain.lower():
                path_lower = parsed.path.lower()
                if not any(path_lower.endswith(ext) for ext in IGNORE_LINK_EXTS):
                    cleaned_links.append(clean_url)

    return cleaned_images, list(dict.fromkeys(cleaned_links))


def crawl_and_archive(
    start_url: str,
    output_zip: str = "images.zip",
    max_depth: int = 0,
    max_pages: int = 25,
    same_domain: bool = True,
    use_browser: bool = False,
    min_bytes: int = 200,
    convert_webp: bool = True,
    keep_webp: bool = False,
    png_quality: str = "Lossless (100)",
    callback=None,
    cancel_event: threading.Event = None,
) -> dict:
    """
    Crawls web pages up to max_depth levels deep, extracts images, and writes
    them into a ZIP archive.

    Returns summary stats dict: {total_images, pages_crawled, output_zip, file_size}.
    """
    def log(msg: str):
        if callback:
            callback({"type": "log", "message": msg})
        else:
            print(msg)

    parsed_start = urlparse(start_url)
    base_domain = parsed_start.netloc

    session = requests.Session()
    session.headers.update({
        "User-Agent": DEFAULT_USER_AGENT,
        "Referer": start_url,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    })

    # Queue of (url, current_depth)
    queue = deque([(start_url, 0)])
    visited_pages = set()
    visited_images = set()

    total_saved = 0
    pages_crawled = 0
    used_filenames = set()

    log(f"[*] Starting crawl: {start_url}")
    log(f"[*] Depth limit: {max_depth} | Max pages: {max_pages} | Domain lock: {same_domain}")

    # Ensure parent directory exists
    zip_dir = os.path.dirname(os.path.abspath(output_zip))
    if zip_dir and not os.path.exists(zip_dir):
        os.makedirs(zip_dir, exist_ok=True)

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        while queue and pages_crawled < max_pages:
            if cancel_event and cancel_event.is_set():
                log("[!] Crawl cancelled by user.")
                break

            current_url, depth = queue.popleft()
            if current_url in visited_pages:
                continue

            visited_pages.add(current_url)
            pages_crawled += 1
            page_slug = slugify_url(current_url)

            log(f"\n--- [Page {pages_crawled}/{max_pages}] (Depth {depth}/{max_depth}): {current_url} ---")
            if callback:
                callback({
                    "type": "progress",
                    "page_num": pages_crawled,
                    "max_pages": max_pages,
                    "current_url": current_url,
                    "depth": depth,
                    "total_saved": total_saved
                })

            page_images = []
            page_links = []

            # 1. Fetch images & links
            if use_browser:
                page_images, page_links = extract_images_and_links_browser(current_url, base_domain, same_domain)
            else:
                try:
                    resp = session.get(current_url, timeout=15)
                    resp.raise_for_status()
                    html = resp.text
                    page_images = extract_images_from_html(html, current_url)
                    if depth < max_depth:
                        page_links = extract_links_from_html(html, current_url, base_domain, same_domain)
                except Exception as e:
                    log(f"[!] Error loading {current_url}: {e}")

                # Browser fallback if 0 images found statically
                if not page_images and PLAYWRIGHT_AVAILABLE:
                    log("[*] No images found via static HTML; trying headless browser...")
                    page_images, browser_links = extract_images_and_links_browser(current_url, base_domain, same_domain)
                    if not page_links and depth < max_depth:
                        page_links = browser_links

            log(f"[*] Discovered {len(page_images)} image(s) on page.")

            # 2. Download page images
            for idx, img_url in enumerate(page_images, start=1):
                if cancel_event and cancel_event.is_set():
                    break

                if img_url in visited_images:
                    continue
                visited_images.add(img_url)

                try:
                    # Data URI handling
                    if img_url.startswith("data:image/"):
                        if ";base64," in img_url:
                            header, encoded_data = img_url.split(";base64,", 1)
                            img_data = base64.b64decode(encoded_data)
                        else:
                            header, raw_data = img_url.split(",", 1)
                            img_data = unquote(raw_data).encode("utf-8")

                        mime_match = re.search(r"data:(image/[^;]+)", header)
                        mime = mime_match.group(1).lower() if mime_match else "image/jpeg"
                        ext = MIME_TO_EXT.get(mime, ".jpg")

                        if len(img_data) < min_bytes or is_placeholder(img_url):
                            continue

                        raw_name = f"inline_{idx}{ext}"
                    else:
                        img_resp = session.get(img_url, timeout=15)
                        if img_resp.status_code != 200:
                            continue

                        img_data = img_resp.content
                        if len(img_data) < min_bytes:
                            continue

                        content_type = img_resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
                        default_ext = MIME_TO_EXT.get(content_type, ".jpg")

                        parsed_img = urlparse(img_url)
                        base = os.path.basename(unquote(parsed_img.path))
                        if base and os.path.splitext(base)[1].lower() in COMMON_IMAGE_EXTS:
                            raw_name = sanitize_filename(base)
                        else:
                            raw_name = f"image_{total_saved + 1:03d}{default_ext}"

                    # Check and process WebP to PNG conversion
                    is_img_webp = is_webp(img_data, raw_name, content_type)
                    orig_webp_saved = False

                    if convert_webp and is_img_webp:
                        try:
                            orig_webp_data = img_data
                            orig_name = raw_name
                            png_bytes = convert_webp_to_png(orig_webp_data, png_quality)

                            # Change filename extension to .png
                            base_root, _ = os.path.splitext(raw_name)
                            raw_name = f"{base_root}.png"
                            img_data = png_bytes

                            # If keep_webp is True, store the original in a webp_originals subfolder
                            if keep_webp:
                                if max_depth > 0:
                                    webp_entry = f"p{pages_crawled:02d}_{page_slug}/webp_originals/{orig_name}"
                                else:
                                    webp_entry = f"webp_originals/{total_saved + 1:03d}_{orig_name}"

                                if webp_entry in used_filenames:
                                    w_root, w_ext = os.path.splitext(webp_entry)
                                    webp_entry = f"{w_root}_{total_saved + 1}{w_ext}"
                                used_filenames.add(webp_entry)
                                archive.writestr(webp_entry, orig_webp_data)
                                orig_webp_saved = True
                        except Exception as conv_err:
                            log(f"[!] Warning: WebP conversion failed ({conv_err}); saving original.")

                    # Organize into subfolders by page if multi-page crawl
                    if max_depth > 0:
                        folder_prefix = f"p{pages_crawled:02d}_{page_slug}"
                        entry_path = f"{folder_prefix}/{raw_name}"
                    else:
                        entry_path = f"{total_saved + 1:03d}_{raw_name}"

                    if entry_path in used_filenames:
                        root, ext = os.path.splitext(entry_path)
                        entry_path = f"{root}_{total_saved + 1}{ext}"
                    used_filenames.add(entry_path)

                    archive.writestr(entry_path, img_data)
                    total_saved += 1
                    kb = len(img_data) / 1024

                    log_extra = f" [Converted WebP -> PNG, Quality: {png_quality}]" if (convert_webp and is_img_webp) else ""
                    if orig_webp_saved:
                        log_extra += " (Original kept in webp_originals/)"
                    log(f"[+] Saved {entry_path} ({kb:.1f} KB){log_extra}")

                    if callback:
                        callback({"type": "image_saved", "total_saved": total_saved, "entry_path": entry_path})

                except Exception as e:
                    pass

            # 3. Add discovered links to BFS queue if within depth limit
            if depth < max_depth:
                new_links_count = 0
                for link in page_links:
                    if link not in visited_pages and link not in [q[0] for q in queue]:
                        queue.append((link, depth + 1))
                        new_links_count += 1
                if new_links_count > 0:
                    log(f"[*] Queued {new_links_count} child link(s) for depth {depth + 1}")

    file_size = os.path.getsize(output_zip) if os.path.exists(output_zip) else 0

    if total_saved > 0:
        log(f"\n[OK] Crawl complete! Saved {total_saved} image(s) across {pages_crawled} page(s).")
        log(f"[OK] Archive saved: {os.path.abspath(output_zip)} ({file_size / (1024 * 1024):.2f} MB)")
    else:
        if os.path.exists(output_zip):
            os.remove(output_zip)
        log("\n[!] No images saved.")

    summary = {
        "start_url": start_url,
        "total_images": total_saved,
        "pages_crawled": pages_crawled,
        "output_zip": os.path.abspath(output_zip) if total_saved > 0 else "",
        "file_size": file_size,
        "max_depth": max_depth,
    }

    if callback:
        callback({"type": "finished", "summary": summary})

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Recursively crawl web pages up to X levels deep and archive images into a ZIP."
    )
    parser.add_argument("url", nargs="?", help="Starting web page URL")
    parser.add_argument("-o", "--output", default="images.zip", help="Output ZIP file name (default: images.zip)")
    parser.add_argument("-d", "--depth", type=int, default=0, help="Crawl depth levels (0 = single page, 1 = 1 level deep, etc.)")
    parser.add_argument("-m", "--max-pages", type=int, default=25, help="Maximum number of pages to crawl (default: 25)")
    parser.add_argument("-b", "--browser", action="store_true", help="Use headless browser for dynamic JS rendering")
    parser.add_argument("--any-domain", action="store_true", help="Allow crawling outside the starting domain")
    parser.add_argument("--min-size", type=int, default=200, help="Minimum file size in bytes to keep")
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
            url = input("Enter starting web page URL: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            sys.exit(0)

    if not url:
        print("Error: No URL provided.")
        sys.exit(1)

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    crawl_and_archive(
        start_url=url,
        output_zip=args.output,
        max_depth=args.depth,
        max_pages=args.max_pages,
        same_domain=not args.any_domain,
        use_browser=args.browser,
        min_bytes=args.min_size,
        convert_webp=not args.no_convert_webp,
        keep_webp=args.keep_webp,
        png_quality=args.png_quality,
    )


if __name__ == "__main__":
    main()
