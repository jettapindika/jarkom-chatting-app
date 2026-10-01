# Desain UI Dark lounge

Disetujui pengguna untuk langsung diimplementasikan pada 1 Oktober 2026.

## Tujuan dan batas

Mengubah klien web menjadi antarmuka messaging yang fokus pada percakapan. Hanya `web/` dan dokumentasi UI; backend Python, protokol, dan kontrak WebSocket tidak berubah. Tidak menambah dependency, room, channel, fake conversation, unread count, atau delivery receipt.

## Arah visual

Dark lounge: charcoal hangat, panel solid, teks krem, amber untuk aksi utama dan pesan sendiri. Tema terang tetap tersedia. ENERGY 2 / RHYTHM 2 / MOTION 1. Font sistem sans untuk percakapan; monospace untuk perintah dan byte. Radius 8/14/20 px membedakan kontrol, bubble, dan shell. Garis pemisah menggantikan bayangan berulang. Keempat warna L7/L6/L5/L4 dipertahankan sebagai encoding data dan diperiksa AA di kedua tema.

## Struktur

Header kompak berisi nama produk, dua navigasi yang sudah ada, dan theme toggle. Desktop memakai roster nyata di kiri, chat di tengah, panduan perintah di kanan. Tidak ada daftar conversation buatan. Pada tablet, panduan turun ke bawah. Pada ponsel, chat di atas dengan daftar user dan panduan di bawah; input tetap dapat dijangkau. Form nickname berada pada sidebar dan tidak membutakan ruang chat saat sudah terhubung.

## Transcript

Satu timeline frontend dengan sequence lokal monotonik, satu batas buffer, dan key stabil. Bubble berasal dari envelope BROADCAST/PRIVATE, bukan parsing teks CLI. Pesan sendiri ditentukan oleh sender terhadap nickname saat diterima; PRIVATE keluar membawa payload.to, PRIVATE masuk tidak. Teks, sender dan timestamp dari server. Notis USER_JOIN/LEAVE/LIST/NICK_OK/ERROR/DISCONNECT berasal dari envelope. Hanya output ActionKind.SHOW lokal (/help, perintah tak dikenal, nickname invalid) yang memakai line fallback; pasangan line+message remote tidak ditampilkan dua kali. Timeline menjaga urutan antar-output lokal, notis, dan chat walau buffer penuh. Komposer tetap mengirim melalui bridge.send dengan history Up/Down.

## Interaksi dan status

Tidak menambah fitur backend. Semua kontrol mempunyai aksi nyata. Roster memakai inisial nickname nyata sebagai penanda teks, bukan foto rekaan. Form, empty state, connecting, disconnected, dan error tetap jelas. Keyboard focus terlihat; target sentuh minimal 44 px. Scroll otomatis mengikuti pesan baru hanya jika pembaca berada dekat akhir, bukan menarik pembaca dari pesan lama.

## Verifikasi

`npm run typecheck`, `npm run build`, dan browser nyata melawan server/bridge Python tanpa modifikasi. Tiga client: broadcast, PRIVATE dua arah, /help, /list, nickname duplikat, /nick, perintah salah, pesan besar, PRIVATE ke user tidak ada, disconnect. Periksa overflow, focus, mobile/tablet/desktop, kedua tema, navigasi visualizer, payload byte dan clear traces. Tidak menambah test runner atau test wiring.