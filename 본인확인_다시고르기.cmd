@echo off
chcp 65001 >nul
rem 본인 확인 방식(Windows Hello / 잠금 비밀번호)을 처음부터 다시 고르게 합니다. 다음에 글자를 누르면 다시 묻습니다.
"%~dp0.venv\Scripts\python.exe" "%~dp0windows_hello.py" --reset
echo 본인 확인 방식을 지웠습니다. 다음에 글자를 누르면 처음처럼 다시 고릅니다.
pause
