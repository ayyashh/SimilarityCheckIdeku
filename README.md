# SimilarityCheckIdeku

REST API untuk memeriksa apakah nama ide baru mirip dengan ide yang sudah tercatat. Saat request masuk, API membuat embedding sementara untuk nama ide tersebut, lalu membandingkannya dengan embedding aktif yang sudah tersimpan di SQL Server. Repository ini belum menyediakan proses untuk mengindeks ide lama atau mengisi tabel semantic index.

## Fitur

- Endpoint pemeriksaan kemiripan ide sebelum ide disimpan.
- Embedding multibahasa dengan `paraphrase-multilingual-mpnet-base-v2` dari Sentence Transformers.
- Evaluasi kandidat dengan Gemini, Groq GPT-OSS 20B, atau Groq GPT-OSS 120B melalui endpoint terpisah. Jika evaluasi AI gagal, service mengembalikan skor cosine sebagai fallback.
- Autentikasi endpoint dengan JWT Bearer Token.
- Data ide dan semantic index dibaca dari SQL Server. Ambang kemiripan dan jumlah hasil diatur melalui `.env`.

## Kebutuhan

- Python dan `pip`.
- Microsoft ODBC Driver 18 for SQL Server, dipasang terpisah dari paket Python.
- SQL Server yang dapat dijangkau dan berisi tabel yang digunakan proyek, termasuk `dbo.Ideas` dan `dbo.IdeaSemanticIndex`.
- Koneksi internet saat pertama kali mengunduh model embedding.
- API key Gemini dan/atau Groq untuk mengaktifkan evaluasi pada endpoint masing-masing. Tanpa provider yang dapat digunakan, endpoint mengembalikan skor cosine sebagai fallback.

## Instalasi

Jalankan perintah berikut dari root repository di PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Salin contoh konfigurasi, lalu edit `.env`:

```powershell
Copy-Item .env.example .env
```

Isi `.env` dengan konfigurasi lokal. Jangan commit file `.env`.

| Variabel | Keterangan |
| --- | --- |
| `DB_DRIVER` | Nama driver ODBC; biasanya `ODBC Driver 18 for SQL Server`. |
| `DB_SERVER` | Nama host/instance SQL Server, misalnya `localhost\\SQLEXPRESS`. |
| `DB_DATABASE` | Nama database aplikasi. |
| `DB_UID`, `DB_PASSWORD` | Kredensial SQL Server. Jika keduanya tidak diisi, proyek mencoba Windows Authentication. |
| `GEMINI_API_KEY` | API key Gemini. Diperlukan untuk evaluasi AI; tanpa konfigurasi yang valid hasil memakai fallback cosine. |
| `GEMINI_MODEL` | Nama model Gemini yang digunakan. |
| `GROQ_API_KEY` | API key Groq. Diperlukan oleh endpoint Groq; tanpa konfigurasi yang valid hasil memakai fallback cosine. |
| `GROQ_MODEL` | Nama model Groq; default `openai/gpt-oss-20b`. |
| `GROQ_MODEL_120B` | Nama model Groq pembanding; default `openai/gpt-oss-120b`. |
| `GROQ_MODEL_QWEN_27B` | Nama model Groq Qwen pembanding; default `qwen/qwen3.8-27b`. |
| `SIMILARITY_AI_MAX_CANDIDATES` | Jumlah maksimum kandidat yang dikirim untuk evaluasi AI. |
| `SIMILARITY_REVIEW_THRESHOLD` | Ambang cosine untuk pencarian review umum; default `0.70`. |
| `SIMILARITY_SUBMIT_THRESHOLD` | Ambang cosine untuk pemeriksaan ide sebelum submit; default `0.40`. |
| `SIMILARITY_TOP_K` | Nilai `topK` yang dipakai sebagai default metadata hasil; default `5`. |
| `JWT_SECRET_KEY` | Secret acak yang digunakan untuk menandatangani JWT. Ganti nilai contoh sebelum membuat token. |
| `JWT_ALGORITHM` | Algoritma JWT; contoh konfigurasi menggunakan `HS256`. |
| `JWT_EXPIRE_MINUTES` | Masa berlaku token dalam menit. |
| `JWT_ISSUER`, `JWT_AUDIENCE` | Nilai issuer dan audience yang divalidasi pada token. |

Buat secret JWT acak di PowerShell:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Salin hasilnya ke `JWT_SECRET_KEY` di `.env`.

## Menjalankan API

Dengan virtual environment aktif dan `.env` terisi:

```powershell
python -m uvicorn app.main:app --reload
```

Alamat lokal:

- Dokumentasi interaktif: <http://127.0.0.1:8000/docs>
- Health check: <http://127.0.0.1:8000/health>

Saat pertama kali fitur embedding dipakai, Sentence Transformers mengunduh model yang digunakan proyek. Waktu unduh bergantung pada koneksi internet.

## Autentikasi

Token dibuat secara lokal; API tidak menyediakan endpoint login atau endpoint publik untuk menerbitkan token. Setelah `JWT_SECRET_KEY` dikonfigurasi, jalankan:

```powershell
python generate_api_token.py
```

Untuk memberi nama subject token:

```powershell
python generate_api_token.py --subject nama-client
```

Salin token yang tercetak. Di `/docs`, klik **Authorize** dan masukkan token sebagai Bearer token. Untuk pemanggilan HTTP, kirim header:

```text
Authorization: Bearer <JWT>
```

Endpoint similarity dilindungi autentikasi JWT. `/health` tidak memerlukan token.

## Endpoint

### `POST /api/documents/validate`

Menerima satu file PDF sebagai `multipart/form-data`, mempertahankan teks per halaman, mengekstrak tabel ke baris/kolom, dan menyertakan teks footer per halaman. Ekstraksi memakai PyMuPDF; urutan teks disusun menurut posisi halaman. Jika area footer tidak memiliki teks PDF, sistem mencoba OCR dengan Tesseract. OCR memerlukan instalasi Tesseract dan data bahasa `eng` serta `ind` di komputer yang menjalankan API. Teks hasil ekstraksi lalu dibandingkan dengan struktur Handover dari `scripts/extracted-handover-template.txt` serta kriteria Business Case di validator. Untuk Handover, kelima bagian bernomor I–V diambil dari file template dan wajib ditemukan sesuai urutannya; teks isi yang berubah-ubah tidak perlu sama persis. Respons menyertakan tipe dokumen, skor, alasan, bagian yang cocok/belum ditemukan, teks footer, dan tabel.

Contoh request dari PowerShell:

```powershell
$headers = @{ Authorization = "Bearer <JWT>" }
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/documents/validate" `
  -Method Post `
  -Headers $headers `
  -Form @{ file = Get-Item "C:\docs\handover.pdf" }
```

Respons sukses:

```json
{
  "result": {
    "fileName": "handover.pdf",
    "isValid": true,
    "documentType": "business_case",
    "matchPercentage": 80.0,
    "matchedCount": 4,
    "totalMarkers": 5,
    "reason": "Dokumen teridentifikasi sebagai Business Case. 4/5 parameter utama terpenuhi (80.00%).",
    "matchedMarkers": [
      "Problem / Current Situation",
      "Objective / Target",
      "Proposed Solution / Improvement",
      "Benefit / Impact"
    ],
    "unmatchedMarkers": ["Implementation / Action Plan"]
  },
  "pages": [
    {
      "pageNumber": 2,
      "footerText": "BPI_KPC_KPC_BPIMSE3.06_DOC_TMPi_003 | Page 2 of 4",
      "footerOcrUsed": false,
      "ocrWarning": null
    }
  ],
  "tables": [
    {
      "pageNumber": 2,
      "rows": [
        ["No.", "KPI Proyek", "Plan (Before)", "Actual (After)"],
        ["A.", "Financial Benefit", "100 liter/hari", "90 liter/hari"]
      ]
    }
  ]
}
```

Endpoint dilindungi JWT Bearer Token dan menerima PDF hingga 20 MB. `400` dikembalikan untuk file selain PDF, PDF rusak/terenkripsi, atau PDF tanpa teks yang dapat diekstrak. OCR otomatis saat ini ditujukan untuk footer; OCR seluruh halaman hasil scan belum diaktifkan. Untuk OCR footer, instal Tesseract OCR dan data bahasa `eng` serta `ind` secara terpisah dari paket Python.

Untuk memeriksa hasil ekstraksi secara lokal tanpa memanggil API:

```powershell
python scripts/test_pdf_extraction.py "C:\docs\handover.pdf"
python scripts/test_pdf_extraction.py "C:\docs\handover.pdf" --output extracted.txt --tables-output candidate-tables.json
```

`tables.json` menyimpan struktur header tabel isi dan satu struktur footer yang berulang pada halaman 2 sampai akhir. API membandingkan header tabel; isi baris, judul proyek, nilai metadata, dan nomor halaman tidak dibandingkan sebagai nilai tetap. File `candidate-tables.json` adalah hasil ekstraksi otomatis untuk PDF kandidat. Respons menyertakan `tableValidation` yang menunjukkan tabel yang cocok dan header yang belum ditemukan.

### `POST /api/ideas/similarity/check`

Menggunakan Gemini dengan prompt, validasi, dan pemetaan skor evaluator yang sama dengan endpoint Groq.

### `POST /api/ideas/similarity/check/groq`

Menggunakan Groq dengan model yang ditentukan oleh `GROQ_MODEL` (default `openai/gpt-oss-20b`). Endpoint ini berbagi prompt, validasi keluaran, metodologi evaluasi, dan fallback dengan endpoint Gemini.

### `POST /api/ideas/similarity/check/groq-120b`

Menggunakan Groq dengan model yang ditentukan oleh `GROQ_MODEL_120B` (default `openai/gpt-oss-120b`). Endpoint ini memakai API key Groq yang sama dan metodologi evaluator yang sama, sehingga bisa dibandingkan langsung dengan Groq GPT-OSS 20B.

### `POST /api/ideas/similarity/check/groq-qwen-27b`

Menggunakan Groq dengan model yang ditentukan oleh `GROQ_MODEL_QWEN_27B` (default `qwen/qwen3.8-27b`). Endpoint ini memakai API key Groq yang sama serta prompt, validasi, metodologi evaluasi, dan fallback yang sama dengan endpoint lainnya.

Kedua endpoint menerima body yang sama:

Memeriksa nama ide draft terhadap ide yang memiliki embedding aktif di semantic index. Request hanya menerima nama ide:

```json
{
  "ideaName": "Sistem pemantauan kualitas air berbasis sensor"
}
```

Contoh pemanggilan dari PowerShell setelah server berjalan dan token dibuat:

```powershell
$headers = @{ Authorization = "Bearer <JWT>" }
$body = @{ ideaName = "Sistem pemantauan kualitas air berbasis sensor" } | ConvertTo-Json
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/ideas/similarity/check" `
  -Method Post `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body

# Gunakan endpoint ini untuk membandingkan Groq GPT-OSS 120B
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/ideas/similarity/check/groq-120b" `
  -Method Post `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body

# Gunakan endpoint ini untuk evaluasi dengan Groq
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/ideas/similarity/check/groq" `
  -Method Post `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

Contoh bentuk respons:

```json
{
  "found": true,
  "threshold": 0.4,
  "topK": 5,
  "evaluationMethod": "gemini",
  "returnedCount": 1,
  "results": [
    {
      "ideaId": 123,
      "ideaName": "Pemantauan air kolam menggunakan IoT",
      "description": "Deskripsi ide yang tercatat",
      "ideaSolution": "Solusi yang diusulkan",
      "toDivision": "Nama divisi",
      "similarityPercentage": 76.5,
      "cosineSimilarityPercentage": 68.2,
      "aiVerdict": "RELATED",
      "isOpposite": false,
      "reason": {
        "summary": "Ringkasan alasan kemiripan",
        "matchedFields": ["IdeaName"],
        "highlights": ["pemantauan kualitas air"],
        "evidence": []
      },
      "difference": {
        "queryFocus": ["kualitas air", "sensor"],
        "candidateFocus": ["air kolam", "IoT"],
        "differences": ["Detail fokus solusi berbeda"],
        "sharedPoints": ["Sama-sama memantau kualitas air"]
      }
    }
  ],
  "message": null
}
```

Nilai pada contoh hanya ilustrasi dan mengandaikan semantic index sudah berisi embedding aktif yang sesuai dengan data di `dbo.Ideas`. `evaluationMethod` menunjukkan apakah hasil dievaluasi oleh Gemini (`gemini`), Groq (`groq`), memakai fallback cosine (`cosine_fallback`), atau belum dievaluasi (`not_evaluated`). Skor persentase dan daftar kandidat bergantung pada isi semantic index dan pengaturan database. Jika belum ada embedding aktif yang memenuhi ambang, API mengembalikan `found: false`, `results: []`, dan pesan `No sufficiently similar ideas found.`

Respons error utama:

- `400 Bad Request`: `ideaName` kosong atau hanya berisi spasi.
- `401 Unauthorized`: Bearer token tidak ada, kedaluwarsa, atau tidak valid.
- `500 Internal Server Error`: terjadi kegagalan yang tidak ditangani pada proses pemeriksaan.

## Alur kerja

1. API menerima `ideaName` dan membuat embedding sementara untuk query. Embedding ini tidak disimpan.
2. Sistem membandingkan embedding query dengan embedding aktif yang sudah ada di `dbo.IdeaSemanticIndex` menggunakan cosine similarity.
3. Kandidat yang melewati `SIMILARITY_SUBMIT_THRESHOLD` diproses lebih lanjut. Jumlah kandidat untuk AI dibatasi oleh `SIMILARITY_AI_MAX_CANDIDATES`.
4. Provider yang dipilih oleh endpoint (Gemini atau Groq) mengevaluasi kandidat dan menghasilkan skor/verdict beserta alasan dan perbedaan. Jika evaluasi gagal, service memakai cosine sebagai fallback.
5. Metadata ide diambil dari `dbo.Ideas`, lalu dikembalikan sebagai JSON. Endpoint ini tidak menyimpan ide draft atau membuat record embedding.

## Struktur proyek

```text
app/
  api/            Endpoint FastAPI dan schema request/response
  auth/           Pembuatan, validasi, dan dependency JWT
  core/           Pembacaan konfigurasi dari .env
  repositories/   Akses SQL Server untuk data ide dan semantic index
  services/       Embedding, cosine similarity, evaluator Gemini/Groq, dan orkestrasi pemeriksaan
generate_api_token.py
requirements.txt
```

## Validasi handover terjadwal

Batch membaca lampiran pada `dbo.Ideas.AttachmentFiles`, lalu hanya memilih nama
file yang cocok dengan `*_HandoverDocument_*`. Path relatif terlebih dahulu
diselesaikan terhadap `ATTACHMENT_ROOT`, lalu terhadap direktori aplikasi.
Validator batch saat ini hanya mendukung PDF; format lain dicatat dengan status
`unsupported` dan tidak dicoba ulang. Status `error` dapat dicoba kembali pada
batch berikutnya. Hasil validasi (`valid` atau `invalid`) tidak diproses ulang
selama nama file yang sama tercatat untuk `IdeaId` tersebut.

Jalankan `scripts/migrate_document_validation_batch.sql` pada database sebelum
mengaktifkan scheduler. Atur `ATTACHMENT_ROOT` ke root yang benar-benar memuat
path relatif dalam `AttachmentFiles`, lalu atur konfigurasi berikut:

```env
INDEX_SCHEDULER_ENABLED=true
INDEX_SCHEDULER_HOUR=2
INDEX_SCHEDULER_MINUTE=0
INDEX_SCHEDULER_TIMEZONE=Asia/Makassar
```

Scheduler berjalan di dalam proses FastAPI. Jalankan aplikasi dengan satu worker
(satu instance) agar batch tidak dieksekusi bersamaan oleh beberapa proses.
Riwayat batch disimpan di `dbo.IdeaDocumentValidationHistory`.

## Catatan data

**Indexing belum diimplementasikan dalam repository ini.** Kode hanya membuat embedding sementara untuk nama ide yang sedang diperiksa; kode tidak membuat embedding untuk semua ide lama dan tidak mengisi `dbo.IdeaSemanticIndex`. Karena itu, hasil pemeriksaan yang bermakna memerlukan proses indexing terpisah yang mengisi embedding aktif dan metadata yang cocok di `dbo.Ideas`. Jika tabel semantic index masih kosong, API dapat berjalan tetapi tidak akan menemukan kandidat (`found: false`). Skema database juga harus memiliki kolom-kolom yang digunakan repository di `app/repositories/`.

Threshold dan `TopK` tidak disimpan di database. Nilainya diatur melalui `.env`, dengan default `SIMILARITY_REVIEW_THRESHOLD = 0.70`, `SIMILARITY_SUBMIT_THRESHOLD = 0.40`, dan `SIMILARITY_TOP_K = 5`.
