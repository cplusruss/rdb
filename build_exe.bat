@echo off
cd /d "%~dp0"
rem the build tree lives outside Dropbox: its sync locks the thousands of files PyInstaller writes and rewrites
if not defined OUT set OUT=C:\tmp\rdb-build
if not defined PY set PY=C:\Python\313\python.exe
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name rdb ^
 --icon "%~dp0src\rdb.ico" --paths "%~dp0src" ^
 --workpath "%OUT%\work" --distpath "%OUT%\dist" --specpath "%OUT%" ^
 --collect-data docx ^
 --exclude-module tkinter --exclude-module pytest ^
 --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide2 ^
 --exclude-module pandas --exclude-module numpy --exclude-module scipy --exclude-module matplotlib ^
 --exclude-module torch --exclude-module tensorflow --exclude-module numba --exclude-module dask ^
 --exclude-module sympy --exclude-module IPython --exclude-module ipykernel --exclude-module jupyter_client ^
 --exclude-module notebook --exclude-module setuptools --exclude-module pkg_resources --exclude-module trio --exclude-module cupy ^
 --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets ^
 --exclude-module PySide6.QtWebChannel --exclude-module PySide6.QtWebEngineQuick ^
 "%~dp0src\rdb.py" || exit /b 1
if not exist dist mkdir dist || exit /b 1
rem never rdb.exe in the root or src: that name is the pylauncher launcher
move /y "%OUT%\dist\rdb.exe" dist\rdb.exe || exit /b 1
