"""Ekstraksi shopid/itemid dari URL produk Shopee."""

from __future__ import annotations

import re

URL_ID_RE = re.compile(r"i\.(\d+)\.(\d+)")


def parse_ids_from_url(url: str) -> tuple[str, str]:
    match = URL_ID_RE.search(url)
    if not match:
        raise ValueError(f"Pola 'i.<shopid>.<itemid>' tidak ditemukan di URL: {url}")
    return match.group(1), match.group(2)


def resolve_target(entry: dict | str) -> dict:
    """Normalisasi satu entri produk menjadi {url, shopid, itemid, category}."""
    if isinstance(entry, str):
        entry = {"url": entry}
    url = entry.get("url", "")
    if url:
        shopid, itemid = parse_ids_from_url(url)
    elif entry.get("shopid") and entry.get("itemid"):
        shopid, itemid = str(entry["shopid"]), str(entry["itemid"])
        url = url or f"https://shopee.co.id/product/{shopid}/{itemid}"
    else:
        raise ValueError(f"Entri produk tidak punya url/shopid+itemid: {entry!r}")
    return {
        "url": url,
        "shopid": shopid,
        "itemid": itemid,
        "category": entry.get("category", ""),
        "product_name": entry.get("product_name", ""),
    }
