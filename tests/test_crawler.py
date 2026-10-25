"""Small regression tests for the Web Image Crawler.

Run from the repo root with: python -m unittest discover -s tests -v
No Playwright browser is needed for these tests.
"""

import base64
import io
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import archive_store
import crawler
import fetch_images
from PIL import Image


def make_png_bytes(size=(64, 64), color=(200, 30, 30)):
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestExtraction(unittest.TestCase):
    def test_srcset_picks_highest(self):
        best = crawler.parse_best_from_srcset(
            "/small.jpg 400w, /large.jpg 1200w, /medium.jpg 800w"
        )
        self.assertEqual(best, "/large.jpg")

    def test_srcset_density_picks_highest(self):
        best = fetch_images.parse_best_from_srcset("/1x.png 1x, /2x.png 2x")
        self.assertEqual(best, "/2x.png")

    def test_placeholder_detection(self):
        self.assertTrue(crawler.is_placeholder("https://example.com/spacer.gif"))
        self.assertTrue(crawler.is_placeholder("data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP"))
        self.assertFalse(crawler.is_placeholder("https://example.com/photo.jpg"))

    def test_extract_prefers_lazy_src_and_srcset(self):
        html = """
        <html><body>
          <img src="placeholder.svg" data-src="/real.jpg"
               srcset="/small.jpg 400w, /big.jpg 1200w">
          <img src="/plain.jpg">
        </body></html>
        """
        found = crawler.extract_images_from_html(html, "https://example.com/page")
        self.assertIn("https://example.com/real.jpg", found)
        self.assertIn("https://example.com/plain.jpg", found)
        self.assertNotIn("https://example.com/placeholder.svg", found)


class TestCrawlAndArchive(unittest.TestCase):
    def test_data_uri_image_is_saved_not_dropped(self):
        """Regression test: data URI images used to hit an unbound content_type."""
        png = make_png_bytes()
        data_uri = "data:image/png;base64," + base64.b64encode(png).decode()
        html = f'<html><body><img src="{data_uri}"></body></html>'
        with tempfile.TemporaryDirectory() as tmp:
            page_path = os.path.join(tmp, "index.html")
            with open(page_path, "w", encoding="utf-8") as f:
                f.write(html)
            handler = partial(SimpleHTTPRequestHandler, directory=tmp)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/index.html"
                out = os.path.join(tmp, "out.zip")
                summary = crawler.crawl_and_archive(
                    url, output_zip=out, max_depth=0, min_bytes=10
                )
                self.assertEqual(summary["total_images"], 1)
                with zipfile.ZipFile(out) as z:
                    self.assertEqual(len(z.namelist()), 1)
            finally:
                server.shutdown()
                server.server_close()


class TestArchiveExtraction(unittest.TestCase):
    def test_traversal_entry_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            evil = os.path.join(tmp, "evil.zip")
            with zipfile.ZipFile(evil, "w") as z:
                z.writestr("../escape.txt", b"nope")
            dest = os.path.join(tmp, "dest")
            with self.assertRaises(ValueError):
                archive_store.extract_archive(evil, dest)
            self.assertFalse(os.path.exists(os.path.join(tmp, "escape.txt")))


if __name__ == "__main__":
    unittest.main()
