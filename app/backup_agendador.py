"""Backup automático em segundo plano.

Roda dentro do próprio servidor: cada processo do Gunicorn tenta pegar um lock de
arquivo em ``data/``; só quem conseguir executa o agendador (se esse processo morrer,
o lock é liberado e outro assume na próxima tentativa). A cada verificação, se o
backup mais recente tiver mais de ``BACKUP_AUTO_INTERVALO_HORAS``, gera um novo.
Como o critério é a idade do último backup, um PC que fica desligado à noite faz o
backup logo depois de ligado.

O resultado da última tentativa fica em ``data/backups/.status_backup.json`` para
a interface mostrar (sucesso, falha e mensagem de erro).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime

try:  # Linux/WSL/Docker; em Windows nativo o agendador simplesmente não roda.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

ARQUIVO_STATUS = '.status_backup.json'
ARQUIVO_LOCK = '.backup_agendador.lock'
VERIFICAR_A_CADA_SEGUNDOS = 10 * 60

_iniciado = False
_trava_inicio = threading.Lock()


def ler_status(backups_dir: str) -> dict:
    try:
        with open(os.path.join(backups_dir, ARQUIVO_STATUS), encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def gravar_status(backups_dir: str, **campos) -> None:
    status = ler_status(backups_dir)
    status.update(campos)
    caminho = os.path.join(backups_dir, ARQUIVO_STATUS)
    tmp = caminho + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(status, fh, ensure_ascii=False)
    os.replace(tmp, caminho)


def idade_ultimo_backup_horas(backups_dir: str, listar) -> float | None:
    """Horas desde o backup mais recente em disco (None se não houver nenhum)."""
    recentes = listar(backups_dir, limite=1)
    if not recentes:
        return None
    return (time.time() - recentes[0]['mtime']) / 3600


def _pegar_lock(data_dir: str):
    """Tenta o lock exclusivo sem bloquear. Retorna o arquivo aberto (manter aberto!) ou None."""
    if fcntl is None:
        return None
    fh = open(os.path.join(data_dir, ARQUIVO_LOCK), 'a')
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


def executar_se_necessario(app, executar_backup, backups_dir: str, listar, intervalo_horas: float) -> bool:
    """Gera backup se o último for mais velho que o intervalo. Retorna True se gerou."""
    idade = idade_ultimo_backup_horas(backups_dir, listar)
    if idade is not None and idade < intervalo_horas:
        return False
    agora = datetime.now().isoformat(timespec='seconds')
    try:
        info = executar_backup()
    except Exception as exc:  # noqa: BLE001 — qualquer falha precisa aparecer na interface
        app.logger.exception('Backup automático falhou')
        gravar_status(backups_dir, ultima_tentativa=agora, ultima_falha=agora, erro=str(exc)[:300])
        return False
    gravar_status(
        backups_dir, ultima_tentativa=agora, ultimo_sucesso=agora, erro=None,
        arquivo=info['zip_filename'], copia_nuvem=bool(info.get('sync_path')),
    )
    app.logger.info('Backup automático concluído: %s', info['zip_filename'])
    return True


def iniciar(app, *, data_dir, backups_dir, executar_backup, listar, intervalo_horas: float) -> None:
    """Inicia (uma vez por processo) a thread que disputa o lock e roda o agendador."""
    global _iniciado
    with _trava_inicio:
        if _iniciado or fcntl is None:
            return
        _iniciado = True

    def laco():
        lock = None
        while True:
            if lock is None:
                lock = _pegar_lock(data_dir)
                if lock is not None:
                    app.logger.info('Agendador de backup ativo neste processo (pid %s).', os.getpid())
            if lock is not None:
                try:
                    with app.app_context():
                        executar_se_necessario(app, executar_backup, backups_dir, listar, intervalo_horas)
                except Exception:  # noqa: BLE001 — a thread nunca pode morrer
                    app.logger.exception('Erro inesperado no agendador de backup')
            time.sleep(VERIFICAR_A_CADA_SEGUNDOS)

    threading.Thread(target=laco, name='backup-agendador', daemon=True).start()
