@echo off
setlocal

set PYTHON_EXE=.venv\Scripts\python.exe

if not exist "%PYTHON_EXE%" (
	echo Khong tim thay .venv\Scripts\python.exe
	echo Hay tao moi truong truoc: python -m venv .venv
	pause
	exit /b 1
)

"%PYTHON_EXE%" -m pip install -r requirements.txt
"%PYTHON_EXE%" -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File generate_icon.ps1

if exist app_icon.ico (
	"%PYTHON_EXE%" -m PyInstaller --noconfirm --onefile --windowed --name SelediumVocabularyScraper --icon app_icon.ico --hidden-import selenium.webdriver.chrome.webdriver --hidden-import selenium.webdriver.chromium.webdriver selenium_vocab_gui.py
) else (
	"%PYTHON_EXE%" -m PyInstaller --noconfirm --onefile --windowed --name SelediumVocabularyScraper --hidden-import selenium.webdriver.chrome.webdriver --hidden-import selenium.webdriver.chromium.webdriver selenium_vocab_gui.py
)

if exist dist\SelediumVocabularyScraper.exe (
	copy /Y dist\SelediumVocabularyScraper.exe SelediumVocabularyScraper.exe >nul
)

echo.
echo Build xong.
echo - Double-click file: SelediumVocabularyScraper.exe
echo - File goc van co trong: dist\SelediumVocabularyScraper.exe
pause