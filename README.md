# shopee_commet — Krawler Ulasan Shopee

Mengumpulkan dataset komentar/ulasan produk Shopee lewat endpoint internal
`/api/v2/item/get_ratings`, dieksekusi **di dalam sesi browser Chrome asli yang
sudah login**.

Kenapa bukan `requests`? Shopee memblokir sesi anonim dengan error `90309999`
(badan anti-fraud minta token hasil solve captcha), dan halaman produk diarahkan
ke `/verify/traffic` / `/verify/captcha`.

## Backend: kenapa Chrome asli + CDP (bukan WebDriver)

| Pendekatan | Hasil uji |
|---|---|
| `requests` / sesi anonim | ditolak `90309999` |
| Selenium / SeleniumBase UC Mode (headless & headed) | ditolak `90309999`; widget captcha Shopee gagal generate ("Verifikasi gagal") — terdeteksi chromedriver |
| **Chrome dijalankan biasa + DevTools Protocol (tanpa chromedriver)** | **lolos, `error:0`, data ulasan terbaca** |

Backend `cdp` (default) membuka Chrome lewat perintah biasa
(`--remote-debugging-port`) lalu mengendalikannya via protokol DevTools:
`navigator.webdriver = false`, tidak ada sinyal automasi, cookie login Anda
dipakai apa adanya.

> Catatan: `sb.solve_captcha()` / `uc_gui_click_captcha()` dari SeleniumBase
> hanya mendukung Cloudflare Turnstile, reCAPTCHA, dan Incapsula — **bukan**
> slider custom Shopee. Pure CDP Mode-nya (`sb_cdp`) juga teruji sama-sama
> ditolak saat profil belum dipercaya; yang menentukan adalah profil, bukan
> klien CDP-nya.

## Persiapan

```bash
pip install -r requirements.txt
```

Butuh file **`cookies.json`** — hasil export cookie dari browser Anda yang sudah
login Shopee (format EditThisCookie / JSON array). Tanpa ini, jalankan login
manual (lihat di bawah).

> Catatan: Chrome 154 di mesin ini crash (Wayland) tanpa flag
> `--disable-features=WaylandWpColorManagerV1`; flag sudah otomatis dipasang
> (`config/settings.py: DISABLE_FEATURES`).

## Cara pakai

### 1. Sesi login (sekali)

```bash
python main.py import-cookies --file cookies.json   # dari export browser
# ATAU login manual (buka Chrome, klik Masuk, login):
python main.py login
# Kalau muncul halaman tantangan, selesaikan sekali ini:
python main.py verify --url "https://shopee.co.id/...-i.SHOPID.ITEMID"
```

Profil disimpan di `.chrome_session/` — sesudah `verify` sukses, run berikutnya
lolos tanpa captcha lagi.

### 2. Cek akses sebuah produk

```bash
python main.py info --url "https://shopee.co.id/...-i.SHOPID.ITEMID"
```

### 3. Kumpulkan ulasan

```bash
python main.py crawl --products products.json
```

`products.json` (boleh banyak produk, satu sesi browser untuk semuanya):

```json
[
  {"url": "https://shopee.co.id/...-i.955006040.20284106051", "category": "headset"},
  {"shopid": "188380895", "itemid": "25462161057", "category": "deodorant"}
]
```

Opsi penting:

| Opsi | Default | Fungsi |
|---|---|---|
| `--backend` | `cdp` | `cdp` = Chrome asli tanpa WebDriver; `uc` = SeleniumBase |
| `--max` | 500 | Batas total ulasan per produk (termasuk yang sudah ada dari resume) |
| `--delay` | 1.5 | Jeda detik antar request |
| `--limit` | 50 | Ulasan per request (maks 50) |
| `--stars N` | 0 | 0 = semua; 1..5 = hanya bintang itu |
| `--headless` | off | Sembunyikan browser (default: jendela terbuka) |
| `--include-empty` | off | Simpan juga ulasan tanpa teks (rating saja) |
| `--include-author` | off | Simpan username penulis (default: tidak) |
| `--no-resume` | off | Abaikan checkpoint, mulai dari awal |
| `--force` | off | crawl ulang produk yang sudah `done` |

## Hasil

Untuk tiap produk (`data/<itemid>_<shopid>.…`):

- `.jsonl` — ditulis baris-demi-baris saat crawl (**checkpoint / resume**)
- `.csv` — digenerate ulang dari JSONL setelah tiap produk selesai
- `.meta.json` — offset terakhir, status selesai, nama produk, error

Kolom CSV:

```
review_id, review_text, rating, date_review, author_username, like_count,
product_name, product_category, shopid, itemid, product_url, scraped_at
```

Resume otomatis: kalau crawl terputus (Ctrl-C, diblokir, mati listrik), jalankan
perintah yang sama — lanjut dari offset terakhir, duplikat dibuang via `review_id`.

## Cara kerja

1. Sesi dibuka: Chrome asli (profil `.chrome_session`) → kunjungan anonim dulu ke
   Shopee → cookie login dari `cookies.json` dipasang → reload. (Urutan ini
   penting; cookie-dulu sering memicu `/verify/traffic`.)
2. `get_ratings` dipanggil via `fetch()` **same-origin dari dalam halaman
   Shopee** (membawa cookie sesi + `x-csrftoken`), paginasi `offset` sampai habis.
3. Kalau muncul `/verify/…` atau `90309999`, crawl berhenti + simpan checkpoint.
   Jangan spam reload — tunggu beberapa menit, jalankan ulang perintah yang sama
   (resume otomatis), atau selesaikan captcha di jendela browser.

Jeda antar request (`--delay`) dihormati, hanya data ulasan publik yang diambil,
dan identitas pembeli tidak disimpan kecuali `--include-author` dipilih.

## Troubleshooting

- **`/verify/traffic` muncul terus** → ini **bukan blokir IP** (browser Chrome
  asli Anda tetap lancar saat crawler ditolak), melainkan **sidik jari/profil**:
  Shopee mencocokkan cookie device (`SC_DFP`, `ds`) dengan profil yang
  dipakai. Solusinya: `python main.py verify --url …` → selesaikan captcha
  **sekali** di jendela Chrome, profil `.chrome_session` lalu dianggap
  dipercaya. Crawler sendiri memberi jeda 25–50s antar percobaan — jangan
  spam reload (menaikkan skor risiko).
- **`90309999`** → cookie login belum terpasang / sudah kedaluwarsa → export ulang
  `cookies.json` dari browser yang sedang login.
- **0 ulasan tapi API OK** → hampir pasti `--limit > 50`; Shopee membalas
  `ratings: []` untuk limit di atas 50 (otomatis di-clamp, lihat log crawl).
- **Chrome crash / core dump** → pastikan `DISABLE_FEATURES` di
  `config/settings.py` masih berisi `WaylandWpColorManagerV1`.
- **Chrome nyangkut setelah proses di-kill** → tutup manual; profil
  `.chrome_session` tidak boleh dipakai dua Chrome sekaligus.
- **Mulai dari awal** → `--no-resume` (atau hapus file di `data/`).
