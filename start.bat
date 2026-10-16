@echo off
cd /d "%~dp0"
echo PopPay Hub — FAIL toast Windows. Tab browser boleh ditutup.
python -m pip install -r requirements.txt
start "" cmd /c "ping 127.0.0.1 -n 3 >nul & start http://127.0.0.1:3999/"
python hub.py
pause
