#!/usr/bin/env python3
"""Materialize a human-readable symlink view of immutable authoritative PDFs.

The downloader's content-addressed files remain the provenance store. This view
can be rebuilt at any time without renaming uploaded R2 keys or invalidating
manifest paths.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.constants import ARTIFACTS, PROJECT_ROOT  # noqa: E402

DOWNLOAD_MANIFEST = ARTIFACTS / "01_authoritative_download" / "manifest.json"
CORPUS_ROOT = PROJECT_ROOT / "authoritative_corpus"
VIEW_MANIFEST = CORPUS_ROOT / "manifest.json"


def load_downloader_module():
    path = Path(__file__).with_name("01_authoritative_download.py")
    spec = importlib.util.spec_from_file_location("authoritative_download_for_organizer", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def display_name(title: str) -> str:
    value = re.sub(r"(?i)\.pdf$", "", title.strip())
    if "_" in value and " " not in value:
        value = value.replace("_", " ")
    value = re.sub(r"[\x00-\x1f/:]+", " - ", value)
    value = re.sub(r"\s+", " ", value).strip(" .-")
    return value or "Document"


def inferred_year(record: dict[str, Any], downloader) -> int | None:
    if record.get("publication_year"):
        return int(record["publication_year"])
    candidate = downloader.Candidate(
        source_id=record["source_id"],
        country=record["country"],
        publisher=record["publisher"],
        authority_type=record["authority_type"],
        collection_id=record["collection_id"],
        landing_url=record["landing_url"],
        url=record["canonical_url"],
        title=record["title"],
        verify_tls=True,
    )
    return downloader.publication_year(candidate)


def view_path(record: dict[str, Any], downloader) -> Path:
    source_folder = re.sub(r"^[a-z]{2}_", "", record["source_id"])
    year = inferred_year(record, downloader)
    filename = f"{display_name(record['title'])}--{record['identity']}.pdf"
    return Path(
        str(record["country"]).lower(),
        source_folder,
        record["collection_id"],
        str(year) if year else "undated",
        filename,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a readable authoritative PDF corpus view")
    parser.add_argument("--country", action="append", default=[])
    parser.add_argument("--source", action="append", default=[])
    args = parser.parse_args()
    countries = {item.upper() for item in args.country}
    sources = set(args.source)

    payload = json.loads(DOWNLOAD_MANIFEST.read_text())
    downloader = load_downloader_module()
    rows: list[dict[str, Any]] = []
    linked = skipped = failed = 0
    for identity, value in sorted(payload["documents"].items()):
        record = dict(value)
        record["identity"] = identity
        if countries and str(record["country"]).upper() not in countries:
            continue
        if sources and record["source_id"] not in sources:
            continue
        target = PROJECT_ROOT / record["current"]["local_path"]
        link = CORPUS_ROOT / view_path(record, downloader)
        try:
            if not target.is_file():
                raise FileNotFoundError(target)
            link.parent.mkdir(parents=True, exist_ok=True)
            relative_target = os.path.relpath(target, link.parent)
            if link.is_symlink():
                if os.readlink(link) == relative_target:
                    skipped += 1
                else:
                    link.unlink()
                    link.symlink_to(relative_target)
                    linked += 1
            elif link.exists():
                raise FileExistsError(f"refusing to replace non-symlink: {link}")
            else:
                link.symlink_to(relative_target)
                linked += 1
            rows.append(
                {
                    "identity": identity,
                    "source_id": record["source_id"],
                    "publisher": record["publisher"],
                    "collection_id": record["collection_id"],
                    "publication_year": inferred_year(record, downloader),
                    "title": record["title"],
                    "view_path": link.relative_to(PROJECT_ROOT).as_posix(),
                    "target_path": target.relative_to(PROJECT_ROOT).as_posix(),
                    "canonical_url": record["canonical_url"],
                }
            )
        except Exception as exc:
            failed += 1
            print(f"failed {identity}: {exc}")
    CORPUS_ROOT.mkdir(parents=True, exist_ok=True)
    VIEW_MANIFEST.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "document_count": len(rows),
                "linked": linked,
                "skipped": skipped,
                "failed": failed,
                "documents": rows,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(f"organized corpus: documents={len(rows)} linked={linked} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
