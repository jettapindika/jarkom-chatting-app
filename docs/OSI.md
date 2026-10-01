# Pemetaan Lapisan OSI

Dokumen ini memetakan implementasi chat ini ke model OSI: lapisan mana yang
ditulis sendiri di dalam repositori, lapisan mana yang diserahkan ke sistem
operasi, dan bagaimana keduanya diamati. Pembaca yang dituju adalah siapa pun
yang perlu menjelaskan atau memverifikasi bahwa proyek ini benar-benar berlapis —
termasuk saat demo dan saat menjawab pertanyaan penguji. Bagian Wireshark di
dokumen ini bisa diikuti langkah demi langkah sambil aplikasi berjalan.

---

## 1. Ringkasan

Proyek ini mengimplementasikan **L7, L6, L5, dan L4** dalam kode Python. Setiap
lapisan adalah paket tersendiri, dan `trace/` mencatat setiap PDU yang melewatinya.

**L3 sampai L1 tidak diimplementasikan di kode, dan tidak dipalsukan.** Tidak ada
paket `network/`, `datalink/`, atau `physical/` di repositori. Ketiga lapisan itu
nyata — ia adalah stack TCP/IP milik sistem operasi — dan diamati lewat Wireshark.
Alasan keputusan ini ada di bagian 5.

---

## 2. Peta lapisan

| Lapisan | Nama | Diimplementasikan di | PDU yang dicatat | Berkas kunci |
| --- | --- | --- | --- | --- |
| 7 | Application | ya | `data` | `app/`, `server/`, `client/`, `bridge/` |
| 6 | Presentation | ya | `message` | `presentation/` |
| 5 | Session | ya | `message` | `session/` |
| 4 | Transport | ya | `segment` | `transport/` |
| 3 | Network | tidak, diserahkan ke OS | — | — |
| 2 | Data Link | tidak, diserahkan ke OS | — | — |
| 1 | Physical | tidak, diserahkan ke OS | — | — |

Nama dan nomor ini bukan konvensi dokumentasi, melainkan enum yang benar-benar
ada di kode: `trace/events.py` mendefinisikan `Layer.APPLICATION = 7`,
`Layer.PRESENTATION = 6`, `Layer.SESSION = 5`, dan `Layer.TRANSPORT = 4`.
Pemetaan PDU di atas juga berasal dari sana lewat `LAYER_PDU_TYPES`, dengan nama
lapisan di `LAYER_NAMES`.

Satu-satunya lapisan yang punya dua implementasi konkret adalah L4, karena server
dan klien memang berbeda: `AsyncTcpChannel` (asyncio streams) untuk server, dan
`BlockingTcpChannel` (socket biasa) untuk klien CLI, bridge, dan test. Keduanya
mengekspos empat operasi yang sama, yaitu antarmuka yang di spesifikasi disebut
`Transport.send_frame(bytes)`.

---

## 3. Tanggung jawab setiap lapisan

### L7 — Application

**Melakukan:** memahami maksud user dan mengubahnya menjadi satu pesan protokol,
lalu merender pesan masuk kembali menjadi teks. Di sinilah `parse_input` mengubah
`"/msg sari halo"` menjadi `Command(kind=PRIVATE, argument="sari", text="halo")`,
dan `plan` mengubahnya menjadi `Action` berisi envelope siap kirim.

**Tidak melakukan:** tidak menyentuh socket, tidak mem-parse JSON, tidak tahu
bagaimana pesan diframing. `app/chat_logic.py` bahkan tidak mengimpor apa pun dari
`transport/`.

**Bukti:** `app/commands.py`, `app/chat_logic.py`, dan handler di
`server/chat_server.py` (`_on_broadcast`, `_on_private`, `_on_user_list`,
`_on_nick`).

### L6 — Presentation

**Melakukan:** serialisasi dan deserialisasi. `encode` memvalidasi envelope lalu
mengubahnya menjadi byte JSON UTF-8; `decode` melakukan sebaliknya. Modul ini juga
yang menegakkan aturan bentuk pesan: field wajib `("type", "sender", "payload",
"timestamp")`, penolakan field asing, batas panjang nickname 1–24 karakter, dan
batas panjang teks 4096 karakter.

**Tidak melakukan:** tidak tahu apa itu koneksi, tidak menangani urutan pesan,
tidak menambahkan header apa pun ke byte yang dihasilkannya.

**Bukti:** `presentation/codec.py`, `presentation/schema.py`. Pilihan
`ensure_ascii=False` dan tanpa `indent` didokumentasikan di docstring `codec.py`:
UTF-8 adalah encodingnya, jadi karakter non-ASCII dikirim apa adanya, dan payload
tetap ringkas sekaligus enak dibaca di Wireshark.

### L5 — Session

**Melakukan:** empat hal, tidak lebih. **Identitas** — session id yang diberikan
server saat handshake dan ikut di header setiap PDU berikutnya. **Urutan** —
counter monotonik per sesi, supaya penerima bisa membedakan PDU yang terduplikasi
dari yang baru. **State** — mesin status `SessionState` yang menentukan apa yang
boleh dikirim kapan. **Liveness** — `HeartbeatMonitor` yang menentukan kapan peer
yang diam dianggap hilang.

**Tidak melakukan:** tidak memiliki socket. Transformasinya mengubah pesan menjadi
payload transport dan sebaliknya; melakukan I/O adalah tugas driver
(`ClientSession` untuk CLI dan bridge, driver async untuk server). Karena socket
dikeluarkan dari lapisan ini, aturan sequencing dan envelope hanya punya satu
implementasi dan bisa diuji tanpa jaringan.

**Bukti:** `session/session.py`, `session/envelope.py`, `session/heartbeat.py`,
`session/session_state.py`.

### L4 — Transport

**Melakukan:** memindahkan byte dan memotongnya kembali menjadi frame. Ia
menambahkan length prefix 4 byte big-endian, menyangga byte yang datang sebagian
(`FrameDecoder`), dan melaporkan kegagalan socket. `MAX_FRAME_SIZE = 64 * 1024`.

**Tidak melakukan:** tidak mem-parse JSON, tidak tahu apa itu nickname, tidak
pernah memeriksa isi payload. Layer ini sengaja "bodoh": ia membingkai, memindahkan
byte, dan melaporkan error.

**Bukti:** `transport/framing.py`, `transport/channel.py`, `transport/errors.py`.

### L3–L1

Ditangani oleh kernel. Lihat bagian 5 dan 6.

---

## 4. Header 24 byte sebagai bukti pelapisan

Klaim "setiap lapisan menambahkan header sendiri" paling mudah dibuktikan dengan
melihat satu frame di kabel.

```
+----------+------------------------+------------------------------+
| 4 byte   | 24 byte                | sisa                         |
| prefix   | header sesi            | JSON UTF-8                   |
| L4       | L5                     | L6                           |
+----------+------------------------+------------------------------+
offset 0   offset 4                 offset 28
```

Header L5 berisi session id 16 byte (UUID mentah) di offset 4, lalu sequence
number uint64 big-endian 8 byte di offset 20. Yang penting untuk pembuktian: JSON
selalu dimulai di **offset 28**, dan `7b 22` — byte untuk `{"` — tidak akan
muncul sebelum itu. Pembaca yang melihat hex view akan menemukan 24 byte pertama
setelah prefix bukan JSON, melainkan biner milik lapisan sesi.

Alasannya tertulis di `session/envelope.py`: kalau header sesi juga JSON, frame
akan menjadi satu gumpalan JSON yang tidak terbedakan dan klaim pelapisan tidak
bisa diverifikasi dengan melihat byte. Alasan keduanya soal biaya: sequence number
dibaca pada setiap frame, dan integer pada offset tetap lebih murah didekode
daripada satu parse JSON.

---

## 5. Mengapa L3–L1 tidak dipalsukan

Membuat paket `network/` atau `datalink/` yang menghasilkan header IP dan Ethernet
palsu akan menghasilkan kode yang tidak mengirim apa pun dan tidak membuktikan apa
pun. Header IP yang dibuat sendiri tidak akan pernah sampai ke kabel, karena kernel
yang menuliskannya, bukan program Python.

Yang benar-benar terjadi saat aplikasi ini berjalan sudah cukup: kernel menambahkan
header TCP, lalu header IP, lalu header Ethernet, dan mengirimkannya lewat
antarmuka jaringan. Semua itu bisa direkam dan dibaca dengan Wireshark tanpa satu
baris kode tambahan. Karena itu L3–L1 diserahkan ke sistem operasi dan
didemonstrasikan lewat capture, bukan disimulasikan.

Docstring kelas `PduType` di `trace/events.py` menyatakan hal yang sama dari sisi
observability: "Layers 3-1 are handled by the OS and never emit events; see
`docs/OSI.md` for the Wireshark evidence that covers them." Karena itu pula
`Layer` hanya memuat 7, 6, 5, dan 4 — tidak ada nilai palsu untuk lapisan yang
tidak menghasilkan event.

---

## 6. Panduan Wireshark

Bagian ini bisa diikuti sambil aplikasi berjalan. Port default chat adalah **9009**.

### Langkah 0 — siapkan dua terminal

```bash
# Terminal 1
python run_server.py --host 0.0.0.0 --port 9009
```

```bash
# Terminal 2 (nanti, setelah capture dimulai)
python run_client.py --host 127.0.0.1 --port 9009 --nick budi
```

### Langkah 1 — pilih antarmuka dan mulai capture

Buka Wireshark, pilih antarmuka loopback (`lo0` di macOS, `Loopback` di Windows,
`lo` di Linux), lalu isi **capture filter** dengan:

```
tcp port 9009
```

Capture filter bekerja di kernel dan membuang trafik lain sebelum terekam, jadi
tidak ada gunanya memakai filter ini jika port server diubah — sesuaikan angkanya.

### Langkah 2 — jalankan klien

Mulai capture, baru kemudian jalankan `run_client.py`. Setiap frame yang
dipertukarkan akan muncul. Untuk mempersempit tampilan, isi **display filter**:

```
tcp.port == 9009
```

### Langkah 3 — temukan three-way handshake

Three-way handshake TCP terjadi tepat saat klien menjalankan
`socket.create_connection`. Filter:

```
tcp.flags.syn == 1
```

Yang akan terlihat, berurutan:

1. `SYN` dari klien ke server — klien memilih port sumber acak dan meminta koneksi.
2. `SYN, ACK` dari server — server menyetujui dan memilih nomor urutnya sendiri.
3. `ACK` dari klien — koneksi resmi terbuka.

Paket-paket ini murni L4: isinya belum ada satu byte pun dari protokol chat kita.

### Langkah 4 — baca pesan aplikasi

Klik kanan pada salah satu paket data, pilih **Follow > TCP Stream**. Jendela yang
muncul menampilkan payload yang digabung ulang dalam urutan byte, dan di situlah
JSON protokol kita terlihat: `{"type":"CONNECT","sender":"budi",...}` dan
seterusnya. Ini cara tercepat membaca percakapan tanpa mengurus batas segmen TCP.

### Langkah 5 — lihat length prefix di hex pane

Kembali ke daftar paket, klik satu paket yang membawa pesan chat, lalu buka pane
**Packet Bytes** di bagian bawah. Byte-byte pertama setelah header TCP adalah
milik protokol kita:

```
00 00 00 XX   <- 4 byte length prefix (L4)
.. .. .. ..   <- 16 byte session id (L5)
.. .. .. ..   <- 8 byte sequence number (L5)
7b 22 ...     <- awal JSON: {"type": ... (L6)
```

Perhatikan bahwa `7b 22` baru muncul di offset 28 relatif terhadap awal payload
TCP. Jika Anda memakai `tools/frame_dump.py`, skrip itu mencetak pemisahan yang
sama secara eksplisit, sehingga byte di Wireshark bisa dicocokkan dengan byte yang
dihitung aplikasi.

### Langkah 6 — cocokkan dengan alat kita

Di terminal ketiga:

```bash
python tools/frame_dump.py --port 9009 --nick dumper
```

Skrip ini terhubung, mengirim `CONNECT`, lalu mengirim `BROADCAST` dengan
`sequence=2`, dan mencetak setiap frame dalam dua arah dengan tiga header dipisah:

```
L4 length prefix : N bytes (00 00 00 xx)
L5 session id    : <hex 16 byte>
L5 sequence      : <angka>
L6 body          : N bytes of JSON
```

Setelah itu ia mencetak hex dump 96 byte pertama. Bandingkan angka panjang dan
byte awalnya dengan yang ditampilkan Wireshark pada paket yang bersesuaian; kalau
sama, berarti yang kita hitung di aplikasi memang yang dikirim ke kabel.

### Pemetaan tampilan Wireshark ke lapisan OSI

| Yang terlihat di Wireshark | Lapisan |
| --- | --- |
| Ethernet II: MAC tujuan, MAC sumber, EtherType | L2 Data Link |
| Internet Protocol Version 4: alamat sumber, alamat tujuan, TTL, protokol | L3 Network |
| Transmission Control Protocol: port sumber, port tujuan, nomor urut, flags | L4 Transport |
| SYN / SYN, ACK / ACK saat koneksi dibuka | L4 Transport |
| 4 byte pertama setelah header TCP: length prefix | L4 (buatan kita) |
| 24 byte berikutnya: session id dan sequence | L5 (buatan kita) |
| Sisa payload: `{"type":"BROADCAST", ...}` | L6 (buatan kita) |
| Arti pesan itu bagi user | L7 (buatan kita) |

Dua baris terakhir dari tabel di atas adalah intinya: Wireshark hanya bisa
menampilkan byte, sedangkan pemisahan antara L4, L5, dan L6 pada payload itu
sepenuhnya ditentukan oleh kode kita — dan `tools/frame_dump.py` adalah cara
membuktikan bahwa pemisahan itu memang seperti yang diklaim.

---

## 7. Batas yang jujur

- **L3–L1 tidak diimplementasikan di kode.** Yang ada hanya pengamatan terhadap
  apa yang dilakukan kernel. Tidak ada header IP atau Ethernet yang dibuat oleh
  program ini.
- **Tidak ada trace event untuk L3–L1.** `Layer` hanya memuat 7, 6, 5, dan 4;
  tidak ada nilai palsu yang dibuat untuk lapisan yang tidak menghasilkan event.
- **Capture Wireshark memerlukan hak akses khusus.** Di macOS dan Linux, membaca
  antarmuka jaringan biasanya butuh izin administrator, dan pada loopback trafik
  tidak pernah meninggalkan mesin sehingga tidak bisa diamati dari perangkat lain.
- **Analisis TCP mendalam di luar cakupan.** Dokumen ini memakai Wireshark untuk
  menunjukkan pelapisan dan handshake, bukan untuk menganalisis kontrol kongesti,
  retransmisi, atau window scaling.
