# [1.Library]

# [1. PENGATURAN WAKTU & TANGGAL]
from datetime import datetime, timedelta, timezone  # Buat nyatet tanggal & jam transaksi
import time                                         # Buat pasang stopwatch / jeda waktu anti-spam klik

# [2. KEAMANAN & TOKEN ]
from functools import wraps                         # Buat pasang "gembok" keamanan halaman (wajib login)
import secrets                                      # Buat bikin kode token
import string                                       # Kumpulan huruf A-Z & angka 0-9 untuk kode token

# [3. FILE, LAPORAN & SISTEM]
import csv                                          # Buat fitur download laporan kas ke Excel/CSV
from io import StringIO                             # Buat bikin file download instan di memori RAM
import os                                           # Buat baca file.env dan kelola folder upload QRIS

# [4. FORMAT TEKS & VALIDASI]
import locale                                       # Buat ngerapihin format uang otomatis jadi Rupiah (Rp)
import random                                       # Buat ngacak kata-kata pesan tagihan biar gak dianggap spam
import re                                           # Buat ngecek ketikan email itu valid (ada tanda @)


# [2. IMPORT CONFIG & ENVIRONMENT]
from dotenv import load_dotenv                          # Memuat konfigurasi rahasia dari file .env

# [3. IMPORT FLASK & EKSTENSI WEB]
from flask import (
    Flask,              # Inti utama aplikasi web Flask
    Response,           # Objek respon HTTP kustom (untuk download file CSV)
    flash,              # Menampilkan pesan alert/notifikasi sementara ke user
    redirect,           # Mengarahkan browser ke halaman/route lain
    render_template,    # Merender file template HTML dengan data dinamis
    request,            # Menangkap data input form, method HTTP & query URL
    session,            # Menyimpan sesi login user secara aman di cookie browser
    url_for,            # Membentuk URL dinamis berdasarkan nama fungsi view
)
from flask_apscheduler import APScheduler               # Penjadwal tugas otomatis latar belakang (cron job tagihan)
from flask_mail import Mail, Message                    # Layanan kirim email SMTP (notifikasi tagihan & OTP)
import pytz                                             # Pengatur zona waktu regional Indonesia (Asia/Jakarta)
from sqlalchemy import func, or_                        # Fungsi agregat SQL (SUM, COUNT) dan operator logika OR
from werkzeug.security import check_password_hash, generate_password_hash  # Hashing dan verifikasi password akun
from werkzeug.utils import secure_filename              # Sanitasi nama file upload bukti pembayaran agar aman

# [4. IMPORT TABEL DATABASE (MODELS)]
from models import (
    BukuKas,            # Model tabel buku kas kelas/organisasi
    KasKonfirmasi,      # Model tabel konfirmasi pembayaran transfer dari siswa
    KasSiswa,           # Model tabel profil dan nomor kontak siswa
    KasTagihan,         # Model tabel daftar tagihan kas per siswa
    KasTransaction,     # Model tabel pencatatan transaksi masuk & keluar
    NotificationLog,    # Model tabel riwayat log pengiriman notifikasi
    User,               # Model tabel akun pengguna / admin
    db,                 # Objek koneksi database SQLAlchemy
)

# [CONFIG] Muat variabel rahasia dari file .env
load_dotenv(override=True)

# [HELPER] Pembuat token acak unik untuk tagihan kas
def generate_tagihan_token():
    for _ in range(20):
        t = "TAG-" + secrets.token_hex(3).upper()
        if not KasTagihan.query.filter_by(token=t).first():
            return t
    return "TAG-" + secrets.token_hex(4).upper()

# [HELPER] Pembuat token acak unik untuk buku kas kelas
def generate_buku_token():
    for _ in range(20):
        t = "KAS-" + secrets.token_hex(3).upper()
        if not BukuKas.query.filter_by(token=t).first():
            return t
    return "KAS-" + secrets.token_hex(4).upper()

# [CONFIG] Inisialisasi Web Server Flask & Secret Key Sesi
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "default_secret_key")


# ==========================================
# [CONFIG] KONEKSI DATABASE (MYSQL / SQLITE)
# ==========================================

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL", "mysql+pymysql://root:@localhost/nusapay_db"
)

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)


# ==========================================
# [CONFIG] PENGIRIMAN EMAIL GMAIL (SMTP)
# ==========================================
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", 587))
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "True").lower() in [
    "true",
    "on",
    "1",
]
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD", "").replace(" ", "")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER")

mail = Mail(app)

# ==========================================
# [CONFIG] PENJADWAL LATAR BELAKANG (SCHEDULER)
# ==========================================

app.config["SCHEDULER_API_ENABLED"] = True
app.config["SCHEDULER_TIMEZONE"] = "Asia/Jakarta"
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

# [CACHE] Mencegah tombol diklik berkali-kali dalam waktu singkat
_LAST_REMINDER_CACHE = {}


# ==========================================
# [TAG: HELPER TOKEN] GENERATOR TOKEN ACAK
# Penjelasan: Menghasilkan token acak untuk referensi nota / pengingat
# ==========================================
def generate_random_token(length: int = 4) -> str:
    """
    Menghasilkan token unik untuk referensi pesan pengingat email.
    """
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))


# ==========================================
# [TAG: EMAIL TEKS] PESAN TAGIHAN KAS TEKS
# Penjelasan: Format pesan teks ringkas untuk notifikasi kas
# ==========================================
def build_anti_ban_kas_message(
    nama_siswa: str,
    kelas: str,
    tagihan_fmt: str,
    sudah_bayar_fmt: str,
    sisa_fmt: str,
    has_qris: bool = False,
    nama_organisasi: str = "Kelas",
    url_sudah_bayar: str = None,
    url_belum_bisa: str = None,
) -> str:
    """
    Membuat pesan pengingat tagihan kas siswa (Format Teks Rapi).
    """
    ref_code = f"NP{datetime.now().strftime('%d%H%M')}-{generate_random_token(4)}"
    waktu = datetime.now().strftime("%d/%m/%Y %H:%M WIB")

    if has_qris:
        qris_info = (
            "💳 PEMBAYARAN DIGITAL (QRIS):\n"
            "Foto QRIS resmi pembayaran telah dilampirkan/ditampilkan pada pesan ini.\n"
            "Silakan scan kode QRIS untuk melakukan pembayaran kas.\n"
        )
    else:
        qris_info = (
            "💳 CARA PEMBAYARAN:\n"
            "Pembayaran dapat dilakukan secara tunai langsung kepada Bendahara Kelas atau transfer sesuai arahan bendahara.\n"
        )

    tombol_konfirmasi_txt = ""
    if url_sudah_bayar:
        tombol_konfirmasi_txt = (
            " KONFIRMASI STATUS PEMBAYARAN:\n"
            "Silakan klik tautan di bawah ini untuk konfirmasi otomatis ke Bendahara:\n"
            f"• Sudah Bayar       : {url_sudah_bayar}\n\n"
        )

    pesan = (
        f"Halo {nama_siswa},\n\n"
        f"Pemberitahuan Tagihan Iuran Kas & Kegiatan ({kelas}) via NusaPay:\n\n"
        f"📋 RINCIAN TAGIHAN KAS:\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Nama Siswa        : {nama_siswa}\n"
        f"• Kelas / Rombel    : {kelas}\n"
        f"• Total Tagihan     : Rp {tagihan_fmt}\n"
        f"• Sudah Dibayarkan  : Rp {sudah_bayar_fmt}\n"
        f"• Sisa Pembayaran   : Rp {sisa_fmt}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{qris_info}\n"
        f"{tombol_konfirmasi_txt}"
        f"Mohon untuk segera menyelesaikan pembayaran kas sebelum batas waktu.\n"
        f"Bila sudah melakukan pembayaran, silakan konfirmasi melalui tombol di atas agar segera dicatat.\n\n"
        f"Terima kasih banyak atas kerja sama dan kedisiplinannya! 🙏\n\n"
        f"Salam hangat,\n"
        f"Bendahara {nama_organisasi}\n"
        f"NusaPay • Solusi Pengelolaan Kas & Pengingat Tagihan Modern\n"
        f"No. Referensi: #{ref_code} • {waktu}"
    )
    return pesan


# ==========================================
# [TAG: EMAIL HTML + QRIS] FORMAT EMAIL RESMI GMAIL DENGAN QRIS & TOMBOL KONFIRMASI
# Penjelasan: Menyusun template email HTML responsif dengan foto barcode QRIS dan tombol aksi
# ==========================================
def build_html_kas_message(
    nama_siswa: str,
    kelas: str,
    tagihan_fmt: str,
    sudah_bayar_fmt: str,
    sisa_fmt: str,
    has_qris: bool = False,
    nama_organisasi: str = "Kelas",
    url_sudah_bayar: str = None,
    url_belum_bisa: str = None,
) -> str:
    """
    Membuat template email HTML responsif yang elegan untuk Gmail dengan tombol konfirmasi interaktif.
    """
    ref_code = f"NP{datetime.now().strftime('%d%H%M')}-{generate_random_token(4)}"
    waktu = datetime.now().strftime("%d %b %Y, %H:%M WIB")

    if has_qris:
        qris_html = """
        <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 14px; padding: 20px; margin-top: 20px; text-align: center;">
          <div style="font-size: 14px; font-weight: 700; color: #0f172a; margin-bottom: 4px;">
            📸 QRIS Pembayaran Kas
          </div>
          <p style="font-size: 12px; color: #64748b; line-height: 1.4; margin: 0 0 14px 0;">
            Silakan scan kode QRIS di bawah ini untuk melakukan pembayaran kas:
          </p>
          <div style="display: inline-block; background-color: #ffffff; padding: 12px; border-radius: 12px; border: 1px solid #cbd5e1; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);">
            <img src="cid:qris_kas_image" alt="QRIS Pembayaran Kas" style="max-width: 250px; width: 100%; height: auto; display: block; border-radius: 6px; margin: 0 auto;" />
          </div>
          <p style="font-size: 11px; color: #94a3b8; margin: 12px 0 0 0;">
            *Setelah transfer / scan QRIS berhasil, konfirmasi pembayaran Anda melalui tombol di bawah.
          </p>
        </div>
        """
    else:
        qris_html = """
        <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px; margin-top: 20px;">
          <p style="font-size: 12px; color: #64748b; line-height: 1.5; margin: 0;">
            💵 <strong>Metode Pembayaran:</strong> Silakan lakukan pembayaran tunai kepada Bendahara Kelas atau transfer sesuai arahan bendahara.
          </p>
        </div>
        """

    tombol_konfirmasi_html = ""
    if url_sudah_bayar:
        tombol_konfirmasi_html = f"""
        <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 14px; padding: 20px; margin-top: 22px; text-align: center;">
          <div style="font-size: 14px; font-weight: 700; color: #0f172a; margin-bottom: 6px;">
             Konfirmasi Status Pembayaran
          </div>
          <p style="font-size: 12px; color: #64748b; line-height: 1.5; margin: 0 0 16px 0;">
            Sudah melakukan pembayaran kas? Klik tombol di bawah ini:
          </p>
          <table align="center" border="0" cellpadding="0" cellspacing="0" style="margin: 0 auto;">
            <tr>
              <td style="padding: 4px 6px;">
                <a href="{url_sudah_bayar}" target="_blank" style="display: inline-block; background-color: #16a34a; color: #ffffff; text-decoration: none; font-size: 13px; font-weight: 700; padding: 12px 24px; border-radius: 10px; box-shadow: 0 2px 4px rgba(22, 163, 74, 0.25);">
                   Saya Sudah Bayar
                </a>
              </td>
            </tr>
          </table>
          <p style="font-size: 11px; color: #94a3b8; margin: 10px 0 0 0;">
            *Pilihan Anda akan otomatis terkirim ke web Bendahara untuk diverifikasi.
          </p>
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>Pengingat Tagihan Kas</title>
    </head>
    <body style="margin: 0; padding: 20px; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
      <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 580px; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); border: 1px solid #e2e8f0;">
        <tr>
          <td style="background: linear-gradient(135deg, #003B73 0%, #00529C 50%, #0284c7 100%); padding: 26px 30px; text-align: left; color: #ffffff;">
            <div style="font-size: 22px; font-weight: 900; letter-spacing: -0.5px; margin-bottom: 4px;">
              nusa<span style="color: #38bdf8;">pay</span>
            </div>
            <div style="font-size: 13px; color: #e0f2fe; font-weight: 500;">
              Sistem Pengelolaan Kas & Pengingat Tagihan
            </div>
          </td>
        </tr>
        <tr>
          <td style="padding: 30px;">
            <h2 style="font-size: 18px; font-weight: 700; color: #0f172a; margin-top: 0; margin-bottom: 10px;">
              Halo, {nama_siswa} 👋
            </h2>
            <p style="font-size: 14px; color: #475569; line-height: 1.6; margin-top: 0; margin-bottom: 20px;">
              Berikut rincian status iuran kas dan kegiatan <strong>{kelas}</strong> Anda saat ini:
            </p>
            <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; margin-bottom: 20px;">
              <tr>
                <td style="padding: 12px 18px; font-size: 13px; color: #64748b; border-bottom: 1px solid #e2e8f0;">Siswa / Anggota</td>
                <td style="padding: 12px 18px; font-size: 13px; font-weight: 600; color: #1e293b; text-align: right; border-bottom: 1px solid #e2e8f0;">{nama_siswa}</td>
              </tr>
              <tr>
                <td style="padding: 12px 18px; font-size: 13px; color: #64748b; border-bottom: 1px solid #e2e8f0;">Kelas / Organisasi</td>
                <td style="padding: 12px 18px; font-size: 13px; font-weight: 600; color: #1e293b; text-align: right; border-bottom: 1px solid #e2e8f0;">{kelas}</td>
              </tr>
              <tr>
                <td style="padding: 12px 18px; font-size: 13px; color: #64748b; border-bottom: 1px solid #e2e8f0;">Total Tagihan</td>
                <td style="padding: 12px 18px; font-size: 13px; font-weight: 600; color: #1e293b; text-align: right; border-bottom: 1px solid #e2e8f0;">Rp {tagihan_fmt}</td>
              </tr>
              <tr>
                <td style="padding: 12px 18px; font-size: 13px; color: #64748b; border-bottom: 1px solid #e2e8f0;">Sudah Dibayar</td>
                <td style="padding: 12px 18px; font-size: 13px; font-weight: 600; color: #16a34a; text-align: right; border-bottom: 1px solid #e2e8f0;">Rp {sudah_bayar_fmt}</td>
              </tr>
              <tr style="background-color: #fff1f2;">
                <td style="padding: 14px 18px; font-size: 14px; font-weight: 700; color: #e11d48;">Sisa Pembayaran</td>
                <td style="padding: 14px 18px; font-size: 18px; font-weight: 800; color: #e11d48; text-align: right;">Rp {sisa_fmt}</td>
              </tr>
            </table>

            {qris_html}

            {tombol_konfirmasi_html}

            <p style="font-size: 13px; color: #64748b; line-height: 1.6; margin-top: 24px; margin-bottom: 0;">
              Mohon segera menyelesaikan pembayaran kepada Bendahara. Apabila sudah melunasi, silakan klik tombol konfirmasi di atas agar tercatat otomatis. Terima kasih atas kerja sama dan kedisiplinannya! 🙏
            </p>
          </td>
        </tr>
        <tr>
          <td style="background-color: #f8fafc; padding: 20px 30px; border-top: 1px solid #e2e8f0; font-size: 11px; color: #94a3b8; text-align: center; line-height: 1.5;">
            Pengingat otomatis diterbitkan oleh Bendahara {nama_organisasi} via <strong>NusaPay</strong>.<br>
            No. Referensi: #{ref_code} • {waktu}
          </td>
        </tr>
      </table>
    </body>
    </html>
    """
    return html


# ==========================================
# [TAG: EMAIL TAGIHAN UMUM] PESAN PENGIRIMAN TAGIHAN SATU SISWA
# Penjelasan: Membuat isi pesan pengingat tagihan umum dengan variasi kata acak agar tidak terdeteksi spam
# ==========================================
def build_anti_ban_tagihan_message(nama: str, nominal: str) -> str:
    """
    Membuat pesan tagihan umum.
    """

    greetings = [f"Halo {nama}", f"Hai {nama}", f"Halo kak {nama}", f"Salam {nama}"]

    openings = [
        f"Anda memiliki tagihan NusaPay sebesar Rp {nominal}.",
        f"Pemberitahuan tagihan aktif NusaPay senilai Rp {nominal} telah diterbitkan.",
        f"Informasi tagihan Anda saat ini di NusaPay: Rp {nominal}.",
    ]

    closings = [
        "Mohon segera melakukan pembayaran sebelum jatuh tempo.",
        "Silakan selesaikan pembayaran melalui kanal yang tersedia.",
        "Diharapkan untuk melakukan pelunasan tagihan tersebut.",
    ]

    friendly_actions = [
        "💡 Balas pesan ini jika Anda butuh informasi atau konfirmasi bukti bayar.",
        "💡 Abaikan pesan ini jika Anda sudah melakukan pelunasan.",
        "💡 Hubungi pengelola dengan membalas chat ini bila ada kendala nominal.",
    ]

    thanks = [
        "Terima kasih atas kerja samanya! 🙏",
        "Terima kasih banyak! 🙏",
        "Terima kasih atas kepercayaannya! ✨",
    ]

    salam = random.choice(greetings)
    pembuka = random.choice(openings)
    ajakan = random.choice(closings)
    tindakan = random.choice(friendly_actions)
    terima_kasih = random.choice(thanks)

    ref_code = f"#BILL{datetime.now().strftime('%d%H%M')}-{generate_random_token(4)}"
    waktu = datetime.now().strftime("%d/%m %H:%M")

    pesan = (
        f"{salam},\n\n"
        f"{pembuka}\n"
        f"{ajakan}\n\n"
        f"{tindakan}\n\n"
        f"{terima_kasih}\n\n"
        f"- NusaPay\n"
        f"Kode Ref: {ref_code} • {waktu}"
    )

    return pesan


# ==========================================
# [TAG: KIRIM EMAIL] FUNGSI UTAMA PENGIRIMAN EMAIL VIA GMAIL
# Penjelasan: Mengirim email ke siswa, mendukung teks, HTML, dan foto QRIS inline (langsung muncul di badan email)
# ==========================================


def send_email_notification(
    to_email: str,
    subject: str,
    message_body: str,
    user_id: int = None,
    bill_id: int = None,
    html_body: str = None,
    attachment_path: str = None,
    attachment_filename: str = None,
    **kwargs,
):
    """
    Mengirim pesan email

    Return:
        (True, "Email terkirim") jika berhasil
        (False, pesan_error) jika gagal
    """
    if not to_email:
        return False, "Alamat email tujuan tidak boleh kosong."

    if "@" not in to_email:
        return False, f"Format email '{to_email}' tidak valid."

    try:
        msg = Message(subject=subject, recipients=[to_email], body=message_body)
        if html_body:
            msg.html = html_body

        if attachment_path and os.path.exists(attachment_path):
            real_ext = os.path.splitext(attachment_path)[1].lower() or ".jpg"
            filename = attachment_filename or f"QRIS_Pembayaran_Kas{real_ext}"
            if attachment_filename and not attachment_filename.lower().endswith(real_ext):
                base_fn = os.path.splitext(attachment_filename)[0]
                filename = f"{base_fn}{real_ext}"

            ctype = "image/jpeg"
            if real_ext == ".png":
                ctype = "image/png"
            elif real_ext == ".webp":
                ctype = "image/webp"
            elif real_ext in [".jpg", ".jpeg"]:
                ctype = "image/jpeg"

            with open(attachment_path, "rb") as fp:
                msg.attach(
                    filename=filename,
                    content_type=ctype,
                    data=fp.read(),
                    disposition="inline",
                    headers={"Content-ID": "<qris_kas_image>"},
                )

        mail.send(msg)

        print(f"[Email] Berhasil dikirim ke {to_email}")

        if user_id:
            try:
                log = NotificationLog(
                    user_id=user_id, bill_id=bill_id, channel="email", status="sent"
                )
                db.session.add(log)
                db.session.commit()
            except Exception as log_err:
                db.session.rollback()
                print(f"[Email] Catat log error: {log_err}")

        return True, "Email berhasil terkirim."

    except Exception as e:
        clean_msg = str(e)
        print(f"[Email] Gagal mengirim ke {to_email}: {clean_msg}")

        friendly_err = f"Gagal mengirim email: {clean_msg}. Pastikan konfigurasi MAIL_USERNAME dan MAIL_PASSWORD di .env sudah benar."

        if user_id:
            try:
                log = NotificationLog(
                    user_id=user_id,
                    bill_id=bill_id,
                    channel="email",
                    status="failed",
                    pesan_error=friendly_err,
                )
                db.session.add(log)
                db.session.commit()
            except Exception:
                db.session.rollback()

        return False, friendly_err


# ==========================================
# [TAG: BUKU KAS AKTIF] AMBIL RUANG KERJA KAS YANG SEDANG DIPAKAI
# Penjelasan: Mengambil buku kas aktif dari session, atau buku terbaru milik user
# ==========================================
def get_user_active_buku(user_id):
    """
    Mengambil buku kas aktif milik user_id saat ini berdasarkan session['buku_id'].
    Jika belum ada atau invalid, mengambil buku kas terbaru milik user.
    Jika user belum memiliki buku kas sama sekali, otomatis membuatkan satu ruang kerja baru.
    """
    buku_id = session.get("buku_id")
    buku = None
    if buku_id:
        buku = BukuKas.query.filter_by(id=buku_id, user_id=user_id).first()

    if not buku:
        # Ambil buku kas terbaru milik user jika session belum diset
        buku = BukuKas.query.filter_by(user_id=user_id).order_by(BukuKas.id.desc()).first()
        if buku:
            session["buku_id"] = buku.id

    if not buku:
        user = User.query.get(user_id)
        nama_kelas = "Kas Kelas"
        if user and user.full_name:
            nama_kelas = f"Kas {user.full_name}"
        buku = BukuKas(
            user_id=user_id,
            nama=nama_kelas,
            token=generate_buku_token(),
        )
        db.session.add(buku)
        db.session.commit()
        session["buku_id"] = buku.id

    return buku


# Alias untuk kompatibilitas fungsi lain
get_or_create_user_buku = get_user_active_buku


# [TAG: TOKEN KONFIRMASI] BUAT ATAU AMBIL TOKEN UNIK SISWA
# Penjelasan: Token ini dipakai sebagai URL di tombol Gmail 'Saya Sudah Bayar' agar tiap siswa unik
def get_or_create_confirmation_token(user_id, buku_id, siswa_id):
    """
    Mengambil token konfirmasi unik siswa atau membuat draft baru jika belum ada.
    Digunakan untuk tautan tombol 'Saya Sudah Bayar' di pesan Gmail.
    """
    konfirmasi = KasKonfirmasi.query.filter_by(
        user_id=user_id, siswa_id=siswa_id, status="pending"
    ).first()
    if not konfirmasi:
        konfirmasi = KasKonfirmasi.query.filter_by(
            user_id=user_id, siswa_id=siswa_id, status="draft"
        ).first()
    if not konfirmasi:
        token = f"KNF-{siswa_id}-{secrets.token_hex(12)}"
        konfirmasi = KasKonfirmasi(
            user_id=user_id,
            buku_id=buku_id,
            siswa_id=siswa_id,
            status="draft",
            token_konfirmasi=token,
        )
        db.session.add(konfirmasi)
        db.session.commit()
    return konfirmasi.token_konfirmasi


# [TAG: GUARD LOGIN] WAJIB LOGIN UNTUK AKSES HALAMAN
# Penjelasan: Decorator pembatas halaman - jika belum login, diarahkan ke halaman login
def login_required(f):

    @wraps(f)
    def decorated_function(*args, **kwargs):

        if "user_id" not in session:

            flash(
                "Silakan login terlebih dahulu " "untuk mengakses halaman ini.",
                "warning",
            )

            return redirect(url_for("login"))

        return f(*args, **kwargs)

    return decorated_function


# [TAG: GUARD BENDAHARA] WAJIB LOGIN SEBAGAI BENDAHARA
# Penjelasan: Penjaga ganda - cek login, cek role, dan pastikan buku kas aktif selalu valid
def bendahara_required(f):

    @wraps(f)
    def decorated_function(*args, **kwargs):

        if "user_id" not in session:
            flash(
                "Silakan login terlebih dahulu untuk mengakses halaman ini.", "warning"
            )
            return redirect(url_for("login"))

        role = session.get("user_role", "bendahara")
        if role == "siswa":
            flash(
                "Akses terbatas: Anda masuk sebagai Siswa. Halaman bendahara tidak dapat diakses.",
                "warning",
            )
            return redirect(url_for("portal_siswa"))

        # Pastikan session['buku_id'] selalu valid dan milik user_id yang sedang login
        user_id = session["user_id"]
        buku = get_user_active_buku(user_id)
        if not buku:
            flash("Silakan buat pencatatan kas terlebih dahulu.", "info")
            return redirect(url_for("dashboard", modal="tambah_buku"))

        session["buku_id"] = buku.id

        return f(*args, **kwargs)

    return decorated_function


# ==========================================
# [TAG: INIT DB] BUAT TABEL DATABASE SAAT PERTAMA KALI DIJALANKAN
# Penjelasan: Jika tabel belum ada di database, otomatis dibuat di sini
# ==========================================
with app.app_context():

    try:

        db.create_all()

        print("Database connected and tables created successfully.")

    except Exception as e:

        print(f"Database initialization info/error: {e}")


# ==========================================
# [TAG: ROUTE PUBLIK] HALAMAN BERANDA, LOGIN, DAFTAR, DAN CEK TAGIHAN
# Penjelasan: Halaman yang bisa dibuka tanpa login - beranda, login, daftar akun, dan cek status tagihan
# ==========================================


@app.route("/")  # [ROUTE] Halaman utama / landing page
def home():

    return render_template("index.html")


@app.route("/index")
def index():

    return redirect(url_for("home"))


@app.route("/cek_tagihan", methods=["GET", "POST"])  # [ROUTE] Cek tagihan siswa via token kas
def cek_tagihan():
    token_input = (
        request.form.get("token")
        or request.args.get("token")
        or request.form.get("identifier")
        or request.args.get("identifier")
        or ""
    ).strip()

    if not token_input:
        flash("Silakan masukkan Token Kas Anda terlebih dahulu.", "warning")
        return redirect(url_for("home", _anchor="cek-tagihan"))

    clean_upper = token_input.upper()
    buku = BukuKas.query.filter(
        or_(
            func.upper(BukuKas.token) == clean_upper,
            func.upper(BukuKas.portal_username) == clean_upper,
            func.upper(BukuKas.nama) == clean_upper,
        )
    ).first()

    tagihan = None
    if not buku:
        tagihan = KasTagihan.query.filter(func.upper(KasTagihan.token) == clean_upper).first()
        if tagihan and tagihan.buku_id:
            buku = BukuKas.query.get(tagihan.buku_id)

    if buku or tagihan:
        token_to_use = buku.token if buku else tagihan.token
        session.clear()
        session["portal_token"] = token_to_use
        session["buku_id"] = buku.id if buku else (tagihan.buku_id if tagihan else None)
        flash(f"Data kas berhasil ditemukan: {buku.nama if buku else tagihan.judul}", "success")
        return redirect(url_for("portal_siswa"))
    else:
        flash(f"Token Kas '{token_input}' tidak ditemukan. Silakan periksa kembali token kas Anda.", "danger")
        return redirect(url_for("home", _anchor="cek-tagihan"))


@app.route("/login", methods=["GET", "POST"])  # [ROUTE] Form login bendahara
def login():

    if request.method == "POST":
        identifier = (
            request.form.get("identifier")
            or request.form.get("username")
            or request.form.get("email")
            or request.form.get("token")
        )
        password = (request.form.get("password") or "").strip()

        if not identifier or not identifier.strip():
            flash("Silakan masukkan Alamat Gmail Anda.", "warning")
            return render_template("login.html")

        identifier_clean = identifier.strip()
        ident_lower = identifier_clean.lower()

        # 1. Cek apakah yang diinput adalah Token Kas (siswa memasukkan token di login)
        clean_upper = identifier_clean.upper()
        if clean_upper.startswith("KAS-") or clean_upper.startswith("TAG-"):
            buku_check = BukuKas.query.filter(func.upper(BukuKas.token) == clean_upper).first()
            tagihan_check = KasTagihan.query.filter(func.upper(KasTagihan.token) == clean_upper).first()
            if buku_check or tagihan_check:
                return redirect(url_for("cek_tagihan", token=clean_upper))

        # 2. Login Akun Pengguna / Bendahara (Prioritas: Email atau Username unik)
        user = User.query.filter(
            or_(
                func.lower(User.email) == ident_lower,
                func.lower(User.username) == ident_lower,
            )
        ).first()

        # Verifikasi password jika cocok email/username
        if user:
            pw_ok = check_password_hash(user.password_hash, password) or (
                user.plain_password and user.plain_password == password
            )
            if not pw_ok:
                user = None

        # Jika belum cocok, cari kandidat berdasarkan nama lengkap atau no hp
        if not user:
            candidates = User.query.filter(
                or_(
                    func.lower(User.full_name) == ident_lower,
                    func.replace(func.lower(User.full_name), " ", "") == ident_lower.replace(" ", ""),
                    User.whatsapp_number == identifier_clean,
                    User.telegram_chat_id == identifier_clean,
                )
            ).all()
            for cand in candidates:
                if check_password_hash(cand.password_hash, password) or (
                    cand.plain_password and cand.plain_password == password
                ):
                    user = cand
                    break

        if user:
            session.clear()
            session["user_id"] = user.id
            session["user_name"] = user.full_name
            session["user_role"] = getattr(user, "role", "bendahara")
            session["user_email"] = user.email
            session["user_username"] = user.username
            session["siswa_id"] = getattr(user, "siswa_id", None)

            # Jika akun siswa terdaftar
            if session["user_role"] == "siswa":
                siswa_obj = KasSiswa.query.get(user.siswa_id) if user.siswa_id else None
                buku_target = None
                if siswa_obj and siswa_obj.buku_id:
                    buku_target = BukuKas.query.get(siswa_obj.buku_id)
                elif siswa_obj:
                    buku_target = BukuKas.query.filter_by(user_id=siswa_obj.user_id).first()

                if buku_target and buku_target.token:
                    session["portal_token"] = buku_target.token
                    session["buku_id"] = buku_target.id

                flash(f"Selamat datang di Portal Siswa, {user.full_name}!", "success")
                return redirect(url_for("portal_siswa"))

            # Pengguna biasa / Bendahara -> Ambil buku kas terakhir milik user
            user_buku = BukuKas.query.filter_by(user_id=user.id).order_by(BukuKas.id.desc()).first()
            if user_buku:
                session["buku_id"] = user_buku.id
            else:
                session.pop("buku_id", None)

            session["welcome_data"] = {
                "type": "login",
                "user_name": user.full_name,
            }
            flash(f"Selamat datang kembali, {user.full_name}!", "success")
            return redirect(url_for("dashboard"))

        # 3. Kredensial tidak cocok
        flash("Gmail/Email atau Password salah. Silakan periksa kembali.", "danger")
        return render_template(
            "login.html",
            error="Gmail/Email atau Password salah.",
            identifier=identifier_clean
        )

    # Pengunjung siswa yang hanya melihat token tidak memiliki akun login, bersihkan sesi
    if "user_id" not in session:
        session.clear()
    else:
        session.pop("portal_token", None)

    identifier_val = request.args.get("identifier") or request.args.get("token") or ""
    return render_template("login.html", identifier=identifier_val)


@app.route("/signup", methods=["GET", "POST"])  # [ROUTE] Daftar akun bendahara baru
def signup():

    if request.method == "POST":

        full_name = (request.form.get("fullname") or request.form.get("fullName") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = (request.form.get("password") or "").strip()
        whatsapp_number = (
            request.form.get("whatsapp_number")
            or request.form.get("no_hp")
            or request.form.get("telegram_id")
            or ""
        ).strip()

        if not email or "@" not in email:
            flash("Format alamat email tidak valid.", "warning")
            return render_template("signup.html", fullname=full_name, whatsapp_number=whatsapp_number)

        if not password:
            flash("Kata sandi tidak boleh kosong.", "warning")
            return render_template("signup.html", email=email, fullname=full_name, whatsapp_number=whatsapp_number)

        # Cek apakah email sudah terdaftar (case-insensitive)
        existing_user = User.query.filter(func.lower(User.email) == email).first()

        if existing_user:
            flash("Alamat email ini sudah digunakan oleh akun lain. Silakan gunakan alamat email baru untuk membuat akun mandiri Anda.", "danger")
            return render_template("signup.html", email="", fullname=full_name, whatsapp_number=whatsapp_number)

        # Buat username yang dijamin unik agar tidak terjadi database IntegrityError
        raw_prefix = email.split("@")[0]
        base_username = re.sub(r"[^a-zA-Z0-9_]", "", raw_prefix).lower() or "user"
        username = base_username
        counter = 1
        while User.query.filter(func.lower(User.username) == username.lower()).first():
            username = f"{base_username}{counter}"
            counter += 1

        hashed_pw = (
            generate_password_hash(password)
            if password
            else generate_password_hash("123456")
        )

        try:
            new_user = User(
                full_name=full_name or "Pengguna NusaPay",
                username=username,
                email=email,
                password_hash=hashed_pw,
                plain_password=password,
                whatsapp_number=whatsapp_number,
                telegram_chat_id=whatsapp_number,
                role="bendahara",
            )
            db.session.add(new_user)
            db.session.flush()

            # Otomatis buatkan 1 Buku Kas aktif & Token Kas bersih KHUSUS untuk akun baru ini
            token_kas = generate_buku_token()
            default_buku = BukuKas(
                user_id=new_user.id,
                nama=f"Kas {new_user.full_name}",
                token=token_kas,
                portal_username=username,
                portal_password=password or "123456",
            )
            db.session.add(default_buku)
            db.session.commit()

            # Bersihkan sesi lama sepenuhnya sebelum masuk
            session.clear()

            flash(
                f"Pendaftaran berhasil! Akun baru Anda ({email}) telah aktif dengan database mandiri. Silakan masuk.",
                "success",
            )
            return redirect(url_for("login", identifier=email))

        except Exception as e:
            db.session.rollback()
            flash("Terjadi kendala saat mendaftarkan akun. Silakan coba kembali.", "danger")
            return render_template("signup.html")

    # Saat membuka form signup, bersihkan sesi agar tidak tercampur dengan akun lain
    session.clear()
    return render_template("signup.html")


# ==========================================
# HALAMAN SUKSES LOGIN & REGISTRASI (COMPATIBILITY)
# ==========================================
@app.route("/auth-success")
def auth_success():
    return redirect(url_for("dashboard"))


@app.route("/logout")  # [ROUTE] Keluar dari sesi, hapus session lalu redirect ke login
def logout():

    session.clear()

    flash("Anda telah berhasil keluar dari akun.", "info")

    return redirect(url_for("login"))


# ==========================================
# [TAG: DASHBOARD] HALAMAN RINGKASAN UTAMA BENDAHARA
# Penjelasan: Tampilkan saldo, transaksi terbaru, siswa menunggak, dan daftar buku kas milik user
# ==========================================
@app.route("/dashboard")  # [ROUTE] Dashboard utama setelah login
@login_required
def dashboard():
    user_id = session["user_id"]
    user = User.query.get(user_id)
    
    buku_list = BukuKas.query.filter_by(user_id=user_id).all()
    has_buku_token_update = False
    for b in buku_list:
        if not b.token:
            b.token = generate_buku_token()
            has_buku_token_update = True
    if has_buku_token_update:
        db.session.commit()

    # Pop welcome_data agar animasi hanya tayang sekali tepat setelah login / registrasi
    welcome_data = session.pop("welcome_data", None)

    # Ambil data transaksi, siswa, dan tagihan riil KHUSUS milik user ini (data isolation)
    all_transactions = (
        KasTransaction.query.filter_by(user_id=user_id)
        .order_by(KasTransaction.tanggal.desc(), KasTransaction.created_at.desc())
        .all()
    )
    all_siswa = KasSiswa.query.filter_by(user_id=user_id).all()
    tagihan_list = (
        KasTagihan.query.filter_by(user_id=user_id)
        .order_by(KasTagihan.jatuh_tempo.asc())
        .limit(5)
        .all()
    )

    total_pemasukan = sum(float(t.nominal or 0) for t in all_transactions if t.tipe == "masuk")
    total_pengeluaran = sum(float(t.nominal or 0) for t in all_transactions if t.tipe == "keluar")
    total_saldo = total_pemasukan - total_pengeluaran
    recent_transactions = all_transactions[:5]

    total_belum_lunas = sum(float(s.sisa or 0) for s in all_siswa if s.status != "Lunas")
    total_siswa_menunggak = len([s for s in all_siswa if s.sisa > 0])

    active_buku_id = session.get("buku_id")
    active_buku = next((b for b in buku_list if b.id == active_buku_id), None)
    if not active_buku and buku_list:
        active_buku = buku_list[0]
        session["buku_id"] = active_buku.id
    elif not active_buku:
        session.pop("buku_id", None)

    # Format tanggal bahasa Indonesia untuk header
    hari_indo = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    bulan_indo = [
        "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember"
    ]
    now = datetime.now()
    tgl_hari_ini = f"{hari_indo[now.weekday()]}, {now.day} {bulan_indo[now.month - 1]} {now.year}"

    konfirmasi_pending_count = KasKonfirmasi.query.filter_by(
        user_id=user_id, status="pending"
    ).count()

    return render_template(
        "dashboard.html",
        buku_list=buku_list,
        welcome_data=welcome_data,
        user=user,
        total_saldo=total_saldo,
        total_pemasukan=total_pemasukan,
        total_pengeluaran=total_pengeluaran,
        total_belum_lunas=total_belum_lunas,
        total_siswa_menunggak=total_siswa_menunggak,
        recent_transactions=recent_transactions,
        tagihan_list=tagihan_list,
        active_buku=active_buku,
        tgl_hari_ini=tgl_hari_ini,
        konfirmasi_pending_count=konfirmasi_pending_count,
    )

# ==========================================
# [TAG: BUKU KAS CRUD] TAMBAH, PILIH, HAPUS, UBAH SANDI PORTAL BUKU KAS
# Penjelasan: Tiap user bisa punya banyak buku kas (misal: Kas Kelas 7A, Kas OSIS). Semua terisolasi per user.
# ==========================================


@app.route("/buku_kas/tambah", methods=["POST"])  # [ROUTE] Tambah buku kas baru
@login_required
def tambah_buku_kas():
    nama = request.form.get("nama")
    deskripsi = request.form.get("deskripsi")
    if nama and nama.strip():
        buku = BukuKas(
            user_id=session["user_id"],
            nama=nama.strip(),
            deskripsi=deskripsi.strip() if deskripsi else None,
            token=generate_buku_token(),
        )
        db.session.add(buku)
        db.session.commit()
        session["buku_id"] = buku.id
        flash(f'Pencatatan kas "{buku.nama}" berhasil dibuat!', "success")
        return redirect(url_for("pilih_buku_kas", buku_id=buku.id))
    return redirect(url_for("dashboard"))

@app.route("/buku_kas/pilih/<int:buku_id>")
@login_required
def pilih_buku_kas(buku_id):
    buku = BukuKas.query.filter_by(id=buku_id, user_id=session["user_id"]).first_or_404()
    session["buku_id"] = buku.id
    flash(f"Memasuki {buku.nama}", "success")
    return redirect(url_for("penagihankas"))

@app.route("/buku_kas/hapus/<int:buku_id>", methods=["POST"])
@login_required
def hapus_buku_kas(buku_id):
    user_id = session.get("user_id")
    buku = BukuKas.query.filter_by(id=buku_id, user_id=user_id).first_or_404()
    nama_buku = buku.nama

    # 1. Hapus file fisik bukti transfer / foto resi yang tersimpan di server
    transactions = KasTransaction.query.filter_by(buku_id=buku.id).all()
    for t in transactions:
        if t.foto_bukti:
            file_path = os.path.join(app.root_path, "static", "uploads", t.foto_bukti)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass

    konfirmasis = KasKonfirmasi.query.filter_by(buku_id=buku.id).all()
    for k in konfirmasis:
        if k.bukti_transfer:
            file_path = os.path.join(app.root_path, "static", "uploads", k.bukti_transfer)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass

    # 2. Hapus seluruh data konfirmasi, transaksi, tagihan, dan siswa di buku ini secara permanen
    KasKonfirmasi.query.filter_by(buku_id=buku.id).delete(synchronize_session=False)
    KasTransaction.query.filter_by(buku_id=buku.id).delete(synchronize_session=False)
    KasTagihan.query.filter_by(buku_id=buku.id).delete(synchronize_session=False)
    KasSiswa.query.filter_by(buku_id=buku.id).delete(synchronize_session=False)

    # 3. Hapus entitas pencatatan kas itu sendiri
    db.session.delete(buku)
    db.session.commit()

    # 4. Perbarui session buku aktif jika buku yang dihapus sedang aktif
    other_buku = BukuKas.query.filter(BukuKas.user_id == user_id, BukuKas.id != buku_id).first()
    if session.get("buku_id") == buku_id:
        if other_buku:
            session["buku_id"] = other_buku.id
        else:
            session.pop("buku_id", None)

    flash(f'Pencatatan kas "{nama_buku}" beserta seluruh data di dalamnya berhasil dihapus permanen.', "success")
    return redirect(url_for("dashboard"))

@app.route("/buku_kas/ubah_password_portal/<int:buku_id>", methods=["POST"])
@login_required
def ubah_password_portal(buku_id):
    buku = BukuKas.query.filter_by(id=buku_id, user_id=session["user_id"]).first_or_404()
    new_pw = request.form.get("portal_password")
    if new_pw and new_pw.strip():
        buku.portal_password = new_pw.strip()
        db.session.commit()
        flash(f'Kata sandi portal siswa untuk "{buku.nama}" berhasil diperbarui menjadi: {new_pw.strip()}', "success")
    return redirect(url_for("penagihankas"))

# ==========================================
# MANAGEMENT KAS SISWA / KELAS
# ==========================================


@app.route("/kas")
@app.route("/penagihan")
def penagihan():

    return redirect(url_for("penagihankas"))


# ==========================================
# [TAG: PENGELUARAN KAS] CATAT, EDIT, DAN HAPUS PENGELUARAN BENDAHARA
# Penjelasan: Semua transaksi tipe 'keluar' (pengeluaran) disimpan di sini per buku kas aktif
# ==========================================


@app.route("/pengeluarankas", methods=["GET", "POST"])  # [ROUTE] Halaman pengeluaran kas
@bendahara_required
def pengeluarankas():

    user_id = session["user_id"]
    buku = get_or_create_user_buku(user_id)
    session["buku_id"] = buku.id

    if request.method == "POST":

        return pengeluarankas_tambah()

    transactions = (
        KasTransaction.query.filter_by(user_id=user_id, buku_id=buku.id, tipe="keluar")
        .order_by(KasTransaction.tanggal.desc(), KasTransaction.created_at.desc())
        .all()
    )

    total_pengeluaran = sum(float(t.nominal or 0) for t in transactions)

    kas_masuk_all = KasTransaction.query.filter_by(user_id=user_id, buku_id=buku.id, tipe="masuk").all()

    total_kas_masuk = sum(float(t.nominal or 0) for t in kas_masuk_all)

    saldo_kas_aktif = total_kas_masuk - total_pengeluaran

    total_transaksi = len(transactions)

    pengeluaran_terbesar = max(
        [float(t.nominal or 0) for t in transactions], default=0.0
    )

    terbesar_item = next(
        (t for t in transactions if float(t.nominal or 0) == pengeluaran_terbesar), None
    )

    terbesar_nama = terbesar_item.keterangan if terbesar_item else "-"

    terbesar_kategori = terbesar_item.kategori if terbesar_item else "-"

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return render_template(
        "pengeluarankas.html",
        transactions=transactions,
        total_pengeluaran=total_pengeluaran,
        total_kas_masuk=total_kas_masuk,
        saldo_kas_aktif=saldo_kas_aktif,
        total_transaksi=total_transaksi,
        pengeluaran_terbesar=pengeluaran_terbesar,
        terbesar_nama=terbesar_nama,
        terbesar_kategori=terbesar_kategori,
        today_str=today_str,
    )


@app.route("/pengeluarankas/tambah", methods=["POST"])  # [ROUTE] Tambah transaksi pengeluaran baru
@bendahara_required
def pengeluarankas_tambah():

    user_id = session["user_id"]
    buku = get_or_create_user_buku(user_id)
    buku_id = buku.id

    keterangan = (
        request.form.get("keperluan")
        or request.form.get("keterangan")
        or "Pengeluaran Kas"
    )

    nominal_str = request.form.get("nominal", "0")

    kategori = request.form.get("kategori", "Operasional")

    tanggal_str = request.form.get("tanggal")

    metode = request.form.get("metode_pembayaran") or "Kas Tunai"

    catatan = request.form.get("catatan") or ""

    try:

        cleaned_nom = (
            str(nominal_str)
            .replace("Rp", "")
            .replace("rp", "")
            .replace(" ", "")
            .replace(".", "")
            .replace(",", "")
            .strip()
        )

        nominal = float(cleaned_nom)

    except (ValueError, AttributeError):

        nominal = 0.0

    if nominal <= 0:

        flash("Nominal pengeluaran harus lebih dari 0.", "warning")

        return redirect(url_for("pengeluarankas"))

    if tanggal_str:

        try:

            tanggal_obj = datetime.strptime(tanggal_str, "%Y-%m-%d").date()

        except ValueError:

            tanggal_obj = datetime.now(timezone.utc).date()

    else:

        tanggal_obj = datetime.now(timezone.utc).date()

    new_kas_out = KasTransaction(
        user_id=user_id,
        buku_id=buku_id,
        tipe="keluar",
        keterangan=keterangan.strip(),
        nominal=nominal,
        tanggal=tanggal_obj,
        kategori=kategori.strip(),
        metode_pembayaran=metode.strip(),
        catatan=catatan.strip(),
    )

    db.session.add(new_kas_out)
    db.session.commit()

    flash(
        f'Pengeluaran kas "{keterangan}" '
        f"sebesar Rp {nominal:,.0f} "
        f"berhasil dicatat!",
        "success",
    )

    return redirect(url_for("pengeluarankas"))


@app.route("/pengeluarankas/edit/<int:trans_id>", methods=["POST"])  # [ROUTE] Edit data pengeluaran
@bendahara_required
def pengeluarankas_edit(trans_id):

    user_id = session["user_id"]

    trans = KasTransaction.query.filter_by(id=trans_id, user_id=user_id).first_or_404()

    keterangan = request.form.get("keperluan") or request.form.get("keterangan")

    nominal_str = request.form.get("nominal")

    kategori = request.form.get("kategori")

    tanggal_str = request.form.get("tanggal")

    metode = request.form.get("metode_pembayaran")

    catatan = request.form.get("catatan")

    if keterangan:
        trans.keterangan = keterangan.strip()

    if nominal_str:

        try:

            trans.nominal = float(
                str(nominal_str)
                .replace("Rp", "")
                .replace(".", "")
                .replace(",", "")
                .strip()
            )

        except ValueError:
            pass

    if kategori:
        trans.kategori = kategori.strip()

    if metode:
        trans.metode_pembayaran = metode.strip()

    if catatan is not None:
        trans.catatan = catatan.strip()

    if tanggal_str:

        try:

            trans.tanggal = datetime.strptime(tanggal_str, "%Y-%m-%d").date()

        except ValueError:
            pass

    db.session.commit()

    flash("Catatan pengeluaran kas berhasil diperbarui!", "success")

    return redirect(url_for("pengeluarankas"))


@app.route("/pengeluarankas/hapus/<int:trans_id>", methods=["POST"])  # [ROUTE] Hapus catatan pengeluaran
@bendahara_required
def pengeluarankas_hapus(trans_id):

    user_id = session["user_id"]

    trans = KasTransaction.query.filter_by(id=trans_id, user_id=user_id).first_or_404()

    ket = trans.keterangan

    db.session.delete(trans)
    db.session.commit()

    flash(f'Catatan pengeluaran "{ket}" ' f"berhasil dihapus dari kas kelas.", "danger")

    return redirect(url_for("pengeluarankas"))


@app.route("/penagihan/ekspor")  # [ROUTE] Ekspor daftar siswa & tagihan ke file CSV
@bendahara_required
def penagihan_ekspor():
    user_id = session["user_id"]
    buku = get_or_create_user_buku(user_id)
    siswa_list = (
        KasSiswa.query.filter_by(user_id=user_id, buku_id=buku.id).order_by(KasSiswa.nama.asc()).all()
    )

    def generate():
        data = StringIO()
        writer = csv.writer(data)

        # Header
        writer.writerow(
            [
                "ID Siswa",
                "Nama Siswa",
                "Kelas",
                "Kontak",
                "Total Tagihan",
                "Sudah Bayar",
                "Sisa Tagihan",
                "Status",
                "Catatan",
            ]
        )
        yield data.getvalue()
        data.seek(0)
        data.truncate(0)

        for siswa in siswa_list:
            sisa = float(siswa.tagihan or 0) - float(siswa.sudah_bayar or 0)
            writer.writerow(
                [
                    siswa.nis or "-",
                    siswa.nama,
                    siswa.kelas,
                    siswa.kontak or "-",
                    f"{siswa.tagihan:,.0f}",
                    f"{siswa.sudah_bayar:,.0f}",
                    f"{sisa:,.0f}",
                    siswa.status,
                    siswa.catatan or "-",
                ]
            )
            yield data.getvalue()
            data.seek(0)
            data.truncate(0)

    response = Response(generate(), mimetype="text/csv")
    response.headers.set(
        "Content-Disposition",
        "attachment",
        filename=f"laporan_kas_{datetime.now().strftime('%Y%m%d')}.csv",
    )
    return response


# ==========================================
# [TAG: PENAGIHAN KAS] HALAMAN UTAMA MANAJEMEN SISWA & TAGIHAN
# Penjelasan: Tampilkan daftar siswa, tagihan aktif, riwayat setoran, dan konfirmasi pembayaran yang masuk
# ==========================================


@app.route("/penagihankas", methods=["GET", "POST"])  # [ROUTE] Halaman penagihan & daftar siswa
@bendahara_required
def penagihankas():

    user_id = session["user_id"]
    buku = get_or_create_user_buku(user_id)
    buku_id = buku.id
    session["buku_id"] = buku_id

    if not buku.token:
        buku.token = generate_buku_token()
        db.session.commit()

    # [PEMBERSIHAN DATA YATIM / ORPHAN]
    # Data lama tanpa buku_id dihapus permanen agar tidak muncul ke pencatatan baru
    KasKonfirmasi.query.filter_by(user_id=user_id, buku_id=None).delete(synchronize_session=False)
    KasTransaction.query.filter_by(user_id=user_id, buku_id=None).delete(synchronize_session=False)
    KasTagihan.query.filter_by(user_id=user_id, buku_id=None).delete(synchronize_session=False)
    KasSiswa.query.filter_by(user_id=user_id, buku_id=None).delete(synchronize_session=False)
    db.session.commit()

    # [DATA BUKU KAS AKTIF] Ambil daftar siswa KHUSUS untuk buku kas / pencatatan ini saja
    siswa_list = (
        KasSiswa.query.filter_by(user_id=user_id, buku_id=buku_id)
        .order_by(KasSiswa.id.asc())
        .all()
    )

    # [SINKRONISASI KELAS DENGAN PENCATATAN]
    # Otomatis sesuaikan kelas siswa agar selalu mengikuti nama pencatatan kas aktif (misal: "XII PPLG")
    if buku and buku.nama:
        updated_kelas = False
        for s in siswa_list:
            if s.kelas != buku.nama:
                s.kelas = buku.nama
                updated_kelas = True
        if updated_kelas:
            db.session.commit()

    # [DATA BUKU KAS AKTIF] Ambil daftar tagihan KHUSUS untuk buku kas / pencatatan ini saja
    tagihan_list = (
        KasTagihan.query.filter_by(user_id=user_id, buku_id=buku_id)
        .order_by(KasTagihan.created_at.desc())
        .all()
    )

    # Pastikan setiap tagihan memiliki token unik
    has_token_update = False
    for t in tagihan_list:
        if not t.token:
            t.token = generate_tagihan_token()
            has_token_update = True
    if has_token_update:
        db.session.commit()

    active_tagihan_id = request.args.get("tagihan_id", type=int)
    tagihan_kas = None
    if active_tagihan_id:
        tagihan_kas = next((t for t in tagihan_list if t.id == active_tagihan_id), None)
    if not tagihan_kas and tagihan_list:
        tagihan_kas = tagihan_list[0]

    riwayat_kas_masuk = (
        KasTransaction.query.filter_by(user_id=user_id, buku_id=buku_id, tipe="masuk")
        .order_by(KasTransaction.tanggal.desc(), KasTransaction.created_at.desc())
        .limit(20)
        .all()
    )

    total_terkumpul = sum(float(s.sudah_bayar or 0) for s in siswa_list)

    total_target = sum(float(s.tagihan or 0) for s in siswa_list)

    persen_tercapai = (
        int(round(total_terkumpul / total_target * 100)) if total_target > 0 else 0
    )

    total_lunas = sum(
        1 for s in siswa_list if s.status.lower() == "lunas" or s.sisa <= 0
    )

    total_belum_lunas = len(siswa_list) - total_lunas

    total_sisa = sum(s.sisa for s in siswa_list)

    total_siswa = len(siswa_list)

    pengeluaran_all = KasTransaction.query.filter_by(
        user_id=user_id, buku_id=buku_id, tipe="keluar"
    ).all()

    total_pengeluaran = sum(float(t.nominal or 0) for t in pengeluaran_all)

    kas_masuk_all = KasTransaction.query.filter_by(user_id=user_id, buku_id=buku_id, tipe="masuk").all()

    total_kas_masuk = sum(float(t.nominal or 0) for t in kas_masuk_all)

    saldo_kas_aktif = total_kas_masuk - total_pengeluaran

    total_tagihan_buku = sum(float(t.nominal or 0) for t in tagihan_list)

    user = User.query.get(user_id)

    # Konfirmasi pembayaran siswa khusus untuk siswa di pencatatan kas ini
    konfirmasi_pending_list = (
        KasKonfirmasi.query.filter_by(user_id=user_id, status="pending")
        .join(KasSiswa, KasKonfirmasi.siswa_id == KasSiswa.id)
        .filter(KasSiswa.buku_id == buku_id)
        .order_by(KasKonfirmasi.created_at.desc())
        .all()
    )

    return render_template(
        "penagihankas.html",
        buku=buku,
        user=user,
        siswa_list=siswa_list,
        tagihan_kas=tagihan_kas,
        tagihan_list=tagihan_list,
        total_tagihan_buku=total_tagihan_buku,
        riwayat_kas_masuk=riwayat_kas_masuk,
        total_terkumpul=total_terkumpul,
        total_target=total_target,
        persen_tercapai=persen_tercapai,
        total_lunas=total_lunas,
        total_belum_lunas=total_belum_lunas,
        total_sisa=total_sisa,
        total_siswa=total_siswa,
        total_pengeluaran=total_pengeluaran,
        saldo_kas_aktif=saldo_kas_aktif,
        konfirmasi_pending_list=konfirmasi_pending_list,
    )


# ==========================================
# TAMBAH SISWA
# ==========================================


@app.route("/penagihan/tambah_siswa", methods=["POST"])
@bendahara_required
def penagihan_tambah_siswa():

    user_id = session["user_id"]
    buku = get_or_create_user_buku(user_id)
    buku_id = buku.id

    nama = request.form.get("nama") or request.form.get("nama_siswa")

    default_kelas = buku.nama if (buku and buku.nama) else "Kelas"
    raw_kelas = (request.form.get("kelas") or "").strip()
    if not raw_kelas or raw_kelas in ["Kelas XII-IPA-1", "XII-IPA-1", "Kelas"]:
        kelas = default_kelas
    else:
        kelas = raw_kelas

    kontak = request.form.get("kontak") or ""

    whatsapp_number = request.form.get("whatsapp_number") or ""

    tagihan_str = request.form.get("tagihan") or request.form.get("nominal")

    status_input = request.form.get("status") or "Belum Lunas"

    catatan = request.form.get("catatan") or ""

    if not nama:

        flash("Nama siswa wajib diisi.", "warning")

        return redirect(url_for("penagihankas"))

    active_tagihan_sum = sum(
        float(t.nominal or 0)
        for t in KasTagihan.query.filter_by(user_id=user_id, buku_id=buku_id).all()
    )
    default_nominal = active_tagihan_sum if active_tagihan_sum > 0 else 50000.0

    try:
        tagihan = float(
            str(tagihan_str).replace("Rp", "").replace(".", "").replace(",", "").strip()
        )
    except (ValueError, AttributeError):
        tagihan = default_nominal

    user_siswa_count = KasSiswa.query.filter_by(user_id=user_id).count()
    nis = f"SIS-{user_siswa_count + 1:03d}"

    if status_input.lower() == "lunas":

        sudah_bayar = tagihan
        status = "Lunas"

    else:

        sudah_bayar = 0.0
        status = "Belum Lunas"

    new_siswa = KasSiswa(
        user_id=user_id,
        buku_id=buku_id,
        nis=nis,
        nama=nama.strip().upper(),
        kelas=kelas.strip(),
        kontak=kontak.strip(),
        whatsapp_number=whatsapp_number.strip(),
        tagihan=tagihan,
        sudah_bayar=sudah_bayar,
        status=status,
        catatan=catatan.strip(),
    )

    db.session.add(new_siswa)
    db.session.commit()

    if sudah_bayar > 0:

        kas_masuk = KasTransaction(
            user_id=user_id,
            buku_id=buku_id,
            siswa_id=new_siswa.id,
            tipe="masuk",
            keterangan=(
                f"Setoran Awal Iuran: " f"{new_siswa.nama} " f"({new_siswa.kelas})"
            ),
            nominal=sudah_bayar,
            tanggal=datetime.now(timezone.utc).date(),
            kategori="Iuran Kas Siswa",
            metode_pembayaran="Kas Tunai",
            catatan=(
                f"Pelunasan langsung saat " f"pendaftaran siswa " f"{new_siswa.nama}"
            ),
        )

        db.session.add(kas_masuk)
        db.session.commit()

    flash(
        f'Data siswa "{nama}" ({nis}) ' f"berhasil ditambahkan ke penagihan kas!",
        "success",
    )

    return redirect(url_for("penagihankas"))


# ==========================================
# BUAT TAGIHAN KAS
# ==========================================


@app.route("/penagihan/buat_tagihan", methods=["POST"])
@bendahara_required
def penagihan_buat_tagihan():

    user_id = session["user_id"]
    buku = get_or_create_user_buku(user_id)
    buku_id = buku.id

    judul = (
        request.form.get("judul")
        or request.form.get("nama_tagihan")
        or "Iuran Kas Kelas"
    )

    nominal_str = request.form.get("nominal") or request.form.get("jumlah", "50000")

    kategori = request.form.get("kategori") or "Uang Kas Bulanan"

    catatan = request.form.get("catatan") or ""

    jatuh_tempo_str = request.form.get("jatuh_tempo")
    jatuh_tempo_date = None
    if jatuh_tempo_str:
        try:
            jatuh_tempo_date = datetime.strptime(jatuh_tempo_str, "%Y-%m-%d").date()
        except:
            pass

    terapkan_semua = request.form.get("terapkan_semua", "on") in [
        "on",
        "1",
        "true",
        True,
    ]

    try:
        nominal = float(
            str(nominal_str).replace("Rp", "").replace(".", "").replace(",", "").strip()
        )
    except (ValueError, AttributeError):
        nominal = 50000.0

    token = generate_tagihan_token()

    new_tagihan = KasTagihan(
        user_id=user_id,
        buku_id=buku_id,
        judul=judul.strip(),
        nominal=nominal,
        kategori=kategori.strip(),
        catatan=catatan.strip(),
        jatuh_tempo=jatuh_tempo_date,
        token=token,
    )

    db.session.add(new_tagihan)

    # Otomatis menambahkan nominal tagihan kas/event ini ke total tagihan siswa di buku kas ini saja
    all_siswa = KasSiswa.query.filter_by(user_id=user_id, buku_id=buku_id).all()
    for s in all_siswa:
        s.tagihan = float(s.tagihan or 0) + float(nominal)
        if float(s.sudah_bayar or 0) >= float(s.tagihan):
            s.status = "Lunas"
        elif float(s.sudah_bayar or 0) > 0:
            s.status = "Belum Lunas"
        else:
            s.status = "Belum Bayar"

    db.session.commit()

    flash(
        f'Tagihan "{judul}" (Rp {nominal:,.0f}) berhasil disimpan! Total tagihan anggota di pencatatan ini bertambah otomatis.', "success"
    )

    return redirect(url_for("penagihankas"))


# ==========================================
# [TAG: HAPUS TAGIHAN] HAPUS TAGIHAN & KURANGI NOMINAL DARI SISWA
# Penjelasan: Saat tagihan dihapus, saldo tagihan tiap siswa otomatis dikurangi nominal yang sama
# ==========================================


@app.route("/penagihan/hapus_tagihan/<int:tagihan_id>", methods=["POST"])  # [ROUTE] Hapus tagihan
@bendahara_required
def penagihan_hapus_tagihan(tagihan_id):
    user_id = session["user_id"]
    buku = get_or_create_user_buku(user_id)

    tagihan = KasTagihan.query.filter_by(id=tagihan_id, user_id=user_id).first_or_404()

    judul = tagihan.judul
    nominal = float(tagihan.nominal or 0)

    # Kurangi tagihan dari siswa jika sebelumnya diterapkan ke semua siswa di buku kas ini
    if nominal > 0:
        all_siswa = KasSiswa.query.filter_by(user_id=user_id, buku_id=buku.id).all()
        for s in all_siswa:
            s.tagihan = max(0.0, float(s.tagihan or 0) - nominal)
            if float(s.sudah_bayar or 0) >= float(s.tagihan):
                s.status = "Lunas"
            elif float(s.sudah_bayar or 0) > 0:
                s.status = "Belum Lunas"
            else:
                s.status = "Belum Bayar"

    db.session.delete(tagihan)
    db.session.commit()

    flash(f'Tagihan "{judul}" berhasil dihapus.', "success")
    return redirect(url_for("penagihankas"))


# ==========================================
# [TAG: CATAT BAYAR] INPUT SETORAN PEMBAYARAN SISWA
# Penjelasan: Catat bayaran siswa → perbarui sudah_bayar & status, lalu masuk ke riwayat kas masuk
# ==========================================


@app.route("/penagihan/catat_bayar", methods=["POST"])  # [ROUTE] Catat pembayaran iuran siswa
@bendahara_required
def penagihan_catat_bayar():

    user_id = session["user_id"]
    buku = get_or_create_user_buku(user_id)

    siswa_id = request.form.get("siswa_id")

    nominal_str = request.form.get("nominal")

    tanggal_str = request.form.get("tanggal")

    metode_bayar = request.form.get("metode_pembayaran") or "Kas Tunai"

    if not siswa_id:

        flash("Data siswa tidak ditemukan.", "danger")

        return redirect(url_for("penagihankas"))

    siswa = KasSiswa.query.filter_by(id=siswa_id, user_id=user_id).first_or_404()

    try:

        nominal = float(
            str(nominal_str).replace("Rp", "").replace(".", "").replace(",", "").strip()
        )

    except (ValueError, AttributeError):

        nominal = 0.0

    if nominal <= 0:

        flash("Nominal pembayaran harus lebih dari 0.", "warning")

        return redirect(url_for("penagihankas"))

    if tanggal_str:

        try:

            tanggal_obj = datetime.strptime(tanggal_str, "%Y-%m-%d").date()

        except ValueError:

            tanggal_obj = datetime.now(timezone.utc).date()

    else:

        tanggal_obj = datetime.now(timezone.utc).date()

    siswa.sudah_bayar = float(siswa.sudah_bayar or 0) + nominal

    if siswa.sudah_bayar >= float(siswa.tagihan or 0):

        siswa.status = "Lunas"

    else:

        siswa.status = "Belum Lunas"

    new_kas_in = KasTransaction(
        user_id=user_id,
        buku_id=buku.id,
        siswa_id=siswa.id,
        tipe="masuk",
        keterangan=(f"Pembayaran Iuran Kas: " f"{siswa.nama} ({siswa.kelas})"),
        nominal=nominal,
        tanggal=tanggal_obj,
        kategori="Iuran Kas Siswa",
        metode_pembayaran=metode_bayar,
        catatan=(f"Pembayaran setoran kas " f"{siswa.nama} ({siswa.nis or '-'})"),
    )

    db.session.add(new_kas_in)
    db.session.commit()

    flash(
        f"Pembayaran Rp {nominal:,.0f} "
        f"untuk {siswa.nama} berhasil dicatat "
        f"ke Kas Masuk!",
        "success",
    )

    return redirect(url_for("penagihankas"))


# ==========================================
# [TAG: EDIT SISWA] UBAH DATA SISWA (NAMA, KELAS, KONTAK, TAGIHAN)
# Penjelasan: Edit data siswa dan sesuaikan status lunas/belum lunas secara otomatis
# ==========================================


@app.route("/penagihan/edit_siswa/<int:siswa_id>", methods=["POST"])  # [ROUTE] Edit data siswa
@bendahara_required
def penagihan_edit_siswa(siswa_id):

    user_id = session["user_id"]

    siswa = KasSiswa.query.filter_by(id=siswa_id, user_id=user_id).first_or_404()

    nama = request.form.get("nama")
    kelas = request.form.get("kelas")
    kontak = request.form.get("kontak")
    whatsapp_number = request.form.get("whatsapp_number")
    tagihan_str = request.form.get("tagihan")
    catatan = request.form.get("catatan")

    if nama:
        siswa.nama = nama.strip().upper()

    if kelas:
        raw_k = kelas.strip()
        buku = get_or_create_user_buku(user_id)
        default_k = buku.nama if (buku and buku.nama) else "Kelas"
        if not raw_k or raw_k in ["Kelas XII-IPA-1", "XII-IPA-1"]:
            siswa.kelas = default_k
        else:
            siswa.kelas = raw_k

    if kontak is not None:
        siswa.kontak = kontak.strip()

    if whatsapp_number is not None:
        siswa.whatsapp_number = whatsapp_number.strip()

    if catatan is not None:
        siswa.catatan = catatan.strip()

    if tagihan_str:

        try:

            tagihan = float(
                str(tagihan_str)
                .replace("Rp", "")
                .replace(".", "")
                .replace(",", "")
                .strip()
            )

            siswa.tagihan = tagihan

            if float(siswa.sudah_bayar or 0) >= tagihan:

                siswa.status = "Lunas"

            elif float(siswa.sudah_bayar or 0) > 0:

                siswa.status = "Belum Lunas"

            else:

                siswa.status = "Belum Bayar"

        except (ValueError, AttributeError):

            pass

    db.session.commit()

    flash(f'Data siswa "{siswa.nama}" ' f"berhasil diperbarui!", "success")

    return redirect(url_for("penagihankas"))


# ==========================================
# HAPUS SISWA
# ==========================================


@app.route("/penagihan/hapus_siswa/<int:siswa_id>", methods=["POST"])  # [ROUTE] Hapus siswa dari daftar
@bendahara_required
def penagihan_hapus_siswa(siswa_id):

    user_id = session["user_id"]

    siswa = KasSiswa.query.filter_by(id=siswa_id, user_id=user_id).first_or_404()

    nama = siswa.nama

    KasTransaction.query.filter_by(siswa_id=siswa.id).update({"siswa_id": None})

    db.session.delete(siswa)
    db.session.commit()

    flash(
        f'Data siswa "{nama}" ' f"berhasil dihapus dari daftar penagihan kas.", "info"
    )

    return redirect(url_for("penagihankas"))


# ==========================================
# [TAG: INGATKAN SISWA] KIRIM PENGINGAT TAGIHAN KE SATU SISWA VIA EMAIL
# Penjelasan: Kirim email pengingat tagihan + foto QRIS ke satu siswa, ada cooldown 60 detik anti-spam
# ==========================================


@app.route("/penagihan/ingatkan/<int:siswa_id>", methods=["POST"])  # [ROUTE] Ingatkan satu siswa via email
@bendahara_required
def penagihan_ingatkan(siswa_id):

    user_id = session["user_id"]

    siswa = KasSiswa.query.filter_by(id=siswa_id, user_id=user_id).first_or_404()

    if not siswa.kontak or siswa.kontak.strip() in ["-", "", "0"] or "@" not in siswa.kontak:

        flash(
            f'Alamat email untuk siswa "{siswa.nama}" belum valid. '
            f"Silakan lengkapi email siswa di menu Edit Siswa.",
            "warning",
        )

        return redirect(url_for("penagihankas"))

    sisa_tagihan = int(siswa.sisa)

    sisa_fmt = f"{sisa_tagihan:,}".replace(",", ".")

    tagihan_fmt = f"{int(siswa.tagihan):,}".replace(",", ".")

    sudah_bayar_fmt = f"{int(siswa.sudah_bayar):,}".replace(",", ".")

    # Anti-Spam Cooldown Guard: hindari klik berulang dalam waktu singkat ke nomor yang sama
    cache_key = f"remind_{user_id}_{siswa_id}"
    last_sent = _LAST_REMINDER_CACHE.get(cache_key)
    now_ts = time.time()
    COOLDOWN_SECONDS = (
        60  # Jeda 60 detik per kontak agar nomor tidak dicap membombardir
    )

    if last_sent and (now_ts - last_sent) < COOLDOWN_SECONDS:
        sisa_waktu = int(COOLDOWN_SECONDS - (now_ts - last_sent))
        flash(
            f"⚠️ Pengingat untuk {siswa.nama} baru saja dikirim. "
            f"Harap tunggu {sisa_waktu} detik sebelum mengirim lagi guna menghindari spam email.",
            "warning",
        )
        return redirect(url_for("penagihankas"))

    user = User.query.get(user_id)
    buku = get_or_create_user_buku(user_id)
    nama_organisasi = buku.nama if (buku and buku.nama) else (user.full_name or "Kelas")

    qris_attachment = None
    has_qris = False
    if user and user.qris_path:
        qris_full = os.path.join(app.root_path, "static", "uploads", user.qris_path)
        if os.path.exists(qris_full):
            qris_attachment = qris_full
            has_qris = True

    # URL konfirmasi untuk tombol di pesan Gmail
    token_konf = get_or_create_confirmation_token(user_id, buku.id, siswa.id)
    base_url = request.host_url.rstrip("/")
    url_sudah_bayar = f"{base_url}/konfirmasi_pembayaran/{token_konf}"

    # Buat isi pesan teks dan HTML rapi
    pesan_text = build_anti_ban_kas_message(
        nama_siswa=siswa.nama,
        kelas=siswa.kelas,
        tagihan_fmt=tagihan_fmt,
        sudah_bayar_fmt=sudah_bayar_fmt,
        sisa_fmt=sisa_fmt,
        has_qris=has_qris,
        nama_organisasi=nama_organisasi,
        url_sudah_bayar=url_sudah_bayar,
    )

    pesan_html = build_html_kas_message(
        nama_siswa=siswa.nama,
        kelas=siswa.kelas,
        tagihan_fmt=tagihan_fmt,
        sudah_bayar_fmt=sudah_bayar_fmt,
        sisa_fmt=sisa_fmt,
        has_qris=has_qris,
        nama_organisasi=nama_organisasi,
        url_sudah_bayar=url_sudah_bayar,
    )

    # Kirim pengingat email dengan lampiran foto QRIS jika ada
    success, result = send_email_notification(
        to_email=siswa.kontak.strip(),
        subject=f"Pengingat Tagihan Kas - {siswa.nama} ({siswa.kelas})",
        message_body=pesan_text,
        html_body=pesan_html,
        attachment_path=qris_attachment,
        attachment_filename="QRIS_Pembayaran_Kas.jpg" if qris_attachment else None,
        user_id=user_id,
    )

    if success:
        _LAST_REMINDER_CACHE[cache_key] = now_ts
        qris_status_text = " (disertai lampiran foto QRIS)" if has_qris else ""
        flash(
            f"✅ Pengingat Email berhasil dikirim ke {siswa.nama} ({siswa.kontak}){qris_status_text}!",
            "success",
        )
    else:
        flash(
            f"Gagal mengirim Email ke {siswa.nama} ({siswa.kontak}): {result}", "danger"
        )

    return redirect(url_for("penagihankas"))


# ==========================================
# [TAG: INGATKAN SEMUA] KIRIM PENGINGAT EMAIL KE SEMUA SISWA YANG MASIH MENUNGGAK
# Penjelasan: Blast pengingat tagihan ke seluruh siswa yang belum lunas dan memiliki email valid
# ==========================================


@app.route("/penagihan/ingatkan_semua", methods=["POST"])  # [ROUTE] Kirim pengingat ke semua siswa menunggak
@bendahara_required
def penagihan_ingatkan_semua():
    user_id = session["user_id"]
    buku = get_or_create_user_buku(user_id)
    buku_id = buku.id

    # Ambil seluruh siswa aktif yang masih memiliki tunggakan
    query = KasSiswa.query.filter(
        KasSiswa.user_id == user_id,
        KasSiswa.tagihan > KasSiswa.sudah_bayar,
    )
    if buku_id:
        query = query.filter(KasSiswa.buku_id == buku_id)

    siswa_list = query.all()

    if not siswa_list:
        flash("Tidak ada tagihan tertunggak! Seluruh siswa telah melunasi iuran kas.", "info")
        return redirect(url_for("penagihankas"))

    # Filter siswa yang memiliki alamat email valid
    siswa_dengan_email = [s for s in siswa_list if s.kontak and "@" in s.kontak.strip()]

    if not siswa_dengan_email:
        flash(
            f"Terdapat {len(siswa_list)} siswa yang belum lunas, namun belum ada yang memiliki alamat email valid di kontak. Silakan lengkapi email siswa terlebih dahulu.",
            "warning",
        )
        return redirect(url_for("penagihankas"))

    user = User.query.get(user_id)
    nama_organisasi = buku.nama if (buku and buku.nama) else (user.full_name or "Kelas")

    qris_attachment = None
    has_qris = False
    if user and user.qris_path:
        qris_full = os.path.join(app.root_path, "static", "uploads", user.qris_path)
        if os.path.exists(qris_full):
            qris_attachment = qris_full
            has_qris = True

    berhasil = 0
    gagal = 0
    now_ts = time.time()

    base_url = request.host_url.rstrip("/")
    for siswa in siswa_dengan_email:
        tagihan_fmt = f"{int(siswa.tagihan):,}".replace(",", ".")
        sudah_bayar_fmt = f"{int(siswa.sudah_bayar):,}".replace(",", ".")
        sisa_fmt = f"{int(siswa.sisa):,}".replace(",", ".")

        token_konf = get_or_create_confirmation_token(user_id, buku.id, siswa.id)
        url_sudah_bayar = f"{base_url}/konfirmasi_pembayaran/{token_konf}"

        pesan_text = build_anti_ban_kas_message(
            nama_siswa=siswa.nama,
            kelas=siswa.kelas,
            tagihan_fmt=tagihan_fmt,
            sudah_bayar_fmt=sudah_bayar_fmt,
            sisa_fmt=sisa_fmt,
            has_qris=has_qris,
            nama_organisasi=nama_organisasi,
            url_sudah_bayar=url_sudah_bayar,
        )

        pesan_html = build_html_kas_message(
            nama_siswa=siswa.nama,
            kelas=siswa.kelas,
            tagihan_fmt=tagihan_fmt,
            sudah_bayar_fmt=sudah_bayar_fmt,
            sisa_fmt=sisa_fmt,
            has_qris=has_qris,
            nama_organisasi=nama_organisasi,
            url_sudah_bayar=url_sudah_bayar,
        )

        success, _ = send_email_notification(
            to_email=siswa.kontak.strip(),
            subject=f"Pengingat Tagihan Kas - {siswa.nama} ({siswa.kelas})",
            message_body=pesan_text,
            html_body=pesan_html,
            attachment_path=qris_attachment,
            attachment_filename="QRIS_Pembayaran_Kas.jpg" if qris_attachment else None,
            user_id=user_id,
        )

        if success:
            cache_key = f"remind_{user_id}_{siswa.id}"
            _LAST_REMINDER_CACHE[cache_key] = now_ts
            berhasil += 1
        else:
            gagal += 1

    total_target = len(siswa_dengan_email)
    tanpa_email = len(siswa_list) - total_target

    if berhasil > 0:
        qris_note = " (disertai lampiran foto QRIS)" if has_qris else ""
        pesan_sukses = f"✅ Pengingat tagihan berhasil dikirim serentak ke {berhasil} siswa via Email Gmail{qris_note}!"
        if tanpa_email > 0:
            pesan_sukses += f" ({tanpa_email} siswa belum memiliki email)."
        if gagal > 0:
            pesan_sukses += f" ({gagal} pengiriman gagal)."
        flash(pesan_sukses, "success")
    else:
        flash(
            f"Gagal mengirim pengingat ke {total_target} siswa. Pastikan koneksi dan pengaturan Gmail SMTP aktif.",
            "danger",
        )

    return redirect(url_for("penagihankas"))


# ==========================================
# [TAG: KONFIRMASI PEMBAYARAN] HALAMAN SISWA KONFIRMASI STATUS BAYAR VIA TOKEN EMAIL
# Penjelasan: Siswa klik tautan di email Gmail → isi form konfirmasi → dikirim ke bendahara untuk diverifikasi
# ==========================================


@app.route("/konfirmasi_pembayaran/<token_konfirmasi>", methods=["GET", "POST"])  # [ROUTE] Konfirmasi bayar siswa
def konfirmasi_pembayaran_siswa(token_konfirmasi):
    """
    Halaman konfirmasi untuk siswa yang mengeklik tombol 'Saya Sudah Bayar' di pesan email Gmail.
    Siswa dapat mengirim status pembayaran, bukti transfer (opsional), dan catatan ke Bendahara.
    """
    konfirmasi = KasKonfirmasi.query.filter_by(token_konfirmasi=token_konfirmasi).first_or_404()
    siswa = konfirmasi.siswa
    bendahara = konfirmasi.bendahara
    buku = konfirmasi.buku or (BukuKas.query.get(konfirmasi.buku_id) if konfirmasi.buku_id else None)
    nama_organisasi = buku.nama if (buku and buku.nama) else (bendahara.full_name or "Kas Kelas")

    sisa_tagihan = max(0, int(siswa.tagihan - (siswa.sudah_bayar or 0)))

    has_qris = False
    qris_url = None
    if bendahara and bendahara.qris_path:
        qris_full = os.path.join(app.root_path, "static", "uploads", bendahara.qris_path)
        if os.path.exists(qris_full):
            has_qris = True
            qris_url = url_for("static", filename=f"uploads/{bendahara.qris_path}")

    if request.method == "POST":
        nominal_raw = request.form.get("nominal_bayar", str(sisa_tagihan))
        try:
            # Bersihkan semua karakter non-digit agar mendukung format '10.000', 'Rp 10.000', atau '10000'
            cleaned_digits = "".join(c for c in str(nominal_raw) if c.isdigit())
            nominal_val = float(cleaned_digits) if cleaned_digits else float(sisa_tagihan)
        except Exception:
            nominal_val = float(sisa_tagihan)
        if nominal_val <= 0:
            nominal_val = float(sisa_tagihan) if sisa_tagihan > 0 else 10000.0
        pesan_siswa = request.form.get("pesan_siswa", "").strip()

        file = request.files.get("bukti_transfer")
        if file and file.filename:
            ext = os.path.splitext(file.filename)[1].lower()
            if ext in [".jpg", ".jpeg", ".png", ".webp"]:
                filename = f"bukti_{konfirmasi.id}_{int(time.time())}{ext}"
                filepath = os.path.join(app.root_path, "static", "uploads", filename)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                file.save(filepath)
                konfirmasi.bukti_transfer = filename

        konfirmasi.tipe = "sudah_bayar"
        konfirmasi.nominal_bayar = nominal_val
        konfirmasi.pesan_siswa = pesan_siswa
        konfirmasi.status = "pending"
        konfirmasi.responded_at = datetime.now()
        db.session.commit()

        flash(f"✅ Terima kasih {siswa.nama}! Konfirmasi pembayaran sebesar Rp {int(nominal_val):,} telah dikirim ke Bendahara.", "success")
        return redirect(url_for("konfirmasi_pembayaran_siswa", token_konfirmasi=token_konfirmasi))

    return render_template(
        "konfirmasi_siswa.html",
        konfirmasi=konfirmasi,
        siswa=siswa,
        bendahara=bendahara,
        nama_organisasi=nama_organisasi,
        sisa_tagihan=sisa_tagihan,
        has_qris=has_qris,
        qris_url=qris_url,
    )


@app.route("/penagihan/konfirmasi_terima/<int:konfirmasi_id>", methods=["POST"])  # [ROUTE] Bendahara terima konfirmasi & potong tagihan
@login_required
def penagihan_konfirmasi_terima(konfirmasi_id):
    """
    Bendahara menekan tombol 'Konfirmasi & Kurangi Tagihan'.
    Tagihan siswa otomatis berkurang (siswa.sudah_bayar bertambah sejumlah nominal yang dibayar),
    status siswa otomatis menjadi Lunas jika tagihannya telah tercukupi,
    dan transaksi kas masuk otomatis dicatat ke pembukuan kas.
    """
    user_id = session.get("user_id")
    konfirmasi = KasKonfirmasi.query.filter_by(id=konfirmasi_id, user_id=user_id).first_or_404()
    siswa = KasSiswa.query.filter_by(id=konfirmasi.siswa_id, user_id=user_id).first()

    if not siswa:
        flash("Data siswa tidak ditemukan.", "danger")
        return redirect(url_for("penagihankas"))

    nominal = float(konfirmasi.nominal_bayar or (siswa.tagihan - (siswa.sudah_bayar or 0)))
    if nominal <= 0:
        nominal = float(siswa.sisa) if siswa.sisa > 0 else 0

    # 1. Update pembayaran siswa (mengurangi sisa tagihan)
    siswa.sudah_bayar = float(siswa.sudah_bayar or 0) + nominal
    if siswa.sudah_bayar >= siswa.tagihan:
        siswa.status = "Lunas"
    else:
        siswa.status = "Belum Lunas"

    # 2. Catat otomatis ke Kas Masuk pembukuan
    buku_id = konfirmasi.buku_id or siswa.buku_id or session.get("buku_id")
    transaksi = KasTransaction(
        user_id=user_id,
        buku_id=buku_id,
        tipe="masuk",
        nominal=nominal,
        keterangan=f"Iuran kas dari {siswa.nama} (Konfirmasi Gmail)",
        kategori="Iuran Kas",
        tanggal=datetime.now().date(),
    )
    db.session.add(transaksi)

    # 3. Ubah status konfirmasi menjadi diterima
    konfirmasi.status = "diterima"
    konfirmasi.responded_at = datetime.now()
    db.session.commit()

    flash(
        f"✅ Pembayaran {siswa.nama} sebesar Rp {int(nominal):,} berhasil dikonfirmasi! "
        f"Tagihan siswa otomatis berkurang dan dicatat ke kas masuk.",
        "success",
    )
    return redirect(url_for("penagihankas"))


@app.route("/penagihan/konfirmasi_tolak/<int:konfirmasi_id>", methods=["POST"])  # [ROUTE] Bendahara tutup/tolak konfirmasi
@login_required
def penagihan_konfirmasi_tolak(konfirmasi_id):
    """
    Bendahara menolak konfirmasi atau menandai pesan 'Belum Bisa Bayar' sebagai sudah dibaca.
    """
    user_id = session.get("user_id")
    konfirmasi = KasKonfirmasi.query.filter_by(id=konfirmasi_id, user_id=user_id).first_or_404()
    siswa = konfirmasi.siswa

    konfirmasi.status = "ditolak"
    konfirmasi.responded_at = datetime.now()
    db.session.commit()

    nama_siswa = siswa.nama if siswa else "Siswa"
    flash(f"Pemberitahuan dari {nama_siswa} telah ditandai selesai/ditutup.", "info")
    return redirect(url_for("penagihankas"))


# ==========================================
# [TAG: KIRIM TAGIHAN MANUAL] KIRIM EMAIL TAGIHAN LANGSUNG KE SISWA TERTENTU
# Penjelasan: Kirim email tagihan umum (bukan pengingat kas) ke satu siswa lewat form
# ==========================================


@app.route("/kirim-tagihan", methods=["POST"])  # [ROUTE] Kirim tagihan manual via email
@login_required
def kirim_tagihan():

    email_tujuan = (
        request.form.get("email")
        or request.form.get("kontak")
        or request.form.get("no_hp")
    )

    nama = request.form.get("nama") or "Pengguna"

    jumlah_tagihan = request.form.get("nominal") or request.form.get("tagihan", "0")

    pesan = build_anti_ban_tagihan_message(nama=nama, nominal=jumlah_tagihan)

    if not email_tujuan or "@" not in email_tujuan:
        flash(f"Gagal: Alamat email tujuan untuk {nama} tidak valid.", "warning")
        return redirect(url_for("penagihankas"))

    success, result = send_email_notification(
        email_tujuan, f"Tagihan Baru - {nama}", pesan, user_id=session.get("user_id")
    )

    if success:
        flash(
            f"Notifikasi tagihan berhasil dikirim ke {nama} ({email_tujuan}) via Email!",
            "success",
        )
    else:
        flash(f"Gagal mengirim notifikasi email ke {nama}: {result}", "danger")

    return redirect(url_for("penagihankas"))





# ==========================================
# [TAG: PORTAL SISWA] HALAMAN KHUSUS SISWA CEK STATUS TAGIHAN SENDIRI
# Penjelasan: Siswa login dengan token kas buku atau token tagihan → lihat rincian tagihan dan status lunas/belum
# ==========================================


@app.route("/portal", methods=["GET", "POST"])  # [ROUTE] Portal siswa (alias)
@app.route("/portal_siswa", methods=["GET", "POST"])  # [ROUTE] Portal siswa utama
def portal_siswa():
    # 1. Fitur keluar sepenuhnya dari akun
    if request.args.get("logout") or request.args.get("keluar"):
        session.clear()
        flash("Anda telah berhasil keluar.", "info")
        return redirect(url_for("login"))

    # 2. Fitur ganti kas (kembali ke landing page untuk memasukkan token kas lain)
    if request.args.get("ganti") or request.args.get("logout_kas"):
        session.pop("portal_token", None)
        flash("Silakan masukkan Token Kas yang ingin Anda buka.", "info")
        return redirect(url_for("home", _anchor="cek-tagihan"))

    # Cek apakah token diberikan langsung via query parameter atau form
    req_token = (request.args.get("token") or request.form.get("token") or "").strip()
    if req_token:
        clean_upper = req_token.upper()
        buku_found = BukuKas.query.filter(
            or_(
                func.upper(BukuKas.token) == clean_upper,
                func.upper(BukuKas.portal_username) == clean_upper,
                func.upper(BukuKas.nama) == clean_upper,
            )
        ).first()
        tagihan_found = None
        if not buku_found:
            tagihan_found = KasTagihan.query.filter(func.upper(KasTagihan.token) == clean_upper).first()
            if tagihan_found and tagihan_found.buku_id:
                buku_found = BukuKas.query.get(tagihan_found.buku_id)
        if buku_found or tagihan_found:
            token_direct = (buku_found.token if buku_found else tagihan_found.token).upper()
            session["portal_token"] = token_direct
            session["buku_id"] = buku_found.id if buku_found else (tagihan_found.buku_id if tagihan_found else None)

    # KETENTUAN: Pengguna WAJIB memiliki token sesi aktif atau token kas valid
    token = session.get("portal_token")
    if not token and "user_id" in session:
        user_id = session.get("user_id")
        b_active = get_or_create_user_buku(user_id)
        if b_active and b_active.token:
            token = b_active.token
            session["portal_token"] = token

    if not token:
        flash("Silakan masukkan Token Kas Anda untuk melihat rincian tagihan kas.", "info")
        return redirect(url_for("home", _anchor="cek-tagihan"))

    # 1. Cari berdasarkan token workspace pencatatan (1 token untuk 1 pencatatan)
    buku = BukuKas.query.filter(func.upper(BukuKas.token) == token).first()
    tagihan = None

    if buku:
        session["portal_token"] = token
        bendahara = User.query.get(buku.user_id)
        siswa_list = KasSiswa.query.filter_by(user_id=buku.user_id, buku_id=buku.id).order_by(KasSiswa.nama.asc()).all()
        tagihan_list = (
            KasTagihan.query.filter_by(user_id=buku.user_id, buku_id=buku.id)
            .order_by(KasTagihan.created_at.desc())
            .all()
        )
        tagihan = tagihan_list[0] if tagihan_list else None

        pengeluaran_list = (
            KasTransaction.query.filter_by(user_id=buku.user_id, buku_id=buku.id, tipe="keluar")
            .order_by(KasTransaction.tanggal.desc(), KasTransaction.created_at.desc())
            .all()
        )
        masuk_list = KasTransaction.query.filter_by(user_id=buku.user_id, buku_id=buku.id, tipe="masuk").all()
    else:
        # Fallback kompatibilitas: cari berdasarkan token tagihan
        tagihan = KasTagihan.query.filter(func.upper(KasTagihan.token) == token).first()
        if not tagihan:
            flash(f"Token pencatatan kas '{token}' tidak ditemukan atau tidak valid. Silakan periksa kembali token Anda.", "danger")
            session.pop("portal_token", None)
            return render_template("portal_siswa.html", tagihan=None, buku=None, entered_token=token)

        session["portal_token"] = token
        bendahara = User.query.get(tagihan.user_id)
        buku = BukuKas.query.get(tagihan.buku_id) if tagihan.buku_id else None

        if tagihan.buku_id:
            siswa_list = KasSiswa.query.filter_by(buku_id=tagihan.buku_id).order_by(KasSiswa.nama.asc()).all()
            pengeluaran_list = (
                KasTransaction.query.filter_by(buku_id=tagihan.buku_id, tipe="keluar")
                .order_by(KasTransaction.tanggal.desc(), KasTransaction.created_at.desc())
                .all()
            )
            masuk_list = KasTransaction.query.filter_by(buku_id=tagihan.buku_id, tipe="masuk").all()
        else:
            siswa_list = KasSiswa.query.filter_by(user_id=tagihan.user_id).order_by(KasSiswa.nama.asc()).all()
            pengeluaran_list = (
                KasTransaction.query.filter_by(user_id=tagihan.user_id, tipe="keluar")
                .order_by(KasTransaction.tanggal.desc(), KasTransaction.created_at.desc())
                .all()
            )
            masuk_list = KasTransaction.query.filter_by(user_id=tagihan.user_id, tipe="masuk").all()

    # Jika belum ada KasTagihan pada buku kas ini, buat representasi data agar template portal tidak error
    if not tagihan:
        class DummyTagihan:
            judul = buku.nama if buku else "Pencatatan Kas"
            kategori = "Iuran Kas"
            jatuh_tempo = None
            catatan = buku.deskripsi if buku else ""
            nominal = float(siswa_list[0].tagihan) if (siswa_list and siswa_list[0].tagihan) else 0.0
        tagihan = DummyTagihan()

    # Klasifikasi siswa: Sudah Bayar (Lunas) dan Belum Bayar
    for s in siswa_list:
        s.is_lunas = (s.status.lower() == "lunas" or s.sisa <= 0)

    total_siswa = len(siswa_list)
    siswa_lunas = [s for s in siswa_list if s.is_lunas]
    siswa_belum_lunas = [s for s in siswa_list if not s.is_lunas]
    total_lunas = len(siswa_lunas)
    total_belum_lunas = len(siswa_belum_lunas)

    total_terkumpul = sum(float(s.sudah_bayar or 0) for s in siswa_list)
    total_target = sum(float(s.tagihan or 0) for s in siswa_list)
    persen_tercapai = int(round(total_terkumpul / total_target * 100)) if total_target > 0 else 0

    total_pengeluaran = sum(float(t.nominal or 0) for t in pengeluaran_list)
    total_kas_masuk = sum(float(t.nominal or 0) for t in masuk_list)
    saldo_kas = total_kas_masuk - total_pengeluaran

    return render_template(
        "portal_siswa.html",
        tagihan=tagihan,
        token=token,
        buku=buku,
        bendahara=bendahara,
        siswa_list=siswa_list,
        siswa_lunas=siswa_lunas,
        siswa_belum_lunas=siswa_belum_lunas,
        total_siswa=total_siswa,
        total_lunas=total_lunas,
        total_belum_lunas=total_belum_lunas,
        total_terkumpul=total_terkumpul,
        total_target=total_target,
        persen_tercapai=persen_tercapai,
        pengeluaran_list=pengeluaran_list,
        total_pengeluaran=total_pengeluaran,
        total_kas_masuk=total_kas_masuk,
        saldo_kas=saldo_kas,
    )


# (Route pengingat WA telah dihapus)


# [TAG: BUAT AKUN SISWA] BENDAHARA BUAT AKUN LOGIN UNTUK SISWA
# Penjelasan: Otomatis buat akun user bertipe 'siswa' agar siswa bisa login mandiri ke portal siswa
@app.route("/penagihan/buat_akun_siswa/<int:siswa_id>", methods=["POST"])  # [ROUTE] Buat akun siswa
@bendahara_required
def penagihan_buat_akun_siswa(siswa_id):
    bendahara_id = session["user_id"]
    siswa = KasSiswa.query.filter_by(id=siswa_id, user_id=bendahara_id).first_or_404()

    # Periksa apakah siswa sudah memiliki akun login
    existing_user = User.query.filter_by(siswa_id=siswa.id).first()
    if existing_user:
        flash(
            f"Akun portal untuk {siswa.nama} sudah aktif (Username: {existing_user.username}).",
            "info",
        )
        return redirect(url_for("penagihankas"))

    # Buat username berdasarkan NIS atau nama
    base_username = (
        (siswa.nis or f"SIS{siswa.id:03d}").replace(" ", "").replace("-", "").lower()
    )
    username = base_username
    counter = 1
    while User.query.filter_by(username=username).first():
        username = f"{base_username}{counter}"
        counter += 1

    # Tentukan email unik
    if (
        siswa.kontak
        and "@" in siswa.kontak
        and not User.query.filter_by(email=siswa.kontak.strip()).first()
    ):
        email = siswa.kontak.strip()
    else:
        email = f"{username}@siswa.nusapay.local"

    # Generate kata sandi acak yang aman jika tidak ada custom password
    raw_password = request.form.get("password")
    if not raw_password:
        alphabet = string.ascii_uppercase + string.digits
        raw_password = ''.join(secrets.choice(alphabet) for _ in range(8))
        
    pwd_hash = generate_password_hash(raw_password)

    new_user = User(
        full_name=siswa.nama,
        username=username,
        email=email,
        password_hash=pwd_hash,
        plain_password=raw_password,
        role="siswa",
        siswa_id=siswa.id,
        whatsapp_number=siswa.kontak or "",
    )

    db.session.add(new_user)
    db.session.commit()

    flash(
        f'Akun Portal Siswa berhasil dibuat untuk "{siswa.nama}"! Username: {username} | Kata Sandi: {raw_password}',
        "success",
    )
    return redirect(url_for("penagihankas"))


# ==========================================
# [TAG: PENGATURAN] PROFIL AKUN, KONTAK, JADWAL REMINDER, QRIS, DAN KAS BERULANG
# Penjelasan: Halaman pengaturan user - email, QRIS upload, jadwal pengingat otomatis, dan kas berulang bulanan
# ==========================================


@app.route("/pengaturan", methods=["GET", "POST"])  # [ROUTE] Halaman pengaturan akun
@bendahara_required
def pengaturan():

    user_id = session["user_id"]

    user = User.query.get(user_id)

    if request.method == "POST":

        wa = request.form.get("whatsapp_number") or request.form.get("no_whatsapp")
        reminder_time = request.form.get("reminder_time")

        if wa:

            user.whatsapp_number = wa.strip()
            user.telegram_chat_id = wa.strip()
            
        if reminder_time:
            user.reminder_time = reminder_time.strip()
            
        user.is_kas_berulang_aktif = request.form.get("is_kas_berulang_aktif") == "on"
        
        kas_berulang_nominal = request.form.get("kas_berulang_nominal")
        if kas_berulang_nominal:
            try:
                user.kas_berulang_nominal = float(kas_berulang_nominal.replace("Rp", "").replace(".", "").replace(",", "").strip())
            except ValueError:
                pass
                
        kas_berulang_tanggal = request.form.get("kas_berulang_tanggal")
        if kas_berulang_tanggal:
            user.kas_berulang_tanggal = int(kas_berulang_tanggal)
            
        kas_berulang_jatuh_tempo_hari = request.form.get("kas_berulang_jatuh_tempo_hari")
        if kas_berulang_jatuh_tempo_hari:
            user.kas_berulang_jatuh_tempo_hari = int(kas_berulang_jatuh_tempo_hari)

        # Handle QRIS Image Upload
        qris_file = request.files.get("qris_image")
        if qris_file and qris_file.filename != "":
            filename = secure_filename(qris_file.filename)
            filename = f"qris_{user.id}_{int(time.time())}_{filename}"
            upload_path = os.path.join(app.root_path, "static", "uploads", filename)
            os.makedirs(os.path.dirname(upload_path), exist_ok=True)
            
            # Delete old image if exists to save space
            if user.qris_path:
                old_path = os.path.join(app.root_path, "static", "uploads", user.qris_path)
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except Exception:
                        pass

            qris_file.save(upload_path)
            user.qris_path = filename

        db.session.commit()
        flash("Pengaturan berhasil diperbarui!", "success")

        return redirect(url_for("pengaturan"))

    return render_template("pengaturan.html", user=user)


# ==========================================
# [TAG: TEST EMAIL] KIRIM EMAIL UJI COBA UNTUK CEK KONFIGURASI SMTP
# Penjelasan: Verifikasi bahwa pengiriman email Gmail berfungsi dengan mengirim email dummy ke alamat tujuan
# ==========================================


@app.route("/pengaturan/test_email", methods=["POST"])  # [ROUTE] Test kirim email
@bendahara_required
def pengaturan_test_email():

    user_id = session["user_id"]
    user = User.query.get(user_id)

    target_email = request.form.get("email") or (user.email if user else "")

    if not target_email:
        flash("Masukkan alamat email terlebih dahulu.", "warning")
        return redirect(url_for("pengaturan"))

    token_rand = generate_random_token(4)
    waktu_skrg = datetime.now().strftime("%d/%m/%Y %H:%M")

    pesan_tes = (
        f"Halo! Ini adalah pesan uji coba dari NusaPay "
        f"via Email.\n\n"
        f"Pesan ini digunakan untuk memastikan koneksi "
        f"SMTP Email sudah berjalan dengan benar.\n\n"
        f"Layanan pengingat tagihan dan notifikasi kas "
        f"NusaPay siap digunakan. 🙏\n\n"
        f"- NusaPay Manager\n"
        f"Ref: #TEST-{token_rand} • {waktu_skrg}"
    )

    success, result = send_email_notification(
        target_email, "Email Uji Coba NusaPay", pesan_tes, user_id=user_id
    )

    if success:
        flash(f"✅ Pesan uji coba Email berhasil dikirim ke {target_email}!", "success")
    else:
        flash(f"⚠️ {result}", "danger")

    return redirect(url_for("pengaturan"))


# ==========================================
# [TAG: BACKGROUND JOB] JADWAL OTOMATIS - REMINDER HARIAN & TAGIHAN BERULANG
# Penjelasan: Scheduler berjalan tiap menit. Jika cocok dengan jam reminder user, kirim email ke semua siswa menunggak.
#             Jika hari ini = tanggal terbit kas berulang, otomatis buat tagihan + tambah ke semua siswa.
# ==========================================


@scheduler.task("cron", id="auto_reminder_job", minute="*")  # [JOB] Cron tiap menit, cek jadwal reminder
def auto_reminder_job():
    with app.app_context():
        tz = pytz.timezone("Asia/Jakarta")
        now = datetime.now(tz)
        current_time_str = now.strftime("%H:%M")
        today = now.date()
        
        print(f"[SCHEDULER] Cek auto-reminder {today} jam {current_time_str}")

        # Ambil user yang waktunya pas, atau belum diset dan jamnya 08:00
        users_to_remind = User.query.filter(
            or_(
                User.reminder_time == current_time_str,
                db.and_(
                    or_(User.reminder_time == None, User.reminder_time == ""),
                    current_time_str == "08:00"
                )
            )
        ).all()

        for user in users_to_remind:
            # 0. Generate Tagihan Berulang Otomatis jika hari ini adalah tanggal terbitnya
            if user.is_kas_berulang_aktif and user.kas_berulang_tanggal == today.day:
                try:
                    locale.setlocale(locale.LC_TIME, "id_ID.UTF-8")
                except:
                    pass
                bulan_ini = today.strftime("%B %Y")
                judul_tagihan = f"Kas Otomatis - {bulan_ini}"
                
                existing_bill = KasTagihan.query.filter_by(user_id=user.id, judul=judul_tagihan).first()
                if not existing_bill:
                    jatuh_tempo_otomatis = today + timedelta(days=user.kas_berulang_jatuh_tempo_hari)
                    new_bill = KasTagihan(
                        user_id=user.id,
                        judul=judul_tagihan,
                        nominal=user.kas_berulang_nominal,
                        kategori="Uang Kas Bulanan",
                        jatuh_tempo=jatuh_tempo_otomatis,
                        catatan="Dibuat otomatis oleh sistem."
                    )
                    db.session.add(new_bill)
                    
                    # Tambahkan tagihan ini ke semua siswa
                    siswas = KasSiswa.query.filter_by(user_id=user.id).all()
                    for s in siswas:
                        s.tagihan = float(s.tagihan or 0) + float(user.kas_berulang_nominal)
                        s.status = "Belum Lunas" if float(s.sudah_bayar or 0) > 0 else "Belum Bayar"
                    
                    db.session.commit()
                    print(f"[AUTO BILL] Tagihan {judul_tagihan} dibuat untuk user {user.username}")

            # 1. Cek Tagihan Uang Kas
            kas_dues = KasTagihan.query.filter_by(user_id=user.id, jatuh_tempo=today).all()
            if kas_dues:
                qris_attachment = None
                has_qris = False
                if user.qris_path:
                    qris_full = os.path.join(app.root_path, "static", "uploads", user.qris_path)
                    if os.path.exists(qris_full):
                        qris_attachment = qris_full
                        has_qris = True

                for kas in kas_dues:
                    if kas.buku_id:
                        siswas = (
                            KasSiswa.query.filter_by(user_id=kas.user_id, buku_id=kas.buku_id)
                            .filter(KasSiswa.status != "Lunas")
                            .all()
                        )
                    else:
                        siswas = (
                            KasSiswa.query.filter_by(user_id=kas.user_id)
                            .filter(KasSiswa.status != "Lunas")
                            .all()
                        )
                    
                    for siswa in siswas:
                        if not siswa.kontak or "@" not in siswa.kontak:
                            continue
                        tagihan_fmt = "{:,.0f}".format(siswa.tagihan).replace(",", ".")
                        sudah_bayar_fmt = "{:,.0f}".format(siswa.sudah_bayar or 0).replace(",", ".")
                        sisa_fmt = "{:,.0f}".format(siswa.sisa).replace(",", ".")
                        
                        token_konf = get_or_create_confirmation_token(user.id, kas.buku_id, siswa.id)
                        base_url = os.getenv("APP_URL", "http://127.0.0.1:5000").rstrip("/")
                        url_sudah_bayar = f"{base_url}/konfirmasi_pembayaran/{token_konf}"

                        pesan_text = build_anti_ban_kas_message(
                            nama_siswa=siswa.nama,
                            kelas=siswa.kelas,
                            tagihan_fmt=tagihan_fmt,
                            sudah_bayar_fmt=sudah_bayar_fmt,
                            sisa_fmt=sisa_fmt,
                            has_qris=has_qris,
                            nama_organisasi=user.full_name or "Kelas",
                            url_sudah_bayar=url_sudah_bayar,
                        )
                        pesan_html = build_html_kas_message(
                            nama_siswa=siswa.nama,
                            kelas=siswa.kelas,
                            tagihan_fmt=tagihan_fmt,
                            sudah_bayar_fmt=sudah_bayar_fmt,
                            sisa_fmt=sisa_fmt,
                            has_qris=has_qris,
                            nama_organisasi=user.full_name or "Kelas",
                            url_sudah_bayar=url_sudah_bayar,
                        )

                        send_email_notification(
                            to_email=siswa.kontak.strip(),
                            subject=f"Pengingat Tagihan Kas - {kas.judul} ({siswa.nama})",
                            message_body=pesan_text,
                            html_body=pesan_html,
                            attachment_path=qris_attachment,
                            attachment_filename="QRIS_Pembayaran_Kas.jpg" if qris_attachment else None,
                            user_id=user.id,
                        )


# ==========================================
# [TAG: MAIN] JALANKAN FLASK SERVER
# Penjelasan: Entry point utama - baca HOST/PORT/FLASK_DEBUG dari environment, lalu jalankan server
# ==========================================

if __name__ == "__main__":

    host = os.getenv("HOST", "0.0.0.0")

    port = int(os.getenv("PORT", 5000))

    debug = os.getenv("FLASK_DEBUG", "1") == "1"

    app.run(debug=debug, host=host, port=port)
    