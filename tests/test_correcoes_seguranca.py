"""Testes das correções de sessão, recuperação de senha, validação e permissões."""

from __future__ import annotations

import random
import uuid


from database import (
    ACAO_AUTH_RECUPERACAO,
    ACAO_AUTH_RECUPERACAO_FALHOU,
    ROLE_ADMIN,
    ROLE_USUARIO,
    Associado,
    Auditoria,
    Usuario,
    db,
)
from tests.cpf_utils import cpf_digitos_validos
from tests.test_crud_associados import _payload_cadastro


def _ip_aleatorio():
    return f'10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}'


def _login(client, credenciais):
    r = client.post('/login', data={'username': credenciais['username'], 'senha': credenciais['senha']})
    assert r.status_code == 302


# --- Sessão revalidada no banco ---------------------------------------------------------

def test_usuario_excluido_perde_sessao(client, flask_app, usuario_comum):
    _login(client, usuario_comum)
    assert client.get('/').status_code == 200

    with flask_app.app_context():
        db.session.delete(db.session.get(Usuario, usuario_comum['id']))
        db.session.commit()

    r = client.get('/perfil')
    assert r.status_code == 302
    assert 'login' in (r.headers.get('Location') or '').lower()
    with client.session_transaction() as sess:
        assert 'usuario_id' not in sess


def test_admin_rebaixado_perde_acesso_admin(client, flask_app, usuario_comum):
    with flask_app.app_context():
        u = db.session.get(Usuario, usuario_comum['id'])
        u.role = ROLE_ADMIN
        db.session.commit()

    _login(client, usuario_comum)
    assert client.get('/usuarios').status_code == 200

    with flask_app.app_context():
        u = db.session.get(Usuario, usuario_comum['id'])
        u.role = ROLE_USUARIO
        db.session.commit()

    r = client.get('/usuarios')
    assert r.status_code == 302
    assert '/usuarios' not in (r.headers.get('Location') or '')


# --- Recuperação de senha ---------------------------------------------------------------

def test_recuperacao_bloqueia_apos_falhas(client, flask_app, usuario_comum):
    import nucleo

    ip = _ip_aleatorio()
    dados_errados = {
        'username': usuario_comum['username'],
        'palavra_recuperacao': 'frase errada',
        'nova_senha': 'nova_senha_123',
    }
    for _ in range(nucleo.LOGIN_MAX_FALHAS_IP):
        client.post('/esqueci_senha', data=dados_errados, environ_base={'REMOTE_ADDR': ip})

    with flask_app.app_context():
        falhas = Auditoria.query.filter_by(acao=ACAO_AUTH_RECUPERACAO_FALHOU, ip_origem=ip).count()
        assert falhas == nucleo.LOGIN_MAX_FALHAS_IP

    # Mesmo com a frase correta, o IP está bloqueado.
    r = client.post(
        '/esqueci_senha',
        data={**dados_errados, 'palavra_recuperacao': 'frase comum'},
        environ_base={'REMOTE_ADDR': ip},
    )
    assert r.status_code == 200
    assert 'Muitas tentativas'.encode() in r.data
    with flask_app.app_context():
        assert db.session.get(Usuario, usuario_comum['id']).check_senha(usuario_comum['senha'])


def test_recuperacao_bem_sucedida_fica_na_auditoria(client, flask_app, usuario_comum):
    r = client.post(
        '/esqueci_senha',
        data={
            'username': usuario_comum['username'],
            'palavra_recuperacao': 'frase comum',
            'nova_senha': 'senha_recuperada_123',
        },
        environ_base={'REMOTE_ADDR': _ip_aleatorio()},
    )
    assert r.status_code == 302
    with flask_app.app_context():
        assert db.session.get(Usuario, usuario_comum['id']).check_senha('senha_recuperada_123')
        assert Auditoria.query.filter_by(
            acao=ACAO_AUTH_RECUPERACAO, usuario_username=usuario_comum['username'],
        ).count() == 1


# --- Validação do cadastro --------------------------------------------------------------

def test_cadastro_matricula_duplicada_mensagem_especifica(admin_client):
    matricula = f'DUP-{uuid.uuid4().hex[:8].upper()}'
    r = admin_client.post('/cadastro', data=_payload_cadastro(matricula=matricula, cpf_digits=cpf_digitos_validos()))
    assert r.status_code == 302

    r = admin_client.post('/cadastro', data=_payload_cadastro(matricula=matricula, cpf_digits=cpf_digitos_validos()))
    assert r.status_code == 200
    assert f'Já existe um associado com a matrícula {matricula}'.encode() in r.data


def test_cadastro_cpf_duplicado_mensagem_especifica(admin_client):
    cpf = cpf_digitos_validos()
    r = admin_client.post('/cadastro', data=_payload_cadastro(matricula=f'C1-{uuid.uuid4().hex[:8]}', cpf_digits=cpf))
    assert r.status_code == 302

    r = admin_client.post('/cadastro', data=_payload_cadastro(matricula=f'C2-{uuid.uuid4().hex[:8]}', cpf_digits=cpf))
    assert r.status_code == 200
    assert 'Já existe um associado com o CPF'.encode() in r.data


def test_cadastro_data_invalida_mensagem_especifica(admin_client, flask_app):
    matricula = f'DT-{uuid.uuid4().hex[:8]}'
    dados = _payload_cadastro(matricula=matricula, cpf_digits=cpf_digitos_validos())
    dados['data_nascimento'] = '2999-01-01'
    r = admin_client.post('/cadastro', data=dados)
    assert r.status_code == 200
    assert 'Data de nascimento fora do intervalo'.encode() in r.data
    with flask_app.app_context():
        assert Associado.query.filter_by(matricula=matricula).first() is None


def test_cadastro_campo_obrigatorio_vazio(admin_client):
    dados = _payload_cadastro(matricula=f'OB-{uuid.uuid4().hex[:8]}', cpf_digits=cpf_digitos_validos())
    dados['endereco'] = '   '
    r = admin_client.post('/cadastro', data=dados)
    assert r.status_code == 200
    assert 'Preencha os campos obrigatórios: Endereço'.encode() in r.data


def test_editar_para_matricula_de_outro_associado_e_recusado(admin_client, flask_app):
    m1 = f'E1-{uuid.uuid4().hex[:8]}'
    m2 = f'E2-{uuid.uuid4().hex[:8]}'
    admin_client.post('/cadastro', data=_payload_cadastro(matricula=m1, cpf_digits=cpf_digitos_validos()))
    admin_client.post('/cadastro', data=_payload_cadastro(matricula=m2, cpf_digits=cpf_digitos_validos()))

    with flask_app.app_context():
        a2 = Associado.query.filter_by(matricula=m2).first()
        aid = a2.id
        payload = {
            'nome': a2.nome, 'matricula': m1, 'cpf': a2.cpf, 'rg': a2.rg,
            'telefone': '', 'telefone_whatsapp': '', 'endereco': a2.endereco,
            'data_nascimento': a2.data_nascimento.isoformat(),
            'data_admissao': a2.data_admissao.isoformat(),
            'email': a2.email, 'dependentes': '',
        }

    r = admin_client.post(f'/editar/{aid}', data=payload, follow_redirects=True)
    assert f'Já existe um associado com a matrícula {m1}'.encode() in r.data
    with flask_app.app_context():
        assert db.session.get(Associado, aid).matricula == m2


# --- Exclusão restrita a administradores ------------------------------------------------

def test_usuario_comum_nao_exclui_associado(client, admin_client, flask_app, usuario_comum):
    matricula = f'EX-{uuid.uuid4().hex[:8]}'
    admin_client.post('/cadastro', data=_payload_cadastro(matricula=matricula, cpf_digits=cpf_digitos_validos()))
    admin_client.post('/logout')
    with flask_app.app_context():
        aid = Associado.query.filter_by(matricula=matricula).first().id

    _login(client, usuario_comum)
    r = client.post(f'/excluir/{aid}')
    assert r.status_code == 302
    with flask_app.app_context():
        assert db.session.get(Associado, aid) is not None


# --- Limite da lista simples ------------------------------------------------------------

def test_lista_simples_respeita_limite(admin_client, monkeypatch):
    monkeypatch.setenv('EXPORTAR_LISTA_SIMPLES_MAX', '1')
    admin_client.post('/cadastro', data=_payload_cadastro(matricula=f'L1-{uuid.uuid4().hex[:8]}', cpf_digits=cpf_digitos_validos()))
    admin_client.post('/cadastro', data=_payload_cadastro(matricula=f'L2-{uuid.uuid4().hex[:8]}', cpf_digits=cpf_digitos_validos()))

    r = admin_client.post('/exportar_lista_simples')
    assert r.status_code == 302
    assert '/listar' in (r.headers.get('Location') or '')
