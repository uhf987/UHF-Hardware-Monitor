@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Nuitka ile exe olustur

rem ======================= AYARLAR (istersen degistir) =======================
rem Ana python dosyasi. Bos birakirsan otomatik bulunur:
rem main.py, app.py, ya da klasorde tek .py varsa o. Birden fazlaysa sorar.
set "MAIN="
rem Exe adi. Bos birakirsan klasor adi kullanilir.
set "APPNAME="
rem Surum (en fazla 4 sayi, ornek 1.0.0.0)
set "VERSION=1.0.0.0"
rem Yayinci / sirket adi (bos birakilabilir)
set "COMPANY="
rem 1 yaparsan konsol acik kalir ve hatalari gorursun
set "DEBUG=0"
rem 1 ise assets, resources, data, images, icons, static, templates, fonts klasorleri otomatik pakete eklenir
set "AUTO_DATA=1"
rem Ek Nuitka secenekleri, ornek: --include-package=yt_dlp
set "EXTRA_OPTS=--windows-uac-admin"
rem Derlemeden sonra cikti klasorune kopyalanacak ek dosyalar (bosluk ile ayir)
set "EXTRA_FILES="
rem ===========================================================================

rem --- Python ---
set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY where py >nul 2>&1 && set "PY=py"
if not defined PY (
  echo Python bulunamadi.
  pause
  exit /b 1
)

rem --- Nuitka ---
%PY% -m nuitka --version >nul 2>&1
if errorlevel 1 (
  echo Nuitka kuruluyor...
  %PY% -m pip install nuitka
  if errorlevel 1 (
    echo Nuitka kurulamadi.
    pause
    exit /b 1
  )
)

rem --- Ana dosya ---
if not "%~1"=="" set "MAIN=%~1"
if not defined MAIN if exist "main.py" set "MAIN=main.py"
if not defined MAIN if exist "app.py" set "MAIN=app.py"
if not defined MAIN (
  set /a COUNT=0
  for %%F in (*.py) do (
    set /a COUNT+=1
    set "ONLY=%%F"
  )
  if !COUNT! equ 1 set "MAIN=!ONLY!"
)
if not defined MAIN (
  echo Bu klasordeki python dosyalari:
  dir /b *.py
  set /p "MAIN=Derlenecek ana dosya adi: "
)
if not exist "%MAIN%" (
  echo Dosya bulunamadi: %MAIN%
  pause
  exit /b 1
)

rem --- Exe adi ---
if not defined APPNAME (
  for %%I in ("%CD%") do set "APPNAME=%%~nxI"
)
set "APPNAME=!APPNAME: =_!"

rem --- Kutuphane eklentisi (arayuz) ---
set "PLUGIN="
set "GUI=0"
findstr /i /m /c:"PyQt5" *.py >nul 2>&1 && (set "PLUGIN=--enable-plugins=pyqt5" & set "GUI=1")
if not defined PLUGIN findstr /i /m /c:"PyQt6" *.py >nul 2>&1 && (set "PLUGIN=--enable-plugins=pyqt6" & set "GUI=1")
if not defined PLUGIN findstr /i /m /c:"PySide6" *.py >nul 2>&1 && (set "PLUGIN=--enable-plugins=pyside6" & set "GUI=1")
if not defined PLUGIN findstr /i /m /c:"PySide2" *.py >nul 2>&1 && (set "PLUGIN=--enable-plugins=pyside2" & set "GUI=1")
if not defined PLUGIN findstr /i /m /c:"tkinter" *.py >nul 2>&1 && (set "PLUGIN=--enable-plugins=tk-inter" & set "GUI=1")

rem --- Konsol ---
set "CONSOLEARG="
if "%GUI%"=="1" if "%DEBUG%"=="0" set "CONSOLEARG=--windows-console-mode=disable"
if "%GUI%"=="1" if "%DEBUG%"=="1" set "CONSOLEARG=--windows-console-mode=attach"

rem --- Simge ---
set "ICON="
set "ICONARGS="
for %%F in (*.ico) do if not defined ICON set "ICON=%%F"
if defined ICON set "ICONARGS=--windows-icon-from-ico="!ICON!" --include-data-files="!ICON!=!ICON!""

rem --- Veri klasorleri ---
set "DATAARGS="
if "%AUTO_DATA%"=="1" (
  for %%D in (assets resources data images icons static templates fonts) do (
    if exist "%%D\" set "DATAARGS=!DATAARGS! --include-data-dir=%%D=%%D"
  )
)

rem --- Surum bilgisi (antivirus yanlis alarmini azaltmaya yardim eder) ---
set "VERARGS=--file-version=%VERSION% --product-version=%VERSION% --product-name="%APPNAME%" --file-description="%APPNAME%""
if defined COMPANY set "VERARGS=!VERARGS! --company-name="!COMPANY!""

echo.
echo  Ana dosya : %MAIN%
echo  Exe adi   : %APPNAME%
echo  Eklenti   : !PLUGIN!
echo  Simge     : !ICON!
echo.

%PY% -m nuitka --standalone --assume-yes-for-downloads --remove-output ^
 --output-dir=out --output-filename="%APPNAME%" --output-folder-name="%APPNAME%" ^
 !PLUGIN! !CONSOLEARG! !ICONARGS! !DATAARGS! !VERARGS! !EXTRA_OPTS! "%MAIN%"

if errorlevel 1 (
  echo.
  echo HATA: derleme basarisiz. Yukaridaki mesajlara bak.
  pause
  exit /b 1
)

set "DIST=out\%APPNAME%"
if not exist "%DIST%\" set "DIST=out\%MAIN:~0,-3%.dist"

echo.
echo Harici dosyalar kopyalaniyor...
for %%F in (*.dll ffmpeg.exe ffprobe.exe yt-dlp.exe %EXTRA_FILES%) do (
  if exist "%%F" (
    copy /y "%%F" "%DIST%\" >nul
    echo   kopyalandi: %%F
  )
)

findstr /i /m /c:"ffmpeg" *.py >nul 2>&1
if not errorlevel 1 if not exist "%DIST%\ffmpeg.exe" echo UYARI: kod ffmpeg kullaniyor gibi gorunuyor ama ffmpeg.exe cikti klasorunde yok. Baskasina verecekseniz ffmpeg.exe'yi proje klasorune koyup tekrar calistirin.

echo.
echo TAMAM. Cikti klasoru: %DIST%
choice /c EH /t 10 /d H /m "ZIP dosyasi da olusturulsun mu"
if errorlevel 2 goto :bitti
powershell -NoProfile -Command "Compress-Archive -Path '%DIST%\*' -DestinationPath '%APPNAME%.zip' -Force"
echo ZIP olusturuldu: %APPNAME%.zip

:bitti
explorer "%DIST%"
pause
endlocal
