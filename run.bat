@echo off
title Running Flask App in VENV
echo Mengaktifkan virtual environment (venv)...
call venv\Scripts\activate.bat
echo Virtual environment (venv) berhasil diaktifkan!
echo Menjalankan Flask Application...
python app.py
pause
