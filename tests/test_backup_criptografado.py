"""Backups criptografados (ZIP AES-256) e restauração com senha."""

from __future__ import annotations

import io
import os
import sqlite3
import stat
import zipfile

import pytest

from backup_service import criar_backup_zip, verificar_backup_zip, zip_criptografado
from database import ACAO_SISTEMA_BACKUP_SENHA, Auditoria

SENHA = 'Senha Forte 123!'


@pytest.fixture
def senha_isolada(tmp_path, monkeypatch):
    """A senha dos backups fica num arquivo temporário (não afeta os outros testes)."""
    import main

    arquivo = tmp_path / '.backup_senha'
    monkeypatch.setattr(main, '_arquivo_senha_backup', lambda: str(arquivo))
    monkeypatch.delenv('BACKUP_SENHA', raising=False)
    return arquivo


@pytest.fixture
def pastas(tmp_path):
    data, fotos, bk = tmp_path / 'data', tmp_path / 'fotos', tmp_path / 'bk'
    for p in (data, fotos, bk):
        p.mkdir()
    sqlite3.connect(data / 'sgc.db').executescript('CREATE TABLE usuarios (id INT); CREATE TABLE associados (id INT);')
    (data / '.flask_secret').write_text('segredo')
    (data / '.backup_senha').write_text(SENHA)  # nunca pode entrar no ZIP
    return {'data': str(data), 'fotos': str(fotos), 'bk': str(bk)}


def _criar(pastas, log, senha):
    return criar_backup_zip(data_dir=pastas['data'], upload_folder=pastas['fotos'], backups_dir=pastas['bk'],
                            sync_dir=None, keep_local=5, keep_sync=5, log=log, senha=senha)


def test_zip_criptografado_exige_senha(pastas, flask_app):
    info = _criar(pastas, flask_app.logger, SENHA)
    assert info['criptografado'] and zip_criptografado(info['zip_path'])
    with zipfile.ZipFile(info['zip_path']) as zf:
        assert '.backup_senha' not in ' '.join(zf.namelist())
        with pytest.raises(RuntimeError):
            zf.read('data/sgc.db')  # sem senha não lê
    verificar_backup_zip(info['zip_path'], SENHA)
    with pytest.raises(ValueError):
        verificar_backup_zip(info['zip_path'], 'senha errada')


def test_sem_senha_continua_zip_comum(pastas, flask_app):
    info = _criar(pastas, flask_app.logger, None)
    assert not info['criptografado'] and not zip_criptografado(info['zip_path'])
    verificar_backup_zip(info['zip_path'])


# --- Interface --------------------------------------------------------------------------

def test_definir_senha_exige_login_e_confirmacao(admin_client, senha_isolada, flask_app):
    base = {'nova_senha_backup': SENHA, 'confirmacao_senha_backup': SENHA, 'senha_login': 'admin123'}
    for ruim, msg in (
        ({**base, 'senha_login': 'errada'}, 'senha de login está incorreta'),
        ({**base, 'nova_senha_backup': 'curta', 'confirmacao_senha_backup': 'curta'}, 'pelo menos 10'),
        ({**base, 'confirmacao_senha_backup': 'outra coisa!!'}, 'não confere'),
    ):
        r = admin_client.post('/admin/backup/senha', data=ruim, follow_redirects=True)
        assert msg.encode() in r.data
        assert not senha_isolada.exists()

    r = admin_client.post('/admin/backup/senha', data=base, follow_redirects=True)
    assert 'ANOTE a senha'.encode() in r.data
    assert senha_isolada.read_text() == SENHA
    assert stat.S_IMODE(os.stat(senha_isolada).st_mode) == 0o600
    with flask_app.app_context():
        assert Auditoria.query.filter_by(acao=ACAO_SISTEMA_BACKUP_SENHA).count() >= 1
        textos = ' '.join(f'{a.descricao} {a.detalhes}' for a in Auditoria.query.all())
        assert SENHA not in textos  # a senha nunca vai para a auditoria


def test_backup_pela_interface_sai_criptografado(admin_client, senha_isolada):
    senha_isolada.write_text(SENHA)
    r = admin_client.post('/admin/backup/gerar')
    assert r.status_code == 200
    z = io.BytesIO(r.data)
    with zipfile.ZipFile(z) as zf:
        assert all(i.flag_bits & 1 for i in zf.infolist())


def _enviar_restore(client, conteudo, senha=''):
    return client.post('/admin/restore', data={
        'arquivo': (io.BytesIO(conteudo), 'backup.zip'), 'senha_backup': senha,
        'confirmar_texto': 'RESTAURAR', 'confirmar_consciencia': '1',
    }, content_type='multipart/form-data', follow_redirects=True)


def test_restaurar_zip_protegido_sem_senha_correta_nao_altera_nada(admin_client, senha_isolada, pastas, flask_app, monkeypatch):
    import main

    info = _criar(pastas, flask_app.logger, SENHA)
    conteudo = open(info['zip_path'], 'rb').read()
    chamou = []
    monkeypatch.setattr(main, 'aplicar_restauracao', lambda **kw: chamou.append(kw))

    # Sem senha salva e sem senha digitada.
    r = _enviar_restore(admin_client, conteudo)
    assert 'protegido por senha'.encode() in r.data and not chamou
    # Senha digitada errada.
    r = _enviar_restore(admin_client, conteudo, 'errada')
    assert 'protegido por senha'.encode() in r.data and not chamou


@pytest.mark.parametrize('digitada, salva', [(SENHA, None), ('', SENHA), ('errada', SENHA)])
def test_restaurar_zip_protegido_com_senha(admin_client, senha_isolada, pastas, flask_app, monkeypatch, digitada, salva):
    """Aceita a senha digitada ou, se ela não servir, a salva no servidor."""
    import main

    info = _criar(pastas, flask_app.logger, SENHA)
    conteudo = open(info['zip_path'], 'rb').read()
    if salva:
        senha_isolada.write_text(salva)
    extraidos = []
    monkeypatch.setattr(main, 'aplicar_restauracao', lambda **kw: extraidos.append(
        os.path.isfile(os.path.join(kw['extract_root'], 'data', 'sgc.db'))))
    monkeypatch.setattr(main, 'criar_backup_zip', lambda **kw: {'zip_filename': 'x', 'size_bytes': 0, 'sync_path': None})
    r = _enviar_restore(admin_client, conteudo, digitada)
    assert extraidos == [True], r.get_data(as_text=True)[:500]


def test_alerta_nuvem_sem_criptografia(admin_client, senha_isolada, monkeypatch, tmp_path):
    monkeypatch.setenv('BACKUP_SYNC_DIR', str(tmp_path / 'nuvem'))
    assert 'indo para a nuvem sem senha'.encode() in admin_client.get('/').data
    senha_isolada.write_text(SENHA)
    assert 'indo para a nuvem sem senha'.encode() not in admin_client.get('/').data
