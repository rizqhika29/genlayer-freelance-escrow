# FreelanceEscrow — Intelligent Contract untuk GenLayer

[![GenVM CI](https://github.com/rizqhika29/genlayer-freelance-escrow/actions/workflows/ci.yml/badge.svg)](https://github.com/rizqhika29/genlayer-freelance-escrow/actions/workflows/ci.yml)

Escrow pekerjaan freelance per milestone. Keputusan "hasil kerja ini memenuhi
kriteria atau tidak" tidak diambil manusia, tapi oleh validator GenLayer: kontrak
mengambil halaman hasil kerja dari web, meminta LLM menilai **setiap kriteria
satu per satu**, dan setiap validator menjalankan ulang blok yang sama lalu
membandingkan hasil penilaian itu.

```py
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
```

## Alur

1. Klien deploy kontrak dengan `job_title`, `job_spec`, dan alamat freelancer.
2. Klien menambah milestone: `add_milestone(title, criteria, amount,
   delivery_deadline)`. Kriteria ditulis satu per baris; kontrak memecahnya
   menjadi item bernomor 1..N (maksimal 12).
3. Klien mendanai milestone: `fund_milestone(index)` sambil mengirim GEN sebesar
   `amount` (payable, nominal harus tepat).
4. Freelancer mengirim hasil kerja sebelum tenggat:
   `submit_deliverable(index, url, note)`.
5. Klien atau freelancer memanggil `judge_milestone(index)`.
6. Approve → nominal dikreditkan ke saldo freelancer di dalam kontrak.
   Reject → freelancer boleh revisi (maksimal 3 percobaan). Setelah percobaan
   terakhir gagal, klien memakai `refund_milestone(index)`.
7. Penerima memanggil `withdraw()` untuk menarik saldo GEN-nya.

## Bagaimana konsensusnya bekerja

```py
def leader_fn():
    rendered = gl.nondet.web.render(deliverable_url, mode="html")
    raw = gl.nondet.exec_prompt(prompt, response_format="json")
    return _parse_judgement(raw, item_count)     # {"verdict","checks","score","reason"}

def validator_fn(leaders_res) -> bool:
    own = leader_fn()                            # validator menjalankan ulang sendiri
    return (leader.get("checks") == own.get("checks")
            and leader.get("verdict") == own.get("verdict")
            and abs(leader.score - own.score) <= 20)
```

- **Setiap kriteria wajib dinilai.** Model harus mengembalikan tepat satu entri
  `{"id": n, "met": true|false}` untuk tiap kriteria; kalau ada yang hilang,
  duplikat, atau id di luar rentang, jawabannya ditolak sebagai `[LLM_ERROR]`
  dan leader diganti.
- **Verdict tidak diambil dari model.** `approve` hanya jika semua kriteria
  `met`, dihitung di kontrak. Model tidak bisa meloloskan kriteria yang gagal.
- **Yang dibandingkan:** vektor `checks` (`"1=1;2=0;"`) harus sama persis, jadi
  validator harus sepakat pada setiap kriteria. Skor hanya diagnostik dan
  toleransinya 20 poin; teks `reason` disimpan untuk tampilan dan tidak pernah
  dipakai lagi dalam keputusan berikutnya.
- **Tidak boleh di dalam blok non-deterministik:** menulis storage, memanggil
  kontrak lain, atau emit pesan. Semua nilai disalin ke variabel lokal dulu.
- **Error leader:** `_handle_leader_error` membedakan error deterministik (harus
  sama), error sementara `[TRANSIENT]` (dua-duanya gagal = setuju), dan error LLM
  `[LLM_ERROR]` (selalu tidak setuju supaya leader diganti).
- **Anti prompt injection:** halaman dan catatan freelancer dimasukkan ke dalam
  pagar `<<<DELIVERABLE ... DELIVERABLE>>>` dengan aturan eksplisit bahwa isinya
  data, bukan instruksi.

## API

Tulis (`write`):

- `add_milestone(title: str, criteria: str, amount: u256, delivery_deadline: u256) -> u32`
  — hanya klien, `delivery_deadline` dalam unix detik dan harus di masa depan.
- `extend_deadline(index: u32, new_deadline: u256) -> None` — hanya klien.
- `fund_milestone(index: u32) -> None` — payable, harus mengirim tepat `amount`.
- `submit_deliverable(index: u32, deliverable_url: str, note: str) -> None` — hanya freelancer, sebelum tenggat.
- `judge_milestone(index: u32) -> str` — hanya klien/freelancer; hasil
  `"approved"`, `"revision_required"`, atau `"failed"`.
- `refund_milestone(index: u32) -> None` — hanya klien, untuk milestone `failed`.
- `recover_milestone(index: u32) -> None` — siapa pun, setelah tenggat + 14 hari.
- `withdraw() -> u256` — mengirim seluruh saldo GEN yang menjadi hak pemanggil.

Baca (`view`):

- `get_client()`, `get_freelancer()`, `get_job_title()`, `get_job_spec()`
- `get_funded_total()`, `get_released_total()`, `get_withdrawn_total()`, `get_balance()`
- `get_my_owed()`, `get_owed(who)` — saldo siap tarik
- `get_milestone_count()`
- `get_milestone_title/criteria/amount/state/deliverable_url/note/attempts/score/reason/checks(index)`
- `get_milestone_attempts_left(index)`, `get_milestone_delivery_deadline(index)`,
  `get_recovery_opens_at(index)`, `get_now()`

State milestone: `draft` → `funded` → `submitted` → (`approved` |
`revision_required` → … | `failed` → `refunded`), plus `expired` untuk recovery.

## Keamanan

- **Double submit / double payout.** Semua transisi dijaga state: submit hanya
  dari `funded`/`revision_required`, judge hanya dari `submitted`, refund hanya
  dari `failed`, recovery hanya dari `funded`/`submitted`/`revision_required`.
  Saldo hanya dikreditkan sekali per milestone, dan `withdraw()` menolkan saldo
  sebelum mengirim GEN (pull payment), jadi transfer yang gagal tidak
  menggandakan pembayaran.
- **Pemanggil luar.** `add_milestone`, `fund_milestone`, `extend_deadline`, dan
  `refund_milestone` hanya untuk klien; `submit_deliverable` hanya untuk
  freelancer; `judge_milestone` hanya klien/freelancer. `recover_milestone`
  sengaja terbuka untuk siapa pun karena nominalnya deterministik (bukan
  diskresi) dan justru mencegah dana terkunci.
- **Dana tidak bisa terkunci selamanya.** Sebelumnya, kalau adjudikasi tidak
  pernah mencapai konsensus (sumber mati) atau freelancer tidak pernah
  mengirim, GEN bisa tertahan tanpa jalan keluar. Sekarang `recover_milestone`
  terbuka setelah `delivery_deadline + 14 hari`: belum ada pengiriman → klien
  dapat 100%; penilaian terakhir menolak → klien 100%; sudah dikirim tapi tak
  pernah ada verdict → dibagi dua.
- **Batas input.** Judul 200, brief 8.000, kriteria 2.000 (maks 12 baris), note
  2.000, URL 500 karakter — mencegah prompt/storage membengkak.
- **Tanpa float.** Skor dan nominal murni integer; tidak ada pembagian `/` yang
  bisa membuat VM crash.

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
jaringan (25 test, sekitar 1 detik):

- state machine milestone dan aturan akses (klien / freelancer / orang luar);
- funding, tenggat, dan perpanjangan tenggat;
- penilaian per kriteria: semua terpenuhi, satu gagal, dan skor rendah tetapi
  kriteria lengkap tetap approve;
- tiga penolakan sampai `failed`, refund, dan recovery (refund penuh, bagi dua,
  serta penolakan pada milestone yang sudah selesai);
- validator yang setuju dan yang menolak ketika satu kriteria berbeda.

Cheatcode: `direct_vm.sender`, `direct_vm.value`, `direct_vm.warp`,
`direct_vm.mock_web`, `direct_vm.mock_llm`, `direct_vm.expect_revert`,
`direct_vm.run_validator()`.

Satu test `withdraw` di-skip: direct mode tidak punya lapisan chain, jadi saldo
kontrak dan pesan eksternal (`emit_transfer`) tidak disimulasikan. Lompatan
transfer itu hanya bisa diuji di Studio atau testnet Bradbury.

## Test integrasi (studionet)

`tests/integration/` menjalankan alur nyata di jaringan GenLayer Studio:

```bash
gltest tests/integration -v --network studionet
```

- deploy, `add_milestone`, funding, submit, judge, dan saldo penerima;
- adversarial: pemanggil luar tidak bisa submit/judge, double submit dan double
  judge ditolak, recovery sebelum jendelanya ditolak, dan recovery setelah
  jendela mengembalikan dana ke klien.

Validator di-mock (`batch_create_mock_validators` dengan jawaban web + LLM
deterministik), jadi hasilnya reproducible dan tidak memakai token LLM.
`genvm_datetime` dipin per transaksi supaya tenggat dan jendela recovery tidak
bergantung jam dinding.

Dua file konfigurasi:

- `gltest.config.yaml` — dipakai apa adanya; akun dibuat otomatis oleh SDK, tanpa
  kunci, sehingga siapa pun yang clone repo bisa menjalankan suite ini.
- `gltest.wallet.config.yaml` — template `studionet_wallet` (chain id 61999) yang
  membaca `${ACCOUNT_PRIVATE_KEY_1}` dari `.env`. CI menyalin file ini ke
  `gltest.config.yaml` hanya kalau secret tersedia, jadi semua transaksi berasal
  dari wallet kamu.

### Deploy sungguhan + bukti untuk portal

Job **Deploy with the studionet wallet** menjalankan `tests/deploy/`:

```bash
# lokal, dengan kunci di .env
gltest tests/deploy -v --network studionet_wallet
```

Hasilnya ditulis ke `deployments/freelance-escrow.studionet.json` dan `.md`:
alamat kontrak, deployer, chain id 61999, waktu deploy, sha256 sumber, dan
receipt transaksi. CI meng-commit file itu ke repo, jadi itulah bukti
deployment yang bisa ditautkan saat submit ke portal GenLayer.

Kunci tidak pernah masuk repo atau chat: simpan di *Settings → Secrets and
variables → Actions* dengan nama `ACCOUNT_PRIVATE_KEY_1` (isi: private key hex
wallet studionet kamu). `.env` sudah di-`.gitignore`. Untuk Bradbury, tambahkan
entri jaringan dengan `${ACCOUNT_PRIVATE_KEY_1}` yang sama.

## Deploy

**Studio** — buka [studio.genlayer.com](https://studio.genlayer.com), tempel isi
kontrak, deploy dengan `job_title`, `job_spec`, dan alamat freelancer (hex).

**CLI** — `genlayer init && genlayer up` untuk Studio lokal, atau pakai `gltest`
untuk test integrasi.

## Catatan

- Halaman hasil kerja diambil dengan `gl.nondet.web.render(url, mode="html")` dan
  dipotong 12.000 karakter. Situs dengan JavaScript berat bisa terbaca tidak
  lengkap; arahkan ke halaman statis, API JSON, atau raw file.
- Waktu diambil dari timestamp transaksi (`datetime.now(timezone.utc)`), nilai
  yang sama di semua validator — bukan jam dinding host.
- Pengiriman GEN ke EOA memakai `@gl.evm.contract_interface` + `emit_transfer`
  (hanya jalan saat finalized). Nilai dalam wei (1 GEN = 10^18 wei).
- Deploy ulang menghasilkan alamat dan state baru; kode yang berubah tidak
  mengubah kontrak yang sudah ter-deploy.
