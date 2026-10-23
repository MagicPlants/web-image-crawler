# 🕷️ Web Image Crawler & Archive Manager by magicplants.net

A desktop application and CLI tool that recursively crawls websites up to **X levels deep**, extracts high-resolution images while filtering out blank lazy-loading placeholders, and archives them directly into organized ZIP packages with a built-in archive manager and image previewer.

---

## ✨ Features

- **Recursive Multi-Level Crawler ("Worming")**:
  - Crawl up to $X$ levels deep (single page, direct links, or recursive exploration).
  - **Domain Lock**: Stays strictly within the starting domain to prevent wandering into the wider internet.
  - **Runaway Safeguards**: Set max page limits and visited-page tracking to avoid infinite crawler loops.
  - **Structured ZIPs**: Groups crawled images into tidy subfolders named after each page slug.

- **Intelligent Image Extraction**:
  - **Bypasses Lazy Loading**: Prioritizes `data-src`, `data-original`, `data-lazy-src`, and `srcset` (automatically selecting the highest resolution available).
  - **Dummy & Placeholder Filtering**: Identifies and drops empty SVG containers, 1x1 spacer GIFs, and blank placeholders.
  - **`<picture>` & `<noscript>` Support**: Captures modern responsive `<source>` formats (WebP, AVIF) and non-JavaScript fallbacks.
  - **Anti-Hotlink Referer Spoofing**: Supplies authentic `Referer` and browser headers so CDNs do not block downloads.

- **Headless Browser Rendering (Playwright)**:
  - Toggle headless Chromium execution to render dynamic JavaScript single-page apps (SPAs) and trigger scroll-based `IntersectionObserver` loaders.

- **Built-in Archive Manager GUI**:
  - **Catalog & History**: Automatically tracks all your scraping sessions in a searchable table.
  - **Direct In-Memory Preview**: Click any image in a ZIP to view a scaled thumbnail and file metadata without unpacking the archive to disk.
  - **Windows Explorer Integration**: Open and highlight archives directly in Windows Explorer.
  - **One-Click Batch Extraction**: Extract archives to any chosen destination folder.

---

## 📁 Project Structure

```text
├── gui.py             # Desktop GUI (Crawler + Archive Manager)
├── crawler.py         # Recursive multi-level crawling engine & CLI
├── fetch_images.py    # Standalone single-page scraper CLI
├── archive_store.py   # Registry manager, ZIP inspection, & preview loader
├── requirements.txt   # Python package dependencies
├── .gitignore         # Git ignore rules for virtualenvs and archives
├── CONTRIBUTING.md    # Contribution guidelines
└── README.md          # Project documentation
```

---

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- Windows, macOS, or Linux

### Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/MagicPlants/web-image-crawler.git
   cd web-image-crawler
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv

   # On Windows (PowerShell):
   .venv\Scripts\Activate.ps1

   # On macOS/Linux:
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. *(Optional, for dynamic JavaScript sites)* Install the Playwright browser:
   ```bash
   playwright install chromium
   ```

---

## 💻 Usage

### 1. Launch the Desktop GUI
```bash
python gui.py
```
- **Tab 1 (Web Crawler & Downloader)**: Enter your target URL, choose crawl depth and page limits, toggle browser rendering if needed, and hit **Start Crawl**.
- **Tab 2 (Archive Manager & Viewer)**: Inspect saved archives, browse files inside the ZIP, preview images, or open them in your default photo viewer.

### 2. Run from the Command Line

#### Single Page Archive:
```bash
python fetch_images.py https://example.com -o my_images.zip
```

#### Multi-Level Deep Crawl:
```bash
# Crawl 2 levels deep, capping at 30 pages:
python crawler.py https://example.com -d 2 -m 30 -o archives/example_deep.zip

# Use headless Chromium for dynamic JavaScript / infinite-scroll sites:
python crawler.py https://example.com -d 1 -b -o archives/example_js.zip
```

#### CLI Options:
| Flag | Description | Default |
| --- | --- | --- |
| `-d, --depth` | Number of levels deep to follow links | `0` (current page only) |
| `-m, --max-pages` | Maximum pages to crawl | `25` |
| `-o, --output` | Destination ZIP path | `images.zip` |
| `-b, --browser` | Use headless browser for JS rendering | Disabled |
| `--any-domain` | Allow crawling external domains | Disabled (stays on domain) |
| `--min-size` | Minimum image size in bytes | `200` |

---

## 🤝 Contributing

Contributions are what make the open-source community such an amazing place to learn, inspire, and create. Any contributions you make are **greatly appreciated**!

If you have an idea for a feature or found a bug:
1. **Fork the Project**
2. **Create your Feature Branch** (`git checkout -b feature/AmazingFeature`)
3. **Commit your Changes** (`git commit -m 'Add some AmazingFeature'`)
4. **Push to the Branch** (`git push origin feature/AmazingFeature`)
5. **Open a Pull Request**

Please check out [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines on code style, issue reporting, and pull request procedures.

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.
