@echo off
REM ============================================================
REM  SGC-AGMEAL - Backup diario para pasta sincronizada (Google Drive)
REM ============================================================
REM  1. Instale o "Google Drive para computador" e crie a pasta
REM     "SGC-AGMEAL-Backup" dentro do seu Google Drive (ou altere
REM     GFOLDER abaixo para outro caminho).
REM  2. Teste com duplo clique neste arquivo.
REM  3. No Agendador de Tarefas do Windows, crie uma tarefa diaria
REM     que execute este .bat (ver README.md).
REM ============================================================

setlocal EnableDelayedExpansion

set "GFOLDER=%USERPROFILE%\Google Drive\SGC-AGMEAL-Backup"
if not exist "%GFOLDER%" mkdir "%GFOLDER%" 2>nul

for /f "delims=" %%I in ('wsl.exe -d Ubuntu wslpath -u "%GFOLDER%" 2^>nul') do set "LINUX=%%I"
if "!LINUX!"=="" (
    echo [ERRO] Nao foi possivel converter o caminho para o WSL.
    echo Verifique se o Ubuntu ^(WSL^) esta instalado e se a pasta existe:
    echo   %GFOLDER%
    pause
    exit /b 1
)

echo Pasta sincronizada ^(WSL^): !LINUX!
echo Executando backup...
wsl.exe -d Ubuntu -e bash -lc "export BACKUP_SYNC_DIR='!LINUX!' && cd ~/sgc-agmeal && .venv/bin/python scripts/backup_cli.py"
if errorlevel 1 (
    echo [ERRO] Falha no backup. Veja mensagens acima.
    pause
    exit /b 1
)
echo OK - Backup concluido. O Google Drive sincronizara sozinho quando houver rede.
exit /b 0
