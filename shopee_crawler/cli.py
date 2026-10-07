"""CLI: login, crawl, info."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from config import settings
from shopee_crawler import export
from shopee_crawler.browser import (
    NotLoggedIn,
    SessionBlocked,
    ShopeeSession,
    run_login,
)
from shopee_crawler.cdp import CdpSession
from shopee_crawler.ids import resolve_target
from shopee_crawler.ratings import paginate


def resolve_profile(args) -> str:
    if getattr(args, "profile", None):
        return args.profile
    if getattr(args, "backend", "cdp") == "cdp":
        return str(settings.CDP_PROFILE_DIR)
    return str(settings.PROFILE_DIR)


def open_session(args, auto_cookies: bool = True):
    """Backend default `cdp` = Chrome asli tanpa WebDriver (lolos anti-bot)."""
    if getattr(args, "backend", "cdp") == "cdp":
        return CdpSession(
            resolve_profile(args),
            headless=getattr(args, "headless", False),
            cookies_file=settings.COOKIES_FILE,
            auto_cookies=auto_cookies,
        )
    return ShopeeSession(resolve_profile(args), headless=getattr(args, "headless", False))


def load_targets(path: str) -> list[dict]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = [raw]
    return [resolve_target(entry) for entry in raw]


def cmd_login(args) -> int:
    if getattr(args, "backend", "cdp") == "cdp":
        return _login_cdp(args)
    ok = run_login(resolve_profile(args), timeout=args.timeout)
    return 0 if ok else 1


def _login_cdp(args) -> int:
    profile = resolve_profile(args)
    print(f"[login] membuka Chrome asli (CDP) — profil: {profile}")
    print("[login] klik 'Masuk' di kanan atas dan login (password / QR).")
    with CdpSession(profile, headless=False, auto_cookies=False) as session:
        session.goto(settings.SITE, wait=6)
        deadline = time.time() + args.timeout
        announced = False
        while time.time() < deadline:
            st = session.state()
            if st.has_login:
                print(f"[login] login terdeteksi ({st.url})")
                time.sleep(4)
                return 0
            if st.wall and not announced:
                print(f"[login] halaman tantangan terdeteksi — selesaikan dulu: {st.url[:90]}")
                announced = True
            time.sleep(2)
    print("[login] timeout — login tidak terdeteksi.", file=sys.stderr)
    return 1


def cmd_import_cookies(args) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"[cookies] file tidak ada: {path}", file=sys.stderr)
        return 2
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = [raw]
    print(f"[cookies] {len(raw)} cookie dari {path}")

    with open_session(args, auto_cookies=False) as session:
        session.goto(settings.SITE, wait=3)
        added, failed = session.add_cookies(raw)
        print(f"[cookies] ditambahkan: {added}/{len(raw)}")
        for name, err in failed[:8]:
            print(f"[cookies]   gagal {name}: {err}", file=sys.stderr)

        session.goto(settings.SITE, wait=6)
        st = session.state()
        print(f"[cookies] url={st.url[:90]}")
        print(f"[cookies] login(SPC_U)={st.has_login} wall={st.wall}")
        if not st.has_login or st.wall:
            print(
                "[cookies] sesi tidak valid / masih ditolak — export ulang cookie "
                "dari browser yang benar-benar sudah login Shopee.",
                file=sys.stderr,
            )
            return 3
        try:
            session.require_ready()
        except (SessionBlocked, NotLoggedIn) as exc:
            print(f"[cookies] {exc}", file=sys.stderr)
            return 3

        targets = load_targets(args.products) if Path(args.products).exists() else []
        if targets:
            payload = session.fetch(targets[0], offset=0, limit=1, rating_type=0)
            if payload.get("error"):
                print(f"[cookies] uji API gagal: {payload['error']}", file=sys.stderr)
                return 2
            summary = (payload.get("data") or {}).get("item_rating_summary") or {}
            print(f"[cookies] uji API OK — total rating: {summary.get('rating_total')}")

    print("[cookies] selesai — profil tersimpan, `python main.py crawl` siap dijalankan.")
    return 0


def _optional_target(args) -> dict | None:
    if getattr(args, "url", None):
        return resolve_target({"url": args.url})
    if getattr(args, "shopid", None) and getattr(args, "itemid", None):
        return resolve_target({"shopid": args.shopid, "itemid": args.itemid})
    try:
        targets = load_targets(getattr(args, "products", "products.json"))
    except Exception:
        return None
    return targets[0] if targets else None


def cmd_verify(args) -> int:
    """Buka Chrome, minta user selesaikan captcha sekali, lalu buktikan sesi lolos.

    Profil (user-data-dir) disimpan permanen, jadi sesudah verifikasi ini
    crawl berikutnya tidak perlu captcha lagi.
    """
    target = _optional_target(args)
    timeout = float(args.timeout)
    print(f"[verify] membuka Chrome — profil: {resolve_profile(args)}")
    with open_session(args) as session:
        session.goto(settings.SITE, wait=3)
        print(
            "\n[verify] INSTRUKSI (selesaikan di jendela Chrome):\n"
            "  1. Selesaikan captcha slider jika muncul.\n"
            "  2. Kalau halaman kosong/galat: tekan R (reload) berulang\n"
            "     sampai puzzle muncul ATAU homepage Shopee tampil normal.\n"
            "  3. Selesai = halaman utama Shopee tampil tanpa 'Geser untuk\n"
            "     menyelesaikan puzzle'. Status dicek otomatis tiap 2 detik.\n"
            "  4. Beri tahu saya di sini setelah selesai (Ctrl+C untuk batal).\n",
            flush=True,
        )
        deadline = time.time() + timeout
        st = None
        while time.time() < deadline:
            try:
                st = session.state()
            except Exception:
                time.sleep(2)
                continue
            if not st.wall and st.has_login:
                break
            print(
                f"[verify] wall={st.wall} login={st.has_login} {st.url[:70]}",
                flush=True,
            )
            time.sleep(4)
        else:
            st = session.state()
            if st.wall:
                print("[verify] timeout — masih di halaman tantangan.", file=sys.stderr)
                return 3
            if not st.has_login:
                print("[verify] timeout — login (SPC_U) tidak terdeteksi.", file=sys.stderr)
                return 3

        print("[verify] wall lolos + login aktif — uji API ...", flush=True)
        if target:
            payload = session.fetch(target, offset=0, limit=1, rating_type=0)
            if payload.get("error"):
                print(f"[verify] API gagal: {payload['error']}", file=sys.stderr)
                return 2
            summary = (payload.get("data") or {}).get("item_rating_summary") or {}
            print(f"[verify] API OK — total rating: {summary.get('rating_total')}")
        print("[verify] SUKSES — profil tersimpan permanen; jalankan `crawl`.")
    return 0


def cmd_info(args) -> int:
    if args.url:
        target = resolve_target({"url": args.url})
    elif args.shopid and args.itemid:
        target = resolve_target({"shopid": args.shopid, "itemid": args.itemid})
    else:
        print("[info] berikan --url ATAU --shopid + --itemid", file=sys.stderr)
        return 2
    with open_session(args) as session:
        session.goto(settings.SITE, wait=4)
        try:
            session.require_ready()
        except (SessionBlocked, NotLoggedIn) as exc:
            print(f"[info] {exc}", file=sys.stderr)
            return 3
        payload = session.fetch(target, offset=0, limit=1, rating_type=0)
        if payload.get("error"):
            print(f"[info] gagal: {payload['error']}", file=sys.stderr)
            return 2
        data = payload.get("data") or {}
        summary = data.get("item_rating_summary") or {}
        name = ""
        for r in data.get("ratings") or []:
            for p in r.get("product_items") or []:
                if p.get("name"):
                    name = p["name"]
                    break
            if name:
                break
        print(f"produk      : {name or target.get('product_name') or '-'}")
        print(f"shopid      : {target['shopid']}")
        print(f"itemid      : {target['itemid']}")
        print(f"total rating: {summary.get('rating_total')}")
        print(f"rv dengan teks: {summary.get('rcount_with_context')}")
        print(f"rating_count: {summary.get('rating_count')}")
    return 0


def cmd_crawl(args) -> int:
    targets = load_targets(args.products)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[crawl] {len(targets)} produk target → {out_dir}")

    with open_session(args) as session:
        session.goto(settings.SITE, wait=5)
        try:
            session.require_ready()
        except (SessionBlocked, NotLoggedIn) as exc:
            print(f"[crawl] {exc}", file=sys.stderr)
            return 3

        for index, target in enumerate(targets, start=1):
            try:
                session.require_ready()
            except (SessionBlocked, NotLoggedIn) as exc:
                print(f"[crawl] {exc}", file=sys.stderr)
                return 3

            slug = export.slug_for(target)
            jsonl = export.jsonl_path(out_dir, slug)
            meta_file = export.meta_path(out_dir, slug)
            meta = {} if args.no_resume else export.read_meta(meta_file)

            if meta.get("done") and not args.force:
                print(f"\n[crawl] ({index}/{len(targets)}) {slug} sudah lengkap — dilewati")
                continue

            existing = export.load_seen(jsonl)
            seen = {r["review_id"] for r in existing if r.get("review_id")}
            start_offset = int(meta.get("offset", 0)) if not args.no_resume else 0

            print(
                f"\n[crawl] ({index}/{len(targets)}) {slug} "
                f"offset={start_offset} sudah_ada={len(existing)}"
            )

            def on_row(row, path=jsonl):
                export.append_jsonl(path, row)

            remaining = max(0, int(args.max) - len(existing))
            if remaining <= 0:
                print(
                    f"[crawl] {slug}: sudah {len(existing)} baris "
                    f"(≥ --max {args.max}) — tidak menarik lagi"
                )
                continue
            try:
                rows, diag = paginate(
                    session,
                    target=target,
                    seen=seen,
                    limit=args.limit,
                    max_reviews=remaining,
                    delay=args.delay,
                    rating_type=args.stars,
                    include_empty=args.include_empty,
                    include_author=args.include_author,
                    start_offset=start_offset,
                    on_row=on_row,
                    product_name=meta.get("product_name") or target.get("product_name"),
                )
            except (SessionBlocked, NotLoggedIn) as exc:
                print(f"[crawl] {exc}", file=sys.stderr)
                return 3

            all_rows = export.load_seen(jsonl)
            export.write_csv(export.csv_path(out_dir, slug), all_rows)
            export.write_meta(
                meta_file,
                {
                    "shopid": target["shopid"],
                    "itemid": target["itemid"],
                    "url": target["url"],
                    "category": target["category"],
                    "product_name": diag.get("product_name") or "",
                    "offset": diag["offset"],
                    "done": bool(diag["done"]) and not diag.get("error"),
                    "error": diag.get("error"),
                    "collected": len(all_rows),
                    "total_ratings": diag.get("total_ratings"),
                },
            )

            summary = export.summarize(all_rows)
            print(
                f"[crawl] {slug}: {summary['total_rows']} baris "
                f"(teks: {summary['with_text']}) → {export.csv_path(out_dir, slug)}"
            )
            print(f"[crawl] distribusi rating: {summary['rating_distribution']}")

            if diag.get("error") and "90309999" in str(diag["error"]):
                print(
                    "[crawl] terkena blokir anti-bot — berhenti. Jalankan "
                    "`python main.py login`, atau ulangi dengan --headful agar bisa "
                    "menyelesaikan captcha secara manual (resume otomatis).",
                    file=sys.stderr,
                )
                return 3

            if index < len(targets):
                time.sleep(args.product_delay)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shopee-crawler",
        description="Kumpulkan ulasan produk Shopee via endpoint get_ratings.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="login manual sekali, sesi disimpan ke profil")
    p_login.add_argument("--profile", default=None, help="direktori profil (default per backend)")
    p_login.add_argument("--backend", choices=("cdp", "uc"), default="cdp",
                        help="cdp=Chrome asli tanpa WebDriver (default), uc=SeleniumBase")
    p_login.add_argument("--timeout", type=float, default=settings.LOGIN_TIMEOUT)
    p_login.set_defaults(func=cmd_login)

    p_ck = sub.add_parser(
        "import-cookies", help="impor cookie login dari file JSON (hasil export browser)"
    )
    p_ck.add_argument("--file", default="cookies.json")
    p_ck.add_argument("--profile", default=None)
    p_ck.add_argument("--backend", choices=("cdp", "uc"), default="cdp")
    p_ck.add_argument("--products", default="products.json", help="file untuk uji API")
    p_ck.add_argument("--headless", action="store_true")
    p_ck.set_defaults(func=cmd_import_cookies)

    p_crawl = sub.add_parser("crawl", help="kumpulkan ulasan dari daftar produk")
    p_crawl.add_argument("--products", default="products.json")
    p_crawl.add_argument("--out", default=str(settings.DATA_DIR))
    p_crawl.add_argument("--profile", default=None)
    p_crawl.add_argument("--backend", choices=("cdp", "uc"), default="cdp")
    p_crawl.add_argument("--max", type=int, default=settings.MAX_REVIEWS)
    p_crawl.add_argument("--limit", type=int, default=settings.PAGE_LIMIT)
    p_crawl.add_argument("--delay", type=float, default=settings.REQUEST_DELAY)
    p_crawl.add_argument("--product-delay", type=float, default=settings.PRODUCT_DELAY)
    p_crawl.add_argument("--stars", type=int, default=0, help="0=semua, 1..5=filter bintang")
    p_crawl.add_argument("--include-empty", action="store_true")
    p_crawl.add_argument("--include-author", action="store_true")
    p_crawl.add_argument("--no-resume", action="store_true")
    p_crawl.add_argument("--force", action="store_true", help=" crawl ulang produk yang done")
    p_crawl.add_argument("--headless", action="store_true", help="crawl tanpa jendela browser (rawan captcha)")
    p_crawl.set_defaults(func=cmd_crawl)

    p_info = sub.add_parser("info", help="cek satu produk: total rating & akses API")
    p_info.add_argument("--url", default=None)
    p_info.add_argument("--shopid", default=None)
    p_info.add_argument("--itemid", default=None)
    p_info.add_argument("--profile", default=None)
    p_info.add_argument("--backend", choices=("cdp", "uc"), default="cdp")
    p_info.add_argument("--headless", action="store_true")
    p_info.set_defaults(func=cmd_info)

    p_ver = sub.add_parser(
        "verify",
        help="sekali saja: selesaikan captcha di Chrome, lalu buktikan API lolos",
    )
    p_ver.add_argument("--url", default=None)
    p_ver.add_argument("--shopid", default=None)
    p_ver.add_argument("--itemid", default=None)
    p_ver.add_argument("--products", default="products.json", help="fallback: produk pertama untuk uji API")
    p_ver.add_argument("--profile", default=None)
    p_ver.add_argument("--backend", choices=("cdp", "uc"), default="cdp")
    p_ver.add_argument("--timeout", type=float, default=600, help="detik menunggu verifikasi")
    p_ver.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    import atexit
    import signal

    from shopee_crawler.cdp import cleanup_all

    atexit.register(cleanup_all)

    def _on_signal(signum, _frame):
        cleanup_all()
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    finally:
        cleanup_all()
