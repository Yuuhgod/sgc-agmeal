#!/usr/bin/env python3
"""
Backup agendado (sem interface web).

Uso típico no WSL (cron diário às 02:00):
    0 2 * * * cd ~/sgc-agmeal && .venv/bin/python scripts/backup_cli.py >>/tmp/sgc-backup.log 2>&1

Variáveis de ambiente (opcional):
    BACKUP_SYNC_DIR   Pasta sincronizada (ex.: Google Drive / OneDrive).
    BACKUP_KEEP_LOCAL / BACKUP_KEEP_SYNC — mesmos padrões do app.

Não envia e-mail nem usa API de nuvem: apenas grava ZIP em data/backups/
e copia para BACKUP_SYNC_DIR se estiver definido.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
APP_DIR = os.path.join(ROOT, 'app')
sys.path.insert(0, APP_DIR)
os.chdir(APP_DIR)

os.environ.setdefault('WTF_CSRF_ENABLED', '0')

from database import ACAO_SISTEMA_BACKUP, Auditoria, db  # noqa: E402

import main as main_mod  # noqa: E402

from backup_service import criar_backup_zip  # noqa: E402


def _backup_keep_local():
    try:
        return max(1, int(os.environ.get('BACKUP_KEEP_LOCAL', '14')))
    except ValueError:
        return 14


def _backup_keep_sync():
    try:
        return max(1, int(os.environ.get('BACKUP_KEEP_SYNC', '60')))
    except ValueError:
        return 60


def _backup_sync_dir():
    d = os.environ.get('BACKUP_SYNC_DIR', '').strip()
    return d or None


def cli_main() -> int:
    application = main_mod.app
    with application.app_context():
        sync = _backup_sync_dir()
        info = criar_backup_zip(
            data_dir=main_mod.data_dir,
            upload_folder=main_mod.UPLOAD_FOLDER,
            backups_dir=main_mod.backups_dir,
            sync_dir=sync,
            keep_local=_backup_keep_local(),
            keep_sync=_backup_keep_sync(),
            log=application.logger,
        )
        application.logger.info(
            'Backup agendado concluído: %s (%s bytes) sync=%s',
            info['zip_filename'],
            info['size_bytes'],
            info['sync_path'] or '(não)',
        )
        db.session.add(
            Auditoria(
                usuario_id=None,
                usuario_username='backup-agendado',
                acao=ACAO_SISTEMA_BACKUP,
                entidade='backup',
                entidade_id=None,
                descricao='Backup ZIP (agendado)',
                detalhes=(
                    f"arquivo={info['zip_filename']}\n"
                    f"tamanho_bytes={info['size_bytes']}\n"
                    f"copia_nuvem={'sim' if info['sync_path'] else 'não'}"
                ),
                ip_origem=None,
            )
        )
        db.session.commit()
    print(info['zip_filename'], info['size_bytes'], 'OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(cli_main())
