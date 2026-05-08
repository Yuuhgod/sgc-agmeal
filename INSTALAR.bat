@echo off
REM ============================================================
REM   SGC-AGMEAL - Instalador unico para Windows
REM ============================================================
REM   Este e o UNICO arquivo que voce precisa executar.
REM
REM   Como usar:
REM     1. Copie a pasta sgc-agmeal para o computador
REM     2. Duplo clique em INSTALAR.bat
REM     3. Aceite o pedido de Administrador (UAC)
REM     4. Siga as mensagens na tela
REM
REM   O script faz automaticamente:
REM     - Instala o WSL2 + Ubuntu (se ainda nao tiver)
REM     - Copia o projeto para dentro do WSL
REM     - Instala todas as dependencias (Python, WeasyPrint, etc.)
REM     - Cria os atalhos SGC-Iniciar.bat e SGC-Parar.bat na Area
REM       de Trabalho
REM ============================================================

setlocal EnableExtensions EnableDelayedExpansion

title SGC-AGMEAL - Instalador

REM ------- 1. Garantir privilegios de Administrador --------------
net session >nul 2>&1
if errorlevel 1 (
    echo Solicitando privilegios de Administrador...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

cls
echo.
echo ============================================================
echo   SGC-AGMEAL - Instalador completo para Windows
echo ============================================================
echo.

set "PROJETO_ORIGEM=%~dp0"
if "%PROJETO_ORIGEM:~-1%"=="\" set "PROJETO_ORIGEM=%PROJETO_ORIGEM:~0,-1%"
echo Pasta do projeto (origem): %PROJETO_ORIGEM%
echo.

REM ------- 2. Verificar se WSL esta instalado --------------------
echo [Etapa 1/4] Verificando WSL2 ...
where wsl.exe >nul 2>nul
if errorlevel 1 goto :instalar_wsl

REM Verifica se ha alguma distribuicao instalada
wsl.exe --status >nul 2>nul
if errorlevel 1 goto :instalar_wsl

REM Verifica especificamente se Ubuntu esta presente
wsl.exe -l -q 2>nul | findstr /i /c:"Ubuntu" >nul
if errorlevel 1 goto :instalar_ubuntu

echo   OK - WSL2 com Ubuntu detectado.
goto :verificar_usuario

:instalar_wsl
echo   WSL2 NAO esta instalado. Instalando agora ...
echo.
echo   Isto pode levar varios minutos. NAO feche esta janela.
echo.
wsl.exe --install -d Ubuntu
if errorlevel 1 (
    echo.
    echo ERRO ao instalar o WSL2.
    echo Tente executar manualmente no PowerShell ^(como Admin^):
    echo     wsl --install -d Ubuntu
    pause
    exit /b 1
)
echo.
echo ============================================================
echo   ATENCAO: o computador precisa ser REINICIADO agora
echo ============================================================
echo.
echo   1. Reinicie o computador
echo   2. Apos reiniciar, o Ubuntu abrira sozinho e pedira para
echo      voce criar um usuario e senha. Faca isso.
echo   3. Depois, execute INSTALAR.bat novamente para concluir.
echo.
pause
exit /b 0

:instalar_ubuntu
echo   WSL2 esta instalado, mas o Ubuntu nao. Instalando ...
wsl.exe --install -d Ubuntu
if errorlevel 1 (
    echo ERRO ao instalar Ubuntu.
    pause
    exit /b 1
)
echo.
echo Apos o Ubuntu abrir e voce criar usuario/senha, execute este
echo INSTALAR.bat novamente.
pause
exit /b 0

REM ------- 3. Verificar se o Ubuntu tem usuario configurado -----
:verificar_usuario
echo.
echo [Etapa 2/4] Verificando configuracao do Ubuntu ...

REM Tenta executar 'whoami' no Ubuntu. Se nao existir usuario padrao,
REM o WSL retorna 'root' ou erro - precisa de configuracao manual.
for /f "delims=" %%U in ('wsl.exe -d Ubuntu -e whoami 2^>nul') do set "WSL_USER=%%U"

if "%WSL_USER%"=="" (
    echo.
    echo   O Ubuntu ainda nao tem usuario configurado.
    echo   Abra o Ubuntu pelo menu Iniciar, crie seu usuario e senha,
    echo   e depois execute este INSTALAR.bat novamente.
    echo.
    pause
    exit /b 0
)

if /i "%WSL_USER%"=="root" (
    echo.
    echo   AVISO: voce esta logado como root no Ubuntu.
    echo   Recomendado criar um usuario comum, mas vou continuar.
)

echo   OK - Usuario Ubuntu detectado: %WSL_USER%

REM ------- 4. Copiar projeto para ~/sgc-agmeal no WSL ----------
echo.
echo [Etapa 3/4] Copiando arquivos do projeto para o WSL ...

REM Converte o caminho Windows para formato WSL
for /f "delims=" %%P in ('wsl.exe -d Ubuntu wslpath -u "%PROJETO_ORIGEM%"') do set "ORIGEM_WSL=%%P"

REM Copia tudo, exceto pastas pesadas/desnecessarias.
wsl.exe -d Ubuntu -e bash -lc "rm -rf ~/sgc-agmeal && mkdir -p ~/sgc-agmeal && cp -r '%ORIGEM_WSL%/.' ~/sgc-agmeal/ && rm -rf ~/sgc-agmeal/.venv ~/sgc-agmeal/.git ~/sgc-agmeal/__pycache__ ~/sgc-agmeal/sgc.pid ~/sgc-agmeal/sgc.log 2>/dev/null; chmod +x ~/sgc-agmeal/instalar.sh 2>/dev/null; true"
if errorlevel 1 (
    echo ERRO ao copiar arquivos para o WSL.
    pause
    exit /b 1
)
echo   OK - Projeto copiado para ~/sgc-agmeal

REM ------- 5. Disparar instalar.sh dentro do WSL ----------------
echo.
echo [Etapa 4/5] Executando instalador do projeto dentro do WSL ...
echo   ^(isto pode levar alguns minutos na primeira vez^)
echo.

wsl.exe -d Ubuntu -e bash -lc "cd ~/sgc-agmeal && bash instalar.sh"
if errorlevel 1 (
    echo.
    echo ERRO durante o instalar.sh. Verifique as mensagens acima.
    pause
    exit /b 1
)

REM ------- 6. URL bonita + atalho .url + auto-start no boot ------
echo.
echo [Etapa 5/5] Configurando URL amigavel e auto-start ...
echo.

set "DOMINIO=sgc.local"
set "URL_FINAL=http://%DOMINIO%"
set "USAR_PORTA_80=1"

REM 6.1 Verifica se a porta 80 esta livre
netstat -ano -p TCP | findstr /R /C:":80 .*LISTENING" >nul 2>nul
if not errorlevel 1 (
    echo   AVISO: porta 80 ja esta em uso por outro programa.
    echo   O atalho usara a porta 5000 ^(URL: http://%DOMINIO%:5000^).
    set "USAR_PORTA_80=0"
    set "URL_FINAL=http://%DOMINIO%:5000"
)

REM 6.2 Adiciona entrada no arquivo hosts (se nao existir)
set "HOSTS_FILE=%SystemRoot%\System32\drivers\etc\hosts"
findstr /R /C:"127\.0\.0\.1.*%DOMINIO%" "%HOSTS_FILE%" >nul 2>nul
if errorlevel 1 (
    echo   - Adicionando %DOMINIO% no arquivo hosts ...
    >>"%HOSTS_FILE%" echo.
    >>"%HOSTS_FILE%" echo 127.0.0.1   %DOMINIO%   # SGC-AGMEAL
) else (
    echo   - Entrada para %DOMINIO% ja existe em hosts.
)

REM 6.3 Configura redirecionamento porta 80 -^> 5000 ^(se possivel^)
if "%USAR_PORTA_80%"=="1" (
    echo   - Configurando redirecionamento de porta 80 -^> 5000 ...
    netsh interface portproxy delete v4tov4 listenport=80 listenaddress=0.0.0.0 >nul 2>nul
    netsh interface portproxy add v4tov4 listenport=80 listenaddress=0.0.0.0 connectport=5000 connectaddress=127.0.0.1 >nul
    if errorlevel 1 (
        echo     AVISO: nao consegui configurar portproxy. Usando porta 5000.
        set "URL_FINAL=http://%DOMINIO%:5000"
    ) else (
        netsh advfirewall firewall delete rule name="SGC-AGMEAL HTTP" >nul 2>nul
        netsh advfirewall firewall add rule name="SGC-AGMEAL HTTP" dir=in action=allow protocol=TCP localport=80 >nul 2>nul
    )
)

REM 6.4 Copia o icone do projeto para uma pasta fixa do Windows
REM (precisa ficar no Windows, nao no WSL, para o atalho funcionar)
set "ICON_DIR=%LOCALAPPDATA%\SGC-AGMEAL"
set "ICON_FILE=%ICON_DIR%\logo.ico"
if not exist "%ICON_DIR%" mkdir "%ICON_DIR%" >nul 2>nul

echo   - Copiando icone do sistema ...
REM Converte o caminho Windows para formato WSL (/mnt/c/...)
for /f "delims=" %%P in ('wsl.exe -d Ubuntu wslpath -u "%ICON_FILE%"') do set "ICON_FILE_WSL=%%P"
wsl.exe -d Ubuntu -e bash -lc "cp ~/sgc-agmeal/app/static/img/logo.ico '%ICON_FILE_WSL%'" >nul 2>nul
if not exist "%ICON_FILE%" (
    echo     AVISO: nao consegui copiar logo.ico - o atalho ficara sem icone.
)

REM 6.5 Cria atalho .url na Area de Trabalho do usuario
REM (Windows 11 frequentemente coloca a Desktop dentro de OneDrive)
set "DESKTOP=%USERPROFILE%\Desktop"
if not exist "%DESKTOP%" set "DESKTOP=%USERPROFILE%\OneDrive\Desktop"

set "ATALHO_URL=%DESKTOP%\SGC-AGMEAL.url"
echo   - Criando atalho na Area de Trabalho: %ATALHO_URL%
if exist "%ICON_FILE%" (
    (
        echo [InternetShortcut]
        echo URL=!URL_FINAL!
        echo IconFile=%ICON_FILE%
        echo IconIndex=0
    ) > "%ATALHO_URL%"
) else (
    (
        echo [InternetShortcut]
        echo URL=!URL_FINAL!
        echo IconIndex=0
    ) > "%ATALHO_URL%"
)

REM 6.6 Cria script .vbs invisivel para auto-start no boot
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS_AUTOSTART=%STARTUP_DIR%\SGC-AGMEAL-AutoStart.vbs"
echo   - Configurando auto-start ao ligar o computador ...
if not exist "%STARTUP_DIR%" mkdir "%STARTUP_DIR%" >nul 2>nul
(
    echo ' SGC-AGMEAL - Inicia o servidor automaticamente no boot do Windows
    echo ' Gerado por INSTALAR.bat - nao edite manualmente
    echo Set WshShell = CreateObject^("WScript.Shell"^)
    echo WshShell.Run "wsl.exe -d Ubuntu -e bash -lc ""cd ~/sgc-agmeal ^&^& bash start.sh""", 0, False
) > "%VBS_AUTOSTART%"

REM 6.7 Inicia o servidor agora mesmo (sem precisar de reboot)
echo   - Iniciando o servidor agora ...
wsl.exe -d Ubuntu -e bash -lc "cd ~/sgc-agmeal && bash start.sh" >nul 2>nul

echo.
echo ============================================================
echo   Instalacao concluida com sucesso!
echo ============================================================
echo.
echo  - Atalho na Area de Trabalho: SGC-AGMEAL ^(duplo clique para abrir^)
echo  - URL personalizada: %URL_FINAL%
echo  - Auto-start: o servidor ja inicia sozinho ao ligar o PC
echo.
echo  Atalhos avancados ^(opcional, ja na Area de Trabalho^):
echo    - SGC-Iniciar.bat   inicia manualmente o servidor
echo    - SGC-Parar.bat     encerra o servidor
echo.
echo  Abrindo o sistema agora ...
timeout /t 2 /nobreak >nul
start "" "%URL_FINAL%"

echo.
pause
endlocal
