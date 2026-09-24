"""Testes da gestão de usuários pelo admin: perfil, desativação, senha provisória e sessão."""

from __future__ import annotations

import time
import uuid

import pytest

from database import ROLE_ADMIN, ROLE_USUARIO, Auditoria, Usuario, db
from tests.test_correcoes_seguranca import _ip_aleatorio, _login, usuario_comum  # noqa: F401 (fixture)


# Obs.: as fixtures `admin_client` e `client` são o MESMO cliente HTTP; quando o teste precisa
# de um segundo usuário logado ao mesmo tempo, cria outro com flask_app.test_client().


def _local(r):
    return r.headers.get('Location') or ''


@pytest.fixture
def outro_admin_client(flask_app, admin_credentials):
    """Cliente logado como um segundo administrador (para editar o admin principal com segurança)."""
    nome = f'adm_{uuid.uuid4().hex[:8]}'
    with flask_app.app_context():
        u = Usuario(username=nome, role=ROLE_ADMIN)
        u.set_senha('senha_admin_2')
        u.set_palavra_recuperacao('frase')
        db.session.add(u)
        db.session.commit()
        uid = u.id
    client = flask_app.test_client()
    _login(client, {'username': nome, 'senha': 'senha_admin_2'})
    yield client
    with flask_app.app_context():
        u = db.session.get(Usuario, uid)
        if u:
            db.session.delete(u)
            db.session.commit()


# --- Perfil e desativação ---------------------------------------------------------------

def test_admin_promove_usuario_e_audita(admin_client, flask_app, usuario_comum):
    r = admin_client.post(f"/usuarios/{usuario_comum['id']}/editar", data={'role': 'admin', 'ativo': '1'})
    assert r.status_code == 302
    with flask_app.app_context():
        assert db.session.get(Usuario, usuario_comum['id']).role == ROLE_ADMIN
        log = Auditoria.query.filter_by(acao='usuario.editado', entidade_id=usuario_comum['id']).first()
        assert 'perfil: "usuario" → "admin"' in log.detalhes


def test_desativar_encerra_sessao_e_bloqueia_login(admin_client, flask_app, usuario_comum):
    client = flask_app.test_client()  # sessão separada da do admin
    _login(client, usuario_comum)
    assert client.get('/').status_code == 200

    admin_client.post(f"/usuarios/{usuario_comum['id']}/editar", data={'role': 'usuario'})  # sem 'ativo' = desativar
    with flask_app.app_context():
        assert db.session.get(Usuario, usuario_comum['id']).ativo is False

    r = client.get('/')
    assert r.status_code == 302 and 'login' in _local(r)

    r = client.post('/login', data={'username': usuario_comum['username'], 'senha': usuario_comum['senha']})
    assert r.status_code == 200
    assert 'Esta conta está desativada'.encode() in r.data

    # Senha errada não revela que a conta existe/está desativada.
    r = client.post('/login', data={'username': usuario_comum['username'], 'senha': 'errada123'})
    assert 'desativada'.encode() not in r.data

    # Recuperação pela frase também não reativa o acesso.
    r = client.post('/esqueci_senha', data={
        'username': usuario_comum['username'], 'palavra_recuperacao': 'frase comum', 'nova_senha': 'outra_senha_1',
    }, environ_base={'REMOTE_ADDR': _ip_aleatorio()})
    assert 'incorretos'.encode() in r.data


def test_reativar_permite_login(admin_client, flask_app, usuario_comum):
    client = flask_app.test_client()  # sessão separada da do admin
    admin_client.post(f"/usuarios/{usuario_comum['id']}/editar", data={'role': 'usuario'})
    admin_client.post(f"/usuarios/{usuario_comum['id']}/editar", data={'role': 'usuario', 'ativo': '1'})
    _login(client, usuario_comum)
    assert client.get('/').status_code == 200


def test_admin_nao_edita_a_si_mesmo(admin_client, flask_app):
    with flask_app.app_context():
        admin = Usuario.query.filter_by(username='admin').first()
    r = admin_client.post(f'/usuarios/{admin.id}/editar', data={'role': 'usuario'}, follow_redirects=True)
    assert 'não pode alterar o próprio perfil'.encode() in r.data
    with flask_app.app_context():
        admin = db.session.get(Usuario, admin.id)
        assert (admin.role, admin.ativo) == (ROLE_ADMIN, True)


def test_outro_admin_pode_rebaixar_admin_se_houver_outro_ativo(outro_admin_client, flask_app):
    with flask_app.app_context():
        admin = Usuario.query.filter_by(username='admin').first()
    r = outro_admin_client.post(f'/usuarios/{admin.id}/editar', data={'role': 'usuario', 'ativo': '1'})
    assert r.status_code == 302
    with flask_app.app_context():
        assert db.session.get(Usuario, admin.id).role == ROLE_USUARIO
    # Restaura para não afetar outros testes.
    outro_admin_client.post(f'/usuarios/{admin.id}/editar', data={'role': 'admin', 'ativo': '1'})
    with flask_app.app_context():
        assert db.session.get(Usuario, admin.id).role == ROLE_ADMIN


def test_unico_admin_ativo_nao_pode_ser_rebaixado(flask_app, admin_credentials):
    import main

    with flask_app.app_context():
        admins = Usuario.query.filter_by(role=ROLE_ADMIN, ativo=True).all()
        # Com um segundo admin DESATIVADO, o principal continua sendo o único ativo.
        inativo = Usuario(username=f'adm_off_{uuid.uuid4().hex[:6]}', role=ROLE_ADMIN, ativo=False)
        inativo.set_senha('x' * 8)
        inativo.set_palavra_recuperacao('x')
        db.session.add(inativo)
        db.session.commit()
        try:
            principal = Usuario.query.filter_by(username='admin').first()
            assert main._usuario_admin_ativo_unico(principal) is (len(admins) == 1)
            assert main._usuario_admin_ativo_unico(inativo) is False
        finally:
            db.session.delete(inativo)
            db.session.commit()


# --- Senha provisória -------------------------------------------------------------------

def test_redefinir_senha_obriga_troca_no_proximo_acesso(admin_client, flask_app, usuario_comum):
    client = flask_app.test_client()  # sessão separada da do admin
    uid = usuario_comum['id']
    r = admin_client.post(f'/usuarios/{uid}/redefinir_senha', data={
        'senha_provisoria': 'provisoria1', 'senha_provisoria_confirmacao': 'provisoria1',
    })
    assert r.status_code == 302
    with flask_app.app_context():
        u = db.session.get(Usuario, uid)
        assert u.trocar_senha is True and u.check_senha('provisoria1')
        assert Auditoria.query.filter_by(acao='usuario.senha_redefinida', entidade_id=uid).count() == 1

    _login(client, {'username': usuario_comum['username'], 'senha': 'provisoria1'})
    for pagina in ('/', '/buscar', '/listar', '/perfil'):
        r = client.get(pagina)
        assert r.status_code == 302 and '/trocar_senha' in _local(r), pagina
    assert client.get('/trocar_senha').status_code == 200

    r = client.post('/trocar_senha', data={'nova_senha': 'provisoria1', 'confirmacao': 'provisoria1'})
    assert 'não pode ser igual à senha provisória'.encode() in r.data
    r = client.post('/trocar_senha', data={'nova_senha': 'minha_senha_1', 'confirmacao': 'outra'})
    assert 'não confere'.encode() in r.data

    r = client.post('/trocar_senha', data={'nova_senha': 'minha_senha_1', 'confirmacao': 'minha_senha_1'})
    assert r.status_code == 302
    assert client.get('/').status_code == 200
    with flask_app.app_context():
        u = db.session.get(Usuario, uid)
        assert u.trocar_senha is False and u.check_senha('minha_senha_1')


def test_redefinir_senha_valida_confirmacao(admin_client, flask_app, usuario_comum):
    r = admin_client.post(f"/usuarios/{usuario_comum['id']}/redefinir_senha", data={
        'senha_provisoria': 'provisoria1', 'senha_provisoria_confirmacao': 'diferente1',
    }, follow_redirects=True)
    assert 'não confere'.encode() in r.data
    with flask_app.app_context():
        assert db.session.get(Usuario, usuario_comum['id']).trocar_senha is False


def test_usuario_novo_troca_senha_no_primeiro_acesso(admin_client, flask_app):
    client = flask_app.test_client()  # sessão separada da do admin
    nome = f'novo_{uuid.uuid4().hex[:8]}'
    r = admin_client.post('/usuarios/novo', data={
        'username': nome, 'senha': 'inicial_123', 'palavra_recuperacao': 'frase', 'role': 'usuario',
        'trocar_senha': '1',
    })
    assert r.status_code == 302
    _login(client, {'username': nome, 'senha': 'inicial_123'})
    assert '/trocar_senha' in _local(client.get('/'))
    with flask_app.app_context():
        db.session.delete(Usuario.query.filter_by(username=nome).first())
        db.session.commit()


def test_usuario_nao_admin_nao_gerencia_usuarios(client, usuario_comum, flask_app):
    _login(client, usuario_comum)
    with flask_app.app_context():
        admin_id = Usuario.query.filter_by(username='admin').first().id
    for url, dados in ((f'/usuarios/{admin_id}/editar', {'role': 'usuario'}),
                       (f'/usuarios/{admin_id}/redefinir_senha',
                        {'senha_provisoria': 'hackeada1', 'senha_provisoria_confirmacao': 'hackeada1'})):
        r = client.post(url, data=dados)
        assert r.status_code == 302 and '/usuarios' not in _local(r)
    with flask_app.app_context():
        admin = db.session.get(Usuario, admin_id)
        assert admin.role == ROLE_ADMIN and not admin.check_senha('hackeada1')


# --- Expiração por inatividade ----------------------------------------------------------

def test_sessao_expira_por_inatividade(client, usuario_comum):
    import main

    _login(client, usuario_comum)
    assert client.get('/').status_code == 200
    with client.session_transaction() as sess:
        sess['ultimo_acesso'] = int(time.time()) - main.SESSAO_INATIVIDADE_MINUTOS * 60 - 5
    r = client.get('/', follow_redirects=True)
    assert 'expirou por inatividade'.encode() in r.data
    with client.session_transaction() as sess:
        assert 'usuario_id' not in sess


# --- Confirmação de exclusão (regressão) ------------------------------------------------

def test_exclusoes_pedem_confirmacao_sem_js_inline(admin_client, usuario_comum):
    """O onsubmit antigo quebrava o atributo com as aspas do |tojson e excluía sem perguntar."""
    for pagina in ('/usuarios',):
        html = admin_client.get(pagina).get_data(as_text=True)
        assert 'onsubmit=' not in html
        assert f'data-confirmar="Remover o usuário {usuario_comum["username"]} definitivamente?' in html
    html = admin_client.post('/buscar', data={}).get_data(as_text=True)
    assert 'onsubmit=' not in html
    assert 'data-confirmar="Excluir ' in html
