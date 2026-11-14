@echo off
rem Run ONCE: makes the bots start automatically when you log in to Windows.
set "TARGET=%~dp0start.bat"
set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SalonBot.lnk"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LINK%'); $s.TargetPath='%TARGET%'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
if errorlevel 1 (
    echo Could not create the shortcut.
    pause
    exit /b 1
)
echo Done. The bots will now start automatically after you log in.
echo To disable: delete SalonBot from the Startup folder (Win+R, shell:startup).
pause
