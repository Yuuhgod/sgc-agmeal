#!/usr/bin/env bash
# Copia (ou atualiza) o projeto para ~/sgc-agmeal no WSL SEM perder dados.
#
# Uso (chamado pelo INSTALAR.bat):  bash copiar_para_wsl.sh <pasta-de-origem>
#
# Numa atualização, a pasta de destino já tem o banco (data/), os backups locais
# (data/backups/) e as fotos (app/static/uploads/). Este script:
#   1. para o servidor, se estiver rodando;
#   2. guarda uma cópia de segurança desses dados em ~/sgc-agmeal-preservado-<data>/;
#   3. substitui o código pelo da origem;
#   4. devolve os dados preservados para o lugar.
# As 3 cópias de segurança mais recentes são mantidas; as mais antigas são removidas.
set -euo pipefail

ORIGEM="${1:?Informe a pasta de origem do projeto}"
DESTINO="${SGC_DESTINO:-$HOME/sgc-agmeal}"
MANTER_PRESERVADOS=3

if [ ! -f "$ORIGEM/app/main.py" ]; then
    echo "ERRO: $ORIGEM não parece ser a pasta do SGC-AGMEAL (app/main.py não encontrado)." >&2
    exit 1
fi

PRESERVADO=""
if [ -d "$DESTINO" ]; then
    # 1. Para o servidor para o banco não ser copiado no meio de uma gravação.
    if [ -f "$DESTINO/stop.sh" ]; then
        bash "$DESTINO/stop.sh" >/dev/null 2>&1 || true
    fi

    # 2. Cópia de segurança dos dados (fora da pasta que será substituída).
    if [ -d "$DESTINO/data" ] || [ -d "$DESTINO/app/static/uploads" ]; then
        PRESERVADO="$HOME/sgc-agmeal-preservado-$(date +%Y%m%d_%H%M%S)"
        mkdir -p "$PRESERVADO"
        if [ -d "$DESTINO/data" ]; then
            cp -a "$DESTINO/data" "$PRESERVADO/data"
        fi
        if [ -d "$DESTINO/app/static/uploads" ]; then
            cp -a "$DESTINO/app/static/uploads" "$PRESERVADO/uploads"
        fi
        echo "  Dados atuais preservados em: $PRESERVADO"
    fi
fi

# 3. Código novo (sem ambiente virtual, git, caches, PID e log antigos).
rm -rf "$DESTINO"
mkdir -p "$DESTINO"
cp -r "$ORIGEM/." "$DESTINO/"
rm -rf "$DESTINO/.venv" "$DESTINO/.git" "$DESTINO/sgc.pid" "$DESTINO/sgc.log"
find "$DESTINO" -name __pycache__ -type d -prune -exec rm -rf {} +

# 4. Devolve os dados preservados (eles prevalecem sobre qualquer data/ da origem).
if [ -n "$PRESERVADO" ]; then
    if [ -d "$PRESERVADO/data" ]; then
        rm -rf "$DESTINO/data"
        cp -a "$PRESERVADO/data" "$DESTINO/data"
    fi
    if [ -d "$PRESERVADO/uploads" ]; then
        rm -rf "$DESTINO/app/static/uploads"
        cp -a "$PRESERVADO/uploads" "$DESTINO/app/static/uploads"
    fi
    echo "  Banco, backups e fotos mantidos."

    # Mantém só as cópias de segurança mais recentes.
    ls -1d "$HOME"/sgc-agmeal-preservado-* 2>/dev/null | sort -r | tail -n +$((MANTER_PRESERVADOS + 1)) \
        | while read -r antigo; do rm -rf "$antigo"; done
fi

chmod +x "$DESTINO/instalar.sh" 2>/dev/null || true
echo "  Projeto copiado para $DESTINO"
