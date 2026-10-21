from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timezone

db = SQLAlchemy()

# ==========================================
# [TAG: MODEL USER] TABEL USER & PENGATURAN AKUN
# Penjelasan: Menyimpan akun bendahara dan siswa beserta profil & QRIS
# ==========================================

class User(db.Model, UserMixin):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    plain_password = db.Column(db.String(50), nullable=True)
    qris_path = db.Column(db.String(255), nullable=True)  # Path file gambar foto QRIS
    role = db.Column(
        db.String(20), nullable=False, default="bendahara"
    )  # 'bendahara' atau 'siswa'
    siswa_id = db.Column(db.Integer, db.ForeignKey("kas_siswa.id"), nullable=True)
    whatsapp_number = db.Column(db.String(50), nullable=True)
    telegram_chat_id = db.Column(
        db.String(50), nullable=True
    )  # fallback kompatibilitas
    reminder_time = db.Column(db.String(5), default="08:00")  # Jam auto-reminder dikirim
    
    # [TAG: SETELAN TAGIHAN OTOMATIS]
    is_kas_berulang_aktif = db.Column(db.Boolean, default=False)
    kas_berulang_nominal = db.Column(db.Numeric(12, 2), default=50000.00)
    kas_berulang_tanggal = db.Column(db.Integer, default=1)  # Tanggal terbit tiap bulan
    kas_berulang_jatuh_tempo_hari = db.Column(db.Integer, default=7)  # Hari batas bayar

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def no_wa(self):
        return self.whatsapp_number or self.telegram_chat_id or ""

    # Relasi Antar Tabel
    buku_kas = db.relationship("BukuKas", backref="user", lazy=True, cascade="all, delete-orphan")
    kas_siswa = db.relationship("KasSiswa", backref="user", lazy=True, cascade="all, delete-orphan", foreign_keys="KasSiswa.user_id")
    kas_tagihan = db.relationship("KasTagihan", backref="user", lazy=True, cascade="all, delete-orphan")
    kas_transactions = db.relationship("KasTransaction", backref="user", lazy=True, cascade="all, delete-orphan")
    notification_logs = db.relationship("NotificationLog", backref="user", lazy=True, cascade="all, delete-orphan")
    siswa_profile = db.relationship("KasSiswa", backref="user_account", uselist=False, foreign_keys=[siswa_id])


# ==========================================
# [TAG: MODEL BUKU KAS] TABEL BUKU KAS KELAS
# Penjelasan: Ruang kerja kas kelas/organisasi (bisa buat multi-kelas)
# ==========================================

class BukuKas(db.Model):
    __tablename__ = "buku_kas"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    nama = db.Column(db.String(100), nullable=False)  # Contoh: "Kas XII IPA 1"
    deskripsi = db.Column(db.Text, nullable=True)
    token = db.Column(db.String(32), unique=True, nullable=True, index=True)  # Token akses portal siswa
    portal_username = db.Column(db.String(50), nullable=True)
    portal_password = db.Column(db.String(100), nullable=True, default="123456")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Relasi Antar Tabel
    siswa = db.relationship("KasSiswa", backref="buku", lazy=True, cascade="all, delete-orphan", foreign_keys="KasSiswa.buku_id")
    tagihan = db.relationship("KasTagihan", backref="buku", lazy=True, cascade="all, delete-orphan", foreign_keys="KasTagihan.buku_id")
    transaksi = db.relationship("KasTransaction", backref="buku", lazy=True, cascade="all, delete-orphan", foreign_keys="KasTransaction.buku_id")


# ==========================================
# [TAG: MODEL SISWA] TABEL DATA ANGGOTA / SISWA
# Penjelasan: Menyimpan daftar nama siswa, tagihan, sudah bayar, dan sisa
# ==========================================

class KasSiswa(db.Model):
    __tablename__ = "kas_siswa"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    buku_id = db.Column(db.Integer, db.ForeignKey("buku_kas.id"), nullable=True)
    nis = db.Column(db.String(50), nullable=True)
    nama = db.Column(db.String(100), nullable=False)
    kelas = db.Column(db.String(50), nullable=False, default="Kelas")
    kontak = db.Column(db.String(50), nullable=True)  # Alamat email siswa untuk pengingat
    whatsapp_number = db.Column(db.String(50), nullable=True)
    tagihan = db.Column(db.Numeric(12, 2), nullable=False, default=50000.00)  # Total tagihan berjalan
    sudah_bayar = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)  # Total uang yang sudah disetor
    status = db.Column(db.String(20), default="Belum Lunas")  # 'Lunas', 'Belum Lunas', 'Belum Bayar'
    catatan = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    transaksi_masuk = db.relationship("KasTransaction", backref="siswa", lazy=True)

    # [TAG: PROPERTY SISA] Menghitung sisa tunggakan kas siswa
    @property
    def sisa(self):
        t = float(self.tagihan or 0)
        b = float(self.sudah_bayar or 0)
        return max(0.0, t - b)

    # [TAG: PROPERTY INISIAL] Mendapatkan inisial nama untuk avatar
    @property
    def inisial(self):
        if not self.nama:
            return "KS"
        parts = self.nama.strip().split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return parts[0][:2].upper()


# ==========================================
# [TAG: MODEL TAGIHAN] TABEL DAFTAR TAGIHAN KAS
# Penjelasan: Judul tagihan kas yang dibuat bendahara & tanggal jatuh tempo
# ==========================================

class KasTagihan(db.Model):
    __tablename__ = "kas_tagihan"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    buku_id = db.Column(db.Integer, db.ForeignKey("buku_kas.id"), nullable=True)
    judul = db.Column(db.String(100), nullable=False)  # Contoh: "Uang Kas Minggu 1"
    nominal = db.Column(db.Numeric(12, 2), nullable=False, default=50000.00)
    kategori = db.Column(db.String(50), nullable=False, default="Uang Kas Bulanan")
    jatuh_tempo = db.Column(db.Date, nullable=True)
    catatan = db.Column(db.Text, nullable=True)
    token = db.Column(db.String(32), unique=True, nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ==========================================
# [TAG: MODEL TRANSAKSI] BUKU BESAR PEMASUKAN & PENGELUARAN
# Penjelasan: Catatan riil setiap arus uang masuk dan keluar dari kas kelas
# ==========================================

class KasTransaction(db.Model):
    __tablename__ = "kas_transactions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    buku_id = db.Column(db.Integer, db.ForeignKey("buku_kas.id"), nullable=True)
    siswa_id = db.Column(db.Integer, db.ForeignKey("kas_siswa.id"), nullable=True)
    tipe = db.Column(db.String(10), nullable=False)  # 'masuk' (setoran siswa) atau 'keluar' (belanja kelas)
    keterangan = db.Column(db.String(255), nullable=False)
    nominal = db.Column(db.Numeric(12, 2), nullable=False)
    tanggal = db.Column(db.Date, nullable=False, default=lambda: datetime.now(timezone.utc).date())
    kategori = db.Column(db.String(50), nullable=False)
    metode_pembayaran = db.Column(db.String(50), nullable=True)  # 'Tunai' atau 'Transfer / QRIS'
    catatan = db.Column(db.Text, nullable=True)
    bukti_nota = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def inisial(self):
        if not self.keterangan:
            return "PK"
        parts = self.keterangan.strip().split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return parts[0][:2].upper()


# ==========================================
# [TAG: MODEL LOG EMAIL] RIWAYAT NOTIFIKASI
# Penjelasan: Catatan log setiap email pengingat yang terkirim ke siswa
# ==========================================

class NotificationLog(db.Model):
    __tablename__ = "notification_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    bill_id = db.Column(db.Integer, nullable=True)
    channel = db.Column(db.String(20), nullable=False, default="email")
    status = db.Column(db.String(20), default="sent")
    pesan_error = db.Column(db.Text, nullable=True)
    sent_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ==========================================
# [TAG: MODEL KONFIRMASI] KONFIRMASI BAYAR DARI SISWA
# Penjelasan: Tempat menampung respon siswa dari tombol email atau form portal
# ==========================================

class KasKonfirmasi(db.Model):
    __tablename__ = "kas_konfirmasi"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    buku_id = db.Column(db.Integer, db.ForeignKey("buku_kas.id"), nullable=True)
    siswa_id = db.Column(db.Integer, db.ForeignKey("kas_siswa.id"), nullable=False)
    tipe = db.Column(db.String(30), nullable=False, default="sudah_bayar")  # 'sudah_bayar' atau 'belum_bisa_bayar'
    nominal_bayar = db.Column(db.Numeric(12, 2), nullable=True)
    pesan_siswa = db.Column(db.Text, nullable=True)
    bukti_transfer = db.Column(db.String(255), nullable=True)  # File foto struk transfer siswa
    status = db.Column(db.String(20), nullable=False, default="pending")  # 'draft', 'pending', 'diterima', 'ditolak'
    token_konfirmasi = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    responded_at = db.Column(db.DateTime, nullable=True)

    # Relasi Antar Tabel
    siswa = db.relationship("KasSiswa", backref=db.backref("konfirmasi_list", cascade="all, delete-orphan", lazy=True))
    bendahara = db.relationship("User", backref=db.backref("konfirmasi_masuk", cascade="all, delete-orphan", lazy=True))
    buku = db.relationship("BukuKas", backref=db.backref("konfirmasi_buku", cascade="all, delete-orphan", lazy=True))
