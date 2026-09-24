"""Testes do backup automático, da verificação de integridade e dos avisos."""

from __future__ import annotations

import os
import sqlite3
import time
import zipfile

import pytest

import backup_agendador
from backup_service import criar_backup_zip, listar_backups_locais, verificar_backup_zip
from database import ACAO_SISTEMA_BACKUP, Auditoria


@pytest.fixture
def pastas(tmp_path):
    data = tmp_path / 'data'
    fotos = tmp_path / 'fotos'
    backups = data / 'backups'
    for p in (data, fotos, backups):
        p.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data / 'sgc.db')
    conn.executescript('CREATE TABLE usuarios (id INTEGER); CREATE TABLE associados (id INTEGER);')
    conn.close()
    (fotos / 'a.jpg').write_bytes(b'\xff\xd8\xff' + b'0' * 50)
    return {'data': str(data), 'fotos': str(fotos), 'backups': str(backups)}


def _criar(pastas, log):
    return criar_backup_zip(
        data_dir=pastas['data'], upload_folder=pastas['fotos'], backups_dir=pastas['backups'],
        sync_dir=None, keep_local=5, keep_sync=5, log=log,
    )


# --- Integridade ------------------------------------------------------------------------

def test_backup_valido_passa_na_verificacao(pastas, flask_app):
    info = _criar(pastas, flask_app.logger)
    verificar_backup_zip(info['zip_path'])  # não levanta
    with zipfile.ZipFile(info['zip_path']) as zf:
        assert {'data/sgc.db', 'fotos/a.jpg', 'LEIA-ME.txt'} <= set(zf.namelist())


def test_zip_truncado_e_recusado(pastas, flask_app, tmp_path):
    info = _criar(pastas, flask_app.logger)
    dados = open(info['zip_path'], 'rb').read()
    ruim = tmp_path / 'ruim.zip'
    ruim.write_bytes(dados[: len(dados) // 2])
    with pytest.raises(ValueError):
        verificar_backup_zip(str(ruim))


def test_zip_com_banco_estranho_e_recusado(tmp_path):
    db = tmp_path / 'outro.db'
    sqlite3.connect(db).executescript('CREATE TABLE qualquer (id INTEGER);')
    z = tmp_path / 'b.zip'
    with zipfile.ZipFile(z, 'w') as zf:
        zf.write(db, 'data/sgc.db')
    with pytest.raises(ValueError, match='tabelas esperadas'):
        verificar_backup_zip(str(z))


def test_zip_com_banco_corrompido_e_recusado(tmp_path):
    z = tmp_path / 'c.zip'
    with zipfile.ZipFile(z, 'w') as zf:
        zf.writestr('data/sgc.db', b'isto nao e um banco sqlite' * 100)
    with pytest.raises(ValueError):
        verificar_backup_zip(str(z))


# --- Agendador --------------------------------------------------------------------------

def test_agendador_so_faz_backup_quando_o_ultimo_esta_velho(pastas, flask_app):
    chamadas = []

    def executar():
        chamadas.append(1)
        return _criar(pastas, flask_app.logger)

    args = (flask_app, executar, pastas['backups'], listar_backups_locais, 24)
    assert backup_agendador.executar_se_necessario(*args) is True  # nenhum backup ainda
    assert backup_agendador.executar_se_necessario(*args) is False  # acabou de fazer
    assert len(chamadas) == 1

    # Envelhece o backup existente para 25 horas atrás.
    recente = listar_backups_locais(pastas['backups'])[0]['caminho']
    velho = time.time() - 25 * 3600
    os.utime(recente, (velho, velho))
    time.sleep(1.1)  # nome do ZIP tem resolução de segundos
    assert backup_agendador.executar_se_necessario(*args) is True
    assert len(chamadas) == 2

    status = backup_agendador.ler_status(pastas['backups'])
    assert status['ultimo_sucesso'] and status['erro'] is None


def test_agendador_registra_falha(pastas, flask_app):
    def falha():
        raise OSError('disco cheio')

    assert backup_agendador.executar_se_necessario(
        flask_app, falha, pastas['backups'], listar_backups_locais, 24,
    ) is False
    status = backup_agendador.ler_status(pastas['backups'])
    assert status['erro'] == 'disco cheio' and status['ultima_falha']


def test_lock_permite_um_so_agendador(tmp_path):
    primeiro = backup_agendador._pegar_lock(str(tmp_path))
    assert primeiro is not None
    try:
        assert backup_agendador._pegar_lock(str(tmp_path)) is None  # outro processo/worker
    finally:
        primeiro.close()
    segundo = backup_agendador._pegar_lock(str(tmp_path))  # liberado: outro assume
    assert segundo is not None
    segundo.close()


def test_backup_automatico_registra_auditoria(flask_app):
    import nucleo

    with flask_app.app_context():
        antes = Auditoria.query.filter_by(acao=ACAO_SISTEMA_BACKUP, usuario_username='backup-automático').count()
        info = nucleo._executar_backup_automatico()
        assert os.path.isfile(info['zip_path'])
        depois = Auditoria.query.filter_by(acao=ACAO_SISTEMA_BACKUP, usuario_username='backup-automático').count()
    assert depois == antes + 1


# --- Avisos -----------------------------------------------------------------------------

def test_painel_alerta_admin_sem_backup_recente(admin_client, flask_app, monkeypatch):
    import nucleo

    monkeypatch.setattr(nucleo.backup_agendador, 'idade_ultimo_backup_horas', lambda *a: None)
    r = admin_client.get('/')
    assert 'Nenhum backup foi feito ainda'.encode() in r.data

    monkeypatch.setattr(nucleo.backup_agendador, 'idade_ultimo_backup_horas', lambda *a: 2.0)
    monkeypatch.setattr(nucleo.backup_agendador, 'ler_status', lambda *a: {})
    r = admin_client.get('/')
    assert 'Fazer backup agora'.encode() not in r.data

    monkeypatch.setattr(nucleo.backup_agendador, 'idade_ultimo_backup_horas', lambda *a: 24 * 10)
    r = admin_client.get('/')
    assert 'O último backup tem 10 dias'.encode() in r.data


def test_painel_mostra_falha_do_backup(admin_client, monkeypatch):
    import nucleo

    monkeypatch.setattr(nucleo.backup_agendador, 'idade_ultimo_backup_horas', lambda *a: 1.0)
    monkeypatch.setattr(nucleo.backup_agendador, 'ler_status', lambda *a: {
        'ultimo_sucesso': '2026-01-01T10:00:00', 'ultima_falha': '2026-01-02T10:00:00', 'erro': 'disco cheio',
    })
    r = admin_client.get('/')
    assert 'O último backup falhou'.encode() in r.data and b'disco cheio' in r.data
    r = admin_client.get('/admin/backup')
    assert 'Falha em 2026-01-02 10:00:00: disco cheio'.encode() in r.data


def test_usuario_comum_nao_ve_alerta_de_backup(flask_app, monkeypatch):
    import nucleo
    from tests.test_correcoes_seguranca import _login
    from database import ROLE_USUARIO, Usuario, db

    monkeypatch.setattr(nucleo.backup_agendador, 'idade_ultimo_backup_horas', lambda *a: None)
    with flask_app.app_context():
        u = Usuario(username='sem_alerta_backup', role=ROLE_USUARIO)
        u.set_senha('senha12345')
        u.set_palavra_recuperacao('x')
        db.session.add(u)
        db.session.commit()
    try:
        c = flask_app.test_client()
        _login(c, {'username': 'sem_alerta_backup', 'senha': 'senha12345'})
        assert 'Nenhum backup'.encode() not in c.get('/').data
    finally:
        with flask_app.app_context():
            db.session.delete(Usuario.query.filter_by(username='sem_alerta_backup').first())
            db.session.commit()
