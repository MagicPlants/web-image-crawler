"""
archive_store.py - Manages the registry of scraped image archives,
providing metadata persistence, scanning, zip inspection, image preview loading, and extraction.

Author: Joe Montgomery (Magic Plants)
Website: https://magicplants.net
"""

__author__ = "Joe Montgomery (Magic Plants)"
__copyright__ = "Copyright 2026, Joe Montgomery (Magic Plants) - https://magicplants.net"

from datetime import datetime
import json
import os
import uuid
import zipfile

STORE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "archives.json")
DEFAULT_ARCHIVES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "archives")


def load_registry() -> list[dict]:
    """Loads the list of tracked archives from disk."""
    if not os.path.exists(STORE_FILE):
        return []
    try:
        with open(STORE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_registry(records: list[dict]):
    """Saves the archive list to disk."""
    try:
        with open(STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2)
    except Exception as e:
        print(f"[!] Error saving registry: {e}")


def add_record(
    filepath: str,
    source_url: str,
    depth: int = 0,
    image_count: int = 0,
    pages_crawled: int = 1,
    file_size: int = 0,
) -> dict:
    """Adds a newly created archive to the registry."""
    records = load_registry()
    abs_path = os.path.abspath(filepath)

    if file_size == 0 and os.path.exists(abs_path):
        file_size = os.path.getsize(abs_path)

    # Check if this file is already registered; update if so
    for r in records:
        if os.path.abspath(r.get("filepath", "")) == abs_path:
            r["source_url"] = source_url
            r["depth"] = depth
            r["image_count"] = image_count
            r["pages_crawled"] = pages_crawled
            r["file_size"] = file_size
            r["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_registry(records)
            return r

    new_record = {
        "id": str(uuid.uuid4())[:8],
        "name": os.path.basename(abs_path),
        "filepath": abs_path,
        "source_url": source_url,
        "depth": depth,
        "image_count": image_count,
        "pages_crawled": pages_crawled,
        "file_size": file_size,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    records.insert(0, new_record)
    save_registry(records)
    return new_record


def delete_record(record_id: str, delete_file: bool = True) -> bool:
    """Deletes an archive record and optionally removes the zip file from disk."""
    records = load_registry()
    updated = []
    deleted = False

    for r in records:
        if r.get("id") == record_id:
            deleted = True
            if delete_file and os.path.exists(r.get("filepath", "")):
                try:
                    os.remove(r["filepath"])
                except Exception as e:
                    print(f"[!] Error deleting file: {e}")
        else:
            updated.append(r)

    if deleted:
        save_registry(updated)
    return deleted


def scan_and_sync_folder(folder_path: str = DEFAULT_ARCHIVES_DIR) -> list[dict]:
    """
    Scans a folder for any .zip files and adds untracked ones to the registry.
    """
    if not os.path.exists(folder_path):
        os.makedirs(folder_path, exist_ok=True)
        return load_registry()

    records = load_registry()
    registered_paths = {os.path.abspath(r.get("filepath", "")) for r in records}

    modified = False
    for filename in os.listdir(folder_path):
        if filename.lower().endswith(".zip"):
            full_path = os.path.abspath(os.path.join(folder_path, filename))
            if full_path not in registered_paths:
                try:
                    with zipfile.ZipFile(full_path, "r") as z:
                        img_count = len([n for n in z.namelist() if not n.endswith("/")])
                    new_item = {
                        "id": str(uuid.uuid4())[:8],
                        "name": filename,
                        "filepath": full_path,
                        "source_url": "Imported archive",
                        "depth": 0,
                        "image_count": img_count,
                        "pages_crawled": 1,
                        "file_size": os.path.getsize(full_path),
                        "created_at": datetime.fromtimestamp(os.path.getmtime(full_path)).strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    records.insert(0, new_item)
                    registered_paths.add(full_path)
                    modified = True
                except Exception:
                    pass

    if modified:
        save_registry(records)

    return records


def inspect_archive(filepath: str) -> list[dict]:
    """Returns a list of all files inside a zip archive with sizes and timestamps."""
    if not os.path.exists(filepath):
        return []

    items = []
    try:
        with zipfile.ZipFile(filepath, "r") as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                dt = datetime(*info.date_time).strftime("%Y-%m-%d %H:%M") if info.date_time else ""
                items.append({
                    "filename": info.filename,
                    "file_size": info.file_size,
                    "compress_size": info.compress_size,
                    "date_time": dt,
                })
    except Exception as e:
        print(f"[!] Error inspecting zip: {e}")

    return items


def read_image_from_zip(filepath: str, inner_filename: str) -> bytes | None:
    """Reads raw image bytes directly from a zip archive into memory."""
    if not os.path.exists(filepath):
        return None
    try:
        with zipfile.ZipFile(filepath, "r") as z:
            return z.read(inner_filename)
    except Exception as e:
        print(f"[!] Error reading {inner_filename} from zip: {e}")
        return None


def extract_archive(filepath: str, destination_dir: str) -> str:
    """Extracts all contents of a zip archive into destination_dir.

    Rejects entries that would escape the destination (absolute paths or
    parent directory traversal), so a hostile archive cannot write outside
    the chosen folder.
    """
    dest = os.path.abspath(destination_dir)
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(filepath, "r") as z:
        for member in z.infolist():
            if member.is_dir():
                continue
            target = os.path.abspath(os.path.join(dest, member.filename))
            if os.path.commonpath([dest, target]) != dest:
                raise ValueError(f"Unsafe archive entry, skipped extraction: {member.filename}")
            z.extract(member, dest)
    return dest
