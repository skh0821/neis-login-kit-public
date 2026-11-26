@echo off
rem Put the desktop icon (points window) and remove the old resident widget's autostart. Log: build\install_icon.log
rem From the Claude app run it through explorer.exe so AppData changes are not redirected into the app's private folder:
rem   Start-Process explorer.exe -ArgumentList "<full path>\widget\library-demo\install_icon.cmd"
rem ASCII only on purpose: cmd reads .cmd files in the ANSI code page (same rule as widget\open_widget.cmd).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_icon.ps1"
