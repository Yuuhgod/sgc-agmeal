"""Testes dos indicadores do painel (aniversariantes e admissões por ano)."""

from __future__ import annotations

import uuid
from datetime import date

from tests.cpf_utils import cpf_digitos_validos
from tests.test_crud_associados import _payload_cadastro
from tests.test_situacao_planilha import _payload_edicao
from database import Associado


def _cadastrar(client, flask_app, nome, nascimento, admissao='2020-03-01'):
    dados = _payload_cadastro(matricula=f'PN-{uuid.uuid4().hex[:8]}', cpf_digits=cpf_digitos_validos(), nome=nome)
    dados.update(data_nascimento=nascimento, data_admissao=admissao)
    assert client.post('/cadastro', data=dados).status_code == 302
    with flask_app.app_context():
        return Associado.query.filter_by(nome=nome).first().id


def test_aniversariantes_do_mes_so_ativos(admin_client, flask_app):
    hoje = date.today()
    ativo = f'Aniversariante Ativo {uuid.uuid4().hex[:6]}'
    desligado = f'Aniversariante Desligado {uuid.uuid4().hex[:6]}'
    outro_mes = f'Aniversariante Outro Mes {uuid.uuid4().hex[:6]}'
    mes_seguinte = hoje.month % 12 + 1

    _cadastrar(admin_client, flask_app, ativo, date(1980, hoje.month, 1).isoformat())
    aid = _cadastrar(admin_client, flask_app, desligado, date(1981, hoje.month, 1).isoformat())
    admin_client.post(f'/editar/{aid}', data=_payload_edicao(flask_app, aid, situacao='desligado'))
    _cadastrar(admin_client, flask_app, outro_mes, date(1982, mes_seguinte, 1).isoformat())

    r = admin_client.get('/')
    assert r.status_code == 200
    assert ativo.encode() in r.data
    assert f'{hoje.year - 1980} anos'.encode() in r.data
    assert desligado.encode() not in r.data
    assert outro_mes.encode() not in r.data


def test_grafico_admissoes_por_ano(admin_client, flask_app):
    ano = date.today().year - 1
    _cadastrar(admin_client, flask_app, f'Admissao Grafico {uuid.uuid4().hex[:6]}', '1990-01-15', f'{ano}-02-01')
    with flask_app.app_context():
        esperado = Associado.query.filter(
            Associado.data_admissao >= date(ano, 1, 1), Associado.data_admissao <= date(ano, 12, 31),
        ).count()

    r = admin_client.get('/')
    assert 'Admissões por ano'.encode() in r.data
    assert f'<td>{ano}</td><td class="text-end">{esperado}</td>'.encode() in r.data
    assert f'data-dica="{ano}: {esperado} admiss'.encode() in r.data
