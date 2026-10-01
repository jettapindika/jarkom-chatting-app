# Panduan Penggunaan

Panduan ini untuk siapa pun yang baru pertama kali menjalankan proyek ini, tanpa
perlu membaca kode lebih dulu. Setiap langkah ditulis lengkap dengan perintah
yang bisa disalin langsung.

Daftar isi:

1. [Apa yang dibutuhkan](#1-apa-yang-dibutuhkan)
2. [Menjalankan server](#2-menjalankan-server)
3. [Menjalankan klien terminal](#3-menjalankan-klien-terminal)
4. [Perintah di dalam chat](#4-perintah-di-dalam-chat)
5. [Mengobrol dengan beberapa orang](#5-mengobrol-dengan-beberapa-orang)
6. [Menjalankan versi web](#6-menjalankan-versi-web)
7. [Melihat jejak lapisan OSI](#7-melihat-jejak-lapisan-osi)
8. [Arti pesan error](#8-arti-pesan-error)
9. [Kalau ada yang tidak jalan](#9-kalau-ada-yang-tidak-jalan)

---

## 1. Apa yang dibutuhkan

| Kebutuhan | Versi | Untuk apa |
| --- | --- | --- |
| Python | 3.11 atau lebih baru | Server dan klien terminal. Wajib. |
| Node.js dan npm | 18 atau lebih baru | Hanya kalau ingin membuka versi web. Opsional. |

Bagian socket hanya memakai standard library Python, jadi tidak ada yang perlu
di-`pip install`. Untuk memeriksa versi yang terpasang:

```bash
python --version
node --version
```

Kalau `python --version` menampilkan 3.10 atau lebih lama, pakai nama lain yang
tersedia di sistem, misalnya `python3.11` atau `python3.12`, untuk semua perintah
di bawah ini.

---

## 2. Menjalankan server

Server harus jalan lebih dulu. Buka satu terminal, lalu:

```bash
python run_server.py --port 9009
```

Kalau berhasil, akan muncul baris seperti ini:

```
2026-10-01T12:43:33.365Z  INFO      server      listening on 127.0.0.1:9009
```

Baris itu artinya server sudah siap menerima koneksi. Biarkan terminal ini
terbuka. Server akan terus berjalan sampai kamu menekan `Ctrl+C`.

Setiap kejadian penting tercatat di terminal ini, dengan timestamp: siapa yang
terhubung, siapa yang keluar, dan error apa yang terjadi. Terminal server adalah
tempat pertama yang harus dilihat kalau ada yang aneh.

### Mengubah port atau alamat

```bash
python run_server.py --host 0.0.0.0 --port 9100
```

`--host 0.0.0.0` membuat server menerima koneksi dari komputer lain di jaringan
yang sama. Tanpa itu, server hanya menerima koneksi dari komputer sendiri.

Opsi lain yang tersedia:

| Opsi | Arti | Bawaan |
| --- | --- | --- |
| `--host` | Alamat yang didengarkan | `127.0.0.1` |
| `--port` | Port TCP | `9009` |
| `--max-clients` | Jumlah klien maksimum | `64` |
| `--heartbeat-interval` | Detik sebelum server mengirim PING | `15` |
| `--log-level` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | `INFO` |
| `--trace` | Tulis jejak lapisan sebagai JSON Lines | mati |

Kalau lupa opsinya, jalankan `python run_server.py --help`.

### Menghentikan server

Tekan `Ctrl+C` di terminal server. Server akan berhenti dengan rapi: setiap klien
yang masih terhubung menerima pesan perpisahan lebih dulu, lalu koneksinya
ditutup. Di terminal server akan terlihat:

```
2026-10-01T12:43:35.482Z  INFO      server      shutting down: 1 client(s) connected
2026-10-01T12:43:35.483Z  INFO      server      shutdown complete
```

Di sisi klien, pesan perpisahan itu muncul sebagai:

```
*** server menutup koneksi
```

---

## 3. Menjalankan klien terminal

Buka terminal **baru** (server tetap jalan di terminal pertamanya), lalu:

```bash
python run_client.py --host 127.0.0.1 --port 9009 --nick budi
```

`--nick` adalah nama yang akan dilihat orang lain. Kalau tidak diberikan, klien
akan menanyakannya lebih dulu. Setelah berhasil, yang muncul adalah:

```
*** terhubung ke 127.0.0.1:9009 sebagai budi (session 3f2a91c4)
*** ketik /help untuk daftar perintah, /quit untuk keluar
> *** 1 user online:
    - budi
```

Tanda `> ` di awal baris adalah prompt tempat kamu mengetik. Prompt itu muncul
lebih dulu, lalu daftar user menyusul dari server.

Sekarang kamu bisa langsung mengetik pesan dan menekan Enter.

### Mengubah alamat server

```bash
python run_client.py --host 192.168.1.20 --port 9009 --nick budi
```

Ganti `192.168.1.20` dengan alamat komputer yang menjalankan server.

| Opsi | Arti | Bawaan |
| --- | --- | --- |
| `--host` | Alamat server | `127.0.0.1` |
| `--port` | Port server | `9009` |
| `--nick` | Nickname | kosong, ditanyakan |
| `--heartbeat-interval` | Detik sebelum klien mengecek server | `15` |
| `--trace` | Tampilkan jejak lapisan | mati |

---

## 4. Perintah di dalam chat

Semua perintah diawali garis miring `/`. Ketik `/help` untuk melihat daftarnya
langsung dari dalam chat.

| Perintah | Kegunaan | Contoh |
| --- | --- | --- |
| `/nick <nama>` | Ganti nickname | `/nick budi_santoso` |
| `/list` | Lihat siapa saja yang sedang online | `/list` |
| `/msg <user> <pesan>` | Kirim pesan pribadi ke satu orang | `/msg siti halo` |
| `/help` | Tampilkan daftar perintah | `/help` |
| `/quit` | Keluar dari chat | `/quit` |

**Baris yang tidak diawali `/` otomatis dikirim ke semua orang.** Jadi untuk
mengobrol biasa kamu tidak perlu mengetik perintah apa pun, cukup tulis
pesannya:

```
halo semuanya, selamat pagi
```

### Melihat daftar user

```
/list
*** 3 user online:
    - budi
    - siti
    - agus
```

Daftar ini juga otomatis diperbarui: begitu ada yang masuk atau keluar, semua
orang menerima pemberitahuannya.

```
*** siti bergabung
*** agus keluar
```

### Mengirim pesan pribadi

```
/msg siti ini hanya untuk kamu
```

Yang kamu lihat sebagai pengirim:

```
18:43:11 → siti: ini hanya untuk kamu
```

Yang dilihat siti sebagai penerima:

```
18:43:11 <budi> (pribadi) ini hanya untuk kamu
```

Orang ketiga tidak menerima pesan ini sama sekali. Kalau nama tujuannya salah
atau orangnya sudah keluar, server akan menjawab:

```
*** error [NO_SUCH_USER] no such user: 'siti'
```

Teks setelah kode berasal dari server apa adanya, jadi bahasanya Inggris,
sementara nasihat untuk pengguna ditulis dalam bahasa Indonesia di tempat lain.

### Mengganti nickname

```
/nick budi_santoso
*** nickname diganti menjadi budi_santoso
```

Orang lain melihat pergantian itu sebagai dua pemberitahuan, bukan satu:

```
*** budi keluar
*** budi_santoso bergabung
```

### Keluar

Ketik `/quit`, atau tekan `Ctrl+C`, atau tekan `Ctrl+D`. Ketiganya sama-sama
menutup koneksi dengan rapi, dan nickname kamu langsung bisa dipakai orang lain.

---

## 5. Mengobrol dengan beberapa orang

Buka beberapa terminal, jalankan satu klien di masing-masing dengan nickname
berbeda. Server yang sama melayani semuanya sekaligus.

```bash
# Terminal 2
python run_client.py --nick siti

# Terminal 3
python run_client.py --nick agus
```

Begitu klien kedua terhubung, klien pertama otomatis melihat:

```
*** siti bergabung
```

Pesan yang kamu kirim akan terlihat oleh semua orang yang sedang online, dengan
timestamp dan nama pengirimnya:

```
18:42:07 <budi> halo siti, ini pesan broadcast
```

### Nickname tidak boleh sama

Kalau ada yang mencoba memakai nickname yang sudah dipakai, klien itu ditolak
dan langsung keluar dengan pesan:

```
*** gagal terhubung: nickname sudah dipakai user lain, coba nickname lain
```

Server tetap melayani yang lain. Yang ditolak hanya klien yang mencoba memakai
nama itu.

### Menguji dengan banyak klien sekaligus

Untuk melihat perilaku server di bawah beban, tersedia skrip simulasi:

```bash
python tools/stress_test.py --clients 50
```

Skrip ini membuka 50 koneksi sekaligus, membuat semuanya terhubung, lalu
menghitung berapa banyak broadcast yang benar-benar diterima tiap klien. Kalau
semua klien menerima jumlah yang sama, berarti tidak ada yang tertinggal.

---

## 6. Menjalankan versi web

Versi web memungkinkan chat dan melihat jejak lapisan langsung di browser. Ini
butuh **dua proses tambahan**, di samping server chat yang sudah jalan.

Urutannya harus begini: server chat dulu, lalu bridge, lalu web.

### Langkah 1: server chat

Sudah jalan dari bagian 2. Kalau belum:

```bash
python run_server.py --port 9009
```

### Langkah 2: bridge

Bridge adalah jembatan antara WebSocket yang dipakai browser dan TCP yang dipakai
server chat. Buka terminal baru:

```bash
python -m bridge.main --port 9009 --ws-port 8787
```

`--port 9009` harus sama dengan port server chat, dan `--ws-port 8787` adalah
port yang akan dipakai browser.

### Langkah 3: web

Buka terminal baru lagi:

```bash
cd web
npm install
npm run dev
```

`npm install` hanya perlu dijalankan sekali. Setelah muncul keterangan bahwa
server siap, buka:

- `http://localhost:3000` untuk halaman chat
- `http://localhost:3000/visualizer` untuk halaman visualizer

Untuk mode produksi:

```bash
cd web
npm run build
npm run start -- -p 3000
```

### Menggunakan halaman chat

1. Isi kolom nickname, lalu klik **Masuk**.
2. Setelah terhubung, daftar user di sebelah kanan akan terisi.
3. Ketik pesan di kolom bawah, lalu klik **Kirim** atau tekan Enter.
4. Perintah seperti `/list` dan `/msg` juga bisa diketik di kolom yang sama,
   persis seperti di klien terminal.

Halaman chat dan klien terminal bisa dipakai bersamaan. Pesan dari terminal akan
muncul di browser, dan sebaliknya.

### Kalau port bridge berbeda

Kalau bridge dijalankan di port lain, beri tahu halaman web lewat variabel
lingkungan sebelum `npm run dev`:

```bash
cd web
NEXT_PUBLIC_CHAT_BRIDGE_PORT=9999 npm run dev
```

---

## 7. Melihat jejak lapisan OSI

Halaman `http://localhost:3000/visualizer` menampilkan perjalanan setiap pesan
melewati empat lapisan yang kita tulis sendiri:

| Lapisan | Nama | Yang dikerjakan |
| --- | --- | --- |
| L7 | Application | Perintah user, aturan chat, dispatch |
| L6 | Presentation | JSON, validasi skema, kode error |
| L5 | Session | Identitas sesi, nomor urut pesan, heartbeat |
| L4 | Transport | Framing panjang pesan, baca tulis byte |

Setiap peristiwa menampilkan lapisannya, arahnya, ringkasan isinya, ukuran dalam
byte, dan payload mentah dalam heksadesimal. Ini yang biasanya hilang saat
belajar jaringan: kode berjalan, tapi tidak ada yang bisa menunjukkan lapisan
mana yang melakukan apa.

Untuk melihat jejak yang sama di terminal, jalankan klien dengan `--trace`:

```bash
python run_client.py --nick budi --trace
```

Jejaknya ditulis sebagai JSON Lines di `stderr`, jadi percakapan tetap bersih di
`stdout`.

Untuk melihat byte yang benar-benar lewat di kabel, tersedia skrip:

```bash
python tools/frame_dump.py --port 9009 --nick dumper
```

Skrip ini menjalankan percakapan skrip dan mencetak setiap frame dua arah dengan
panjang prefix, header sesi, dan badan JSON dipisah, supaya enkapsulasinya
terlihat, bukan sekadar diklaim.

---

## 8. Arti pesan error

Pesan error dari server selalu diawali `*** error [KODE]`. Berikut artinya:

| Kode | Artinya | Yang harus dilakukan |
| --- | --- | --- |
| `NICK_TAKEN` | Nickname sudah dipakai orang lain | Pilih nama lain, atau tunggu yang bersangkutan keluar |
| `NICK_INVALID` | Nickname kosong atau isinya tidak diizinkan | Pakai nama yang lebih sederhana |
| `NO_SUCH_USER` | Tujuan pesan pribadi tidak ada | Cek lagi ejaan namanya dengan `/list` |
| `TEXT_TOO_LONG` | Pesan melebihi batas panjang | Potong pesannya |
| `FRAME_TOO_LARGE` | Satu frame melebihi 64 KiB | Kirim dalam beberapa bagian |
| `MALFORMED` | Frame tidak sesuai format protokol | Biasanya bug, bukan kesalahan pemakaian |
| `PROTOCOL_MISMATCH` | Versi protokol klien tidak cocok dengan server | Pastikan klien dan server dari sumber yang sama |
| `UNEXPECTED_TYPE` | Tipe pesan dikirim pada saat yang salah, misalnya pesan biasa sebelum handshake | Biasanya bug |
| `UNKNOWN_TYPE` | Tipe pesan tidak dikenal server | Biasanya bug |
| `NOT_AUTHENTICATED` | Ada pesan dikirim sebelum handshake selesai | Biasanya bug |

Teks setelah kode berasal dari server dan tidak diterjemahkan, jadi isinya
bahasa Inggris. Kode itu sendiri yang stabil untuk dipakai bercabang.

Dua kode terdefinisi di protokol tetapi tidak pernah benar-benar dikirim, jadi
tidak ada di tabel di atas: `SERVER_FULL` dan `INTERNAL`. Server yang sudah
mencapai batas `--max-clients` tidak mengirim pesan apa pun, melainkan langsung
memutus socket sebelum handshake dijawab. Yang kamu lihat di klien adalah:

```
*** gagal terhubung: koneksi ke 127.0.0.1:9009 terputus saat handshake: connection closed by peer
```

Kalau muncul pesan seperti itu padahal server jelas hidup, periksa apakah
jumlah klien sudah menyentuh `--max-clients`.

Error dari sisi klien, bukan dari server, diawali `***` tanpa kode:

| Pesan | Artinya |
| --- | --- |
| `*** gagal terhubung: ...` | Server tidak bisa dihubungi saat klien dijalankan |
| `*** gagal mengirim: ...` | Koneksi putus di tengah pengiriman |
| `*** server menutup koneksi` | Server dimatikan dengan rapi |
| `*** perintah tidak dikenal` | Perintah diawali `/` tapi tidak ada di daftar |

---

## 9. Kalau ada yang tidak jalan

### `Connection refused` saat klien dijalankan

Server belum jalan, atau alamat dan portnya berbeda.

1. Periksa terminal server, pastikan ada baris `listening on ...`.
2. Pastikan `--port` di klien sama dengan `--port` di server.
3. Kalau server di komputer lain, pastikan server dijalankan dengan
   `--host 0.0.0.0`, bukan `127.0.0.1`.

### Nickname ditolak terus

Nickname itu masih dipakai koneksi lain yang belum benar-benar putus. Tunggu
sekitar 45 detik sampai heartbeat server menyadari koneksi itu sudah mati, lalu
coba lagi. Kalau perlu segera, pakai nickname lain dulu. Angka 45 detik itu
tiga kali interval heartbeat bawaan; kalau server dijalankan dengan
`--heartbeat-interval` yang lebih kecil, waktunya ikut memendek.

### Pesan tidak sampai ke orang lain

Periksa apakah pesannya diawali `/`. Baris yang diawali `/` dianggap perintah,
jadi `/halo` tidak akan terkirim sebagai pesan. Tulis tanpa garis miring.

### Halaman web tidak mau terhubung

Tiga hal yang paling sering jadi penyebab:

1. Bridge belum jalan. Jalankan `python -m bridge.main --port 9009 --ws-port 8787`.
2. Port bridge di halaman web berbeda dengan yang dijalankan. Lihat bagian
   [Kalau port bridge berbeda](#kalau-port-bridge-berbeda).
3. Server chat belum jalan, sehingga bridge tidak punya tujuan.

### Halaman web memutus sesi saat dimuat ulang

Ini perilaku yang disengaja, bukan bug. Memuat ulang halaman membongkar koneksi
lama, dan bridge menutup koneksi TCP-nya. Nickname langsung bisa dipakai lagi
dalam waktu kurang dari satu detik.

### Klien berhenti sendiri tanpa pesan

Kalau server mati mendadak (misalnya komputer server dimatikan paksa), klien
akan mendeteksi koneksi itu sudah mati dalam waktu sekitar tiga kali interval
heartbeat, yaitu sekitar 45 detik dengan pengaturan bawaan. Untuk membuatnya
lebih cepat saat demo, jalankan kedua sisi dengan interval yang lebih kecil:

```bash
python run_server.py --heartbeat-interval 5
python run_client.py --nick budi --heartbeat-interval 5
```

### Melihat lebih banyak detail

Jalankan server dengan `--log-level DEBUG` untuk melihat setiap frame yang
masuk dan keluar:

```bash
python run_server.py --port 9009 --log-level DEBUG
```
