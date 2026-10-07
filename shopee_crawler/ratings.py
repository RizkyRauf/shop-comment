"""Fetch & paginasi endpoint get_ratings dari dalam sesi browser."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from config import settings


def epoch_to_date(ctime) -> str | None:
    try:
        return datetime.fromtimestamp(int(ctime), tz=timezone.utc).date().isoformat()
    except (ValueError, TypeError, OSError):
        return None


def map_rating(rating: dict, target: dict, include_author: bool) -> dict:
    return {
        "review_id": str(rating.get("cmtid") or rating.get("orderid") or ""),
        "review_text": (rating.get("comment") or "").strip(),
        "rating": rating.get("rating_star"),
        "date_review": epoch_to_date(rating.get("ctime")),
        "author_username": (rating.get("author_username") or "") if include_author else "",
        "like_count": rating.get("like_count"),
        "product_name": "",
        "product_category": target.get("category", ""),
        "shopid": target["shopid"],
        "itemid": target["itemid"],
        "product_url": target["url"],
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def paginate(
    session,
    *,
    target: dict,
    seen: set[str],
    limit: int = settings.PAGE_LIMIT,
    max_reviews: int = settings.MAX_REVIEWS,
    delay: float = settings.REQUEST_DELAY,
    rating_type: int = 0,
    include_empty: bool = False,
    include_author: bool = False,
    start_offset: int = 0,
    on_row=None,
    product_name: str | None = None,
) -> tuple[list[dict], dict]:
    """Kumpulkan ulasan satu produk. `on_row` dipanggil untuk tiap baris baru."""
    rows: list[dict] = []
    if limit > settings.MAX_PAGE_LIMIT:
        print(
            f"[crawl] limit {limit} > {settings.MAX_PAGE_LIMIT} — "
            "Shopee hanya menerima maksimal 50, dipakai 50"
        )
        limit = settings.MAX_PAGE_LIMIT
    limit = max(1, int(limit))
    diag: dict = {
        "offset": start_offset,
        "total_ratings": None,
        "raw_seen": 0,
        "done": False,
        "product_name": product_name or "",
        "error": None,
    }
    offset = start_offset
    empty_streak = 0

    while len(rows) < max_reviews:
        payload = session.fetch(target, offset=offset, limit=limit, rating_type=rating_type)
        if payload.get("error"):
            diag["error"] = payload["error"]
            print(f"[crawl] offset {offset}: GAGAL — {payload['error']}")
            break
        data = payload.get("data") or {}
        ratings = data.get("ratings") or []
        summary = data.get("item_rating_summary") or {}
        if diag["total_ratings"] is None:
            diag["total_ratings"] = summary.get("rating_total")
        if not diag["product_name"]:
            for r in ratings:
                for p in r.get("product_items") or []:
                    if p.get("name"):
                        diag["product_name"] = p["name"].strip()
                        break
                if diag["product_name"]:
                    break
        if not ratings:
            diag["done"] = True
            break

        diag["raw_seen"] += len(ratings)
        new_count = 0
        for r in ratings:
            text = (r.get("comment") or "").strip()
            if not text and not include_empty:
                continue
            row = map_rating(r, target, include_author)
            row_id = row["review_id"]
            if row_id and row_id in seen:
                continue
            if row_id:
                seen.add(row_id)
            if diag["product_name"]:
                row["product_name"] = diag["product_name"]
            elif target.get("product_name"):
                row["product_name"] = target["product_name"]
            rows.append(row)
            new_count += 1
            if on_row:
                on_row(row)

        total = diag["total_ratings"] or 0
        print(
            f"[crawl] type={rating_type} offset {offset}: +{new_count} "
            f"(total {len(rows)}/{total or '?'})"
        )
        empty_streak = 0 if new_count else empty_streak + 1
        if empty_streak >= settings.MAX_EMPTY_PAGES:
            print(f"[crawl] {settings.MAX_EMPTY_PAGES} halaman tanpa baris baru — berhenti")
            break

        offset += limit
        diag["offset"] = offset
        if total and offset >= total:
            diag["done"] = True
            break
        time.sleep(delay)

    diag["collected"] = len(rows)
    return rows, diag
