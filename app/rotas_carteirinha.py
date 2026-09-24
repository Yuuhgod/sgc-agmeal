"""Carteirinha do associado (PDF) e página pública de verificação pelo QR code."""

import base64
import hashlib
import hmac
import os
from datetime import datetime
from pathlib import Path

import segno
from flask import flash, make_response, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from database import ACAO_ASSOCIADO_CARTEIRINHA, SITUACAO_ATIVO, Associado, db
from nucleo import (
    LOGO_PDF_URI,
    UPLOAD_FOLDER,
    _env_float,
    app,
    gerar_pdf,
    limiter,
    login_required,
    registrar_auditoria,
)

# ---------------------------------------------------------------------------------------
# Carteirinha com QR code de verificação
# ---------------------------------------------------------------------------------------

CARTEIRINHA_VALIDADE_MESES = int(_env_float('CARTEIRINHA_VALIDADE_MESES', '12', 1))


def _somar_meses(data, meses):
    ano, mes = divmod(data.month - 1 + meses, 12)
    ano, mes = data.year + ano, mes + 1
    ultimo_dia = [31, 29 if ano % 4 == 0 and (ano % 100 or ano % 400 == 0) else 28,
                  31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mes - 1]
    return data.replace(year=ano, month=mes, day=min(data.day, ultimo_dia))


def _assinatura_carteirinha(associado_id, matricula, emissao):
    """HMAC da carteirinha: muda se a matrícula ou a data de emissão mudarem, e não pode
    ser calculado sem a SECRET_KEY (ninguém gera links válidos trocando números)."""
    mensagem = f'carteirinha:{associado_id}:{matricula}:{emissao:%Y%m%d}'.encode()
    digest = hmac.new(app.config['SECRET_KEY'].encode(), mensagem, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:12]).decode().rstrip('=')


def _url_verificacao(associado, emissao):
    caminho = url_for(
        'verificar_carteirinha', associado_id=associado.id, emissao=emissao.strftime('%Y%m%d'),
        assinatura=_assinatura_carteirinha(associado.id, associado.matricula, emissao),
    )
    # O QR é lido por celulares na rede: use o endereço do servidor na rede local, se configurado.
    base = (os.environ.get('SGC_URL_PUBLICA') or request.url_root).rstrip('/')
    return base + caminho


@app.route('/carteirinha/<int:id>')
@login_required
def carteirinha(id):
    associado = db.session.get(Associado, id)
    if associado is None:
        flash('Associado não encontrado.', 'danger')
        return redirect(url_for('buscar'))
    if associado.situacao != SITUACAO_ATIVO:
        flash(f'Carteirinha só é emitida para associados ativos ({associado.nome} está '
              f'{associado.situacao_rotulo.lower()}).', 'warning')
        return redirect(url_for('editar', id=id))

    emissao = datetime.now().date()
    url = _url_verificacao(associado, emissao)
    qr_svg = segno.make(url, error='m').svg_data_uri(scale=4, border=1, dark='#0A2342')
    html = render_template(
        'pdf_carteirinha.html',
        a=associado,
        emissao=emissao,
        validade=_somar_meses(emissao, CARTEIRINHA_VALIDADE_MESES),
        qr_svg=qr_svg,
        url_verificacao=url,
        logo_uri=LOGO_PDF_URI,
        fotos_base_uri=Path(UPLOAD_FOLDER).as_uri(),
    )
    pdf = gerar_pdf(html)
    registrar_auditoria(
        ACAO_ASSOCIADO_CARTEIRINHA,
        entidade='associado',
        entidade_id=associado.id,
        descricao=f'{associado.nome} (matrícula {associado.matricula})',
        detalhes=f'emissão {emissao:%d/%m/%Y}',
        commit=True,
    )
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=carteirinha_{secure_filename(associado.matricula)}.pdf'
    return response


@app.route('/verificar/<int:associado_id>/<emissao>/<assinatura>')
@limiter.limit('30 per minute')
def verificar_carteirinha(associado_id, emissao, assinatura):
    """Página pública (sem login) aberta pelo QR: mostra só nome, matrícula e situação."""
    associado = db.session.get(Associado, associado_id)
    try:
        data_emissao = datetime.strptime(emissao, '%Y%m%d').date()
    except ValueError:
        data_emissao = None
    valida = (
        associado is not None and data_emissao is not None
        and hmac.compare_digest(
            assinatura, _assinatura_carteirinha(associado.id, associado.matricula, data_emissao),
        )
    )
    if not valida:
        return render_template('verificar_carteirinha.html', valida=False), 404

    validade = _somar_meses(data_emissao, CARTEIRINHA_VALIDADE_MESES)
    hoje = datetime.now().date()
    return render_template(
        'verificar_carteirinha.html',
        valida=True,
        a=associado,
        emissao=data_emissao,
        validade=validade,
        vencida=hoje > validade,
        ativo=associado.situacao == SITUACAO_ATIVO,
    )
