@echo off
setlocal
if not exist "%~dp0build\TeacherDeskDemo.exe" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
  if errorlevel 1 exit /b 1
)
if not exist "%~dp0build\TeacherDeskText.exe" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_text.ps1"
  if errorlevel 1 exit /b 1
)
start "" "%~dp0build\TeacherDeskText.exe"
