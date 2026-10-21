# Mengaktifkan venv di PowerShell dan menjalankan app.py
if (Test-Path ".\venv\Scripts\Activate.ps1") {
    & ".\venv\Scripts\Activate.ps1"
}

python app.py
