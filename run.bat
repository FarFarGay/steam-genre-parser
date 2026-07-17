@echo off
rem Double-click launcher: full parse run with resume support.
rem Safe to close/interrupt — progress is checkpointed every 25 games;
rem just run it again and it continues where it stopped.
cd /d "%~dp0"
py -3.9 parse_steam_genre.py %*
echo.
echo ============================================
echo  Run finished. CSV files are in this folder.
echo ============================================
pause
