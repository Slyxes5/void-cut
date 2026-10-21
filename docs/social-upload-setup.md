# Setup upload YouTube Shorts dan TikTok

Setelah render selesai, klik **Upload ke TikTok / YouTube** pada kartu hasil render,
atau **Upload** di antrean Clip Studio. Isi judul, caption/hashtag, pilih akun serta
platform, periksa preview, lalu konfirmasi upload. Tidak ada posting otomatis hanya
karena render selesai. Setiap klip dipilih terpisah; dukungan awal satu akun per platform.
Tombol tersedia untuk hasil render server yang file-nya masih terverifikasi. Hasil
browser/WASM saat ini tetap diunduh dari browser dan belum masuk alur upload backend.

## 1. Persiapan backend

```powershell
venv\Scripts\python.exe -m pip install -r backend/requirements.txt
venv\Scripts\python.exe backend/setup_social_uploads.py
```

Script menambahkan `SOCIAL_TOKEN_ENCRYPTION_KEY` ke `backend/.env` tanpa menampilkan
kuncinya. Simpan backup kunci secara privat. Token akun dienkripsi menggunakan kunci
ini di `%LOCALAPPDATA%/CheatClipPro/social/accounts/` pada Windows, atau
`~/.local/share/CheatClipPro/social/accounts/` pada Linux/macOS. Jangan menghapus atau
mengganti kunci jika ingin membaca koneksi akun lama. Jangan menaruh client secret
atau token di `VITE_*`, kode frontend, Git, atau chat.

Gunakan satu proses backend. Riwayat upload tersimpan di subfolder `social/jobs`,
dan salinan video sementara di `social/spool` mencegah proses cleanup render
menghapus video yang sedang dikirim. File spool dihapus setelah transfer selesai.

## 2. YouTube Shorts

1. Buka [Google Cloud Console](https://console.cloud.google.com/), buat project, dan
   aktifkan **YouTube Data API v3**.
2. Atur OAuth consent screen di Google Auth Platform. Untuk mode Testing, tambahkan
   email akun Anda sebagai test user. Gunakan scope `youtube.upload` dan
   `youtube.readonly` (untuk menampilkan channel tujuan).
3. Buat OAuth Client ID jenis **Web application**. Tambahkan redirect URI yang
   sesuai origin aplikasi, misalnya:
   `http://localhost:5173/social/oauth/youtube/callback`.
4. Isi `backend/.env`:

```dotenv
YOUTUBE_CLIENT_ID=YOUR_GOOGLE_WEB_CLIENT_ID
YOUTUBE_CLIENT_SECRET=YOUR_GOOGLE_CLIENT_SECRET
YOUTUBE_REDIRECT_URI=http://localhost:5173/social/oauth/youtube/callback
```

5. Restart backend, buka aplikasi dari origin yang sama (localhost dan 127.0.0.1
   berbeda), klik **Hubungkan akun**, lalu login pada popup. Tutup popup setelah
   sukses dan klik **Perbarui akun**. Tidak perlu mengunduh ulang video.

YouTube mengklasifikasikan video persegi/vertikal dengan durasi maksimal 3 menit
sebagai Shorts. Backend memeriksa file MP4 hasil render memakai FFprobe sebelum
upload. `#Shorts` bukan pengganti syarat ukuran/durasi. Pilihan Private/Unlisted/Public
dan deklarasi Made for Kids dikirim ke API; pembatasan project/akun tetap berlaku.
Status selesai berarti video diterima YouTube; pemrosesan dan penentuan distribusi
tetap berlangsung di YouTube.

Referensi: [videos.insert](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert),
[resumable uploads](https://developers.google.com/youtube/data/guides/using_resumable_upload_protocol),
[OAuth web apps](https://developers.google.com/identity/protocols/oauth2/web-server),
[ketentuan Shorts](https://support.google.com/youtube/answer/15424877).

## 3. TikTok: kirim draf ke inbox

1. Daftarkan aplikasi di [TikTok for Developers](https://developers.tiktok.com/).
2. Tambahkan **Login Kit** dan **Content Posting API**. Ajukan scope
   `user.info.basic` dan `video.upload`. Persetujuan scope dari TikTok diperlukan;
   menambahkan konfigurasi lokal saja tidak memberi akses API.
3. TikTok Login Kit Web memerlukan redirect URI HTTPS. Gunakan domain HTTPS
   aplikasi Anda yang terdaftar pada TikTok, misalnya
   `https://clip.example.com/social/oauth/tiktok/callback`. Daftarkan domain/URL
   dan test user sesuai pengaturan developer yang tersedia untuk aplikasi Anda.
4. Isi `backend/.env`:

```dotenv
TIKTOK_CLIENT_ID=YOUR_TIKTOK_CLIENT_KEY
TIKTOK_CLIENT_SECRET=YOUR_TIKTOK_CLIENT_SECRET
TIKTOK_REDIRECT_URI=https://clip.example.com/social/oauth/tiktok/callback
TIKTOK_DIRECT_POST_ENABLED=false
TIKTOK_DIRECT_POST_AUDITED=false
```

5. Pastikan reverse proxy meneruskan `/api/` **dan** `/social/oauth/` ke backend.
   Vite sudah mem-proxy keduanya pada mode development. Jika memakai HTTPS remote,
   izinkan origin itu pada konfigurasi login aplikasi yang sudah ada
   (`CHEAT_CLIP_ALLOWED_ORIGINS` / `ALLOWED_ORIGINS`). Jangan membuka API tanpa login.
6. Restart backend, buka aplikasi dari origin HTTPS yang sama dengan redirect URI,
   hubungkan akun TikTok, dan klik **Perbarui akun**.

**Kirim ke draf / inbox TikTok** mentransfer video melalui FILE_UPLOAD. Setelah
TikTok mengonfirmasi `SEND_TO_USER_INBOX`, buka notifikasi inbox di aplikasi TikTok
untuk menyelesaikan posting. Endpoint draf **video** tidak menerima judul/caption:
gunakan **Salin judul + caption** di Void Cut lalu tempel di TikTok.

Referensi: [Login Kit Web](https://developers.tiktok.com/doc/login-kit-web),
[upload draf](https://developers.tiktok.com/doc/content-posting-api-get-started-upload-content),
[transfer media](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide).

## 4. TikTok Direct Post (opsional)

Aktifkan hanya untuk aplikasi yang memenuhi ketentuan dan memiliki scope
`video.publish`. TikTok secara eksplisit tidak menerima tool terbatas untuk
mengupload ke akun sendiri/tim sebagai intended use Direct Post. Ini bukan jalur
untuk melewati review platform. Untuk aplikasi yang memenuhi syarat:

```dotenv
TIKTOK_DIRECT_POST_ENABLED=true
TIKTOK_DIRECT_POST_AUDITED=false
```

Hubungkan ulang TikTok setelah mengubah scope. Untuk client belum audited, pilihan
dibatasi ke **Only me**; TikTok juga mensyaratkan akun private pada saat posting.
Set `TIKTOK_DIRECT_POST_AUDITED=true` hanya setelah audit TikTok sungguh disetujui.
Flag lokal tidak dapat mencabut pembatasan pada server TikTok.

Form Direct Post mengambil creator info terbaru, menampilkan nickname, membatasi
durasi dan privasi sesuai akun, serta menonaktifkan interaksi yang tidak diizinkan.
Privasi harus dipilih sendiri, interaksi default mati, dan tersedia pengungkapan
konten komersial/AI serta persetujuan musik. Judul dan caption digabung menjadi
caption TikTok, maksimal 2200 unit UTF-16.

Referensi: [Direct Post](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post),
[creator info](https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info),
[sharing guidelines dan intended use](https://developers.tiktok.com/doc/content-sharing-guidelines).

## Status, percobaan ulang, dan memutuskan akun

- **queued/uploading**: backend sedang mengirim. Form boleh ditutup.
- **processing**: TikTok menerima file, tetapi hasilnya belum final. Klik
  **Periksa status TikTok**, tanpa mengirim file lagi.
- **completed**: platform mengonfirmasi penerimaan/selesai, dengan tautan jika tersedia.
- **draft**: TikTok mengonfirmasi pengiriman ke inbox; belum diposting publik.
- **failed**: permintaan ditolak. Perbaiki penyebabnya, klik **Siapkan percobaan baru**,
  pilih hanya platform yang gagal, lalu konfirmasi kembali.
- **uncertain**: transfer terputus atau backend restart. Periksa YouTube Studio/TikTok
  terlebih dahulu. Aplikasi tidak mengulang posting otomatis untuk status ini.

ID permintaan disimpan sehingga klik ganda atau respons yang hilang tidak membuat
posting baru. Restart tidak melanjutkan transfer secara otomatis. Status per platform
terpisah sehingga kegagalan TikTok tidak mengulang upload YouTube yang berhasil.
Sampai 200 riwayat dipertahankan; arsipkan file riwayat lama secara manual saat
backend berhenti bila batas tercapai. Jangan mengarsipkan receipt uncertain sebelum
memeriksa platform, karena receipt itu mencegah pengiriman ulang yang tidak sengaja.

**Putuskan koneksi lokal** menghapus token terenkripsi di backend. Untuk mencabut
grant pada provider, gunakan [Google connected apps](https://myaccount.google.com/connections)
atau pengaturan TikTok **Security & permissions → Apps and services permissions**.
Tidak ada video yang dihapus dari platform saat memutuskan koneksi.

Implementasi dapat diuji offline memakai HTTP mock. Koneksi akun dan upload sungguhan
baru dapat diuji setelah kredensial developer, HTTPS TikTok, dan izin scope tersedia.
