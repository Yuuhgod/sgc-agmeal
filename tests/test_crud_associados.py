"""Testes de CRUD de associados (cadastro, busca, edição, exclusão)."""

from __future__ import annotations

import re
import uuid

import pytest

from tests.cpf_utils import cpf_digitos_validos
from database import Associado


def _suffix():
    return uuid.uuid4().hex[:8].upper()


def _payload_cadastro(*, matricula: str, cpf_digits: str, nome: str = 'Associado Teste Pytest'):
    """CPF sem pontuação (11 dígitos); datas em ISO."""
    return {
        'nome': nome,
        'matricula': matricula,
        'cpf': cpf_digits,
        'rg': f'RG-{_suffix()}',
        'telefone': '',
        'telefone_whatsapp': '',
        'endereco': 'Rua Teste, 100',
        'data_nascimento': '1990-01-15',
        'email': f'pytest_{_suffix().lower()}@example.invalid',
        'data_admissao': '2020-03-01',
        'dependentes': '',
        'foto_base64': '',
    }


@pytest.fixture
def matricula_unica():
    return f'TST-{_suffix()}'


def test_cadastro_get_ok(admin_client):
    r = admin_client.get('/cadastro')
    assert r.status_code == 200


def test_cadastro_post_cria_associado(admin_client, matricula_unica, flask_app):
    cpf = cpf_digitos_validos()
    data = _payload_cadastro(matricula=matricula_unica, cpf_digits=cpf)
    r = admin_client.post('/cadastro', data=data)
    assert r.status_code == 302

    with flask_app.app_context():
        a = Associado.query.filter_by(matricula=matricula_unica).first()
        assert a is not None
        assert a.nome == 'Associado Teste Pytest'
        digits = re.sub(r'\D', '', a.cpf)
        assert digits == cpf


def test_cadastro_cpf_invalido_permance_na_pagina(admin_client, matricula_unica):
    data = _payload_cadastro(matricula=matricula_unica, cpf_digits='11111111111')
    r = admin_client.post('/cadastro', data=data)
    assert r.status_code == 200


def test_buscar_post_encontra_associado(admin_client, matricula_unica, flask_app):
    cpf = cpf_digitos_validos()
    admin_client.post('/cadastro', data=_payload_cadastro(matricula=matricula_unica, cpf_digits=cpf))

    r = admin_client.post('/buscar', data={'nome': '', 'matricula': matricula_unica, 'ano': ''})
    assert r.status_code == 200
    assert matricula_unica.encode() in r.data


def test_editar_altera_nome(admin_client, matricula_unica, flask_app):
    cpf = cpf_digitos_validos()
    admin_client.post('/cadastro', data=_payload_cadastro(matricula=matricula_unica, cpf_digits=cpf))

    with flask_app.app_context():
        a = Associado.query.filter_by(matricula=matricula_unica).first()
        assert a is not None
        aid = a.id
        payload = {
            'nome': 'Nome Alterado Pytest',
            'matricula': a.matricula,
            'cpf': a.cpf,
            'rg': a.rg,
            'telefone': a.telefone or '',
            'telefone_whatsapp': a.telefone_whatsapp or '',
            'endereco': a.endereco,
            'data_nascimento': a.data_nascimento.strftime('%Y-%m-%d'),
            'data_admissao': a.data_admissao.strftime('%Y-%m-%d'),
            'email': a.email,
            'dependentes': a.dependentes or '',
        }

    r = admin_client.get(f'/editar/{aid}')
    assert r.status_code == 200

    r = admin_client.post(f'/editar/{aid}', data=payload)
    assert r.status_code == 302

    with flask_app.app_context():
        atualizado = Associado.query.get(aid)
        assert atualizado is not None
        assert atualizado.nome == 'Nome Alterado Pytest'


def test_excluir_remove_associado(admin_client, matricula_unica, flask_app):
    cpf = cpf_digitos_validos()
    admin_client.post('/cadastro', data=_payload_cadastro(matricula=matricula_unica, cpf_digits=cpf))

    with flask_app.app_context():
        a = Associado.query.filter_by(matricula=matricula_unica).first()
        assert a is not None
        aid = a.id

    r = admin_client.post(f'/excluir/{aid}')
    assert r.status_code == 302

    with flask_app.app_context():
        assert Associado.query.get(aid) is None
