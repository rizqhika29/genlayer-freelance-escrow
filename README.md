# FreelanceEscrow — Intelligent Contract untuk GenLayer

[![GenVM CI](https://github.com/rizqhika29/genlayer-freelance-escrow/actions/workflows/ci.yml/badge.svg)](https://github.com/rizqhika29/genlayer-freelance-escrow/actions/workflows/ci.yml)

Escrow pekerjaan freelance per milestone. Keputusan "hasil kerja ini memenuhi
kriteria atau tidak" tidak diambil manusia, tapi oleh validator GenLayer: kontrak
mengambil halaman hasil kerja dari web, meminta LLM menilainya terhadap kriteria,
dan setiap validator menjalankan ulang blok yang sama lalu membandingkan
keputusannya.

```py
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
```

## Alur

1. Klien deploy kontrak dengan `job_title`, `job_spec`, dan alamat freelancer.
2. Klien menambah milestone: `add_milestone(title, criteria, amount)`.
3. Klien mendanai milestone: `fund_milestone(index)` sambil mengirim GEN sebesar
   `amount` (metode payable, nominal harus tepat).
4. Freelancer mengirim hasil kerja: `submit_deliverable(index, url, note)`.
5. Klien atau freelancer memanggil `judge_milestone(index)`.
6. Approve → nominal dikreditkan ke saldo freelancer di dalam kontrak.
   Reject → freelancer boleh revisi (maksimal 3 percobaan). Setelah percobaan
   terakhir gagal, klien memakai `refund_milestone(index)`.
7. Penerima memanggil `withdraw()` untuk menarik saldo GEN-nya.

Pembayaran bersifat *pull*: `judge_milestone` dan `refund_milestone` hanya
mencatat saldo, `withdraw()` yang mengirim GEN. Transfer yang gagal bisa diulang
dan escrow tidak pernah tertinggal setengah jadi.

## Bagaimana konsensusnya bekerja

```py
def leader_fn():
    rendered = gl.nondet.web.render(deliverable_url, mode="html")
    raw = gl.nondet.exec_prompt(prompt, response_format="json")
    return _parse_judgement(raw)          # {"verdict", "score", "reason"}

def validator_fn(leaders_res) -> bool:
    own = leader_fn()                      # validator menjalankan ulang sendiri
    return (leader.get("verdict") == own.get("verdict")
            and abs(leader.score - own.score) <= 20)
```

- **Dibandingkan:** `verdict` harus sama persis, `score` toleransi 20 poin. Teks
  `reason` tidak dibandingkan karena dua kali jalan LLM pasti beda kata.
- **Tidak boleh di dalam blok non-deterministik:** menulis storage, memanggil
  kontrak lain, atau emit pesan. Semua nilai disalin ke variabel lokal dulu,
  karena storage tidak dapat diakses dari `gl.nondet.*`.
- **Error leader:** `_handle_leader_error` membedakan error deterministik (harus
  sama), error sementara `[TRANSIENT]` (dua-duanya gagal = setuju), dan error LLM
  `[LLM_ERROR]` (selalu tidak setuju supaya leader diganti).
- **Anti prompt injection:** isi halaman dan catatan freelancer dimasukkan ke
  dalam pagar `<<<DELIVERABLE ... DELIVERABLE>>>` dengan aturan eksplisit bahwa
  isinya data, bukan instruksi.

## API

Tulis (`write`):

- `add_milestone(title: str, criteria: str, amount: u256) -> u32` — hanya klien.
- `fund_milestone(index: u32) -> None` — payable, harus mengirim tepat `amount`.
- `submit_deliverable(index: u32, deliverable_url: str, note: str) -> None` — hanya freelancer.
- `judge_milestone(index: u32) -> str` — hanya klien/freelancer; hasil
  `"approved"`, `"revision_required"`, atau `"failed"`.
- `refund_milestone(index: u32) -> None` — hanya klien, untuk milestone `failed`.
- `withdraw() -> u256` — mengirim seluruh saldo GEN yang menjadi hak pemanggil.

Baca (`view`):

- `get_client()`, `get_freelancer()`, `get_job_title()`, `get_job_spec()`
- `get_funded_total()`, `get_released_total()`, `get_withdrawn_total()`, `get_balance()`
- `get_my_owed()`, `get_owed(who)` — saldo siap tarik
- `get_milestone_count()`
- `get_milestone_title/criteria/amount/state/deliverable_url/note/attempts/score/reason(index)`
- `get_milestone_attempts_left(index)`

State milestone: `draft` → `funded` → `submitted` → (`approved` |
`revision_required` → … | `failed` → `refunded`).

## Struktur repo

```
contracts/freelance_escrow.py     kontrak
tests/direct/                     test direct mode (mock web + LLM)
.github/workflows/ci.yml          lint + test di GitHub Actions
requirements.txt                  genlayer-py, genlayer-test, genvm-linter
```

## Lokal

```bash
pip install -r requirements.txt
genvm-lint check contracts/freelance_escrow.py
pytest tests/direct -v
```

Test direct mode menjalankan kontrak langsung di Python tanpa Docker dan tanpa
jaringan (17 test, sekitar 1 detik):

- state machine milestone dan aturan akses (klien / freelancer / orang luar);
- funding dengan nominal tepat;
- tiga penolakan sampai `failed`, lalu refund ke klien;
- approval yang mengkreditkan saldo freelancer;
- validator yang setuju dan yang menolak leader.

Cheatcode: `direct_vm.sender`, `direct_vm.value`, `direct_vm.mock_web`,
`direct_vm.mock_llm`, `direct_vm.expect_revert`, `direct_vm.run_validator()`.

Satu test `withdraw` di-skip: direct mode tidak punya lapisan chain, jadi saldo
kontrak dan pesan eksternal (`emit_transfer`) tidak disimulasikan. Lompatan
transfer itu hanya bisa diuji di Studio atau testnet Bradbury.

## Deploy

**Studio** — buka [studio.genlayer.com](https://studio.genlayer.com), tempel isi
kontrak, deploy dengan `job_title`, `job_spec`, dan alamat freelancer (hex).

**CLI** — `genlayer init && genlayer up` untuk Studio lokal, atau pakai `gltest`
untuk test integrasi.

## Catatan

- Halaman hasil kerja diambil dengan `gl.nondet.web.render(url, mode="html")` dan
  dipotong 12.000 karakter. Situs dengan JavaScript berat bisa terbaca tidak
  lengkap; arahkan ke halaman statis, API JSON, atau raw file.
- Pengiriman GEN ke EOA memakai `@gl.evm.contract_interface` + `emit_transfer`
  (hanya jalan saat finalized).
- Nilai dalam wei (1 GEN = 10^18 wei).
- Deploy ulang menghasilkan alamat dan state baru; kode yang berubah tidak
  mengubah kontrak yang sudah ter-deploy.
