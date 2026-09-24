"""Testes de LGPD: consentimento, exportação dos dados do titular e anonimização."""

from __future__ import annotations

import json
import os
import uuid
from datetime import date

from database import (
    ACAO_ASSOCIADO_ANONIMIZAR,
    ACAO_ASSOCIADO_CONSENTIMENTO,
    ACAO_ASSOCIADO_DADOS_TITULAR,
    Associado,
    Auditoria,
    Dependente,
    db,
)
from tests.cpf_utils import cpf_digitos_validos
from tests.test_correcoes_seguranca import _login
from tests.test_crud_associados import _payload_cadastro
from tests.test_dependentes import _com_dependentes
from tests.test_situacao_planilha import _payload_edicao


def _cadastrar(client, flask_app, consentimento=False, dependentes=()):
    dados = _payload_cadastro(matricula=f'LG-{uuid.uuid4().hex[:8]}', cpf_digits=cpf_digitos_validos(),
                              nome=f'Titular LGPD {uuid.uuid4().hex[:6]}')
    if dependentes:
        dados = _com_dependentes(dados, *dependentes)
    if consentimento:
        dados['consentimento'] = '1'
    assert client.post('/cadastro', data=dados).status_code == 302
    with flask_app.app_context():
        return Associado.query.filter_by(matricula=dados['matricula']).first().id


def _get(flask_app, aid):
    with flask_app.app_context():
        return db.session.get(Associado, aid)


# --- Consentimento ----------------------------------------------------------------------

def test_consentimento_no_cadastro(admin_client, flask_app):
    import main

    aid = _cadastrar(admin_client, flask_app, consentimento=True)
    a = _get(flask_app, aid)
    assert a.consentimento_versao == main.TERMO_CONSENTIMENTO_VERSAO
    assert a.consentimento_por == 'admin' and a.consentimento_data.date() == date.today()
    assert _get(flask_app, _cadastrar(admin_client, flask_app)).consentimento_data is None


def test_revogar_e_registrar_na_edicao(admin_client, flask_app):
    aid = _cadastrar(admin_client, flask_app, consentimento=True)

    # Edição por formulário sem a seção LGPD (ex.: outras telas/testes) não mexe no consentimento.
    admin_client.post(f'/editar/{aid}', data=_payload_edicao(flask_app, aid))
    assert _get(flask_app, aid).consentimento_data is not None

    admin_client.post(f'/editar/{aid}', data=_payload_edicao(flask_app, aid, lgpd_form='1'))
    assert _get(flask_app, aid).consentimento_data is None

    admin_client.post(f'/editar/{aid}', data=_payload_edicao(flask_app, aid, lgpd_form='1', consentimento='1'))
    assert _get(flask_app, aid).consentimento_data is not None

    with flask_app.app_context():
        detalhes = [log.detalhes for log in Auditoria.query.filter_by(acao=ACAO_ASSOCIADO_CONSENTIMENTO, entidade_id=aid)
                    .order_by(Auditoria.id)]
    assert detalhes == ['consentimento registrado (termo versão 1.0)', 'consentimento revogado',
                        'consentimento registrado (termo versão 1.0)']


def test_termo_em_pdf(admin_client, flask_app):
    aid = _cadastrar(admin_client, flask_app)
    r = admin_client.get(f'/associado/{aid}/termo_consentimento')
    assert r.status_code == 200 and r.data.startswith(b'%PDF')


# --- Dados do titular -------------------------------------------------------------------

def test_exportar_dados_do_titular(admin_client, flask_app):
    aid = _cadastrar(admin_client, flask_app, consentimento=True, dependentes=[('Filha Titular', 'Filho(a)', '', '')])
    r = admin_client.get(f'/associado/{aid}/dados_titular')
    assert r.status_code == 200
    assert 'attachment' in r.headers['Content-Disposition']
    dados = json.loads(r.data)
    a = _get(flask_app, aid)
    assert dados['associado']['cpf'] == a.cpf and dados['associado']['nome'] == a.nome
    assert dados['dependentes'][0]['nome'] == 'Filha Titular'
    assert dados['consentimento']['registrado'] is True
    assert any(h['acao'] == 'Cadastrou associado' for h in dados['historico_de_tratamento'])
    with flask_app.app_context():
        assert Auditoria.query.filter_by(acao=ACAO_ASSOCIADO_DADOS_TITULAR, entidade_id=aid).count() == 1


def test_dados_do_titular_so_admin(flask_app, usuario_comum, admin_client):
    aid = _cadastrar(admin_client, flask_app)
    c = flask_app.test_client()
    _login(c, usuario_comum)
    assert c.get(f'/associado/{aid}/dados_titular').status_code == 302


# --- Anonimização -----------------------------------------------------------------------

def test_nao_anonimiza_ativo(admin_client, flask_app):
    aid = _cadastrar(admin_client, flask_app)
    r = admin_client.post(f'/associado/{aid}/anonimizar', data={'confirmacao': 'ANONIMIZAR'})
    assert r.status_code == 302
    assert _get(flask_app, aid).anonimizado_em is None


def test_confirmacao_errada_nao_anonimiza(admin_client, flask_app):
    aid = _cadastrar(admin_client, flask_app)
    admin_client.post(f'/editar/{aid}', data=_payload_edicao(flask_app, aid, situacao='desligado'))
    admin_client.post(f'/associado/{aid}/anonimizar', data={'confirmacao': 'sim'})
    assert _get(flask_app, aid).anonimizado_em is None


def test_anonimizar_apaga_dados_e_limpa_auditoria(admin_client, flask_app):
    import main

    aid = _cadastrar(admin_client, flask_app, consentimento=True,
                     dependentes=[('Dependente Secreto', 'Cônjuge', '', '')])
    original = _get(flask_app, aid)
    nome, cpf, matricula = original.nome, original.cpf, original.matricula
    nascimento = original.data_nascimento

    # Foto no disco, para conferir que é apagada.
    foto = f'lgpd_{uuid.uuid4().hex[:6]}.jpg'
    caminho_foto = os.path.join(main.UPLOAD_FOLDER, foto)
    with open(caminho_foto, 'wb') as fh:
        fh.write(b'\xff\xd8\xff' + b'0' * 20)
    with flask_app.app_context():
        db.session.get(Associado, aid).foto_perfil = foto
        db.session.commit()

    admin_client.post(f'/editar/{aid}', data=_payload_edicao(flask_app, aid, situacao='desligado',
                                                              situacao_motivo=f'Pedido de {nome}'))
    # Um registro de OUTRA entidade que cita a matrícula (como faz a importação).
    with flask_app.app_context():
        db.session.add(Auditoria(usuario_username='admin', acao='associado.importar', entidade='associado',
                                 descricao='importação', detalhes=f'matrículas: X-1, {matricula}, {matricula}0'))
        db.session.commit()

    assert admin_client.get(f'/associado/{aid}/anonimizar').status_code == 200
    r = admin_client.post(f'/associado/{aid}/anonimizar', data={'confirmacao': 'ANONIMIZAR'})
    assert r.status_code == 302

    with flask_app.app_context():
        a = db.session.get(Associado, aid)
        assert a.anonimizado_em is not None
        assert a.nome == f'Associado anonimizado #{aid}' and a.cpf == f'ANON-{aid:06d}' == a.matricula
        assert (a.rg, a.email, a.endereco, a.telefone, a.foto_perfil) == ('', '', '', None, None)
        assert a.data_nascimento == date(nascimento.year, 1, 1)
        assert a.situacao == 'desligado' and a.data_admissao is not None
        assert a.situacao_motivo is None and a.consentimento_data is None
        assert Dependente.query.filter_by(associado_id=aid).count() == 0

        textos = ' '.join(f'{log.descricao} {log.detalhes}' for log in Auditoria.query.all())
        for dado in (nome, cpf, 'Dependente Secreto'):
            assert dado not in textos, dado
        importacao = Auditoria.query.filter_by(descricao='importação').order_by(Auditoria.id.desc()).first()
        # Só a matrícula exata é trocada; "matrícula + 0" (outro associado) fica intacta.
        assert importacao.detalhes == f'matrículas: X-1, [anonimizado #{aid}], {matricula}0'
        assert Auditoria.query.filter_by(acao=ACAO_ASSOCIADO_ANONIMIZAR, entidade_id=aid).count() == 1
    assert not os.path.exists(caminho_foto)

    # Depois de anonimizado: não edita, não emite termo nem carteirinha.
    assert admin_client.get(f'/editar/{aid}').status_code == 302
    assert admin_client.get(f'/associado/{aid}/termo_consentimento').status_code == 404
    assert admin_client.get(f'/carteirinha/{aid}').status_code == 302


def test_anonimizar_so_admin(flask_app, usuario_comum, admin_client):
    aid = _cadastrar(admin_client, flask_app)
    admin_client.post(f'/editar/{aid}', data=_payload_edicao(flask_app, aid, situacao='desligado'))
    c = flask_app.test_client()
    _login(c, usuario_comum)
    c.post(f'/associado/{aid}/anonimizar', data={'confirmacao': 'ANONIMIZAR'})
    assert _get(flask_app, aid).anonimizado_em is None
