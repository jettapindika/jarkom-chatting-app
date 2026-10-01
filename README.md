# Jarkom Chat

Aplikasi chat multi-user dengan server konkuren, protokol application-layer yang
dirancang sendiri, dan klien terminal maupun browser. Dibuat untuk mata kuliah
Jaringan Komputer, dengan aturan main: komunikasi memakai Socket API langsung,
tanpa library chat siap pakai.

Tiga hal yang membedakan proyek ini dari latihan socket biasa:

1. **Protokolnya dirancang sendiri**, lengkap dengan framing, handshake, dan
   penanganan error. Bukan JSON yang ditempel di atas TCP tanpa aturan.
2. **Empat lapisan teratas ditulis sebagai lapisan terpisah** (L7 Application,
   L6 Presentation, L5 Session, L4 Transport), dan setiap lapisan melaporkan
   apa yang dilakukannya.
3. **Jejak lapisan itu bisa dilihat langsung di browser**, per pesan, dengan
   payload mentahnya. Ini yang biasanya hilang saat belajar jaringan: kode
   berjalan, tapi tidak ada yang bisa menunjukkan lapisan mana yang melakukan
   apa.

---

## Daftar isi

- [Fitur](#fitur)
- [Arsitektur singkat](#arsitektur-singkat)
- [Kebutuhan](#kebutuhan)
- [Menjalankan](#menjalankan)
- [Contoh penggunaan](#contoh-penggunaan)
- [Pengujian](#pengujian)
- [Keputusan teknis](#keputusan-teknis)
- [Struktur proyek](#struktur-proyek)
- [Dokumentasi](#dokumentasi)
- [Batasan yang diketahui](#batasan-yang-diketahui)

---

## Fitur

**Server**

- Listen di alamat dan port yang bisa dikonfigurasi lewat argumen atau variabel
  lingkungan; tidak ada yang ditulis mati di dalam kode.
- Melayani banyak client sekaligus dalam satu event loop `asyncio`, tanpa saling
  memblokir.
- Mengelola daftar user aktif dan menolak nickname yang sudah dipakai.
- Broadcast ke semua user, pesan pribadi ke satu user, dan notifikasi saat ada
  user bergabung atau keluar.
- Logging aktivitas berkala dengan timestamp.
- Heartbeat untuk mendeteksi koneksi yang sudah mati.
- Shutdown yang rapi: setiap client diberi tahu sebelum koneksi ditutup.

**Klien terminal**

- Dua thread: satu membaca keyboard, satu membaca socket. Pesan masuk tidak
  pernah membuat input terblokir.
- Perintah `/nick`, `/list`, `/msg`, `/help`, `/quit`.
- Tampilan pesan dengan timestamp dan pengirim.
- Flag `--trace` untuk melihat perjalanan tiap pesan melewati empat lapisan.

**Klien web**

- Halaman chat di `/`: konek, kirim pesan, daftar user aktif yang diperbarui
  otomatis.
- Halaman visualizer di `/visualizer`: jejak L7 sampai L4 untuk tiap pesan,
  dikelompokkan per pesan, dengan payload heksadesimal yang bisa dibuka.
- Tema terang dan gelap.
- Memakai protokol dan aturan perintah yang sama persis dengan klien terminal.

---

## Arsitektur singkat

```
┌──────────────┐   WebSocket    ┌──────────────┐   TCP + protokol   ┌──────────────┐
│   Browser    │ ─────────────► │    Bridge    │ ─────────────────► │ Chat server  │
│  (web/)      │ ◄───────────── │  (bridge/)   │ ◄───────────────── │  (server/)   │
└──────────────┘                └──────────────┘                    └──────────────┘
                                                                           ▲
                                                                           │ TCP + protokol
                                                                    ┌──────────────┐
                                                                    │ Klien CLI    │
                                                                    │ (client/)    │
                                                                    └──────────────┘
```

Empat lapisan yang kita tulis sendiri dipakai bersama oleh klien CLI, bridge,
dan server:

| Lapisan | Modul | Tanggung jawab |
| --- | --- | --- |
| L7 Application | `app/`, `server/`, `client/` | Perintah user, dispatch, aturan chat |
| L6 Presentation | `presentation/` | JSON, skema, kode error |
| L5 Session | `session/` | Identitas sesi, urutan PDU, heartbeat |
| L4 Transport | `transport/` | Framing, baca tulis byte stream |

Penjelasan lengkap ada di [`docs/ARSITEKTUR.md`](docs/ARSITEKTUR.md) dan
[`docs/OSI.md`](docs/OSI.md).

---

## Kebutuhan

- **Python 3.11 atau lebih baru.** Tidak ada dependensi pihak ketiga untuk
  bagian socket; semuanya standard library.
- **Node.js 18 atau lebih baru** dan npm, hanya kalau ingin menjalankan bagian
  web. Bagian Python berjalan tanpa Node sama sekali.

---

## Menjalankan

### 1. Server

```bash
python run_server.py --port 9009
```

### 2. Klien terminal

Di terminal lain, sebanyak yang diinginkan:

```bash
python run_client.py --host 127.0.0.1 --port 9009 --nick budi
```

Kalau `--nick` tidak diberikan, klien akan menanyakannya saat dijalankan.

### 3. Bagian web (opsional)

Bagian web butuh dua proses tambahan. Bridge adalah jembatan antara WebSocket
yang dipakai browser dan protokol TCP yang dipakai server chat.

```bash
# Bridge
python -m bridge.main --port 9009 --ws-port 8787

# Web
cd web
npm install
npm run dev
```

Buka `http://localhost:3000` untuk chat, dan `http://localhost:3000/visualizer`
untuk melihat jejak lapisan.

Untuk mode produksi:

```bash
cd web
npm run build
npm run start -- -p 3000
```

### Menggeser alamat atau port

Tidak ada alamat yang ditulis mati. Urutannya: argumen baris perintah, lalu
variabel lingkungan, lalu nilai bawaan.

| Komponen | Variabel lingkungan | Bawaan |
| --- | --- | --- |
| Server dan klien | `CHAT_HOST`, `CHAT_PORT` | `127.0.0.1`, `9009` |
| Nickname klien | `CHAT_NICK` | kosong, ditanyakan |
| Bridge, sisi browser | `CHAT_BRIDGE_HOST`, `CHAT_BRIDGE_PORT` | `127.0.0.1`, `8787` |
| Halaman web | `NEXT_PUBLIC_CHAT_BRIDGE_HOST`, `NEXT_PUBLIC_CHAT_BRIDGE_PORT` | `127.0.0.1`, `8787` |

Contoh, server di port lain:

```bash
python run_server.py --port 9100
python run_client.py --port 9100 --nick budi
```

Contoh, menjalankan server di semua antarmuka supaya bisa diakses dari komputer
lain di jaringan yang sama:

```bash
python run_server.py --host 0.0.0.0 --port 9009
python run_client.py --host 192.168.1.20 --port 9009 --nick budi
```

### Opsi baris perintah

| Perintah | Opsi |
| --- | --- |
| `run_server.py` | `--host`, `--port`, `--max-clients`, `--heartbeat-interval`, `--log-level`, `--trace` |
| `run_client.py` | `--host`, `--port`, `--nick`, `--heartbeat-interval`, `--trace` |
| `python -m bridge.main` | `--host`, `--port`, `--ws-host`, `--ws-port`, `--trace`, `--log-level` |

`--trace` menulis jejak lapisan sebagai JSON Lines di `stderr`, berguna untuk
membaca apa yang terjadi tanpa membuka browser.

---

## Contoh penggunaan

### Dua klien mengobrol

Klien pertama:

```
$ python run_client.py --nick budi
*** terhubung ke 127.0.0.1:9009 sebagai budi (session 3f2a91c4)
*** ketik /help untuk daftar perintah, /quit untuk keluar
*** 2 user online:
    - budi
    - siti
halo siti, ini pesan broadcast
18:42:07 <budi> halo siti, ini pesan broadcast
```

Klien kedua menerima baris yang sama tanpa diminta:

```
18:42:07 <budi> halo siti, ini pesan broadcast
```

### Perintah

| Perintah | Arti |
| --- | --- |
| `/nick <nama>` | Ganti nickname |
| `/list` | Tampilkan user yang sedang online |
| `/msg <user> <pesan>` | Kirim pesan pribadi ke satu user |
| `/help` | Tampilkan daftar perintah |
| `/quit` | Keluar |

Baris yang tidak diawali `/` selalu dikirim sebagai broadcast. Mengetik kalimat
biasa lalu menekan Enter adalah kasus yang paling sering dipakai, jadi ia tidak
butuh awalan apa pun.

### Pesan pribadi

```
/msg siti ini hanya untuk kamu
18:43:11 → siti: ini hanya untuk kamu
```

Sisi pengirim melihat tujuan pesannya. Sisi penerima melihat pengirimnya:

```
18:43:11 <budi> (pribadi) ini hanya untuk kamu
```

Pengirim lain tidak menerima pesan ini sama sekali. Kalau tujuannya tidak ada,
server menjawab dengan kode error `NO_SUCH_USER`, bukan diam.

### Menjalankan stress test

```bash
python tools/stress_test.py --clients 50
```

Skrip ini membuka 50 koneksi bersamaan, membuat semuanya handshake, broadcast,
lalu menghitung berapa broadcast yang benar-benar diterima tiap klien. Angka yang
pendek di situ berarti ada klien yang tertinggal.

### Melihat byte di kabel

```bash
python tools/frame_dump.py --port 9009 --nick dumper
```

Skrip ini menjalankan percakapan skrip dan mencetak setiap frame dua arah,
dengan panjang prefix, header sesi, dan badan JSON dipisah supaya enkapsulasinya
terlihat, bukan sekadar diklaim.

---

## Pengujian

```bash
python -m unittest discover -s tests -t .
```

Suite berisi 272 pengujian yang mencakup encode dan decode protokol, framing,
handshake, urutan lapisan, registry user, jalur server, WebSocket, dan bridge.
Tidak ada yang membutuhkan jaringan luar; semuanya berjalan di loopback atau
tanpa socket sama sekali.

Untuk memastikan tidak ada socket yang bocor, jalankan dengan peringatan sumber
daya dijadikan error:

```bash
python -W error::ResourceWarning -m unittest discover -s tests -t .
```

Hasil pengujian manual, termasuk skenario tiga klien bersamaan, disconnect
paksa, nickname duplikat, pesan besar, dan private message ke user yang tidak
ada, ada di [`docs/LAPORAN_PENGUJIAN.md`](docs/LAPORAN_PENGUJIAN.md).

---

## Keputusan teknis

Setiap pilihan di bawah ini punya alasan yang bisa dipertanggungjawabkan, bukan
sekadar bawaan.

| Keputusan | Pilihan | Alasan |
| --- | --- | --- |
| Transport | TCP | Chat butuh pengiriman yang andal dan berurutan. Kehilangan satu pesan diam-diam lebih buruk daripada lambat sedikit. |
| Framing | Panjang 4 byte big-endian + JSON UTF-8 | TCP adalah byte stream tanpa batas pesan. Panjang di depan membuat penerima tahu persis berapa byte yang harus dibaca, tanpa perlu menganggap isi pesan tidak pernah mengandung karakter tertentu. |
| Bahasa | Python 3.11+ | Standard library-nya sudah punya `socket`, `asyncio`, `threading`, dan `struct`. Tidak ada dependensi pihak ketiga di jalur socket. |
| Konkurensi server | `asyncio`, satu event loop | Chat didominasi operasi I/O, bukan CPU. Satu event loop menghapus seluruh kelas bug race pada daftar user, karena tidak ada dua task yang benar-benar berjalan bersamaan. |
| Konkurensi klien | `threading`, dua thread | Klien harus bisa membaca keyboard dan socket sekaligus. Dua thread blocking jauh lebih sederhana dibaca daripada state machine async untuk dua sumber input. |
| Batas ukuran frame | 64 KiB | Melindungi server dari klien yang mengirim panjang palsu. Pesan yang lebih besar ditolak dengan kode error, bukan membuat server kehabisan memori. |
| Heartbeat | PING dan PONG berkala | Koneksi TCP bisa mati tanpa memberi tahu siapa pun, misalnya kabel dicabut. Tanpa heartbeat, nickname user itu tertahan selamanya. |
| Laporan lapisan | Modul `trace/` terpisah | Lapisan tidak boleh tahu cara menampilkannya. Satu emitter, banyak sink: JSON Lines untuk terminal, WebSocket untuk browser. |
| Bridge | Proses Python terpisah | Ia bisa mengimpor stack lapisan yang sama dengan klien CLI, jadi jejak di browser adalah jejak asli, bukan rekonstruksi. Koneksi panjang juga tidak cocok ditahan di dalam Route Handler Next.js. |

---

## Struktur proyek

```
jarkom-chatting-app/
├── run_server.py           Titip masuk server
├── run_client.py           Titip masuk klien CLI
├── app/                    L7: parsing perintah dan aturan chat
├── presentation/           L6: JSON, skema, kode error
├── session/                L5: identitas sesi, urutan, heartbeat
├── transport/              L4: framing dan baca tulis socket
├── server/                 Sisi server: registry user, dispatch, logging
├── client/                 Sisi klien CLI: koneksi, penerima, tampilan
├── bridge/                 Jembatan WebSocket ke TCP, ditulis tangan
├── trace/                  Emitter dan sink peristiwa lapisan
├── util/                   Heksadesimal dan utilitas waktu
├── tools/                  Stress test, frame dump, skenario manual
├── tests/                  Suite pengujian
├── docs/                   Dokumentasi
└── web/                    Klien browser Next.js
```

---

## Dokumentasi

| Dokumen | Isi |
| --- | --- |
| [`docs/PROTOCOL.md`](docs/PROTOCOL.md) | Spesifikasi protokol lengkap: format pesan, framing, daftar tipe, diagram alur, penanganan error |
| [`docs/ARSITEKTUR.md`](docs/ARSITEKTUR.md) | Arsitektur sistem, alasan pemilihan TCP dan model konkurensi, alur data |
| [`docs/OSI.md`](docs/OSI.md) | Pemetaan kode ke lapisan OSI dan bukti untuk lapisan yang tidak kita tulis |
| [`docs/WEB.md`](docs/WEB.md) | Arsitektur web, kontrak browser dan bridge, cara menjalankan |
| [`docs/PANDUAN_PENGGUNAAN.md`](docs/PANDUAN_PENGGUNAAN.md) | Panduan langkah demi langkah untuk pengguna awam |
| [`docs/LAPORAN_PENGUJIAN.md`](docs/LAPORAN_PENGUJIAN.md) | Skenario uji, hasil, dan temuan |

---

## Batasan yang diketahui

Ditulis terbuka supaya tidak ada yang mengira ini lebih lengkap dari yang
sebenarnya.

- **Tanpa enkripsi.** Pesan berjalan sebagai teks biasa. Cocok untuk latihan di
  jaringan lokal, tidak untuk data sungguhan.
- **Tanpa autentikasi.** Siapa pun yang bisa menjangkau port server boleh masuk
  dan memakai nickname apa pun yang belum dipakai.
- **Tanpa penyimpanan riwayat.** Pesan hanya ada selama ada yang menerima. Tidak
  ada database, jadi klien yang bergabung belakangan tidak melihat percakapan
  sebelumnya.
- **Visualizer berhenti di L6 untuk pesan masuk.** Lapisan L7 di sisi penerima
  dikerjakan pemakai stack, bukan `SessionCore`, jadi tidak ada yang bisa
  dilaporkan. Menampilkannya akan berarti mengarang data.
- **L3 sampai L1 tidak muncul di visualizer.** Lapisan itu dipegang sistem
  operasi dan kartu jaringan. Yang bisa ditunjukkan hanyalah tangkapan paket.
- **Muat ulang halaman web memutus sesi chat.** Provider koneksi dibongkar saat
  halaman dimuat ulang, dan bridge menutup koneksi TCP-nya. Ini perilaku yang
  benar, dan nickname langsung bisa dipakai lagi dalam waktu di bawah satu
  detik.
