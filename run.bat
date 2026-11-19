@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo === Обновление кода из Git ===
git pull

:: Проверяем, есть ли Python 3.13
py -3.13 --version >nul 2>nul
if %errorlevel% neq 0 (
    echo === Установка Python 3.13.5 через winget ===
    winget install --id Python.Python.3.13 --version 3.13.5 --exact --silent --accept-package-agreements --accept-source-agreements
)

if not exist ".venv" (
    echo === Создание виртуального окружения ===
    py -3.13 -m venv .venv
)

echo === Установка/обновление библиотек ===
.venv\Scripts\python.exe -m pip install -r requirements.txt

echo === Запуск интерфейса ===
.venv\Scripts\streamlit.exe run app.py
pause