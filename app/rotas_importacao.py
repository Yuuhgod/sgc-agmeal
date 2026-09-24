"""Importação em lote de associados a partir de planilha (XLSX/CSV)."""

import os
import re
import time
import uuid

from flask import (
    flash,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.datastructures import MultiDict
from werkzeug.utils import secure_filename

from database import (
    ACAO_ASSOCIADO_IMPORTAR,
    PARENTESCO_NAO_INFORMADO,
    PARENTESCOS,
    SITUACOES_ROTULOS,
    Associado,
    Dependente,
    db,
)
from importacao_service import (
    PlanilhaInvalida,
    gerar_modelo_xlsx,
    ler_planilha,
    separar_dependentes,
)
from nucleo import admin_required, app, registrar_auditoria, restore_pending_dir
from rotas_associados import (
    FORMATOS_PLANILHA,
    _validar_dados_associado,
    _validar_dependentes,
)

# ---------------------------------------------------------------------------------------
# Importação em lote
# ---------------------------------------------------------------------------------------

IMPORTACAO_PREFIXO = 'importacao_'
_SITUACAO_POR_ROTULO = {r.lower(): k for k, r in SITUACOES_ROTULOS.items()}


def _form_da_linha(linha):
    """Converte uma linha da planilha no formato de formulário usado pelo cadastro."""
    form = MultiDict({c: v for c, v in linha.items() if c not in ('situacao', 'situacao_data',
                                                                    'situacao_motivo', 'dependentes')})
    situacao = linha.get('situacao', '').strip()
    if situacao:
        form['situacao'] = _SITUACAO_POR_ROTULO.get(situacao.lower(), situacao)
        form['situacao_data'] = linha.get('situacao_data', '')
        form['situacao_motivo'] = linha.get('situacao_motivo', '')
    for nome, parentesco in separar_dependentes(linha.get('dependentes', ''), PARENTESCOS, PARENTESCO_NAO_INFORMADO):
        form.add('dep_nome', nome)
        form.add('dep_parentesco', parentesco)
        form.add('dep_nascimento', '')
        form.add('dep_cpf', '')
    return form


def _validar_importacao(linhas):
    """Valida cada linha com as regras do cadastro, mais duplicidade dentro da planilha."""
    resultado, matriculas, cpfs = [], {}, {}
    for numero, linha in linhas:
        form = _form_da_linha(linha)
        dados, erros = _validar_dados_associado(form)
        dependentes, erros_dep = _validar_dependentes(form, cpf_titular=dados.get('cpf'))
        erros += erros_dep
        matricula, cpf = dados.get('matricula'), dados.get('cpf')
        if matricula and matricula in matriculas:
            erros.append(f'Matrícula repetida na planilha (linha {matriculas[matricula]}).')
        if cpf and cpf in cpfs:
            erros.append(f'CPF repetido na planilha (linha {cpfs[cpf]}).')
        if matricula:
            matriculas.setdefault(matricula, numero)
        if cpf:
            cpfs.setdefault(cpf, numero)
        resultado.append({
            'linha': numero, 'matricula': matricula or linha.get('matricula', ''),
            'nome': dados.get('nome') or linha.get('nome', ''), 'erros': erros, 'ok': not erros,
            'dados': dados, 'dependentes': dependentes,
        })
    return resultado


def _arquivo_importacao(token, extensao):
    if not re.fullmatch(r'[0-9a-f]{32}', token or '') or extensao not in ('csv', 'xlsx'):
        return None
    return os.path.join(restore_pending_dir, f'{IMPORTACAO_PREFIXO}{token}.{extensao}')


def _limpar_importacoes_antigas(horas=24):
    limite = time.time() - horas * 3600
    for nome in os.listdir(restore_pending_dir):
        caminho = os.path.join(restore_pending_dir, nome)
        if nome.startswith(IMPORTACAO_PREFIXO) and os.path.getmtime(caminho) < limite:
            try:
                os.remove(caminho)
            except OSError:
                pass


def _importacao_pendente():
    """(caminho, info) da importação guardada na sessão, se o arquivo ainda existir."""
    info = session.get('importacao') or {}
    caminho = _arquivo_importacao(info.get('token'), info.get('extensao'))
    if caminho and os.path.isfile(caminho):
        return caminho, info
    return None, None


@app.route('/associados/importar/modelo')
@admin_required
def importar_modelo():
    response = make_response(gerar_modelo_xlsx(PARENTESCOS, list(SITUACOES_ROTULOS.values())))
    response.headers['Content-Type'] = FORMATOS_PLANILHA['xlsx'][0]
    response.headers['Content-Disposition'] = 'attachment; filename=modelo_importacao_associados.xlsx'
    return response


@app.route('/associados/importar', methods=['GET', 'POST'])
@admin_required
def importar_associados():
    if request.method == 'GET':
        return render_template('importar.html', username=session.get('username'), previa=None)

    upload = request.files.get('arquivo')
    nome_original = secure_filename(upload.filename) if upload and upload.filename else ''
    extensao = nome_original.rsplit('.', 1)[-1].lower() if '.' in nome_original else ''
    if extensao not in ('csv', 'xlsx'):
        flash('Selecione um arquivo .xlsx ou .csv.', 'warning')
        return redirect(url_for('importar_associados'))

    conteudo = upload.read()
    try:
        linhas, ignoradas = ler_planilha(conteudo, extensao)
    except PlanilhaInvalida as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('importar_associados'))

    # Guarda o arquivo para a confirmação (revalidada lá, pois o banco pode mudar entre as etapas).
    _limpar_importacoes_antigas()
    anterior, _ = _importacao_pendente()
    if anterior:
        os.remove(anterior)
    token = uuid.uuid4().hex
    with open(_arquivo_importacao(token, extensao), 'wb') as fh:
        fh.write(conteudo)
    session['importacao'] = {'token': token, 'extensao': extensao, 'nome': nome_original}

    previa = _validar_importacao(linhas)
    return render_template(
        'importar.html',
        username=session.get('username'),
        previa=previa,
        validas=sum(1 for r in previa if not r['erros']),
        com_erro=sum(1 for r in previa if r['erros']),
        ignoradas=ignoradas,
        nome_arquivo=nome_original,
    )


@app.route('/associados/importar/confirmar', methods=['POST'])
@admin_required
def importar_confirmar():
    caminho, info = _importacao_pendente()
    if not caminho:
        flash('A pré-visualização expirou. Envie a planilha novamente.', 'warning')
        return redirect(url_for('importar_associados'))

    try:
        with open(caminho, 'rb') as fh:
            linhas, _ = ler_planilha(fh.read(), info['extensao'])
        validas = [r for r in _validar_importacao(linhas) if not r['erros']]
        if not validas:
            flash('Nenhuma linha válida para importar.', 'warning')
            return redirect(url_for('importar_associados'))

        # Tudo numa transação: ou entram todas as linhas válidas, ou nenhuma.
        for r in validas:
            associado = Associado(**r['dados'])
            associado.dependentes = [Dependente(**d) for d in r['dependentes']]
            db.session.add(associado)
        matriculas = ', '.join(r['dados']['matricula'] for r in validas)
        registrar_auditoria(
            ACAO_ASSOCIADO_IMPORTAR,
            entidade='associado',
            descricao=f"{len(validas)} associado(s) importado(s) de {info.get('nome') or 'planilha'}",
            detalhes=f'matrículas: {matriculas}'[:4000],
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception('Erro ao importar associados')
        flash('Erro inesperado ao importar. Nada foi gravado; tente novamente.', 'danger')
        return redirect(url_for('importar_associados'))
    finally:
        session.pop('importacao', None)
        if os.path.isfile(caminho):
            os.remove(caminho)

    app.logger.info('Importação por %s: %s associados', session.get('username'), len(validas))
    flash(f'{len(validas)} associado(s) importado(s) com sucesso.', 'success')
    return redirect(url_for('listar_todos'))


@app.route('/associados/importar/cancelar', methods=['POST'])
@admin_required
def importar_cancelar():
    caminho, _ = _importacao_pendente()
    if caminho:
        os.remove(caminho)
    session.pop('importacao', None)
    flash('Importação cancelada. Nada foi gravado.', 'info')
    return redirect(url_for('importar_associados'))
