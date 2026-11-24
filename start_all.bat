@echo off
chcp 65001 >nul
REM start_all.bat — chạy flow2api + Chrome farm trên Windows, không giữ cửa sổ lệnh.
REM Yêu cầu: python đã cài + pip install -r requirements.txt; Chrome đã cài.
REM Cửa sổ flow2api và farm chạy minimized ở taskbar; đóng = tắt app.

cd /d %~dp0
if not exist tmp mkdir tmp

start "flow2api" /min cmd /c "python flow2api.py >> tmp\host_run.log 2>&1"
start "farm" /min cmd /c "python browser_farm.py run >> tmp\farm_run.log 2>&1"

echo Da chay nen: flow2api + farm (2 cua so minimized trong taskbar).
echo Log: tmp\host_run.log, tmp\farm_run.log
echo Luu y: lan dau moi acc can dang nhap Google trong cua so Chrome farm.
pause
