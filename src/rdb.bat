@echo off
cd /d "%~dp0"
start "" pythonw "rdb.py" %*
exit