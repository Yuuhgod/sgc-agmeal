#!/usr/bin/env bash
# ============================================================
#  SGC-AGMEAL — Instalador da parte Linux/WSL do projeto
# ============================================================
#  Normalmente este script é chamado AUTOMATICAMENTE por
#  INSTALAR.bat (no Windows), que cuida da instalação do WSL2
#  e copia os arquivos do projeto para ~/sgc-agmeal antes de
#  executar este instalador.
#
#  Você pode rodá-lo manualmente também:
#
#      bash instalar.sh
#
#  O script faz:
#    - Instala Python e dependências nativas (Cairo / Pango / GLib)
#    - Cria o ambiente virtual e instala pacotes Python
#    - Cria os diretórios necessários (data, uploads)
#    - Gera start.sh e stop.sh (controle do servidor)
#    - Gera SGC-Iniciar.bat e SGC-Parar.bat (atalhos para Windows)
#    - Copia os atalhos .bat para a Área de Trabalho do Windows
# ============================================================

set -e

PROJETO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJETO_DIR"

cor_titulo() { printf '\n\033[1;36m%s\033[0m\n' "$1"; }
cor_passo()  { printf '\033[1;33m▸ %s\033[0m\n'   "$1"; }
cor_ok()     { printf '\033[1;32m✓ %s\033[0m\n'   "$1"; }
cor_aviso()  { printf '\033[1;31m! %s\033[0m\n'   "$1"; }

cor_titulo "==================================================="
cor_titulo "  SGC-AGMEAL — Instalador (WSL2 / Ubuntu)"
cor_titulo "==================================================="
echo "Diretório do projeto: $PROJETO_DIR"
echo

# ------------------------------------------------------------
# 0. Verificações de ambiente
# ------------------------------------------------------------
if ! grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
    cor_aviso "Atenção: este script foi desenhado para rodar dentro do WSL2."
    cor_aviso "Em outros Linux ele funciona, mas os atalhos .bat só servem no Windows."
    echo
fi

if [ ! -f "$PROJETO_DIR/requirements.txt" ] || [ ! -f "$PROJETO_DIR/app/main.py" ]; then
    cor_aviso "ERRO: este script deve ficar na raiz do projeto SGC-AGMEAL."
    cor_aviso "Não encontrei requirements.txt ou app/main.py em $PROJETO_DIR"
    exit 1
fi

# ------------------------------------------------------------
# 1. Dependências do sistema
# ------------------------------------------------------------
cor_passo "[1/6] Atualizando lista de pacotes do sistema..."
sudo apt-get update -qq

cor_passo "[2/6] Instalando Python e dependências nativas do WeasyPrint..."
sudo apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
    libcairo2 libgdk-pixbuf-2.0-0 \
    libffi-dev shared-mime-info \
    fonts-dejavu-core

# ------------------------------------------------------------
# 2. Ambiente virtual + pacotes Python
# ------------------------------------------------------------
cor_passo "[3/6] Criando ambiente virtual em .venv ..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

cor_passo "[4/6] Instalando dependências Python (pode levar 1-2 min)..."
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements.txt --quiet

# ------------------------------------------------------------
# 3. Diretórios necessários
# ------------------------------------------------------------
cor_passo "[5/6] Criando diretórios de dados e uploads..."
mkdir -p data
mkdir -p app/static/uploads/fotos
chmod 700 data

# Regenera logo.ico se logo.jpeg for mais recente (ou se .ico não existir).
if [ -f "app/static/img/logo.jpeg" ]; then
    if [ ! -f "app/static/img/logo.ico" ] || \
       [ "app/static/img/logo.jpeg" -nt "app/static/img/logo.ico" ]; then
        echo "  - Gerando logo.ico (multi-resolução) a partir de logo.jpeg ..."
        .venv/bin/python - <<'PYICO'
from PIL import Image
img = Image.open('app/static/img/logo.jpeg').convert('RGBA')
w, h = img.size
side = min(w, h)
left = (w - side) // 2
top  = (h - side) // 2
img = img.crop((left, top, left + side, top + side))
img.save('app/static/img/logo.ico', format='ICO',
         sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
PYICO
    fi
fi

# ------------------------------------------------------------
# 4. Gerar scripts auxiliares (start.sh, stop.sh)
# ------------------------------------------------------------
cor_passo "[6/6] Gerando scripts de controle (start.sh / stop.sh)..."

cat > "$PROJETO_DIR/start.sh" <<'START_SH'
#!/usr/bin/env bash
# Inicia o Gunicorn em background. Gerado por instalar.sh.
set -e
PROJETO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$PROJETO_DIR/sgc.pid"
LOG_FILE="$PROJETO_DIR/sgc.log"
PORTA="${SGC_PORTA:-5000}"
WORKERS="${SGC_WORKERS:-2}"
cd "$PROJETO_DIR"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "SGC-AGMEAL já está rodando (PID $(cat "$PID_FILE"))."
    echo "Acesse: http://localhost:${PORTA}"
    exit 0
fi

if [ ! -x ".venv/bin/gunicorn" ]; then
    echo "ERRO: ambiente virtual não encontrado."
    echo "Execute primeiro:  bash instalar.sh"
    exit 1
fi

mkdir -p data app/static/uploads/fotos
echo "Iniciando SGC-AGMEAL na porta ${PORTA} com ${WORKERS} worker(s)..."

.venv/bin/gunicorn \
    --chdir "$PROJETO_DIR/app" \
    --workers "$WORKERS" \
    --bind "0.0.0.0:${PORTA}" \
    --access-logfile "$LOG_FILE" \
    --error-logfile "$LOG_FILE" \
    --daemon \
    --pid "$PID_FILE" \
    main:app

sleep 1
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Iniciado com sucesso (PID $(cat "$PID_FILE"))."
    echo "Acesse: http://localhost:${PORTA}"
    echo "Logs em: $LOG_FILE"
else
    echo "ERRO ao iniciar. Verifique o log: $LOG_FILE"
    exit 1
fi
START_SH

cat > "$PROJETO_DIR/stop.sh" <<'STOP_SH'
#!/usr/bin/env bash
# Encerra o Gunicorn pelo PID salvo. Gerado por instalar.sh.
set -e
PROJETO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$PROJETO_DIR/sgc.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "SGC-AGMEAL não está rodando (arquivo PID não encontrado)."
    exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
    echo "Encerrando SGC-AGMEAL (PID $PID)..."
    kill "$PID"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if ! kill -0 "$PID" 2>/dev/null; then break; fi
        sleep 1
    done
    if kill -0 "$PID" 2>/dev/null; then
        echo "Encerramento limpo demorou demais — forçando (kill -9)..."
        kill -9 "$PID" 2>/dev/null || true
    fi
    echo "SGC-AGMEAL encerrado."
else
    echo "Processo PID $PID não está mais ativo. Removendo arquivo PID."
fi
rm -f "$PID_FILE"
STOP_SH

chmod +x "$PROJETO_DIR/start.sh" "$PROJETO_DIR/stop.sh"

# ------------------------------------------------------------
# 5. Gerar atalhos .bat (CRLF para Windows)
# ------------------------------------------------------------

# Caminho do projeto traduzido para o WSL (~/algo). Se o usuário
# colocou em outro lugar, o caminho absoluto também funciona.
WSL_PATH="$PROJETO_DIR"
if [ -n "$HOME" ] && [[ "$PROJETO_DIR" == "$HOME"* ]]; then
    WSL_PATH="~${PROJETO_DIR#$HOME}"
fi

# Gera .bat com terminadores CRLF (formato Windows)
gerar_bat_iniciar() {
    local destino="$1"
    {
        printf '@echo off\r\n'
        printf 'REM SGC-AGMEAL - Iniciar (gerado por instalar.sh)\r\n'
        printf 'setlocal\r\n'
        printf 'set "PROJETO_WSL=%s"\r\n' "$WSL_PATH"
        printf 'set "PORTA=5000"\r\n'
        printf '\r\n'
        printf 'echo.\r\n'
        printf 'echo ============================================================\r\n'
        printf 'echo   SGC-AGMEAL - Iniciando o sistema\r\n'
        printf 'echo ============================================================\r\n'
        printf '\r\n'
        printf 'where wsl.exe ^>nul 2^>nul\r\n'
        printf 'if errorlevel 1 (\r\n'
        printf '    echo ERRO: WSL nao esta instalado.\r\n'
        printf '    echo Abra o PowerShell como Administrador e execute:\r\n'
        printf '    echo     wsl --install -d Ubuntu\r\n'
        printf '    pause\r\n'
        printf '    exit /b 1\r\n'
        printf ')\r\n'
        printf '\r\n'
        printf 'echo Iniciando o servidor no WSL2...\r\n'
        printf 'wsl.exe bash -lc "cd %%PROJETO_WSL%% && bash start.sh"\r\n'
        printf 'if errorlevel 1 (\r\n'
        printf '    echo ERRO ao iniciar o servidor.\r\n'
        printf '    pause\r\n'
        printf '    exit /b 1\r\n'
        printf ')\r\n'
        printf '\r\n'
        printf 'echo Aguardando 3 segundos...\r\n'
        printf 'timeout /t 3 /nobreak ^>nul\r\n'
        printf 'echo Abrindo navegador em http://localhost:%%PORTA%% ...\r\n'
        printf 'start "" "http://localhost:%%PORTA%%"\r\n'
        printf '\r\n'
        printf 'echo.\r\n'
        printf 'echo Sistema iniciado. Pode fechar esta janela.\r\n'
        printf 'timeout /t 5 ^>nul\r\n'
        printf 'endlocal\r\n'
    } > "$destino"
}

gerar_bat_parar() {
    local destino="$1"
    {
        printf '@echo off\r\n'
        printf 'REM SGC-AGMEAL - Parar (gerado por instalar.sh)\r\n'
        printf 'setlocal\r\n'
        printf 'set "PROJETO_WSL=%s"\r\n' "$WSL_PATH"
        printf '\r\n'
        printf 'echo.\r\n'
        printf 'echo ============================================================\r\n'
        printf 'echo   SGC-AGMEAL - Encerrando o sistema\r\n'
        printf 'echo ============================================================\r\n'
        printf '\r\n'
        printf 'where wsl.exe ^>nul 2^>nul\r\n'
        printf 'if errorlevel 1 (\r\n'
        printf '    echo ERRO: WSL nao esta instalado.\r\n'
        printf '    pause\r\n'
        printf '    exit /b 1\r\n'
        printf ')\r\n'
        printf '\r\n'
        printf 'wsl.exe bash -lc "cd %%PROJETO_WSL%% && bash stop.sh"\r\n'
        printf 'if errorlevel 1 (\r\n'
        printf '    echo Houve um problema ao encerrar.\r\n'
        printf '    pause\r\n'
        printf '    exit /b 1\r\n'
        printf ')\r\n'
        printf '\r\n'
        printf 'echo Servidor encerrado.\r\n'
        printf 'timeout /t 3 ^>nul\r\n'
        printf 'endlocal\r\n'
    } > "$destino"
}

cor_passo "Gerando atalhos para Windows (SGC-Iniciar.bat / SGC-Parar.bat)..."
gerar_bat_iniciar "$PROJETO_DIR/SGC-Iniciar.bat"
gerar_bat_parar  "$PROJETO_DIR/SGC-Parar.bat"

# ------------------------------------------------------------
# 6. Oferecer copiar atalhos para a Área de Trabalho do Windows
# ------------------------------------------------------------
DESKTOP_WIN=""
if command -v cmd.exe >/dev/null 2>&1; then
    USERPROFILE_WIN="$(cmd.exe /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r\n')"
    if [ -n "$USERPROFILE_WIN" ]; then
        # Converte C:\Users\xxx -> /mnt/c/Users/xxx
        DESKTOP_WIN="$(wslpath -u "$USERPROFILE_WIN" 2>/dev/null)/Desktop"
        if [ ! -d "$DESKTOP_WIN" ]; then
            # Pode ser OneDrive Desktop em Windows 11
            ONEDRIVE_DESKTOP="$(wslpath -u "$USERPROFILE_WIN" 2>/dev/null)/OneDrive/Desktop"
            [ -d "$ONEDRIVE_DESKTOP" ] && DESKTOP_WIN="$ONEDRIVE_DESKTOP"
        fi
    fi
fi

if [ -n "$DESKTOP_WIN" ] && [ -d "$DESKTOP_WIN" ]; then
    echo
    echo "Detectei a Área de Trabalho do Windows em:"
    echo "  $DESKTOP_WIN"
    resp="s"
    if [ -t 0 ]; then
        # Só pergunta se houver terminal interativo
        read -r -p "Copiar SGC-Iniciar.bat e SGC-Parar.bat para lá? [S/n] " resp || resp="s"
    else
        echo "(modo não-interativo: copiando atalhos automaticamente)"
    fi
    if [[ ! "$resp" =~ ^[Nn]$ ]]; then
        if cp "$PROJETO_DIR/SGC-Iniciar.bat" "$DESKTOP_WIN/" 2>/dev/null && \
           cp "$PROJETO_DIR/SGC-Parar.bat"   "$DESKTOP_WIN/" 2>/dev/null; then
            cor_ok "Atalhos copiados para a Área de Trabalho do Windows."
        else
            cor_aviso "Não consegui copiar para o Desktop (verifique permissões)."
        fi
    fi
fi

# ------------------------------------------------------------
# Resumo final
# ------------------------------------------------------------
echo
cor_titulo "==================================================="
cor_ok    "  Instalação concluída com sucesso!"
cor_titulo "==================================================="
echo
echo "Caminho do projeto no WSL : $WSL_PATH"
echo "Arquivos gerados:"
echo "  - start.sh             (inicia o servidor)"
echo "  - stop.sh              (encerra o servidor)"
echo "  - SGC-Iniciar.bat      (atalho duplo-clique no Windows)"
echo "  - SGC-Parar.bat        (atalho duplo-clique no Windows)"
echo
echo "Para iniciar agora mesmo (no WSL):"
echo "    bash start.sh"
echo
echo "Para uso diário no Windows: duplo clique em SGC-Iniciar.bat"
echo "(o navegador abre automaticamente em http://localhost:5000)"
echo
