"""Busca sem acentos/maiúsculas e PDFs que não buscam recursos por HTTP."""

from __future__ import annotations

import uuid

import pytest

from database import normalizar_busca
from tests.cpf_utils import cpf_digitos_validos
from tests.test_crud_associados import _payload_cadastro


def test_normalizar_busca():
    assert normalizar_busca('JOÃO da Conceição') == 'joao da conceicao'
    assert normalizar_busca('Ângela Müller Çé') == 'angela muller ce'
    assert normalizar_busca(None) is None


@pytest.fixture
def joao(admin_client):
    sufixo = uuid.uuid4().hex[:6].upper()
    nome = f'JOÃO DA CONCEIÇÃO {sufixo}'
    dados = _payload_cadastro(matricula=f'BA-{sufixo}', cpf_digits=cpf_digitos_validos(), nome=nome)
    assert admin_client.post('/cadastro', data=dados).status_code == 302
    return nome, sufixo


@pytest.mark.parametrize('termo', ['joão', 'Joao', 'JOAO', 'conceicao', 'Conceição', 'da conc'])
def test_busca_ignora_acentos_e_maiusculas(admin_client, joao, termo):
    nome, sufixo = joao
    r = admin_client.post('/buscar', data={'nome': f'{termo}'})
    html = r.get_data(as_text=True)
    assert f'<td>{nome}</td>' in html, termo


def test_busca_trata_curingas_como_texto(admin_client, joao):
    nome, _ = joao
    # "%" e "_" digitados não podem virar curinga do LIKE (senão "%" acharia todo mundo).
    html = admin_client.post('/buscar', data={'nome': '%'}).get_data(as_text=True)
    assert f'<td>{nome}</td>' not in html
    html = admin_client.post('/buscar', data={'nome': 'jo_o'}).get_data(as_text=True)
    assert f'<td>{nome}</td>' not in html


def test_exportacao_usa_a_mesma_busca(admin_client, joao):
    nome, sufixo = joao
    r = admin_client.post('/exportar_planilha', data={'formato': 'csv', 'nome_export': f'joao da conceicao {sufixo.lower()}'})
    assert r.status_code == 200
    assert nome in r.data.decode('utf-8-sig')


def test_auditoria_filtra_usuario_sem_acento(admin_client):
    r = admin_client.get('/auditoria?usuario=ADMIN')
    assert r.status_code == 200
    assert 'Entrou no sistema' in r.get_data(as_text=True)


# --- PDFs ------------------------------------------------------------------------------

def test_pdfs_nao_buscam_recursos_por_http(admin_client, joao, monkeypatch):
    """O logo era buscado em http://<servidor>/static/..., travando o worker (≈10 s e sem logo)."""
    import main

    buscados = []
    original = main._BuscadorRecursosPDF.fetch

    def espiar(self, url, headers=None):
        buscados.append(url)
        return original(self, url, headers)

    monkeypatch.setattr(main._BuscadorRecursosPDF, 'fetch', espiar)
    _, sufixo = joao
    for r in (
        admin_client.post('/exportar_lista_simples'),
        admin_client.get(f'/exportar_ficha/BA-{sufixo}'),
        admin_client.post('/exportar_pdf', data={'matricula_export': f'BA-{sufixo}'}),
    ):
        assert r.status_code == 200 and r.data.startswith(b'%PDF')

    assert buscados, 'o logo deveria ter sido carregado'
    assert all(u.startswith('file:') for u in buscados), buscados
    assert any(u.endswith('/img/logo.jpeg') for u in buscados)


@pytest.mark.parametrize('url', [
    'http://127.0.0.1:5000/static/img/logo.jpeg',
    'https://exemplo.com/x.png',
    'file:///etc/passwd',
])
def test_buscador_recusa_http_e_arquivos_fora_das_pastas(url):
    import main

    with pytest.raises(ValueError):
        main._BuscadorRecursosPDF().fetch(url)


def test_buscador_aceita_logo_local():
    import main

    resposta = main._BuscadorRecursosPDF().fetch(main.LOGO_PDF_URI)
    try:
        conteudo = resposta.read() if hasattr(resposta, 'read') else b''
    finally:
        if hasattr(resposta, 'close'):
            resposta.close()
    assert conteudo[:3] == b'\xff\xd8\xff'  # JPEG
