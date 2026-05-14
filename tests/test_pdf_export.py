"""Testes de geração de PDF (WeasyPrint) e exportação."""

from __future__ import annotations

import pytest

from tests.cpf_utils import cpf_digitos_validos


def _payload_cadastro_min(matricula: str, cpf: str):
    return {
        'nome': 'PDF Test Assoc',
        'matricula': matricula,
        'cpf': cpf,
        'rg': 'RG-PDF-1',
        'telefone': '',
        'telefone_whatsapp': '',
        'endereco': 'Rua PDF 1',
        'data_nascimento': '1991-02-02',
        'email': 'pdf_test@example.invalid',
        'data_admissao': '2021-04-04',
        'dependentes': '',
        'foto_base64': '',
    }


def test_exportar_lista_simples_retorna_pdf(admin_client):
    r = admin_client.post('/exportar_lista_simples')
    assert r.status_code == 200
    ct = (r.headers.get('Content-Type') or '').lower()
    assert 'pdf' in ct
    assert r.data[:4] == b'%PDF'


def test_exportar_ficha_retorna_pdf(admin_client, flask_app):
    matricula = 'TST-PDF-FICHA'
    cpf = cpf_digitos_validos()
    admin_client.post('/cadastro', data=_payload_cadastro_min(matricula, cpf))

    r = admin_client.get(f'/exportar_ficha/{matricula}')
    assert r.status_code == 200
    assert 'pdf' in (r.headers.get('Content-Type') or '').lower()
    assert r.data[:4] == b'%PDF'


def test_exportar_pdf_busca_retorna_pdf(admin_client, flask_app):
    matricula = 'TST-PDF-BUSCA'
    cpf = cpf_digitos_validos()
    admin_client.post('/cadastro', data=_payload_cadastro_min(matricula, cpf))

    r = admin_client.post(
        '/exportar_pdf',
        data={
            'nome_export': '',
            'matricula_export': matricula,
            'ano_export': '',
        },
    )
    assert r.status_code == 200
    assert 'pdf' in (r.headers.get('Content-Type') or '').lower()
    assert r.data[:4] == b'%PDF'


def test_exportar_pdf_sem_resultados_redireciona(admin_client):
    r = admin_client.post(
        '/exportar_pdf',
        data={
            'nome_export': '__nenhum_nome_assim__',
            'matricula_export': '__matricula_inexistente__',
            'ano_export': '',
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert 'buscar' in (r.headers.get('Location') or '')


def test_exportar_pdf_sem_filtro_bloqueado_acima_do_limite(monkeypatch, admin_client, flask_app):
    import main

    monkeypatch.setattr(main, '_exportar_pdf_max_sem_filtro', lambda: 1)
    with flask_app.app_context():
        from database import Associado

        if Associado.query.count() <= 1:
            pytest.skip('Poucos associados na BD para testar o limite de exportação sem filtro')

    r = admin_client.post(
        '/exportar_pdf',
        data={'nome_export': '', 'matricula_export': '', 'ano_export': ''},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert 'buscar' in (r.headers.get('Location') or '')
