"""Incremental processing utilities."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional


def should_process(
    item_id: str,
    output_path: Path,
    source_hash: Optional[str] = None,
    hash_store_path: Optional[Path] = None,
) -> bool:
    """
    Determine if an item needs processing.

    Skip processing if:
    1. Output already exists, AND
    2. (Optional) Source content hash matches stored hash

    Args:
        item_id: Unique identifier for the item (e.g., "LUCK_annual_2024_page_045")
        output_path: Where output would be written
        source_hash: Optional hash of source content for change detection
        hash_store_path: Optional path to JSON file storing hashes

    Returns:
        True if item should be processed, False if it can be skipped
    """
    # No output yet - must process
    if not output_path.exists():
        return True

    # If no hash checking requested, output exists = skip
    if source_hash is None:
        return False

    # Check if source changed
    if hash_store_path and hash_store_path.exists():
        try:
            hash_store = json.loads(hash_store_path.read_text())
            stored_hash = hash_store.get(item_id)
            if stored_hash == source_hash:
                return False  # Source unchanged, skip
        except (json.JSONDecodeError, KeyError):
            pass

    return True  # Source changed or no stored hash, process


def compute_file_hash(file_path: Path, algorithm: str = "md5") -> str:
    """
    Compute hash of file contents.

    Args:
        file_path: Path to file
        algorithm: Hash algorithm (md5, sha256, etc.)

    Returns:
        Hex digest of file hash
    """
    hasher = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_content_hash(content: str, algorithm: str = "md5") -> str:
    """
    Compute hash of string content.

    Args:
        content: String content to hash
        algorithm: Hash algorithm (md5, sha256, etc.)

    Returns:
        Hex digest of content hash
    """
    hasher = hashlib.new(algorithm)
    hasher.update(content.encode("utf-8"))
    return hasher.hexdigest()


def update_hash_store(
    hash_store_path: Path,
    item_id: str,
    content_hash: str,
) -> None:
    """
    Update hash store with new hash for item.

    Args:
        hash_store_path: Path to JSON hash store file
        item_id: Item identifier
        content_hash: Hash to store
    """
    hash_store = {}
    if hash_store_path.exists():
        try:
            hash_store = json.loads(hash_store_path.read_text())
        except json.JSONDecodeError:
            pass

    hash_store[item_id] = content_hash
    hash_store_path.parent.mkdir(parents=True, exist_ok=True)
    hash_store_path.write_text(json.dumps(hash_store, indent=2))
