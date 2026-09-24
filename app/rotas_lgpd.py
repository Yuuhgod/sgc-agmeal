"""LGPD: consentimento, termo em PDF, exportação dos dados do titular e anonimização."""

import json
import re
from datetime import datetime

from flask import (
    abort,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from database import (
    ACAO_ASSOCIADO_ANONIMIZAR,
    ACAO_ASSOCIADO_CONSENTIMENTO,
    ACAO_ASSOCIADO_DADOS_TITULAR,
    SITUACAO_ATIVO,
    Associado,
    Auditoria,
    db,
)
from nucleo import (
    LOGO_PDF_URI,
    _remover_foto_do_disco,
    admin_required,
    app,
    gerar_pdf,
    login_required,
    registrar_auditoria,
)

# ---------------------------------------------------------------------------------------
# LGPD: consentimento, dados do titular e anonimização
# ---------------------------------------------------------------------------------------

TERMO_CONSENTIMENTO_VERSAO = '1.0'
ANONIMIZAR_CONFIRMACAO = 'ANONIMIZAR'


def _aplicar_consentimento(associado, marcado):
    """Registra (ou revoga) o consentimento conforme a caixa do formulário e audita a mudança."""
    if marcado and associado.consentimento_versao != TERMO_CONSENTIMENTO_VERSAO:
        associado.consentimento_data = datetime.now()
        associado.consentimento_versao = TERMO_CONSENTIMENTO_VERSAO
        associado.consentimento_por = session.get('username')
        acao = f'registrado (termo versão {TERMO_CONSENTIMENTO_VERSAO})'
    elif not marcado and associado.consentimento_data:
        associado.consentimento_data = associado.consentimento_versao = associado.consentimento_por = None
        acao = 'revogado'
    else:
        return
    registrar_auditoria(
        ACAO_ASSOCIADO_CONSENTIMENTO,
        entidade='associado',
        entidade_id=associado.id,
        descricao=f'{associado.nome} (matrícula {associado.matricula})',
        detalhes=f'consentimento {acao}',
    )


def _data_iso(valor):
    return valor.isoformat() if valor else None


@app.route('/associado/<int:id>/termo_consentimento')
@login_required
def termo_consentimento(id):
    """Termo de consentimento preenchido, para impressão e assinatura do associado."""
    associado = Associado.query.get_or_404(id)
    if associado.anonimizado_em:
        abort(404)
    html = render_template(
        'pdf_termo_consentimento.html',
        a=associado,
        versao=TERMO_CONSENTIMENTO_VERSAO,
        hoje=datetime.now().date(),
        logo_uri=LOGO_PDF_URI,
    )
    response = make_response(gerar_pdf(html))
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=termo_{secure_filename(associado.matricula)}.pdf'
    return response


@app.route('/associado/<int:id>/dados_titular')
@admin_required
def dados_titular(id):
    """Direito de acesso (LGPD art. 18): tudo o que o sistema guarda sobre o associado, em JSON."""
    a = Associado.query.get_or_404(id)
    historico = (
        Auditoria.query.filter_by(entidade='associado', entidade_id=a.id)
        .order_by(Auditoria.data_hora).all()
    )
    dados = {
        'gerado_em': datetime.now().isoformat(timespec='seconds'),
        'controlador': 'AGMEAL - Associação dos Guardas Municipais de Alagoas',
        'associado': {
            'nome': a.nome, 'matricula': a.matricula, 'cpf': a.cpf, 'rg': a.rg,
            'data_nascimento': _data_iso(a.data_nascimento), 'email': a.email,
            'telefone': a.telefone, 'whatsapp': a.telefone_whatsapp, 'endereco': a.endereco,
            'data_admissao': _data_iso(a.data_admissao),
            'situacao': a.situacao_rotulo, 'situacao_desde': _data_iso(a.situacao_data),
            'situacao_motivo': a.situacao_motivo,
            'possui_foto': bool(a.foto_perfil),
            'cadastrado_em': a.data_criacao.isoformat(timespec='seconds') if a.data_criacao else None,
        },
        'dependentes': [
            {'nome': d.nome, 'parentesco': d.parentesco,
             'data_nascimento': _data_iso(d.data_nascimento), 'cpf': d.cpf}
            for d in a.dependentes
        ],
        'consentimento': {
            'registrado': bool(a.consentimento_data),
            'data': a.consentimento_data.isoformat(timespec='seconds') if a.consentimento_data else None,
            'versao_termo': a.consentimento_versao,
            'registrado_por': a.consentimento_por,
        },
        'historico_de_tratamento': [
            {'data_hora': h.data_hora.isoformat(timespec='seconds'), 'acao': h.rotulo,
             'usuario': h.usuario_username, 'detalhes': h.detalhes}
            for h in historico
        ],
    }
    registrar_auditoria(
        ACAO_ASSOCIADO_DADOS_TITULAR,
        entidade='associado',
        entidade_id=a.id,
        descricao=f'{a.nome} (matrícula {a.matricula})',
        commit=True,
    )
    response = make_response(json.dumps(dados, ensure_ascii=False, indent=2))
    response.headers['Content-Type'] = 'application/json; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename=dados_titular_{secure_filename(a.matricula)}.json'
    return response


def _limpar_auditoria_do_titular(associado_id, termos):
    """Remove dados pessoais do texto da auditoria: os registros do próprio associado perdem a
    descrição/detalhes, e nos demais (ex.: importações) nome/CPF/matrícula são substituídos."""
    marcador = f'[anonimizado #{associado_id}]'
    for log in Auditoria.query.filter_by(entidade='associado', entidade_id=associado_id):
        log.descricao, log.detalhes = marcador, None

    termos = [t for t in termos if t and len(t) >= 3]
    if not termos:
        return
    filtro = db.or_(*(db.or_(Auditoria.descricao.contains(t), Auditoria.detalhes.contains(t)) for t in termos))
    padroes = [re.compile(rf'(?<![\w-]){re.escape(t)}(?![\w-])') for t in termos]
    for log in Auditoria.query.filter(filtro):
        for campo in ('descricao', 'detalhes'):
            texto = getattr(log, campo)
            if texto:
                for padrao in padroes:
                    texto = padrao.sub(marcador, texto)
                setattr(log, campo, texto[:200] if campo == 'descricao' else texto)


@app.route('/associado/<int:id>/anonimizar', methods=['GET', 'POST'])
@admin_required
def anonimizar_associado(id):
    """Anonimização (LGPD art. 16/18): apaga os dados pessoais, mantendo só o que serve às
    estatísticas (situação, admissão e ano de nascimento). Irreversível."""
    a = Associado.query.get_or_404(id)
    if a.anonimizado_em:
        flash('Este cadastro já foi anonimizado.', 'info')
        return redirect(url_for('buscar'))
    if a.situacao == SITUACAO_ATIVO:
        flash('Só é possível anonimizar associados inativos ou desligados. Altere a situação antes.', 'warning')
        return redirect(url_for('editar', id=id))

    if request.method == 'GET':
        return render_template('anonimizar.html', username=session.get('username'), a=a,
                               confirmacao=ANONIMIZAR_CONFIRMACAO)

    if request.form.get('confirmacao', '').strip() != ANONIMIZAR_CONFIRMACAO:
        flash(f'Digite exatamente {ANONIMIZAR_CONFIRMACAO} para confirmar.', 'danger')
        return redirect(url_for('anonimizar_associado', id=id))

    termos = [a.nome, a.cpf, a.matricula, re.sub(r'\D', '', a.cpf or '')] + [d.nome for d in a.dependentes] \
        + [d.cpf for d in a.dependentes if d.cpf]
    foto = a.foto_perfil
    codigo = f'ANON-{a.id:06d}'
    try:
        a.nome = f'Associado anonimizado #{a.id}'
        a.matricula = codigo
        a.cpf = codigo
        a.rg = a.email = a.endereco = ''
        a.telefone = a.telefone_whatsapp = None
        a.data_nascimento = a.data_nascimento.replace(month=1, day=1)
        a.foto_perfil = None
        a.dependentes = []
        a.dependentes_texto_legado = None
        a.situacao_motivo = None
        a.consentimento_data = a.consentimento_versao = a.consentimento_por = None
        a.anonimizado_em = datetime.now()
        _limpar_auditoria_do_titular(a.id, termos)
        registrar_auditoria(
            ACAO_ASSOCIADO_ANONIMIZAR,
            entidade='associado',
            entidade_id=a.id,
            descricao=f'Cadastro #{a.id} anonimizado',
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception('Erro ao anonimizar associado id=%s', id)
        flash('Erro ao anonimizar. Nada foi alterado.', 'danger')
        return redirect(url_for('editar', id=id))

    _remover_foto_do_disco(foto)
    flash(
        'Cadastro anonimizado. Os backups antigos ainda contêm os dados originais até serem '
        'substituídos pela rotação automática.',
        'success',
    )
    return redirect(url_for('buscar'))
