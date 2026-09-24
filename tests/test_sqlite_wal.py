"""SQLite em WAL: modo ativo, backup portátil e restauração sem arquivos -wal/-shm antigos."""

from __future__ import annotations

import logging
import os
import sqlite3
import zipfile

from sqlalchemy import text

from backup_service import criar_backup_zip
from database import db
from restore_service import aplicar_restauracao


def test_conexoes_usam_wal_e_busy_timeout(flask_app):
    with flask_app.app_context():
        with db.engine.connect() as conn:
            assert conn.execute(text('PRAGMA journal_mode')).scalar() == 'wal'
            assert conn.execute(text('PRAGMA busy_timeout')).scalar() == 15000


def test_banco_do_backup_vem_no_modo_tradicional(tmp_path, flask_app):
    data, bk, fotos = tmp_path / 'data', tmp_path / 'bk', tmp_path / 'fotos'
    for p in (data, bk, fotos):
        p.mkdir()
    conn = sqlite3.connect(data / 'sgc.db')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript('CREATE TABLE usuarios (id INT); CREATE TABLE associados (id INT); INSERT INTO associados VALUES (1);')
    info = criar_backup_zip(data_dir=str(data), upload_folder=str(fotos), backups_dir=str(bk), sync_dir=None,
                            keep_local=3, keep_sync=3, log=flask_app.logger)
    conn.close()
    extraido = tmp_path / 'x.db'
    with zipfile.ZipFile(info['zip_path']) as zf:
        extraido.write_bytes(zf.read('data/sgc.db'))
    ro = sqlite3.connect(f'file:{extraido}?mode=ro', uri=True)  # abre sozinho, sem -wal/-shm
    assert ro.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
    assert ro.execute('SELECT count(*) FROM associados').fetchone()[0] == 1


def test_restauracao_remove_wal_antigo(tmp_path):
    data, extr, fotos = tmp_path / 'data', tmp_path / 'extr' / 'data', tmp_path / 'fotos'
    for p in (data, extr, fotos):
        p.mkdir(parents=True)
    # Banco atual em WAL com uma gravação ainda no -wal.
    atual = sqlite3.connect(data / 'sgc.db')
    atual.execute('PRAGMA journal_mode=WAL')
    atual.execute('PRAGMA wal_autocheckpoint=0')
    atual.executescript('CREATE TABLE t (v TEXT); INSERT INTO t VALUES (\'antigo\');')
    # Banco do backup.
    novo = sqlite3.connect(extr / 'sgc.db')
    novo.executescript('CREATE TABLE t (v TEXT); INSERT INTO t VALUES (\'restaurado\');')
    novo.close()

    aplicar_restauracao(extract_root=str(tmp_path / 'extr'), data_dir=str(data), upload_folder=str(fotos),
                        log=logging.getLogger('teste'))
    atual.close()

    assert not os.path.exists(data / 'sgc.db-wal') or os.path.getsize(data / 'sgc.db-wal') == 0
    conn = sqlite3.connect(data / 'sgc.db')
    assert conn.execute('SELECT v FROM t').fetchall() == [('restaurado',)]
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_sem_gunicorn_nao_envia_sinal(flask_app, monkeypatch):
    import main

    enviados = []
    monkeypatch.setattr(main.os, 'kill', lambda *a: enviados.append(a))
    with flask_app.test_request_context(environ_base={'SERVER_SOFTWARE': 'Werkzeug/3'}):
        assert main._recarregar_workers_gunicorn() is False
    with flask_app.test_request_context(environ_base={'SERVER_SOFTWARE': 'gunicorn/23.0.0'}):
        assert main._recarregar_workers_gunicorn() is True
    assert len(enviados) == 1 and enviados[0][1] == main.signal.SIGHUP
