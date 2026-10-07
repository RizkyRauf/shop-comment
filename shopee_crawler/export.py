"""Penyimpanan dataset: JSONL (incremental) + CSV (final) + meta resume."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from config import settings


def slug_for(target: dict) -> str:
    return f"{target['itemid']}_{target['shopid']}"


def jsonl_path(out_dir: Path, slug: str) -> Path:
    return out_dir / f"{slug}.jsonl"


def csv_path(out_dir: Path, slug: str) -> Path:
    return out_dir / f"{slug}.csv"


def meta_path(out_dir: Path, slug: str) -> Path:
    return out_dir / f"{slug}.meta.json"


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_seen(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=settings.CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_meta(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_meta(path: Path, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def summarize(rows: list[dict]) -> dict:
    dist = Counter(str(r.get("rating")) for r in rows)
    texted = sum(1 for r in rows if (r.get("review_text") or "").strip())
    return {
        "total_rows": len(rows),
        "with_text": texted,
        "rating_distribution": dict(sorted(dist.items())),
    }
