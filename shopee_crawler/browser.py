"""Sesi browser SeleniumBase (UC Mode) + deteksi login / tembok anti-bot Shopee."""

from __future__ import annotations

import json
import time
from contextlib import ExitStack
from dataclasses import dataclass

from seleniumbase import SB

from config import settings

STATE_JS = """
const text = (document.body && document.body.innerText) || '';
return {
  url: location.href,
  wall: location.href.includes('/verify/')
    || text.includes('Verifikasi untuk melanjutkan')
    || text.includes('Geser untuk menyelesaikan puzzle')
    || text.includes('Verifikasi gagal'),
  title: document.title,
};
"""

JS_FETCH_PROMISE = """
(async () => {
  const [itemid, shopid, offset, limit, rtype, csrf] = __ARGS__;
  const url = __ENDPOINT__ + `?filter=0&flag=1`
    + `&itemid=${itemid}&shopid=${shopid}&type=${rtype}&offset=${offset}&limit=${limit}`;
  let res;
  try {
    res = await fetch(url, {
      credentials: 'include',
      headers: {
        'x-requested-with': 'XMLHttpRequest',
        'x-api-source': 'pc',
        'x-csrftoken': csrf,
        'x-shopee-language': 'id',
      },
    });
  } catch (e) {
    return { error: 'fetch gagal: ' + String(e) };
  }
  const text = await res.text();
  if (res.status !== 200) return { error: 'HTTP ' + res.status };
  let json;
  try { json = JSON.parse(text); }
  catch (e) { return { error: 'respons bukan JSON: ' + text.slice(0, 160) }; }
  if (json && json.error === 90309999) {
    return { error: '90309999 — diblokir anti-bot / sesi belum login' };
  }
  if (json && json.error) return { error: 'error endpoint: ' + String(json.error) };
  return json;
})()
"""


class SessionBlocked(RuntimeError):
    pass


class NotLoggedIn(RuntimeError):
    pass


@dataclass
class SessionState:
    url: str
    wall: bool
    title: str
    has_login: bool = False


def normalize_cookie(c: dict) -> dict:
    """Konversi cookie ekstensi browser (EditThisCookie/JSON export) ke format Selenium."""
    if not c.get("name"):
        raise ValueError("cookie tanpa name")
    item = {
        "name": c["name"],
        "value": c.get("value", ""),
        "path": c.get("path") or "/",
        "domain": c["domain"],
    }
    exp = c.get("expirationDate") or c.get("expiry")
    if exp:
        item["expiry"] = int(exp)
    if c.get("httpOnly"):
        item["httpOnly"] = True
    if c.get("secure"):
        item["secure"] = True
    ss = (c.get("sameSite") or "").lower()
    if ss in ("strict", "lax", "none"):
        item["sameSite"] = ss.capitalize()
    return item


def fetch_expression(target: dict, offset: int, limit: int, rating_type: int, csrf: str) -> str:
    """Ekspresi JS yang mengembalikan Promise<dict> — untuk Runtime.evaluate (CDP)."""
    args = json.dumps(
        [
            str(target["itemid"]),
            str(target["shopid"]),
            int(offset),
            int(limit),
            int(rating_type),
            csrf,
        ]
    )
    return JS_FETCH_PROMISE.replace("__ARGS__", args).replace(
        "__ENDPOINT__", json.dumps(settings.RATINGS_ENDPOINT)
    )


def _fetch_script(target: dict, offset: int, limit: int, rating_type: int, csrf: str) -> str:
    """Script async WebDriver (callback style) — untuk execute_async_script."""
    return (
        "const done = arguments[arguments.length - 1];\n"
        + fetch_expression(target, offset, limit, rating_type, csrf)
        + ".then(done, err => done({ error: String(err) }));"
    )


class ShopeeSession:
    """Sesi SeleniumBase UC Mode dengan profil persisten."""

    def __init__(self, profile_dir, headless: bool = True):
        self.profile_dir = str(profile_dir)
        self.headless = headless
        self._stack = ExitStack()
        self.sb = None

    def __enter__(self) -> "ShopeeSession":
        self.sb = self._stack.enter_context(
            SB(
                uc=True,
                headless=self.headless,
                test=True,
                locale_code="id",
                user_data_dir=self.profile_dir,
                disable_features=settings.DISABLE_FEATURES,
                window_size="1366,900",
            )
        )
        self.sb.driver.set_script_timeout(30)
        return self

    def __exit__(self, *exc) -> None:
        self._stack.close()

    @property
    def driver(self):
        return self.sb.driver

    def goto(self, url: str, wait: float = 3.0) -> None:
        self.sb.driver.get(url)
        if wait:
            time.sleep(wait)

    def cookie(self, name: str) -> str:
        for c in self.sb.driver.get_cookies():
            if c.get("name") == name:
                return c.get("value") or ""
        return ""

    def add_cookies(self, cookies: list[dict]) -> tuple[int, list[tuple[str, str]]]:
        """Tambahkan cookie (format hasil ekstensi browser) ke sesi berjalan."""
        added, failed = 0, []
        for c in cookies:
            try:
                self.sb.driver.add_cookie(normalize_cookie(c))
                added += 1
            except Exception as exc:
                failed.append(
                    (c.get("name", "?"), str(exc).split("\n")[0][:100])
                )
        return added, failed

    def state(self) -> SessionState:
        raw = self.sb.execute_script(STATE_JS)
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
                "Selesaikan captcha di jendela browser, atau jalankan "
                "`python main.py login` untuk sesi login yang sah."
            )
        if not st.has_login:
            raise NotLoggedIn(
                "Sesi belum login (cookie SPC_U tidak ada). "
                "Jalankan `python main.py login` terlebih dahulu."
            )
        return st

    def fetch(self, target: dict, offset: int, limit: int, rating_type: int) -> dict:
        script = _fetch_script(target, offset, limit, rating_type, self.cookie("csrftoken"))
        result = self.sb.driver.execute_async_script(script)
        if not isinstance(result, dict):
            return {"error": "respons tak terduga dari endpoint"}
        return result


def run_login(profile_dir, timeout: float = settings.LOGIN_TIMEOUT) -> bool:
    """Buka browser UC headed, tunggu user login manual, simpan profil."""
    print(f"[login] membuka browser dengan profil: {profile_dir}")
    print("[login] langkah di browser:")
    print("  1. selesaikan captcha (bila muncul halaman /verify/...)")
    print("  2. klik tombol 'Masuk' di kanan atas")
    print("  3. login (password / QR)")
    print("[login] jangan tutup browser sampai muncul 'login terdeteksi'.")
    with ShopeeSession(profile_dir, headless=False) as session:
        session.goto(settings.SITE, wait=6)
        deadline = time.time() + timeout
        last_wall = False
        while time.time() < deadline:
            st = session.state()
            if st.has_login:
                print(f"[login] login terdeteksi ({st.url})")
                time.sleep(4)
                return True
            if st.wall and not last_wall:
                print(f"[login] halaman tantangan terdeteksi — selesaikan dulu di browser: {st.url[:90]}")
                last_wall = True
            elif not st.wall:
                last_wall = False
            time.sleep(2)
        print("[login] timeout — login tidak terdeteksi.")
        return False
