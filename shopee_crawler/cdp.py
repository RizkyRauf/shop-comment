"""Backend Chrome asli + DevTools Protocol mentah (TANPA chromedriver/WebDriver).

Kenapa: Shopee menolak sesi chromedriver (deteksi WebDriver) dengan error
90309999 + captcha yang gagal generate. Chrome yang dijalankan biasa lalu
dikendalikan lewat CDP tidak meninggalkan sinyal automasi (`navigator.webdriver
= false`), dan endpoint get_ratings langsung lolos.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket

from config import settings
from shopee_crawler.browser import (
    NotLoggedIn,
    SessionBlocked,
    SessionState,
    STATE_JS,
    fetch_expression,
    normalize_cookie,
)


_LAUNCHERS: list["ChromeLauncher"] = []


def cleanup_all() -> None:
    """Tutup semua Chrome yang kita jalankan (dipanggil saat keluar/terminate)."""
    for L in list(_LAUNCHERS):
        try:
            L.stop()
        except Exception:
            pass
        _LAUNCHERS.remove(L)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_json(port: int, path: str, method: str = "GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


class CdpError(RuntimeError):
    pass


class CdpClient:
    """Klien DevTools Protocol minimal (sinkron)."""

    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self._id = 0

    def call(self, method: str, params: dict | None = None, timeout: float = 60) -> dict:
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.ws.settimeout(max(0.5, deadline - time.time()))
            try:
                msg = json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:
                break
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CdpError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise CdpError(f"timeout CDP: {method}")

    def evaluate(self, expression: str, await_promise: bool = False, timeout: float = 60):
        try:
            res = self._evaluate_once(expression, await_promise, timeout)
        except CdpError:
            time.sleep(1.5)
            res = self._evaluate_once(expression, await_promise, timeout)
        if "exceptionDetails" in res:
            details = res["exceptionDetails"].get("exception", {}).get("description", "")
            raise CdpError(f"evaluate gagal: {details[:200]}")
        return res.get("result", {}).get("value")

    def _evaluate_once(self, expression: str, await_promise: bool, timeout: float) -> dict:
        res = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
                "userGesture": True,
            },
            timeout=timeout,
        )
        return res

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


class ChromeLauncher:
    """Jalankan Chrome biasa (bukan via chromedriver) dengan CDP aktif."""

    def __init__(self, profile_dir, headless: bool = False, window: str = "1366,900"):
        self.profile_dir = str(profile_dir)
        self.headless = headless
        self.window = window
        self.port = _free_port()
        self.proc: subprocess.Popen | None = None

    def start(self, wait: float = 30.0) -> None:
        chrome = settings.CHROME_BIN
        args = [
            chrome,
            f"--remote-debugging-port={self.port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--disable-features={settings.DISABLE_FEATURES}",
            f"--window-size={self.window}",
            "--lang=id-ID",
        ]
        if self.headless:
            args.append("--headless=new")
        args.append("about:blank")
        self.proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        _LAUNCHERS.append(self)
        deadline = time.time() + wait
        last_err = None
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise CdpError(
                    f"Chrome keluar lebih dulu (kode {self.proc.returncode}). "
                    "Pastikan profil tidak sedang dipakai Chrome lain."
                )
            try:
                _http_json(self.port, "/json/version")
                return
            except Exception as exc:  # belum siap
                last_err = exc
                time.sleep(0.5)
        raise CdpError(f"CDP tidak siap dalam {wait}s ({last_err})")

    def page_ws(self) -> str:
        targets = _http_json(self.port, "/json/list")
        pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        if not pages:
            try:
                created = _http_json(self.port, "/json/new?about:blank", method="PUT")
                return created["webSocketDebuggerUrl"]
            except Exception as exc:
                raise CdpError(f"tidak ada target halaman: {exc}")
        for p in pages:
            if p.get("url", "").startswith(("about:blank", "chrome://newtab")):
                return p["webSocketDebuggerUrl"]
        return pages[0]["webSocketDebuggerUrl"]

    def stop(self) -> None:
        if self in _LAUNCHERS:
            _LAUNCHERS.remove(self)
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class CdpSession:
    """Sesi Chrome-asli via CDP; API serupa ShopeeSession (goto/state/fetch/...)."""

    def __init__(self, profile_dir, headless: bool = False, cookies_file=None,
                 auto_cookies: bool = True):
        self.profile_dir = str(profile_dir)
        self.headless = headless
        self.cookies_file = Path(cookies_file) if cookies_file else None
        self.auto_cookies = auto_cookies
        self.launcher: ChromeLauncher | None = None
        self.cdp: CdpClient | None = None

    def __enter__(self) -> "CdpSession":
        self.launcher = ChromeLauncher(self.profile_dir, headless=self.headless)
        self.launcher.start()
        self.cdp = CdpClient(self.launcher.page_ws())
        self.cdp.call("Page.enable")
        self.cdp.call("Network.enable")
        self._warmup()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self.cdp:
            self.cdp.close()
            self.cdp = None
        if self.launcher:
            self.launcher.stop()
            self.launcher = None

    # --- cookie -----------------------------------------------------------
    def _warmup(self) -> None:
        """Reset sesi ke pengunjung anonim dulu, BARU kembalikan cookie login.

        Urutan ini penting: membuka Shopee dengan cookie auth yang sudah terpasang
        (tanpa kunjungan anonim sebelumnya) sering melempar /verify/traffic yang
        tidak bisa dilewati. Diuji: cookie-dulu ~1/4 lolos, anonim-dulu 4/4 lolos.
        """
        if not self.auto_cookies or not self.cookies_file or not self.cookies_file.exists():
            return
        try:
            file_cookies = json.loads(self.cookies_file.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(file_cookies, dict):
            file_cookies = [file_cookies]

        # gabungkan cookie yang sudah ada di profil + file (file menang)
        merged: dict[tuple, dict] = {}
        for c in self._profile_cookies():
            merged[(c["name"], c["domain"])] = c
        for c in file_cookies:
            if not c.get("name"):
                continue
            merged[(c["name"], c.get("domain", ""))] = self._to_cdp_cookie(c)

        self.cdp.call("Network.clearBrowserCookies")

        for attempt in range(1, 4):
            self.goto(settings.SITE, wait=5)
            if not self.state().wall:
                break
            pause = 25 * attempt
            print(f"[session] halaman tantangan (coba {attempt}/3) — tunggu {pause}s lalu ulangi "
                  "(jangan di-spam, Shopee menaikkan skor risiko)")
            time.sleep(pause)
        else:
            print("[session] GAGAL: Shopee masih menolak. Tunggu beberapa menit lalu jalankan ulang "
                  "(resume aman), atau selesaikan captcha di jendela browser.")
            return

        added, _ = self.add_cookies(list(merged.values()))
        print(f"[session] {added} cookie login dipasang kembali dari {self.cookies_file.name}")
        self.goto(settings.SITE, wait=5)
        if self.state().wall:
            print("[session] wall setelah cookie dipasang — tunggu 30s lalu coba sekali lagi")
            time.sleep(30)
            self.goto(settings.SITE, wait=5)
            if self.state().wall:
                print("[session] masih ditolak — jalankan ulang beberapa menit lagi")

    def _profile_cookies(self) -> list[dict]:
        try:
            res = self.cdp.call("Network.getAllCookies")
        except CdpError:
            return []
        return [
            {
                "name": c["name"],
                "value": c.get("value", ""),
                "path": c.get("path") or "/",
                "domain": c.get("domain", ""),
                "secure": bool(c.get("secure")),
                "httpOnly": bool(c.get("httpOnly")),
                **({"expiry": float(c["expires"])} if c.get("expires", -1) > 0 else {}),
            }
            for c in res.get("cookies", [])
        ]

    @staticmethod
    def _to_cdp_cookie(c: dict) -> dict:
        item = normalize_cookie(c)
        out = {
            "name": item["name"],
            "value": item["value"],
            "path": item["path"],
            "domain": item["domain"],
            "secure": item.get("secure", False),
            "httpOnly": item.get("httpOnly", False),
        }
        if "expiry" in item:
            out["expiry"] = float(item["expiry"])
        return out

    def add_cookies(self, cookies: list[dict]) -> tuple[int, list[tuple[str, str]]]:
        added, failed = 0, []
        for c in cookies:
            try:
                item = normalize_cookie(c)
                params = {
                    "name": item["name"],
                    "value": item["value"],
                    "path": item["path"],
                    "domain": item["domain"],
                    "secure": item.get("secure", False),
                    "httpOnly": item.get("httpOnly", False),
                }
                if "expiry" in item:
                    params["expires"] = float(item["expiry"])
                self.cdp.call("Network.setCookie", params, timeout=15)
                added += 1
            except Exception as exc:
                failed.append((c.get("name", "?"), str(exc)[:100]))
        return added, failed

    def cookie(self, name: str) -> str:
        res = self.cdp.call("Network.getCookies", {"urls": [settings.SITE]})
        for c in res.get("cookies", []):
            if c.get("name") == name:
                return c.get("value") or ""
        return ""

    # --- navigasi & state -------------------------------------------------
    def goto(self, url: str, wait: float = 3.0) -> None:
        self.cdp.call("Page.navigate", {"url": url})
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                ready = self.cdp.evaluate("document.readyState")
            except CdpError:
                time.sleep(0.3)
                continue
            if ready == "complete":
                break
            time.sleep(0.5)
        if wait:
            time.sleep(wait)

    def state(self) -> SessionState:
        raw = self.cdp.evaluate("(() => {\n" + STATE_JS + "\n})()")
        if not isinstance(raw, dict):
            raw = {}
        return SessionState(
            url=raw.get("url", ""),
            wall=bool(raw.get("wall")),
            title=raw.get("title", ""),
            has_login=bool(self.cookie("SPC_U")),
        )

    def require_ready(self) -> SessionState:
        st = self.state()
        if st.wall:
            raise SessionBlocked(
                f"Shopee menampilkan halaman tantangan ({st.url[:80]}…). "
                "Selesaikan captcha di jendela browser lalu jalankan ulang."
            )
        if not st.has_login:
            raise NotLoggedIn(
                "Sesi belum login (cookie SPC_U tidak ada). "
                "Jalankan `python main.py login` atau isi cookies.json."
            )
        return st

    # --- API --------------------------------------------------------------
    def fetch(self, target: dict, offset: int, limit: int, rating_type: int) -> dict:
        expr = fetch_expression(
            target, offset, limit, rating_type, self.cookie("csrftoken")
        )
        try:
            result = self.cdp.evaluate(expr, await_promise=True, timeout=45)
        except CdpError as exc:
            return {"error": f"gagal mengeksekusi fetch: {exc}"}
        if not isinstance(result, dict):
            return {"error": "respons tak terduga dari endpoint"}
        return result
