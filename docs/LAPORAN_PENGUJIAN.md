# Laporan Hasil Pengujian

Laporan ini mencatat apa yang benar-benar dijalankan, apa hasilnya, dan apa yang
ditemukan. Angka di dalamnya disalin dari keluaran program, bukan diperkirakan.

Daftar isi:

1. [Ringkasan](#1-ringkasan)
2. [Lingkungan pengujian](#2-lingkungan-pengujian)
3. [Unit test](#3-unit-test)
4. [Skenario pengujian manual](#4-skenario-pengujian-manual)
5. [Stress test multi-client](#5-stress-test-multi-client)
6. [Verifikasi byte di kabel](#6-verifikasi-byte-di-kabel)
7. [Verifikasi shutdown dan pelepasan nickname](#7-verifikasi-shutdown-dan-pelepasan-nickname)
8. [Verifikasi klien web](#8-verifikasi-klien-web)
9. [Temuan dan perbaikan](#9-temuan-dan-perbaikan)
10. [Batasan pengujian](#10-batasan-pengujian)

---

## 1. Ringkasan

| Area | Cakupan | Hasil |
| --- | --- | --- |
| Unit test otomatis | 261 pengujian, 11 modul | Lulus semua |
| Skenario manual | 7 skenario, 10 pemeriksaan | 10/10 lulus |
| Stress test | 50 klien bersamaan | 50/50 terhubung, 0 gagal |
| Verifikasi byte | 3 tipe frame di kabel | Sesuai spesifikasi |
| Shutdown rapi | Jalur langsung dan jalur Ctrl+C | Farewell sampai ke klien |
| Klien web | Halaman `/` dan `/visualizer` | Render benar, tanpa overflow |

Dua bug nyata ditemukan selama pengujian dan sudah diperbaiki. Keduanya dicatat
di [bagian 9](#9-temuan-dan-perbaikan), termasuk yang pertama karena ia adalah
temuan yang paling layak dibahas.

---

## 2. Lingkungan pengujian

| Komponen | Nilai |
| --- | --- |
| Sistem operasi | macOS (Darwin 27.0.0, arm64) |
| CPU | Apple M1 Pro |
| Python | 3.14.7 (`/opt/homebrew/opt/python@3.14/bin/python3.14`) |
| Node.js | v26.3.0 |
| npm | 11.16.0 |
| Alamat uji | `127.0.0.1`, port 9009 sampai 9013 |
| Jaringan | loopback saja, tanpa jaringan luar |

Seluruh pengujian berjalan di mesin yang sama lewat loopback. Ini penting
disebut karena menguji lewat loopback tidak akan pernah memunculkan gejala yang
khas jaringan nyata: latensi, kehilangan paket, dan fragmentasi. Yang bisa diuji
di sini adalah kebenaran protokol, ketahanan terhadap urutan kejadian yang tidak
enak, dan perilaku saat koneksi putus.

### Perintah unit test

```bash
python -W error::ResourceWarning -m unittest discover -s tests -t .
```

Opsi `-t .` wajib: tanpa itu `unittest` tidak menemukan paket `tests` dari akar
proyek. Flag `-W error::ResourceWarning` membuat socket yang bocor menjadi
kegagalan test, bukan sekadar peringatan yang lewat.

---

## 3. Unit test

Hasil lengkap:

```
Ran 261 tests in 1.597s

OK
```

Rincian per modul:

| Modul | Jumlah | Yang diuji |
| --- | --- | --- |
| `test_framing` | 24 | Encode dan decode length-prefix, batas 64 KiB, frame terpotong |
| `test_presentation` | 31 | JSON, validasi skema, setiap kode error, payload rusak |
| `test_session` | 30 | Identitas sesi, nomor urut PDU, deteksi urutan salah, heartbeat |
| `test_channel` | 26 | Baca tulis byte stream, partial read, timeout, penutupan |
| `test_server` | 30 | Registry, broadcast, pesan pribadi, jalur error, shutdown |
| `test_session` / `test_stack` | 20 | Keempat lapisan dirangkai, urutan peristiwa L4 sampai L7 |
| `test_trace` | 30 | Emitter, sink, urutan peristiwa per arah |
| `test_websocket` | 19 | Handshake RFC 6455, framing, masking, frame terpecah |
| `test_handshake` | 18 | CONNECT, CONNECT_OK, CONNECT_ERR, penolakan nickname |
| `test_registry` | 17 | Daftar user aktif, penambahan, pelepasan nama |
| `test_bridge` | 16 | Kontrak browser ke bridge, penerjemahan pesan |
| **Total** | **261** | |

### Cakupan edge case yang diuji

- **Partial read.** Frame yang tiba terpotong di tengah header dan di tengah
  badan JSON tetap dirakit dengan benar sebelum di-decode.
- **Pesan terlalu besar.** Panjang prefix yang melebihi 64 KiB ditolak dengan
  `FRAME_TOO_LARGE`, bukan membuat server kehabisan memori.
- **Frame rusak.** Tiga frame malformed berturut-turut menutup koneksi; satu
  frame malformed masih bisa dipulihkan dan dijawab dengan kode `MALFORMED`.
- **Nickname duplikat.** Ditolak dengan `NICK_TAKEN`, dan nickname yang sudah
  ditolak tidak boleh menahan nama itu di registry.
- **Session id salah.** Frame yang membawa session id placeholder setelah
  handshake dianggap malformed.
- **Disconnect mendadak.** `ConnectionResetError`, `ConnectionAbortedError`, dan
  `BrokenPipeError` semuanya dipetakan ke satu jalur penanganan.
- **Shutdown saat masih ada klien.** Farewell harus sampai sebelum socket
  ditutup, dan prosesnya harus selesai dalam batas waktu yang ditentukan.
- **WebSocket frame terpecah.** Frame yang dikirim dalam beberapa potongan TCP
  tetap dirakit, termasuk saat masking key ikut terpotong.

---

## 4. Skenario pengujian manual

Dijalankan dengan skrip `tools/scenarios.py` terhadap server nyata di
`127.0.0.1:9009`. Skrip ini memakai klien sungguhan, bukan tiruan, karena bug
yang ingin ditangkap justru hidup di interaksi antara task penulis server dan
buffer kernel.

Waktu jalan: **17,79 detik**. Hasil: **10/10 lulus**.

| # | Skenario | Hasil yang diharapkan | Hasil |
| --- | --- | --- | --- |
| 1 | Tiga klien bersamaan, broadcast saling terlihat | Ketiga klien menerima pesan yang sama | **LULUS** 3/3 klien menerima |
| 2a | Pesan pribadi sampai ke tujuan | Penerima menerima pesannya | **LULUS** `privatB` menerima |
| 2b | Pesan pribadi tidak bocor ke pihak ketiga | Klien lain tidak menerimanya sama sekali | **LULUS** `privatC` tidak menerima |
| 2c | Pesan pribadi ke user yang tidak ada | Server menjawab dengan kode error | **LULUS** `NO_SUCH_USER` |
| 3 | Nickname duplikat ditolak | Klien kedua ditolak, yang pertama tetap jalan | **LULUS** `NICK_TAKEN` |
| 4 | Pesan besar 4000 karakter | Isi pesan utuh, tidak terpotong | **LULUS** diterima 4000 karakter |
| 5 | Ganti nickname dengan `/nick` | User lain melihat `USER_LEAVE` lalu `USER_JOIN` | **LULUS** urutan keduanya benar |
| 6 | Klien putus mendadak (RST) | Server mendeteksi dan memberi tahu yang lain | **LULUS** `USER_LEAVE` diterima |
| 7a | Handshake manual dengan frame buatan sendiri | Balasan `CONNECT_OK` | **LULUS** |
| 7b | Frame rusak tidak menjatuhkan server | Koneksi baru setelahnya tetap berhasil | **LULUS** |

Keluaran ringkas dari skrip:

```
ringkasan: 10/10 skenario lulus
```

### Catatan pada skenario 5

Pergantian nickname sengaja tidak memakai tipe pesan khusus. User lain melihat
pasangan `USER_LEAVE` dengan nama lama dan `USER_JOIN` dengan nama baru. Alasannya
ada di [ARSITEKTUR.md](ARSITEKTUR.md) bagian 5: klien yang hanya mengenal tipe
pesan awal tetap merender perubahan itu dengan benar, karena yang terlihat hanya
"satu user pergi, satu user datang".

### Catatan pada skenario 7

Frame rusak dikirim sebagai byte mentah yang melanggar aturan protokol, bukan
lewat API klien. Tujuannya memastikan jalur validasi di sisi server benar-benar
menolak masukan yang tidak sah, bukan hanya masukan yang ditolak oleh klien.
Setelah frame rusak itu ditangani, sebuah koneksi baru dibuka sebagai saksi:
kalau koneksi itu berhasil, berarti server masih hidup.

---

## 5. Stress test multi-client

```bash
python tools/stress_test.py --clients 50 --port 9009 --hold 3
```

Hasil:

```
connecting 50 clients to 127.0.0.1:9009

completed in 4.26s
  connected successfully : 50/50
  failed                 : 0
  broadcasts received    : min 50, median 50, max 50
  session lifetime       : min 3.08s, max 4.26s
```

Yang dibuktikan angka ini: kelima puluh klien terhubung, tidak ada yang gagal,
dan setiap klien menerima jumlah broadcast yang sama persis. Angka `min` yang
lebih kecil dari `median` akan berarti ada klien yang tertinggal, dan itu tidak
terjadi.

Model konkurensi server adalah satu event loop `asyncio`, jadi 50 koneksi ini
ditangani oleh satu thread tanpa saling memblokir. Yang membuat hasilnya konsisten
bukan jumlah thread, melainkan kenyataan bahwa tidak ada operasi yang menunggu
I/O di dalam jalur yang memegang state bersama.

---

## 6. Verifikasi byte di kabel

Klaim "protokolnya dirancang sendiri" baru berarti kalau byte-nya bisa
ditunjukkan. Skrip `tools/frame_dump.py` mencetak setiap frame dua arah dengan
panjang prefix, header sesi, dan badan JSON dipisah, sehingga enkapsulasinya
terlihat.

```bash
python tools/frame_dump.py --port 9009 --nick dumper
```

Tiga tipe frame yang terverifikasi:

| Frame | Arah | Panjang total | Prefix L4 | Session id L5 | Urutan L5 | Badan L6 |
| --- | --- | --- | --- | --- | --- | --- |
| `CONNECT` | keluar | 143 B | `00 00 00 8b` | placeholder | 0 | JSON 115 B |
| `CONNECT_OK` | masuk | 186 B | `00 00 00 b6` | id sesi | 1 | JSON 158 B |
| `USER_LIST` | masuk | 160 B | `00 00 00 9c` | id sesi | 2 | JSON 132 B |
| `BROADCAST` | masuk | 148 B | `00 00 00 90` | id sesi | 3 | JSON 120 B |

Isi heksadesimal dari frame `USER_LIST`:

```
00 00 00 9c 95 db de 8e 4b d7 43 6b 8b c3 bd a8 0e af c7 de
00 00 00 00 00 00 00 02 7b 22 74 79 70 65 22 3a 22 55 53 45 52 5f ...
```

Empat byte pertama adalah panjang. Dua puluh empat byte berikutnya adalah header
sesi: 16 byte UUID dan 8 byte nomor urut big-endian. Sisanya adalah JSON UTF-8,
yang langsung terlihat dari `7b 22 74 79 70 65 22` atau `{"type"`.

Yang penting dari tabel ini: **header sesi tidak ikut masuk ke JSON.** Identitas
sesi dan nomor urut berada di lapisan L5 sebagai byte biner, bukan sebagai field
di dalam payload. Pemisahan itu disengaja, dan [PROTOCOL.md](PROTOCOL.md)
menjelaskan alasannya.

---

## 7. Verifikasi shutdown dan pelepasan nickname

Dua hal ini diuji terpisah karena keduanya tidak terlihat dari unit test biasa:
keduanya bergantung pada apa yang benar-benar sampai ke socket.

### 7.1 Shutdown mengirim farewell

Diuji lewat dua jalur, karena keduanya melewati kode yang berbeda:

| Jalur | Cara | Klien melihat |
| --- | --- | --- |
| A | `shutdown()` dipanggil langsung | `USER_LIST`, `DISCONNECT`, lalu koneksi tertutup |
| B | `SIGINT` lewat `run_server.py` | `USER_LIST`, `DISCONNECT`, lalu koneksi tertutup |

Log server pada jalur B:

```
2026-10-01T13:25:44.146Z  INFO      server      client saksimati connected from 127.0.0.1:61275
^C2026-10-01T13:25:50.240Z  INFO      server      shutting down: 1 client(s) connected
2026-10-01T13:25:50.241Z  INFO      server      client saksimati disconnected abruptly
2026-10-01T13:25:50.241Z  INFO      server      shutdown complete
```

Yang benar-benar dilihat klien sungguhan pada jalur itu, apa adanya:

```
*** terhubung ke 127.0.0.1:9009 sebagai saksimati (session 14bba6ad)
> *** 1 user online:
    - saksimati
> *** server menutup koneksi
> *** koneksi ke server ditutup
```

Baris `*** server menutup koneksi` adalah terjemahan klien untuk frame
`DISCONNECT`. Baris `1 client(s) connected` di log juga penting: ia berarti
shutdown melihat klien itu masih terdaftar saat mulai, sehingga farewell-nya
punya tujuan. Sebelum perbaikan di [bagian 9.1](#91-farewell-hilang-saat-shutdown),
baris yang sama menulis `0 client(s) connected`, dan klien hanya melihat
`ConnectionClosedError`.

Isi frame farewell yang sampai ke klien, dibaca dari sisi penulis:

```
[stop_writer] budi queue=1
[writer] EXIT normal budi (queue size 0)
>>> klien menerima 461 B
    frame 0: 180 B -> b'pe":"CONNECT_OK","sender":"server","payl'
    frame 1: 154 B -> b'pe":"USER_LIST","sender":"server","paylo'
    frame 2: 115 B -> b'pe":"DISCONNECT","sender":"server","payl'
```

Frame ketiga itu yang sebelumnya hilang.

### 7.2 Nickname dilepas saat koneksi putus

Nickname yang tertahan selamanya adalah kegagalan yang paling mudah terjadi pada
server chat: kalau koneksi mati dan registry tidak membersihkannya, nama itu
tidak bisa dipakai lagi sampai server di-restart. Diuji lewat bridge, karena
bridge adalah jalur yang paling rumit (dua hop, dua protokol):

| Cara putus | Waktu sampai nickname bisa dipakai lagi |
| --- | --- |
| Bridge mengirim WebSocket close frame | 0,30 detik |
| Bridge di-RST paksa | 0,55 detik |

Keduanya jauh di bawah satu detik, jadi tidak ada kebocoran nama.

---

## 8. Verifikasi klien web

Halaman diperiksa di browser headless terhadap build produksi, bukan di
`npm run dev`, supaya yang diuji adalah berkas yang benar-benar akan dipakai.

Hasil build:

```
✓ Compiled successfully in 1141ms
Route (app)                    Size  First Load JS
┌ ○ /                       3.65 kB         103 kB
└ ○ /visualizer             4.62 kB         103 kB
```

Pengukuran DOM pada halaman `/visualizer` dilakukan lewat JavaScript di dalam
halaman, bukan dengan melihat tangkapan layar:

| Yang diukur | Hasil | Artinya |
| --- | --- | --- |
| Baris heksadesimal | 116 | Payload mentah benar-benar dirender |
| Baris heksadesimal meluber | 0 | Tidak ada teks yang terpotong |
| Elemen terpotong | 0 | Tidak ada yang keluar dari kotaknya |
| Overflow halaman | 0 | Tidak ada scroll horizontal |

Angka `0` di tiga baris terakhir adalah yang penting. Payload heksadesimal adalah
teks panjang tanpa spasi, dan itu jenis konten yang paling sering memaksa
halaman melebar. Pengukuran ini memastikan penanganannya benar, bukan sekadar
terlihat benar di satu ukuran layar.

---

## 9. Temuan dan perbaikan

### 9.1 Farewell hilang saat shutdown

**Gejala.** Klien yang masih terhubung saat server dihentikan dengan `Ctrl+C`
tidak menerima pesan perpisahan. Yang terlihat di sisi klien adalah
`ConnectionClosedError: connection closed by peer`, yang persis sama dengan
gejala server yang mati mendadak. Log server juga menulis `0 client(s) connected`
padahal ada klien yang sedang terhubung.

**Dampak.** Klaim di README bahwa "setiap client diberi tahu sebelum koneksi
ditutup" tidak benar pada jalur yang paling sering dipakai orang, yaitu `Ctrl+C`.
Kegagalannya juga senyap: tidak ada satu pun baris log yang menyebut ada masalah.

**Diagnosis.** Dilakukan dengan membungkus `transport.close()` dan
`transport.write()` untuk mencetak stack trace tanpa filter. Hasilnya:

```
transport.close peer=('127.0.0.1', 58110)
  File "server/chat_server.py", line 611, in serve_forever
    await self._server.serve_forever()
  File "asyncio/base_events.py", line 383, in serve_forever
    self.close_clients()
  File "asyncio/base_events.py", line 356, in close_clients
    transport.close()
```

dan tepat setelahnya:

```
transport.write on closing transport peer=('127.0.0.1', 58110) closing=True conn_lost=1
  File "server/chat_server.py", line 554, in _writer_loop
    await self.channel.send_frame(payload)
  File "transport/channel.py", line 156, in send_frame
    self._writer.write(frame)
```

Akar masalahnya ada di Python 3.14. Implementasi `asyncio.Server.serve_forever`
menjalankan tiga hal saat task-nya dibatalkan:

```python
except exceptions.CancelledError:
    try:
        self.close()
        self.close_clients()          # <- menutup transport SETIAP klien
        await self.wait_closed()
    finally:
        raise
```

`close_clients()` menutup socket setiap klien yang sedang terhubung. Pada jalur
nyata, `server/main.py` menghentikan accept loop dengan `serve.cancel()`
**sebelum** `shutdown()` dipanggil. Urutannya karena itu menjadi terbalik:
transport klien ditutup lebih dulu, baru `DISCONNECT` dimasukkan ke queue. Writer
mengambil farewell itu, mencoba menulis ke transport yang sudah `closing`, dan
`send_frame` melempar `ConnectionClosedError` yang ditangkap `_writer_loop`
sebagai `return` normal tanpa log apa pun. Farewell hilang tanpa jejak.

Ini juga menjelaskan mengapa `0 client(s) connected` muncul: penutupan transport
yang terlalu dini memicu teardown koneksi, yang menghapus klien itu dari registry
sebelum `shutdown()` sempat membacanya.

**Mengapa unit test lama tidak menangkapnya.** Test yang ada memanggil
`server.shutdown()` langsung, tanpa membatalkan task accept terlebih dulu. Jalur
itu melewati `serve_forever()` sama sekali, sehingga bug-nya tidak pernah
tersentuh. Bug ini hanya muncul pada urutan yang dipakai produksi.

**Perbaikan.** `ChatServer.serve_forever()` tidak lagi memakai
`asyncio.Server.serve_forever()`. Ia memanggil `start_serving()` langsung, lalu
parkir di `asyncio.Event().wait()` sampai task-nya dibatalkan:

```python
await self._server.start_serving()
await asyncio.Event().wait()
```

Dengan begitu tidak ada yang menutup socket klien selain `shutdown()`, sehingga
urutan queue-farewell-dulu-baru-tutup tetap utuh. Alasan lengkapnya ditulis di
docstring metode itu dan di [ARSITEKTUR.md](ARSITEKTUR.md) bagian 5.3.

**Verifikasi perbaikan.** Regresi dikunci oleh test baru,
`test_shutdown_reaches_clients_after_the_accept_loop_is_cancelled`, yang
menjalankan urutan produksi: batalkan task accept, baru panggil `shutdown()`.
Test itu sudah dibuktikan gagal pada kode lama dengan gejala yang persis sama:

```
transport.errors.ConnectionClosedError: connection closed by peer
```

dan lulus pada kode baru. Setelah perbaikan, kedua jalur shutdown mengirim
`DISCONNECT`, dan log menulis `1 client(s) connected` seperti seharusnya.

### 9.2 Dokumen yang disebut README belum ada

**Gejala.** README menautkan `docs/PANDUAN_PENGGUNAAN.md` dan
`docs/LAPORAN_PENGUJIAN.md`, tetapi kedua berkas itu belum ada.

**Dampak.** Dua dari enam deliverable yang diminta belum terpenuhi, dan tautan di
README menuju berkas yang tidak ada.

**Perbaikan.** Kedua dokumen ditulis. Laporan ini adalah salah satunya.

### 9.3 Ringkasan temuan lain

| Temuan | Sifat | Tindakan |
| --- | --- | --- |
| Farewell hilang pada jalur `Ctrl+C` | Bug nyata | Diperbaiki, dikunci dengan test regresi |
| Dua dokumen yang ditautkan README belum ada | Kelengkapan | Ditulis |
| Jejak lapisan inbound berhenti di L6 | Disengaja | Dibiarkan, dijelaskan di README |
| Empat lapisan saja yang muncul di visualizer | Disengaja | Dibiarkan, dijelaskan di [OSI.md](OSI.md) |

Baris ketiga dan keempat bukan bug. L7 di sisi penerima dikerjakan oleh pemakai
stack, bukan oleh `SessionCore`, jadi tidak ada yang bisa dilaporkan tanpa
mengarang data. L3 sampai L1 dipegang sistem operasi dan kartu jaringan, jadi
yang bisa ditunjukkan hanyalah tangkapan paket.

---

## 10. Batasan pengujian

Ditulis terbuka supaya tidak ada yang mengira cakupannya lebih luas dari yang
sebenarnya.

- **Semua pengujian berjalan di loopback.** Tidak ada latensi nyata, kehilangan
  paket, atau fragmentasi. Perilaku protokol di jaringan yang buruk belum diuji.
- **Tidak ada pengujian dengan `tc netem` atau sejenisnya.** Simulasi jaringan
  lambat dan paket hilang tidak dilakukan.
- **Beban puncak yang diuji adalah 50 klien.** Angka itu cukup untuk menunjukkan
  tidak ada klien yang tertinggal, tetapi bukan uji ketahanan pada beban tinggi.
- **Pelepasan nickname bergantung pada heartbeat.** Koneksi yang mati tanpa
  mengirim apa pun baru dilepas setelah sekitar tiga kali interval heartbeat,
  yaitu sekitar 45 detik dengan pengaturan bawaan. Yang diuji di
  [bagian 7.2](#72-nickname-dilepas-saat-koneksi-putus) adalah jalur cepat, yaitu
  koneksi yang benar-benar terdeteksi putus.
- **Tidak ada pengujian lintas mesin.** Pengujian dengan server dan klien di
  komputer yang berbeda belum dilakukan.
- **Verifikasi visual terbatas pada dua halaman.** Halaman `/` dan `/visualizer`
  diperiksa; tema terang dan gelap sama-sama diperiksa kontrasnya, tetapi tidak
  diuji di berbagai ukuran layar secara menyeluruh.
