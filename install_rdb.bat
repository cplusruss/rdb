@echo off
setlocal
cd /d "%~dp0"

rem Python 3.13: this machine's usual install, the per-user install, else winget puts in the per-user one
set "PY=C:\Python\313\python.exe"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PY%" (
  winget install --id Python.Python.3.13 -e --scope user --silent --accept-package-agreements --accept-source-agreements || goto :fail
)
if not exist "%PY%" (
  echo Python 3.13 not found after install: %PY%
  goto :fail
)

"%PY%" -m pip install --upgrade pip || goto :fail
"%PY%" -m pip install -r requirements.txt || goto :fail

rem RDB_SHORTCUT_DIR overrides the Desktop, so the script can be tried without touching it
if not defined RDB_SHORTCUT_DIR for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "RDB_SHORTCUT_DIR=%%D"
for %%P in ("%PY%") do set "PYW=%%~dpPpythonw.exe"
powershell -NoProfile -Command ^
 "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%RDB_SHORTCUT_DIR%\rdb.lnk');" ^
 "$s.TargetPath = '%PYW%'; $s.Arguments = '\"%~dp0src\rdb.py\"';" ^
 "$s.WorkingDirectory = '%~dp0src'; $s.IconLocation = '%~dp0src\rdb.ico'; $s.Save()" || goto :fail

echo rdb installed: %RDB_SHORTCUT_DIR%\rdb.lnk
exit /b 0

:fail
echo rdb install failed
exit /b 1
