@echo off
chcp 65001 >nul
echo ========================================================
echo   Antigravity Migrator - EXE Derleme Araci
echo ========================================================
echo.

where py >nul 2>nul
if %errorlevel% equ 0 (
    set PY_CMD=py
) else (
    where python >nul 2>nul
    if %errorlevel% equ 0 (
        set PY_CMD=python
    ) else (
        echo [HATA] Python bulunamadi! Lutfen Python'u yukleyip PATH'e ekleyin.
        pause
        exit /b 1
    )
)

echo [1/2] PyInstaller kontrol ediliyor...
%PY_CMD% -m pip show pyinstaller >nul 2>nul
if %errorlevel% neq 0 (
    echo PyInstaller yukleniyor...
    %PY_CMD% -m pip install -r requirements.txt
)

echo [2/2] Tek dosyalik EXE derleniyor...
%PY_CMD% -m PyInstaller --onefile --noconsole --name "AntigravityMigrator" --distpath "." --workpath "build" --specpath "build" migrator.py

if %errorlevel% equ 0 (
    echo.
    echo ========================================================
    echo [BASARILI] AntigravityMigrator.exe basariyla olusturuldu!
    echo ========================================================
) else (
    echo.
    echo [HATA] Derleme sirasinda hata olustu.
)

pause
