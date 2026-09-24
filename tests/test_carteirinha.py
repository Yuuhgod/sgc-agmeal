"""Testes da carteirinha (PDF) e da página pública de verificação pelo QR code."""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

import pytest

from database import ACAO_ASSOCIADO_CARTEIRINHA, Associado, Auditoria, db
from tests.cpf_utils import cpf_digitos_validos
from tests.test_crud_associados import _payload_cadastro
from tests.test_situacao_planilha import _payload_edicao


@pytest.fixture
def associado(admin_client, flask_app):
    dados = _payload_cadastro(matricula=f'CT-{uuid.uuid4().hex[:8]}', cpf_digits=cpf_digitos_validos(),
                              nome=f'Portador {uuid.uuid4().hex[:6]}')
    assert admin_client.post('/cadastro', data=dados).status_code == 302
    with flask_app.app_context():
        a = Associado.query.filter_by(matricula=dados['matricula']).first()
        return {'id': a.id, 'nome': a.nome, 'matricula': a.matricula, 'cpf': a.cpf}


def _url(flask_app, aid, emissao=None):
    import main

    emissao = emissao or date.today()
    with flask_app.test_request_context():
        with flask_app.app_context():
            a = db.session.get(Associado, aid)
            assinatura = main._assinatura_carteirinha(a.id, a.matricula, emissao)
    return f'/verificar/{aid}/{emissao:%Y%m%d}/{assinatura}'


def test_emite_pdf_e_audita(admin_client, flask_app, associado):
    r = admin_client.get(f"/carteirinha/{associado['id']}")
    assert r.status_code == 200
    assert r.data.startswith(b'%PDF')
    with flask_app.app_context():
        assert Auditoria.query.filter_by(acao=ACAO_ASSOCIADO_CARTEIRINHA, entidade_id=associado['id']).count() == 1


def test_qr_aponta_para_url_publica_configurada(admin_client, flask_app, associado, monkeypatch):
    import main

    monkeypatch.setenv('SGC_URL_PUBLICA', 'http://192.168.0.10/')
    capturado = {}
    original = main.render_template

    def espiar(nome, **ctx):
        if nome == 'pdf_carteirinha.html':
            capturado.update(ctx)
        return original(nome, **ctx)

    monkeypatch.setattr(main, 'render_template', espiar)
    admin_client.get(f"/carteirinha/{associado['id']}")
    assert re.fullmatch(rf"http://192\.168\.0\.10/verificar/{associado['id']}/\d{{8}}/[\w-]+", capturado['url_verificacao'])
    assert capturado['validade'] > date.today()


def test_verificacao_publica_mostra_so_dados_minimos(flask_app, associado):
    anonimo = flask_app.test_client()  # sem login
    r = anonimo.get(_url(flask_app, associado['id']))
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Carteirinha válida' in html
    assert associado['nome'] in html and associado['matricula'] in html
    assert associado['cpf'] not in html
    assert 'Rua Teste' not in html  # endereço do payload de teste


@pytest.mark.parametrize('adulterar', [
    lambda u: u[:-2] + ('AA' if not u.endswith('AA') else 'BB'),   # assinatura trocada
    lambda u: re.sub(r'/verificar/(\d+)/', lambda m: f'/verificar/{int(m.group(1)) + 1}/', u),  # outro associado
    lambda u: re.sub(r'/(\d{8})/', '/20990101/', u),               # data de emissão trocada
])
def test_link_adulterado_e_recusado(flask_app, associado, adulterar):
    r = flask_app.test_client().get(adulterar(_url(flask_app, associado['id'])))
    assert r.status_code == 404
    assert 'Carteirinha não reconhecida' in r.get_data(as_text=True)


def test_mudar_matricula_invalida_carteirinha_antiga(admin_client, flask_app, associado):
    url = _url(flask_app, associado['id'])
    admin_client.post(f"/editar/{associado['id']}", data=_payload_edicao(
        flask_app, associado['id'], matricula=associado['matricula'] + 'X'))
    assert flask_app.test_client().get(url).status_code == 404


def test_associado_desligado_aparece_como_nao_ativo(admin_client, flask_app, associado):
    url = _url(flask_app, associado['id'])
    admin_client.post(f"/editar/{associado['id']}", data=_payload_edicao(flask_app, associado['id'], situacao='desligado'))
    html = flask_app.test_client().get(url).get_data(as_text=True)
    assert 'Associado desligado' in html
    assert 'Carteirinha válida' not in html
    # E não se emite carteirinha nova para quem não está ativo.
    r = admin_client.get(f"/carteirinha/{associado['id']}")
    assert r.status_code == 302


def test_carteirinha_vencida(flask_app, associado):
    import main

    emissao = date.today() - timedelta(days=31 * (main.CARTEIRINHA_VALIDADE_MESES + 1))
    html = flask_app.test_client().get(_url(flask_app, associado['id'], emissao)).get_data(as_text=True)
    assert 'Carteirinha vencida' in html


def test_somar_meses():
    import main

    assert main._somar_meses(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert main._somar_meses(date(2023, 1, 31), 1) == date(2023, 2, 28)
    assert main._somar_meses(date(2024, 11, 15), 12) == date(2025, 11, 15)
    assert main._somar_meses(date(2024, 12, 1), 1) == date(2025, 1, 1)


def test_emitir_exige_login(flask_app, associado):
    r = flask_app.test_client().get(f"/carteirinha/{associado['id']}")
    assert r.status_code == 302 and 'login' in r.headers['Location']
