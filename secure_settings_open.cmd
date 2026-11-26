@echo off
rem Open the secure settings window OUTSIDE the Claude desktop app (MSIX would redirect AppData writes).
rem ASCII only on purpose: cmd reads .cmd files in the ANSI code page.
cd /d "%~dp0"
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0secure_settings.pyw" --result "%LOCALAPPDATA%\neis-automation\secure_settings_last.json"
