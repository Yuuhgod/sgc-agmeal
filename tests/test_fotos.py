"""Fotos: normalização (tamanho, formato, orientação) e remoção de metadados EXIF/GPS."""

from __future__ import annotations

import base64
import io
import os
import uuid

import pytest
from PIL import Image

from database import Associado, db
from tests.cpf_utils import cpf_digitos_validos
from tests.test_crud_associados import _payload_cadastro
from tests.test_situacao_planilha import _payload_edicao

GPS = 0x8825
ORIENTACAO = 0x0112


def _foto_de_celular(largura=2000, altura=1500, orientacao=1, formato='JPEG'):
    img = Image.new('RGB', (largura, altura), (200, 210, 230))
    exif = Image.Exif()
    exif[ORIENTACAO] = orientacao
    exif[GPS] = {1: 'S', 2: (9.0, 39.0, 57.0), 3: 'W', 4: (35.0, 44.0, 6.0)}
    buf = io.BytesIO()
    img.save(buf, format=formato, exif=exif.tobytes())
    return buf.getvalue()


def test_normalizar_reduz_e_remove_exif():
    import main

    bruto = _foto_de_celular()
    assert GPS in Image.open(io.BytesIO(bruto)).getexif()
    img = Image.open(io.BytesIO(main._normalizar_foto(bruto)))
    assert img.format == 'JPEG'
    assert img.width <= 600 and img.height <= 800
    assert not img.getexif()


def test_normalizar_aplica_orientacao_do_exif():
    import main

    # Paisagem com orientação 6 ("girar 90°"): deve virar retrato.
    img = Image.open(io.BytesIO(main._normalizar_foto(_foto_de_celular(800, 600, orientacao=6))))
    assert img.height > img.width


def test_png_vira_jpeg():
    import main

    buf = io.BytesIO()
    Image.new('RGBA', (300, 400), (10, 20, 30, 128)).save(buf, format='PNG')
    assert Image.open(io.BytesIO(main._normalizar_foto(buf.getvalue()))).format == 'JPEG'


def test_imagem_corrompida_e_recusada():
    import main

    with pytest.raises(ValueError):
        main._normalizar_foto(b'\xff\xd8\xff' + b'lixo' * 100)


def _foto_do(flask_app, matricula):
    import main

    with flask_app.app_context():
        nome = Associado.query.filter_by(matricula=matricula).first().foto_perfil
    return nome, os.path.join(main.UPLOAD_FOLDER, nome)


def test_cadastro_com_recorte_base64(admin_client, flask_app):
    matricula = f'FT-{uuid.uuid4().hex[:8]}'
    dados = _payload_cadastro(matricula=matricula, cpf_digits=cpf_digitos_validos())
    dados['foto_base64'] = 'data:image/jpeg;base64,' + base64.b64encode(_foto_de_celular()).decode()
    assert admin_client.post('/cadastro', data=dados).status_code == 302
    nome, caminho = _foto_do(flask_app, matricula)
    try:
        assert nome.endswith('.jpg')
        img = Image.open(caminho)
        assert img.width <= 600 and not img.getexif()
    finally:
        os.remove(caminho)


def test_edicao_sem_javascript_envia_arquivo(admin_client, flask_app):
    """Sem JavaScript o campo envia o arquivo original: ele também é normalizado."""
    matricula = f'FE-{uuid.uuid4().hex[:8]}'
    admin_client.post('/cadastro', data=_payload_cadastro(matricula=matricula, cpf_digits=cpf_digitos_validos()))
    with flask_app.app_context():
        aid = Associado.query.filter_by(matricula=matricula).first().id
    dados = _payload_edicao(flask_app, aid)
    dados['foto_perfil'] = (io.BytesIO(_foto_de_celular()), 'celular.jpg')
    r = admin_client.post(f'/editar/{aid}', data=dados, content_type='multipart/form-data')
    assert r.status_code == 302
    nome, caminho = _foto_do(flask_app, matricula)
    try:
        img = Image.open(caminho)
        assert img.width <= 600 and not img.getexif()
    finally:
        os.remove(caminho)
        with flask_app.app_context():
            db.session.get(Associado, aid).foto_perfil = None
            db.session.commit()
