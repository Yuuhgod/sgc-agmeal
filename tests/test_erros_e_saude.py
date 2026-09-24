"""Páginas de erro amigáveis e verificação de saúde."""

from __future__ import annotations

import io

import pytest
from sqlalchemy.exc import OperationalError


def test_404_amigavel(admin_client):
    r = admin_client.get('/pagina-que-nao-existe')
    assert r.status_code == 404
    assert 'Página não encontrada'.encode() in r.data and b'Ir para o in' in r.data


def test_editar_associado_inexistente_da_404(admin_client):
    r = admin_client.get('/editar/99999999')
    assert r.status_code == 404 and 'Página não encontrada'.encode() in r.data


def test_405_amigavel(client):
    r = client.get('/logout')
    assert r.status_code == 405 and 'Operação não permitida'.encode() in r.data


def test_csrf_expirado_explica_o_que_fazer(flask_app, admin_client):
    flask_app.config.update(WTF_CSRF_ENABLED=True, WTF_CSRF_CHECK_DEFAULT=True)
    try:
        r = admin_client.post('/cadastro', data={'nome': 'x'})  # sem csrf_token
    finally:
        flask_app.config.update(WTF_CSRF_ENABLED=False, WTF_CSRF_CHECK_DEFAULT=False)
    assert r.status_code == 400
    assert 'O formulário expirou'.encode() in r.data


def test_413_arquivo_grande(flask_app, admin_client):
    limite = flask_app.config['MAX_CONTENT_LENGTH']
    flask_app.config['MAX_CONTENT_LENGTH'] = 1024
    try:
        r = admin_client.post('/associados/importar', data={'arquivo': (io.BytesIO(b'x' * 5000), 'a.csv')},
                              content_type='multipart/form-data')
    finally:
        flask_app.config['MAX_CONTENT_LENGTH'] = limite
    assert r.status_code == 413 and 'Arquivo grande demais'.encode() in r.data


def test_500_amigavel_e_sem_detalhes(flask_app, admin_client, monkeypatch):
    def quebra():
        raise RuntimeError('detalhe interno secreto')

    monkeypatch.setitem(flask_app.view_functions, 'listar_todos', quebra)
    monkeypatch.setitem(flask_app.config, 'PROPAGATE_EXCEPTIONS', False)
    r = admin_client.get('/listar')
    assert r.status_code == 500
    assert 'Erro interno'.encode() in r.data
    assert b'detalhe interno secreto' not in r.data


def test_saude_ok_sem_login(client):
    r = client.get('/saude')
    assert r.status_code == 200 and r.get_json() == {'status': 'ok'}


def test_saude_503_se_o_banco_falhar(client, monkeypatch):
    import nucleo

    def falha(*a, **k):
        raise OperationalError('SELECT 1', {}, Exception('disco'))

    monkeypatch.setattr(nucleo.db.session, 'execute', falha)
    r = client.get('/saude')
    assert r.status_code == 503 and r.get_json() == {'status': 'erro'}


@pytest.mark.parametrize('rota', ['/saude'])
def test_saude_nao_exige_setup(rota, client):
    assert client.get(rota).status_code in (200, 503)
