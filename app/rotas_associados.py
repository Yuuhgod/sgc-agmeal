"""Associados: painel, cadastro, busca, edição, exclusão, listas e exportações (PDF/planilha)."""

import base64
import binascii
import io
import os
import re
from datetime import datetime
from pathlib import Path

from flask import (
    flash,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import extract
from sqlalchemy.orm import selectinload

from database import (
    ACAO_ASSOCIADO_CRIAR,
    ACAO_ASSOCIADO_EDITAR,
    ACAO_ASSOCIADO_EXCLUIR,
    ACAO_ASSOCIADO_EXPORTAR,
    PARENTESCO_NAO_INFORMADO,
    PARENTESCOS,
    ROLE_ADMIN,
    SITUACAO_ATIVO,
    SITUACOES_ROTULOS,
    Associado,
    Dependente,
    db,
)
from nucleo import (
    MAX_FOTO_BYTES,
    PAGINA_TAMANHO,
    UPLOAD_FOLDER,
    _bytes_sao_imagem_png_ou_jpeg,
    _contem_texto,
    _exportar_lista_simples_max,
    _exportar_pdf_max_sem_filtro,
    _gerar_nome_foto,
    _normalizar_cpf,
    _remover_foto_do_disco,
    _situacao_backup,
    admin_required,
    allowed_file,
    app,
    gerar_pdf,
    login_required,
    registrar_auditoria,
    validar_cpf,
)
from planilha_service import gerar_csv, gerar_xlsx
from rotas_lgpd import _aplicar_consentimento

MESES_PT = (
    'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
    'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
)


@app.route('/')
@login_required
def dashboard():
    contagem = dict(
        db.session.query(Associado.situacao, db.func.count(Associado.id))
        .group_by(Associado.situacao).all()
    )
    hoje = datetime.now().date()

    # Aniversariantes do mês (só ativos), em ordem de dia.
    aniversariantes = (
        Associado.query
        .filter(Associado.situacao == SITUACAO_ATIVO)
        .filter(extract('month', Associado.data_nascimento) == hoje.month)
        .order_by(extract('day', Associado.data_nascimento), Associado.nome)
        .all()
    )

    # Novos cadastros no mês corrente (data_criacao é gravada em UTC; a margem é irrelevante aqui).
    inicio_mes = datetime(hoje.year, hoje.month, 1)
    novos_no_mes = Associado.query.filter(Associado.data_criacao >= inicio_mes).count()

    # Admissões por ano nos últimos 10 anos (inclui anos sem admissão, com zero).
    anos = list(range(hoje.year - 9, hoje.year + 1))
    ano_col = extract('year', Associado.data_admissao)
    por_ano = dict(
        db.session.query(ano_col, db.func.count(Associado.id))
        .filter(ano_col >= anos[0])
        .group_by(ano_col).all()
    )
    admissoes_por_ano = [(ano, int(por_ano.get(ano, 0))) for ano in anos]

    return render_template(
        'dashboard.html',
        username=session.get('username'),
        total=sum(contagem.values()),
        por_situacao={s: contagem.get(s, 0) for s in SITUACOES_ROTULOS},
        hoje=hoje,
        mes_nome=MESES_PT[hoje.month - 1],
        aniversariantes=aniversariantes,
        novos_no_mes=novos_no_mes,
        admissoes_por_ano=admissoes_por_ano,
        admissoes_max=max((n for _, n in admissoes_por_ano), default=0),
        situacao_backup=_situacao_backup() if session.get('role') == ROLE_ADMIN else None,
    )


FOTO_MAX_DIMENSOES = (600, 800)  # 3x4; o recorte no navegador gera 300x400


def _normalizar_foto(raw):
    """Regrava a foto como JPEG de no máximo 600x800, girada conforme o EXIF.

    Além de padronizar o tamanho (fotos de celular tinham vários MB e pesavam nos PDFs),
    remove os metadados EXIF, que podem conter a localização GPS de onde a foto foi tirada."""
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert('RGB')
            img.thumbnail(FOTO_MAX_DIMENSOES)
            saida = io.BytesIO()
            img.save(saida, format='JPEG', quality=85, optimize=True)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ValueError('Não foi possível ler a imagem. Use uma foto PNG ou JPEG.') from exc
    return saida.getvalue()


def _processar_foto_base64(foto_b64):
    """Decodifica uma foto data-URL, valida e retorna (bytes JPEG normalizados, 'jpg').
    Retorna (None, None) se não houver foto."""
    if not foto_b64:
        return None, None
    try:
        header, encoded = foto_b64.split(",", 1)
        raw_foto = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('Dados de foto inválidos.') from exc

    if len(raw_foto) > MAX_FOTO_BYTES:
        raise ValueError('A foto é muito grande (máximo 6 MB).')
    if not _bytes_sao_imagem_png_ou_jpeg(raw_foto):
        raise ValueError('Arquivo de foto inválido. Use apenas PNG ou JPEG.')

    return _normalizar_foto(raw_foto), 'jpg'


CAMPOS_ASSOCIADO_OBRIGATORIOS = {
    'nome': 'Nome',
    'matricula': 'Matrícula',
    'rg': 'RG',
    'cpf': 'CPF',
    'endereco': 'Endereço',
    'data_nascimento': 'Data de nascimento',
    'email': 'E-mail',
    'data_admissao': 'Data de admissão',
}
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


MAX_DEPENDENTES = 20


def _dependentes_do_form(form):
    """Linhas de dependentes do formulário (campos dep_* repetidos), como texto cru.
    Linhas totalmente em branco são ignoradas."""
    colunas = [form.getlist(f'dep_{c}') for c in ('nome', 'parentesco', 'nascimento', 'cpf')]
    linhas = []
    for nome, parentesco, nascimento, cpf in zip(*colunas, strict=False):
        linha = {
            'nome': nome.strip(), 'parentesco': parentesco.strip(),
            'nascimento': nascimento.strip(), 'cpf': cpf.strip(),
        }
        if linha['nome'] or linha['nascimento'] or linha['cpf']:
            linhas.append(linha)
    return linhas


def _validar_dependentes(form, cpf_titular=None):
    """Valida os dependentes do formulário. Retorna (lista de dicts para o modelo, erros)."""
    erros, dependentes, cpfs = [], [], set()
    hoje = datetime.now().date()
    linhas = _dependentes_do_form(form)
    if len(linhas) > MAX_DEPENDENTES:
        erros.append(f'Informe no máximo {MAX_DEPENDENTES} dependentes.')
        return [], erros

    for n, linha in enumerate(linhas, start=1):
        rotulo = f'Dependente {n}'
        if not linha['nome']:
            erros.append(f'{rotulo}: informe o nome.')
        elif len(linha['nome']) > 100:
            erros.append(f'{rotulo}: nome excede 100 caracteres.')
        parentesco = linha['parentesco'] or PARENTESCO_NAO_INFORMADO
        if parentesco not in PARENTESCOS:
            erros.append(f'{rotulo}: parentesco inválido.')

        nascimento = None
        if linha['nascimento']:
            try:
                nascimento = datetime.strptime(linha['nascimento'], '%Y-%m-%d').date()
                if nascimento.year < 1900 or nascimento > hoje:
                    erros.append(f'{rotulo}: data de nascimento fora do intervalo permitido (1900 até hoje).')
            except ValueError:
                erros.append(f'{rotulo}: data de nascimento inválida.')

        cpf = None
        if linha['cpf']:
            if not validar_cpf(linha['cpf']):
                erros.append(f'{rotulo}: CPF inválido.')
            else:
                cpf = _normalizar_cpf(linha['cpf'])
                if cpf == cpf_titular:
                    erros.append(f'{rotulo}: o CPF é o mesmo do associado titular.')
                elif cpf in cpfs:
                    erros.append(f'{rotulo}: CPF repetido na lista de dependentes.')
                cpfs.add(cpf)

        dependentes.append({
            'nome': linha['nome'], 'parentesco': parentesco,
            'data_nascimento': nascimento, 'cpf': cpf,
        })
    return dependentes, erros


def _resumo_dependentes(dependentes):
    # Ordenado: a lista do formulário vem na ordem digitada, a do banco por nome.
    return '; '.join(sorted(d.resumo() for d in dependentes)) or '(nenhum)'


def _validar_dados_associado(form, associado_id=None):
    """Valida o formulário de associado. Retorna (dados, erros): `dados` já normalizados
    para gravar no modelo e `erros` com mensagens específicas para o usuário.

    `associado_id` é o registro em edição (ignorado na checagem de duplicidade)."""
    erros = []
    dados = {
        'nome': form.get('nome', '').strip(),
        'matricula': form.get('matricula', '').strip(),
        'rg': form.get('rg', '').strip(),
        'cpf': form.get('cpf', '').strip(),
        'telefone': form.get('telefone', '').strip(),
        'telefone_whatsapp': form.get('telefone_whatsapp', '').strip(),
        'endereco': form.get('endereco', '').strip(),
        'email': form.get('email', '').strip(),
    }

    faltando = [
        rotulo for campo, rotulo in CAMPOS_ASSOCIADO_OBRIGATORIOS.items()
        if not (dados.get(campo) if campo in dados else form.get(campo, '').strip())
    ]
    if faltando:
        erros.append('Preencha os campos obrigatórios: ' + ', '.join(faltando) + '.')

    if dados['cpf']:
        if validar_cpf(dados['cpf']):
            dados['cpf'] = _normalizar_cpf(dados['cpf'])
        else:
            erros.append('O CPF digitado é matematicamente inválido.')

    if dados['email'] and not EMAIL_RE.match(dados['email']):
        erros.append('O e-mail informado não é válido.')

    hoje = datetime.now().date()
    for campo, rotulo in (('data_nascimento', 'Data de nascimento'), ('data_admissao', 'Data de admissão')):
        valor = form.get(campo, '').strip()
        if not valor:
            continue
        try:
            data = datetime.strptime(valor, '%Y-%m-%d').date()
        except ValueError:
            erros.append(f'{rotulo} inválida.')
            continue
        if data.year < 1900 or data > hoje:
            erros.append(f'{rotulo} fora do intervalo permitido (1900 até hoje).')
            continue
        dados[campo] = data

    if 'data_nascimento' in dados and 'data_admissao' in dados:
        if dados['data_admissao'] < dados['data_nascimento']:
            erros.append('A data de admissão não pode ser anterior à data de nascimento.')

    for campo, rotulo, limite in (
        ('nome', 'Nome', 100), ('matricula', 'Matrícula', 20), ('rg', 'RG', 20),
        ('telefone', 'Telefone', 15), ('telefone_whatsapp', 'WhatsApp', 15), ('email', 'E-mail', 100),
    ):
        if len(dados[campo]) > limite:
            erros.append(f'{rotulo} excede {limite} caracteres.')

    # Situação cadastral: só vem no formulário de edição (novos cadastros entram como ativos).
    if 'situacao' in form:
        situacao = form.get('situacao', '').strip()
        if situacao not in SITUACOES_ROTULOS:
            erros.append('Situação inválida.')
        elif situacao == SITUACAO_ATIVO:
            dados.update(situacao=situacao, situacao_data=None, situacao_motivo=None)
        else:
            motivo = form.get('situacao_motivo', '').strip()
            if len(motivo) > 200:
                erros.append('O motivo da situação excede 200 caracteres.')
            valor = form.get('situacao_data', '').strip()
            try:
                data_situacao = datetime.strptime(valor, '%Y-%m-%d').date() if valor else hoje
            except ValueError:
                erros.append('Data da situação inválida.')
                data_situacao = None
            if data_situacao is not None:
                if data_situacao > hoje:
                    erros.append('A data da situação não pode ser futura.')
                elif 'data_admissao' in dados and data_situacao < dados['data_admissao']:
                    erros.append('A data da situação não pode ser anterior à data de admissão.')
            dados.update(situacao=situacao, situacao_data=data_situacao, situacao_motivo=motivo or None)

    # Duplicidade verificada antes de gravar, com mensagem específica para cada campo.
    duplicados = Associado.query
    if associado_id is not None:
        duplicados = duplicados.filter(Associado.id != associado_id)
    if dados['matricula'] and duplicados.filter(Associado.matricula == dados['matricula']).first():
        erros.append(f'Já existe um associado com a matrícula {dados["matricula"]}.')
    if dados['cpf'] and duplicados.filter(Associado.cpf == dados['cpf']).first():
        erros.append(f'Já existe um associado com o CPF {dados["cpf"]}.')

    return dados, erros


def _render_cadastro():
    """Formulário de cadastro; num POST com erro, devolve as linhas de dependentes digitadas."""
    return render_template(
        'cadastro.html',
        username=session.get('username'),
        dependentes_form=_dependentes_do_form(request.form) if request.method == 'POST' else [],
    )


@app.route('/cadastro', methods=['GET', 'POST'])
@login_required
def cadastro():
    if request.method == 'POST':
        dados, erros = _validar_dados_associado(request.form)
        dependentes, erros_dep = _validar_dependentes(request.form, cpf_titular=dados.get('cpf'))
        erros += erros_dep
        if erros:
            for erro in erros:
                flash(erro, 'danger')
            return _render_cadastro()

        try:
            raw_foto, extensao = _processar_foto_base64(request.form.get('foto_base64'))
        except ValueError as exc:
            flash(str(exc), 'danger')
            return _render_cadastro()

        nome_arquivo = None
        try:
            if raw_foto:
                nome_arquivo = _gerar_nome_foto(dados['matricula'], extensao)
                caminho_salvar = os.path.join(app.config['UPLOAD_FOLDER'], nome_arquivo)
                with open(caminho_salvar, 'wb') as fh:
                    fh.write(raw_foto)

            novo_associado = Associado(foto_perfil=nome_arquivo, **dados)
            novo_associado.dependentes = [Dependente(**d) for d in dependentes]
            db.session.add(novo_associado)
            db.session.flush()  # gera o ID antes do commit p/ usar na auditoria
            registrar_auditoria(
                ACAO_ASSOCIADO_CRIAR,
                entidade='associado',
                entidade_id=novo_associado.id,
                descricao=f'{novo_associado.nome} (matrícula {novo_associado.matricula})',
            )
            _aplicar_consentimento(novo_associado, request.form.get('consentimento') == '1')
            db.session.commit()
            flash('Associado cadastrado com sucesso!', 'success')
            return redirect(url_for('cadastro'))

        except Exception:
            db.session.rollback()
            app.logger.exception('Erro ao cadastrar associado')
            # Limpa foto recém-salva em caso de rollback (evita arquivo órfão).
            _remover_foto_do_disco(nome_arquivo)
            flash('Erro inesperado ao cadastrar. Tente novamente ou consulte o log do servidor.', 'danger')
            return _render_cadastro()

    return _render_cadastro()


FILTROS_BUSCA = ('nome', 'matricula', 'ano', 'situacao')


def _ler_filtros(fonte, sufixo=''):
    """Lê os filtros de busca de um formulário/args (`sufixo` p/ campos ocultos de exportação)."""
    filtros = {c: fonte.get(c + sufixo, '').strip() for c in FILTROS_BUSCA}
    if filtros['situacao'] not in SITUACOES_ROTULOS:
        filtros['situacao'] = ''
    return filtros


def _filtros_texto_vazios(filtros):
    """True se não há filtro de nome, matrícula nem ano (a situação sozinha não reduz o bastante)."""
    return not (filtros['nome'] or filtros['matricula'] or filtros['ano'])


def _aplicar_filtros_busca(query, filtros):
    """Aplica os filtros à consulta. Levanta ValueError se o ano não for numérico."""
    if filtros['nome']:
        query = query.filter(_contem_texto(Associado.nome, filtros['nome']))
    if filtros['matricula']:
        query = query.filter(Associado.matricula == filtros['matricula'])
    if filtros['ano']:
        query = query.filter(extract('year', Associado.data_admissao) == int(filtros['ano']))
    if filtros['situacao']:
        query = query.filter(Associado.situacao == filtros['situacao'])
    return query


@app.route('/buscar', methods=['GET', 'POST'])
@login_required
def buscar():
    resultados = None
    filtros = {c: '' for c in FILTROS_BUSCA}

    if request.method == 'POST':
        filtros = _ler_filtros(request.form)

        try:
            query = _aplicar_filtros_busca(Associado.query.order_by(Associado.nome), filtros)
            resultados = query.all()
        except ValueError:
            flash('Ano de admissão inválido.', 'warning')
            resultados = []

        if not resultados:
            flash('Nenhum registro encontrado com estes filtros.', 'warning')

    # Filtrar só pela situação não conta como filtro para o limite do PDF, mas o aviso
    # só aparece se não houver filtro nenhum ou se o resultado passar do limite.
    aviso_pdf_busca_sem_filtro = (
        request.method == 'POST'
        and _filtros_texto_vazios(filtros)
        and bool(resultados)
        and (not filtros['situacao'] or len(resultados) > _exportar_pdf_max_sem_filtro())
    )

    return render_template(
        'buscar.html',
        username=session.get('username'),
        resultados=resultados,
        filtros=filtros,
        aviso_pdf_busca_sem_filtro=aviso_pdf_busca_sem_filtro,
        exportar_pdf_max_sem_filtro=_exportar_pdf_max_sem_filtro(),
    )


@app.route('/exportar_pdf', methods=['POST'])
@login_required
def exportar_pdf():
    filtros = _ler_filtros(request.form, sufixo='_export')

    try:
        query = _aplicar_filtros_busca(
            Associado.query.options(selectinload(Associado.dependentes)).order_by(Associado.nome), filtros,
        )
        if _filtros_texto_vazios(filtros):
            max_sem = _exportar_pdf_max_sem_filtro()
            total = query.count()
            if total > max_sem:
                flash(
                    f'Exportar PDF sem filtros incluiria os {total} associados e pode bloquear o '
                    f'servidor durante muito tempo. O limite configurado é {max_sem}. '
                    f'Use pelo menos um filtro (nome, matrícula ou ano) ou defina '
                    f'EXPORTAR_PDF_MAX_SEM_FILTRO no ambiente para aumentar o teto — com cuidado.',
                    'warning',
                )
                return redirect(url_for('buscar'))

        resultados = query.all()
    except ValueError:
        flash('Ano de admissão inválido.', 'warning')
        return redirect(url_for('buscar'))

    if not resultados:
        flash('Nenhum dado para exportar.', 'warning')
        return redirect(url_for('buscar'))

    data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    html_renderizado = render_template(
        'pdf_relatorio.html',
        resultados=resultados,
        now=data_geracao,
        fotos_base_uri=Path(UPLOAD_FOLDER).as_uri(),
    )

    pdf = gerar_pdf(html_renderizado)

    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'inline; filename=relatorio_agmeal.pdf'
    return response


FORMATOS_PLANILHA = {
    'csv': ('text/csv; charset=utf-8', gerar_csv),
    'xlsx': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', gerar_xlsx),
}


@app.route('/exportar_planilha', methods=['POST'])
@login_required
def exportar_planilha():
    """Exporta os associados filtrados (mesmos filtros da busca) em CSV ou XLSX."""
    formato = request.form.get('formato', '').strip().lower()
    if formato not in FORMATOS_PLANILHA:
        flash('Formato de planilha inválido.', 'warning')
        return redirect(request.referrer or url_for('buscar'))

    filtros = _ler_filtros(request.form, sufixo='_export')
    try:
        associados = _aplicar_filtros_busca(
            Associado.query.options(selectinload(Associado.dependentes)).order_by(Associado.nome), filtros,
        ).all()
    except ValueError:
        flash('Ano de admissão inválido.', 'warning')
        return redirect(url_for('buscar'))

    if not associados:
        flash('Nenhum dado para exportar.', 'warning')
        return redirect(request.referrer or url_for('buscar'))

    mimetype, gerar = FORMATOS_PLANILHA[formato]
    conteudo = gerar(associados)

    # Planilhas levam dados pessoais para fora do sistema: fica registrado quem exportou o quê.
    filtros_usados = ', '.join(f'{k}={v}' for k, v in filtros.items() if v) or 'nenhum'
    registrar_auditoria(
        ACAO_ASSOCIADO_EXPORTAR,
        entidade='associado',
        descricao=f'{len(associados)} associado(s) em {formato.upper()}',
        detalhes=f'filtros: {filtros_usados}',
        commit=True,
    )

    nome_arquivo = f"associados_{datetime.now().strftime('%Y%m%d_%H%M')}.{formato}"
    response = make_response(conteudo)
    response.headers['Content-Type'] = mimetype
    response.headers['Content-Disposition'] = f'attachment; filename={nome_arquivo}'
    return response


@app.route('/exportar_ficha/<matricula>')
@login_required
def exportar_ficha(matricula):
    associado = Associado.query.filter_by(matricula=matricula).first()

    if not associado:
        flash('Associado não encontrado.', 'danger')
        return redirect(url_for('buscar'))

    data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    html_renderizado = render_template(
        'pdf_relatorio.html',
        resultados=[associado],
        now=data_geracao,
        fotos_base_uri=Path(UPLOAD_FOLDER).as_uri(),
    )

    pdf = gerar_pdf(html_renderizado)

    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=ficha_{associado.matricula}.pdf'
    return response


CAMPOS_ASSOCIADO_AUDITAVEIS = [
    'nome', 'matricula', 'rg', 'cpf', 'telefone', 'telefone_whatsapp',
    'endereco', 'data_nascimento', 'email', 'data_admissao',
    'situacao', 'situacao_data', 'situacao_motivo',
]


def _diff_associado(antes, associado, foto_alterada):
    diffs = []
    for campo in CAMPOS_ASSOCIADO_AUDITAVEIS:
        valor_anterior = antes.get(campo)
        valor_atual = getattr(associado, campo)
        if valor_anterior != valor_atual:
            diffs.append(f'{campo}: "{valor_anterior}" → "{valor_atual}"')
    if foto_alterada:
        diffs.append('foto_perfil: alterada')
    return '\n'.join(diffs) if diffs else '(nenhum campo alterado)'


@app.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar(id):
    associado = Associado.query.get_or_404(id)
    if associado.anonimizado_em:
        flash('Este cadastro foi anonimizado (LGPD) e não pode mais ser editado.', 'warning')
        return redirect(url_for('buscar'))

    if request.method == 'POST':
        foto_anterior = associado.foto_perfil
        nova_foto_nome = None

        dados, erros = _validar_dados_associado(request.form, associado_id=associado.id)
        dependentes, erros_dep = _validar_dependentes(request.form, cpf_titular=dados.get('cpf'))
        erros += erros_dep
        if erros:
            for erro in erros:
                flash(erro, 'danger')
            return redirect(url_for('editar', id=id))

        try:
            antes = {c: getattr(associado, c) for c in CAMPOS_ASSOCIADO_AUDITAVEIS}
            dependentes_antes = _resumo_dependentes(associado.dependentes)
            for campo, valor in dados.items():
                setattr(associado, campo, valor)
            associado.dependentes = [Dependente(**d) for d in dependentes]
            if request.form.get('lgpd_form') == '1':  # só o formulário de edição traz a caixa
                _aplicar_consentimento(associado, request.form.get('consentimento') == '1')

            # Foto recortada no navegador (base64) ou, sem JavaScript, o arquivo enviado.
            try:
                dados_foto, extensao = _processar_foto_base64(request.form.get('foto_base64'))
                foto = request.files.get('foto_perfil')
                if dados_foto is None and foto and foto.filename and allowed_file(foto.filename):
                    bruto = foto.read()
                    if len(bruto) > MAX_FOTO_BYTES:
                        raise ValueError('A foto é muito grande (máximo 6 MB).')
                    if not _bytes_sao_imagem_png_ou_jpeg(bruto):
                        raise ValueError('Arquivo de foto inválido. Use apenas PNG ou JPEG.')
                    dados_foto, extensao = _normalizar_foto(bruto), 'jpg'
            except ValueError as exc:
                flash(str(exc), 'danger')
                db.session.rollback()
                return redirect(url_for('editar', id=id))

            if dados_foto:
                nova_foto_nome = _gerar_nome_foto(associado.matricula, extensao)
                caminho_salvar = os.path.join(app.config['UPLOAD_FOLDER'], nova_foto_nome)
                with open(caminho_salvar, 'wb') as fh:
                    fh.write(dados_foto)
                associado.foto_perfil = nova_foto_nome

            detalhes = _diff_associado(antes, associado, foto_alterada=bool(nova_foto_nome))
            dependentes_depois = _resumo_dependentes(associado.dependentes)
            if dependentes_depois != dependentes_antes:
                linha = f'dependentes: "{dependentes_antes}" → "{dependentes_depois}"'
                detalhes = linha if detalhes == '(nenhum campo alterado)' else f'{detalhes}\n{linha}'
            registrar_auditoria(
                ACAO_ASSOCIADO_EDITAR,
                entidade='associado',
                entidade_id=associado.id,
                descricao=f'{associado.nome} (matrícula {associado.matricula})',
                detalhes=detalhes,
            )

            db.session.commit()

            # Remove a foto antiga do disco só após commit bem-sucedido.
            if nova_foto_nome and foto_anterior and foto_anterior != nova_foto_nome:
                _remover_foto_do_disco(foto_anterior)

            flash('Cadastro atualizado com sucesso!', 'success')
            return redirect(url_for('buscar'))

        except Exception:
            db.session.rollback()
            app.logger.exception('Erro ao atualizar associado id=%s', id)
            if nova_foto_nome:
                _remover_foto_do_disco(nova_foto_nome)
            flash('Erro inesperado ao atualizar. Tente novamente ou consulte o log do servidor.', 'danger')

    return render_template('editar.html', username=session.get('username'), associado=associado)


@app.route('/excluir/<int:id>', methods=['POST'])
@admin_required
def excluir(id):
    associado = Associado.query.get_or_404(id)
    foto_para_remover = associado.foto_perfil
    cpf_capturado = associado.cpf
    nome = associado.nome
    matricula = associado.matricula
    associado_id = associado.id

    try:
        db.session.delete(associado)
        registrar_auditoria(
            ACAO_ASSOCIADO_EXCLUIR,
            entidade='associado',
            entidade_id=associado_id,
            descricao=f'{nome} (matrícula {matricula})',
            detalhes=f'CPF: {cpf_capturado}',
        )
        db.session.commit()
        _remover_foto_do_disco(foto_para_remover)
        flash(f'O registro de {nome} foi excluído com sucesso.', 'success')
    except Exception:
        db.session.rollback()
        app.logger.exception('Erro ao excluir associado id=%s', id)
        flash('Erro ao tentar excluir o registro.', 'danger')

    return redirect(url_for('buscar'))


@app.route('/listar')
@login_required
def listar_todos():
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1

    situacao = request.args.get('situacao', '').strip()
    if situacao not in SITUACOES_ROTULOS:
        situacao = ''

    query = Associado.query.order_by(Associado.nome)
    if situacao:
        query = query.filter(Associado.situacao == situacao)
    paginacao = query.paginate(page=page, per_page=PAGINA_TAMANHO, error_out=False)
    return render_template(
        'listar.html',
        username=session.get('username'),
        associados=paginacao.items,
        paginacao=paginacao,
        situacao=situacao,
    )


@app.route('/exportar_lista_simples', methods=['POST'])
@login_required
def exportar_lista_simples():
    situacao = request.form.get('situacao', '').strip()
    if situacao not in SITUACOES_ROTULOS:
        situacao = ''
    query = Associado.query.order_by(Associado.nome)
    if situacao:
        query = query.filter(Associado.situacao == situacao)

    max_linhas = _exportar_lista_simples_max()
    total = query.count()
    if total > max_linhas:
        flash(
            f'A lista teria {total} associados, acima do limite configurado de {max_linhas}. '
            f'Use a busca com filtros ou aumente EXPORTAR_LISTA_SIMPLES_MAX no ambiente — com cuidado.',
            'warning',
        )
        return redirect(url_for('listar_todos', situacao=situacao or None))

    associados = query.all()
    data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

    html_renderizado = render_template(
        'pdf_lista_simples.html',
        associados=associados,
        now=data_geracao,
        situacao_rotulo=SITUACOES_ROTULOS.get(situacao),
    )

    pdf = gerar_pdf(html_renderizado)

    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'inline; filename=lista_associados.pdf'
    return response
