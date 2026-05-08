import base64
import binascii
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    current_app,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import extract, inspect, text
from weasyprint import HTML
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from database import (
    ACAO_ASSOCIADO_CRIAR,
    ACAO_ASSOCIADO_EDITAR,
    ACAO_ASSOCIADO_EXCLUIR,
    ACAO_AUTH_LOGIN,
    ACAO_AUTH_LOGIN_FALHOU,
    ACAO_AUTH_LOGOUT,
    ACAO_USUARIO_CRIAR,
    ACAO_USUARIO_EXCLUIR,
    ACAO_USUARIO_PALAVRA,
    ACAO_USUARIO_PERFIL,
    ACOES_ROTULOS,
    Associado,
    Auditoria,
    ROLE_ADMIN,
    ROLE_USUARIO,
    ROLES_VALIDAS,
    Usuario,
    db,
)

app = Flask(__name__)

# Corrige scheme/host/ip quando atrás do Nginx (X-Forwarded-*).
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

basedir = os.path.abspath(os.path.dirname(__file__))
data_dir = os.path.join(basedir, '..', 'data')
os.makedirs(data_dir, exist_ok=True)


def _carregar_secret_key():
    env = os.environ.get('SECRET_KEY') or os.environ.get('FLASK_SECRET_KEY')
    if env:
        return env
    path = os.path.join(data_dir, '.flask_secret')
    if os.path.isfile(path):
        try:
            with open(path, encoding='utf-8') as fh:
                return fh.read().strip()
        except PermissionError:
            # Arquivo criado como root (Docker) com modo 600 — tenta corrigir as permissões.
            try:
                os.chmod(path, 0o600)
                with open(path, encoding='utf-8') as fh:
                    return fh.read().strip()
            except (OSError, PermissionError):
                logging.warning(
                    'Não foi possível ler %s (permissão negada). '
                    'Execute: sudo chmod 600 %s   ou defina SECRET_KEY no ambiente.',
                    path, path,
                )
                # Gera uma chave temporária (sessões serão perdidas ao reiniciar).
                return secrets.token_urlsafe(48)
    key = secrets.token_urlsafe(48)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


app.config['SECRET_KEY'] = _carregar_secret_key()

# Define SESSION_COOKIE_SECURE=true em produção (quando houver HTTPS).
_cookie_secure = os.environ.get('SESSION_COOKIE_SECURE', '').lower() in ('1', 'true', 'yes')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=_cookie_secure,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    WTF_CSRF_TIME_LIMIT=3600,
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
)

db_path = os.path.join(data_dir, 'sgc.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

csrf = CSRFProtect(app)

# Observação: memory:// conta por worker do Gunicorn. A defesa principal contra brute force
# é o rate limit no Nginx (nginx.conf). Para contagem compartilhada, aponte
# storage_uri para "redis://<host>:6379" com um Redis no docker-compose.
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["300 per hour"],
    storage_uri=os.environ.get("RATE_LIMIT_STORAGE", "memory://"),
)

UPLOAD_FOLDER = os.path.join(basedir, 'static', 'uploads', 'fotos')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
MAX_FOTO_BYTES = 6 * 1024 * 1024
PAGINA_TAMANHO = 25

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)
app.logger.setLevel(logging.INFO)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _normalizar_cpf(cpf):
    """Retorna o CPF formatado como XXX.XXX.XXX-XX a partir de qualquer entrada."""
    digits = re.sub(r'\D', '', cpf)
    if len(digits) == 11:
        return f'{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}'
    return cpf.strip()


def _bytes_sao_imagem_png_ou_jpeg(data):
    if not data or len(data) < 8:
        return False
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return True
    return data.startswith(b'\xff\xd8\xff')


def _remover_foto_do_disco(nome_arquivo):
    """Remove uma foto do disco com segurança, prevenindo path traversal."""
    if not nome_arquivo:
        return
    nome_seguro = secure_filename(nome_arquivo)
    if not nome_seguro:
        return
    caminho = os.path.join(current_app.config['UPLOAD_FOLDER'], nome_seguro)
    try:
        if os.path.isfile(caminho):
            os.remove(caminho)
    except OSError:
        current_app.logger.warning('Falha ao remover foto: %s', caminho, exc_info=True)


def _gerar_nome_foto(matricula, extensao):
    base = secure_filename(matricula) or 'foto'
    return f"{base}_{uuid.uuid4().hex[:8]}.{extensao}"


db.init_app(app)


def _garantir_coluna_role():
    """Mini-migração idempotente: adiciona 'role' em bancos antigos e define usuários
    pré-existentes como admin (mantendo o acesso total do administrador original).

    Tolerante a corrida entre múltiplos workers do Gunicorn (captura duplicate column)."""
    inspector = inspect(db.engine)
    if 'usuarios' not in inspector.get_table_names():
        return
    colunas = {c['name'] for c in inspector.get_columns('usuarios')}
    if 'role' in colunas:
        return
    try:
        with db.engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE usuarios ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'admin'"
            ))
        app.logger.info("Coluna 'role' adicionada em 'usuarios' (usuários existentes marcados como admin).")
    except Exception as exc:
        # Outro worker pode ter adicionado a coluna em paralelo — ignora se já existir.
        if 'duplicate column' in str(exc).lower():
            app.logger.info("Coluna 'role' já havia sido adicionada por outro processo.")
            return
        raise


with app.app_context():
    try:
        db.create_all()
    except Exception as exc:
        # Ignora erros de "table already exists" em corridas entre workers do Gunicorn.
        if 'already exists' in str(exc).lower():
            app.logger.info('Tabelas já existem (criadas por outro worker) — ignorando.')
        else:
            raise
    _garantir_coluna_role()


def validar_cpf(cpf):
    cpf = re.sub(r'\D', '', cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False

    soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
    digito1 = (soma * 10) % 11
    if digito1 >= 10:
        digito1 = 0
    if digito1 != int(cpf[9]):
        return False

    soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
    digito2 = (soma * 10) % 11
    if digito2 >= 10:
        digito2 = 0
    if digito2 != int(cpf[10]):
        return False

    return True


@app.before_request
def verificar_primeiro_acesso():
    if request.endpoint in ['setup', 'static']:
        return
    if Usuario.query.count() == 0:
        return redirect(url_for('setup'))


@app.after_request
def add_header(response):
    """Cabeçalhos de segurança + impede cache de páginas autenticadas."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            flash('Por favor, faça login para acessar o sistema.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            flash('Por favor, faça login para acessar o sistema.', 'warning')
            return redirect(url_for('login'))
        if session.get('role') != ROLE_ADMIN:
            flash('Apenas administradores podem acessar esta área.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


@app.context_processor
def inject_sessao():
    """Disponibiliza is_admin nos templates sem consultar o DB."""
    return {
        'sessao_is_admin': session.get('role') == ROLE_ADMIN,
        'sessao_role': session.get('role'),
    }


def registrar_auditoria(
    acao,
    entidade=None,
    entidade_id=None,
    descricao=None,
    detalhes=None,
    usuario_id=None,
    usuario_username=None,
    commit=False,
):
    """Adiciona um registro à trilha de auditoria.

    Por padrão NÃO comita — junta na transação atual da rota. Use commit=True para
    casos sem outra escrita pendente (ex.: login)."""
    try:
        if usuario_id is None:
            usuario_id = session.get('usuario_id')
        if not usuario_username:
            usuario_username = session.get('username') or '(anônimo)'

        log = Auditoria(
            usuario_id=usuario_id,
            usuario_username=usuario_username[:80],
            acao=acao,
            entidade=entidade,
            entidade_id=entidade_id,
            descricao=(descricao or None) and str(descricao)[:200],
            detalhes=detalhes,
            ip_origem=(request.remote_addr or '')[:45] if request else None,
        )
        db.session.add(log)
        if commit:
            db.session.commit()
    except Exception:
        # Auditoria nunca deve quebrar a rota principal.
        db.session.rollback() if commit else None
        app.logger.exception('Falha ao registrar auditoria (acao=%s)', acao)


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if Usuario.query.count() > 0:
        return redirect(url_for('login'))

    if request.method == 'POST':
        username = request.form['username'].strip()
        senha = request.form['senha']
        palavra = request.form['palavra_recuperacao']

        if len(senha) < 8:
            flash('A senha deve ter no mínimo 8 caracteres.', 'danger')
            return render_template('setup.html')

        # O usuário criado no setup é sempre administrador.
        novo_admin = Usuario(username=username, role=ROLE_ADMIN)
        novo_admin.set_senha(senha)
        novo_admin.set_palavra_recuperacao(palavra)

        db.session.add(novo_admin)
        db.session.commit()

        flash('Instalação concluída! Faça login com seu novo usuário.', 'success')
        return redirect(url_for('login'))

    return render_template('setup.html')


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if 'usuario_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form['username'].strip()
        senha = request.form['senha']

        usuario = Usuario.query.filter_by(username=username).first()

        if usuario and usuario.check_senha(senha):
            session.clear()
            session['usuario_id'] = usuario.id
            session['username'] = usuario.username
            session['role'] = usuario.role
            session.permanent = True
            app.logger.info('Login bem-sucedido: %s (role=%s)', username, usuario.role)
            registrar_auditoria(
                ACAO_AUTH_LOGIN,
                entidade='usuario',
                entidade_id=usuario.id,
                descricao=f'Login de {usuario.username}',
                usuario_id=usuario.id,
                usuario_username=usuario.username,
                commit=True,
            )
            return redirect(url_for('dashboard'))

        app.logger.warning('Tentativa de login falha para usuário: %s', username)
        registrar_auditoria(
            ACAO_AUTH_LOGIN_FALHOU,
            descricao=f'Tentativa com usuário "{username}"',
            usuario_id=None,
            usuario_username=username or '(vazio)',
            commit=True,
        )
        flash('Usuário ou senha incorretos.', 'danger')

    return render_template('login.html')


@app.route('/logout', methods=['POST'])
def logout():
    if 'usuario_id' in session:
        registrar_auditoria(
            ACAO_AUTH_LOGOUT,
            entidade='usuario',
            entidade_id=session.get('usuario_id'),
            descricao=f'Logout de {session.get("username")}',
            commit=True,
        )
    session.clear()
    return redirect(url_for('login'))


@app.route('/esqueci_senha', methods=['GET', 'POST'])
@limiter.limit("5 per minute", methods=["POST"])
def esqueci_senha():
    if request.method == 'POST':
        username = request.form['username'].strip()
        palavra = request.form['palavra_recuperacao']
        nova_senha = request.form['nova_senha']

        if len(nova_senha) < 8:
            flash('A nova senha deve ter no mínimo 8 caracteres.', 'danger')
            return render_template('esqueci_senha.html')

        usuario = Usuario.query.filter_by(username=username).first()

        if usuario and usuario.verificar_palavra_recuperacao(palavra):
            if usuario.check_senha(nova_senha):
                flash('A nova senha não pode ser igual à senha atual.', 'warning')
                return redirect(url_for('esqueci_senha'))

            usuario.migrar_palavra_recuperacao_se_legado(palavra)
            usuario.set_senha(nova_senha)
            db.session.commit()
            app.logger.info('Senha redefinida via palavra de recuperação: %s', username)
            flash('Senha alterada com sucesso! Você já pode fazer login.', 'success')
            return redirect(url_for('login'))

        app.logger.warning('Recuperação falha para usuário: %s', username)
        flash('Usuário ou Palavra de Recuperação incorretos.', 'danger')

    return render_template('esqueci_senha.html')


@app.route('/')
@login_required
def dashboard():
    total_associados = Associado.query.count()
    return render_template('dashboard.html', username=session.get('username'), total=total_associados)


def _processar_foto_base64(foto_b64):
    """Decodifica uma foto data-URL, valida e retorna (bytes, extensao). Retorna (None, None) se não houver foto."""
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

    extensao = 'png' if 'image/png' in header else 'jpg'
    return raw_foto, extensao


@app.route('/cadastro', methods=['GET', 'POST'])
@login_required
def cadastro():
    if request.method == 'POST':
        try:
            cpf_raw = request.form['cpf'].strip()

            if not validar_cpf(cpf_raw):
                flash('O CPF digitado é matematicamente inválido.', 'danger')
                return render_template('cadastro.html', username=session.get('username'))

            cpf_formatado = _normalizar_cpf(cpf_raw)

            foto_b64 = request.form.get('foto_base64')
            nome_arquivo = None
            try:
                raw_foto, extensao = _processar_foto_base64(foto_b64)
            except ValueError as exc:
                flash(str(exc), 'danger')
                return render_template('cadastro.html', username=session.get('username'))

            if raw_foto:
                nome_arquivo = _gerar_nome_foto(request.form['matricula'], extensao)
                caminho_salvar = os.path.join(app.config['UPLOAD_FOLDER'], nome_arquivo)
                with open(caminho_salvar, 'wb') as fh:
                    fh.write(raw_foto)

            novo_associado = Associado(
                nome=request.form['nome'].strip(),
                matricula=request.form['matricula'].strip(),
                rg=request.form['rg'].strip(),
                cpf=cpf_formatado,
                telefone=request.form.get('telefone', '').strip(),
                telefone_whatsapp=request.form.get('telefone_whatsapp', '').strip(),
                foto_perfil=nome_arquivo,
                endereco=request.form['endereco'].strip(),
                data_nascimento=datetime.strptime(request.form['data_nascimento'], '%Y-%m-%d').date(),
                email=request.form['email'].strip(),
                data_admissao=datetime.strptime(request.form['data_admissao'], '%Y-%m-%d').date(),
                dependentes=request.form.get('dependentes', '').strip(),
            )
            db.session.add(novo_associado)
            db.session.flush()  # gera o ID antes do commit p/ usar na auditoria
            registrar_auditoria(
                ACAO_ASSOCIADO_CRIAR,
                entidade='associado',
                entidade_id=novo_associado.id,
                descricao=f'{novo_associado.nome} (matrícula {novo_associado.matricula})',
            )
            db.session.commit()
            flash('Associado cadastrado com sucesso!', 'success')
            return redirect(url_for('cadastro'))

        except Exception:
            db.session.rollback()
            app.logger.exception('Erro ao cadastrar associado')
            # Limpa foto recém-salva em caso de rollback (evita arquivo órfão).
            if 'nome_arquivo' in locals() and nome_arquivo:
                _remover_foto_do_disco(nome_arquivo)
            flash('Erro ao cadastrar. Verifique se CPF ou Matrícula já existem.', 'danger')
            return render_template('cadastro.html', username=session.get('username'))

    return render_template('cadastro.html', username=session.get('username'))


def _aplicar_filtros_busca(query, nome_busca, matricula_busca, ano_busca):
    if nome_busca:
        query = query.filter(Associado.nome.ilike(f'%{nome_busca}%'))
    if matricula_busca:
        query = query.filter(Associado.matricula == matricula_busca)
    if ano_busca:
        query = query.filter(extract('year', Associado.data_admissao) == int(ano_busca))
    return query


@app.route('/buscar', methods=['GET', 'POST'])
@login_required
def buscar():
    resultados = None
    filtros = {'nome': '', 'matricula': '', 'ano': ''}

    if request.method == 'POST':
        filtros['nome'] = request.form.get('nome', '').strip()
        filtros['matricula'] = request.form.get('matricula', '').strip()
        filtros['ano'] = request.form.get('ano', '').strip()

        try:
            query = _aplicar_filtros_busca(
                Associado.query.order_by(Associado.nome),
                filtros['nome'],
                filtros['matricula'],
                filtros['ano'],
            )
            resultados = query.all()
        except ValueError:
            flash('Ano de admissão inválido.', 'warning')
            resultados = []

        if not resultados:
            flash('Nenhum registro encontrado com estes filtros.', 'warning')

    return render_template(
        'buscar.html',
        username=session.get('username'),
        resultados=resultados,
        filtros=filtros,
    )


@app.route('/exportar_pdf', methods=['POST'])
@login_required
def exportar_pdf():
    nome_busca = request.form.get('nome_export', '').strip()
    matricula_busca = request.form.get('matricula_export', '').strip()
    ano_busca = request.form.get('ano_export', '').strip()

    try:
        query = _aplicar_filtros_busca(
            Associado.query.order_by(Associado.nome),
            nome_busca,
            matricula_busca,
            ano_busca,
        )
        resultados = query.all()
    except ValueError:
        flash('Ano de admissão inválido.', 'warning')
        return redirect(url_for('buscar'))

    if not resultados:
        flash('Nenhum dado para exportar.', 'warning')
        return redirect(url_for('buscar'))

    data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    html_renderizado = render_template('pdf_relatorio.html', resultados=resultados, now=data_geracao)

    pdf = HTML(string=html_renderizado, base_url=request.url_root).write_pdf()

    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'inline; filename=relatorio_agmeal.pdf'
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
    )

    pdf = HTML(string=html_renderizado, base_url=request.url_root).write_pdf()

    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=ficha_{associado.matricula}.pdf'
    return response


CAMPOS_ASSOCIADO_AUDITAVEIS = [
    'nome', 'matricula', 'rg', 'cpf', 'telefone', 'telefone_whatsapp',
    'endereco', 'data_nascimento', 'email', 'data_admissao', 'dependentes',
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

    if request.method == 'POST':
        foto_anterior = associado.foto_perfil
        nova_foto_nome = None
        try:
            cpf_raw = request.form['cpf'].strip()
            if not validar_cpf(cpf_raw):
                flash('O CPF digitado é matematicamente inválido.', 'danger')
                return redirect(url_for('editar', id=id))

            cpf_formatado = _normalizar_cpf(cpf_raw)

            antes = {c: getattr(associado, c) for c in CAMPOS_ASSOCIADO_AUDITAVEIS}

            associado.nome = request.form['nome'].strip()
            associado.matricula = request.form['matricula'].strip()
            associado.rg = request.form['rg'].strip()
            associado.cpf = cpf_formatado
            associado.telefone = request.form.get('telefone', '').strip()
            associado.telefone_whatsapp = request.form.get('telefone_whatsapp', '').strip()
            associado.endereco = request.form['endereco'].strip()

            try:
                associado.data_nascimento = datetime.strptime(request.form['data_nascimento'], '%Y-%m-%d').date()
                associado.data_admissao = datetime.strptime(request.form['data_admissao'], '%Y-%m-%d').date()
            except ValueError:
                db.session.rollback()
                flash('Data de nascimento ou admissão inválida. Use o formato correto.', 'danger')
                return redirect(url_for('editar', id=id))

            associado.email = request.form['email'].strip()
            associado.dependentes = request.form.get('dependentes', '').strip()

            foto = request.files.get('foto_perfil')
            if foto and foto.filename and allowed_file(foto.filename):
                dados_foto = foto.read()
                if len(dados_foto) > MAX_FOTO_BYTES:
                    flash('A foto é muito grande (máximo 6 MB).', 'danger')
                    return redirect(url_for('editar', id=id))
                if not _bytes_sao_imagem_png_ou_jpeg(dados_foto):
                    flash('Arquivo de foto inválido. Use apenas PNG ou JPEG.', 'danger')
                    return redirect(url_for('editar', id=id))

                extensao = foto.filename.rsplit('.', 1)[1].lower()
                nova_foto_nome = _gerar_nome_foto(associado.matricula, extensao)
                caminho_salvar = os.path.join(app.config['UPLOAD_FOLDER'], nova_foto_nome)
                with open(caminho_salvar, 'wb') as fh:
                    fh.write(dados_foto)
                associado.foto_perfil = nova_foto_nome

            detalhes = _diff_associado(antes, associado, foto_alterada=bool(nova_foto_nome))
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
            flash('Erro ao atualizar. Verifique se o novo CPF ou Matrícula já existem no sistema.', 'danger')

    return render_template('editar.html', username=session.get('username'), associado=associado)


@app.route('/excluir/<int:id>', methods=['POST'])
@login_required
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


@app.route('/perfil', methods=['GET', 'POST'])
@login_required
def perfil():
    usuario = db.session.get(Usuario, session['usuario_id'])

    if request.method == 'POST':
        senha_atual = request.form['senha_atual']
        novo_username = request.form['username'].strip()
        nova_senha = request.form.get('nova_senha', '').strip()

        if not usuario.check_senha(senha_atual):
            flash('Senha atual incorreta. Nenhuma alteração foi salva.', 'danger')
            return redirect(url_for('perfil'))

        username_anterior = usuario.username
        senha_alterada = False
        username_alterado = False

        if novo_username != usuario.username:
            existente = Usuario.query.filter_by(username=novo_username).first()
            if existente:
                flash('Este nome de usuário já está em uso.', 'warning')
                return redirect(url_for('perfil'))
            username_alterado = True

        if nova_senha:
            if len(nova_senha) < 8:
                flash('A nova senha deve ter no mínimo 8 caracteres.', 'warning')
                return redirect(url_for('perfil'))
            if usuario.check_senha(nova_senha):
                flash('A nova senha não pode ser igual à atual.', 'warning')
                return redirect(url_for('perfil'))
            usuario.set_senha(nova_senha)
            senha_alterada = True

        # Aplica mudança de username somente após passar todas as validações.
        if username_alterado:
            usuario.username = novo_username

        mudancas = []
        if username_alterado:
            mudancas.append(f'usuário: "{username_anterior}" → "{novo_username}"')
        if senha_alterada:
            mudancas.append('senha alterada')
        if mudancas:
            registrar_auditoria(
                ACAO_USUARIO_PERFIL,
                entidade='usuario',
                entidade_id=usuario.id,
                descricao=f'Perfil de {usuario.username}',
                detalhes='\n'.join(mudancas),
            )
        db.session.commit()
        # Sincroniza a sessão com o username persistido no banco.
        session['username'] = usuario.username
        flash('Perfil administrativo atualizado com sucesso!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('perfil.html', username=session.get('username'), usuario=usuario)


@app.route('/seguranca', methods=['GET', 'POST'])
@login_required
def seguranca():
    usuario = db.session.get(Usuario, session['usuario_id'])

    if request.method == 'POST':
        senha_atual = request.form['senha_atual']
        nova_palavra = request.form['nova_palavra'].strip()

        if not usuario.check_senha(senha_atual):
            flash('Senha atual incorreta.', 'danger')
            return redirect(url_for('seguranca'))

        usuario.set_palavra_recuperacao(nova_palavra)
        registrar_auditoria(
            ACAO_USUARIO_PALAVRA,
            entidade='usuario',
            entidade_id=usuario.id,
            descricao=f'{usuario.username} alterou a frase de segurança',
        )
        db.session.commit()
        flash('Frase de segurança atualizada com sucesso!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('seguranca.html', username=session.get('username'))


@app.route('/listar')
@login_required
def listar_todos():
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1

    paginacao = Associado.query.order_by(Associado.nome).paginate(
        page=page, per_page=PAGINA_TAMANHO, error_out=False
    )
    return render_template(
        'listar.html',
        username=session.get('username'),
        associados=paginacao.items,
        paginacao=paginacao,
    )


@app.route('/usuarios')
@admin_required
def listar_usuarios():
    usuarios = Usuario.query.order_by(Usuario.username).all()
    return render_template(
        'usuarios.html',
        username=session.get('username'),
        usuarios=usuarios,
        usuario_id_atual=session.get('usuario_id'),
    )


@app.route('/usuarios/novo', methods=['GET', 'POST'])
@admin_required
def criar_usuario():
    if request.method == 'POST':
        novo_username = request.form['username'].strip()
        senha = request.form['senha']
        palavra = request.form['palavra_recuperacao'].strip()
        role = request.form.get('role', ROLE_USUARIO).strip()

        if role not in ROLES_VALIDAS:
            flash('Perfil inválido.', 'danger')
            return render_template('usuario_novo.html', username=session.get('username'))

        if not novo_username or not senha or not palavra:
            flash('Usuário, senha e palavra de recuperação são obrigatórios.', 'danger')
            return render_template('usuario_novo.html', username=session.get('username'))

        if len(senha) < 8:
            flash('A senha deve ter no mínimo 8 caracteres.', 'danger')
            return render_template('usuario_novo.html', username=session.get('username'))

        if Usuario.query.filter_by(username=novo_username).first():
            flash('Já existe um usuário com este nome.', 'warning')
            return render_template('usuario_novo.html', username=session.get('username'))

        novo = Usuario(username=novo_username, role=role)
        novo.set_senha(senha)
        novo.set_palavra_recuperacao(palavra)

        try:
            db.session.add(novo)
            db.session.flush()
            registrar_auditoria(
                ACAO_USUARIO_CRIAR,
                entidade='usuario',
                entidade_id=novo.id,
                descricao=f'{novo_username} (perfil: {role})',
            )
            db.session.commit()
            app.logger.info(
                'Usuário criado por %s: %s (role=%s)',
                session.get('username'), novo_username, role,
            )
            flash(f'Usuário "{novo_username}" criado com sucesso.', 'success')
            return redirect(url_for('listar_usuarios'))
        except Exception:
            db.session.rollback()
            app.logger.exception('Erro ao criar usuário %s', novo_username)
            flash('Erro ao criar o usuário. Tente novamente.', 'danger')
            return render_template('usuario_novo.html', username=session.get('username'))

    return render_template('usuario_novo.html', username=session.get('username'))


@app.route('/usuarios/<int:id>/excluir', methods=['POST'])
@admin_required
def excluir_usuario(id):
    usuario = db.session.get(Usuario, id)
    if not usuario:
        flash('Usuário não encontrado.', 'danger')
        return redirect(url_for('listar_usuarios'))

    if usuario.id == session.get('usuario_id'):
        flash('Você não pode excluir a si mesmo.', 'warning')
        return redirect(url_for('listar_usuarios'))

    # Impede remover o último administrador do sistema.
    if usuario.role == ROLE_ADMIN:
        total_admins = Usuario.query.filter_by(role=ROLE_ADMIN).count()
        if total_admins <= 1:
            flash('Não é possível excluir o último administrador do sistema.', 'warning')
            return redirect(url_for('listar_usuarios'))

    try:
        nome_removido = usuario.username
        role_removido = usuario.role
        usuario_id_removido = usuario.id
        db.session.delete(usuario)
        registrar_auditoria(
            ACAO_USUARIO_EXCLUIR,
            entidade='usuario',
            entidade_id=usuario_id_removido,
            descricao=f'{nome_removido} (perfil: {role_removido})',
        )
        db.session.commit()
        app.logger.info('Usuário removido por %s: %s', session.get('username'), nome_removido)
        flash(f'Usuário "{nome_removido}" removido com sucesso.', 'success')
    except Exception:
        db.session.rollback()
        app.logger.exception('Erro ao excluir usuário id=%s', id)
        flash('Erro ao remover o usuário.', 'danger')

    return redirect(url_for('listar_usuarios'))


@app.route('/auditoria')
@admin_required
def auditoria():
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1

    filtros = {
        'usuario': request.args.get('usuario', '').strip(),
        'acao': request.args.get('acao', '').strip(),
        'entidade': request.args.get('entidade', '').strip(),
        'data_de': request.args.get('data_de', '').strip(),
        'data_ate': request.args.get('data_ate', '').strip(),
    }

    query = Auditoria.query

    if filtros['usuario']:
        query = query.filter(Auditoria.usuario_username.ilike(f"%{filtros['usuario']}%"))
    if filtros['acao']:
        query = query.filter(Auditoria.acao == filtros['acao'])
    if filtros['entidade']:
        query = query.filter(Auditoria.entidade == filtros['entidade'])
    if filtros['data_de']:
        try:
            d = datetime.strptime(filtros['data_de'], '%Y-%m-%d')
            query = query.filter(Auditoria.data_hora >= d)
        except ValueError:
            flash('Data inicial inválida.', 'warning')
    if filtros['data_ate']:
        try:
            d = datetime.strptime(filtros['data_ate'], '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(Auditoria.data_hora < d)
        except ValueError:
            flash('Data final inválida.', 'warning')

    paginacao = query.order_by(Auditoria.data_hora.desc()).paginate(
        page=page, per_page=50, error_out=False,
    )

    usuarios_distintos = [
        u[0] for u in db.session.query(Auditoria.usuario_username)
        .distinct().order_by(Auditoria.usuario_username).all()
    ]

    return render_template(
        'auditoria.html',
        username=session.get('username'),
        registros=paginacao.items,
        paginacao=paginacao,
        filtros=filtros,
        acoes=ACOES_ROTULOS,
        usuarios_distintos=usuarios_distintos,
    )


@app.route('/exportar_lista_simples', methods=['POST'])
@login_required
def exportar_lista_simples():
    associados = Associado.query.order_by(Associado.nome).all()
    data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

    html_renderizado = render_template('pdf_lista_simples.html', associados=associados, now=data_geracao)

    pdf = HTML(string=html_renderizado, base_url=request.url_root).write_pdf()

    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'inline; filename=lista_associados.pdf'
    return response


if __name__ == '__main__':
    _debug = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    _host = os.environ.get('FLASK_HOST', '127.0.0.1')
    _port = int(os.environ.get('FLASK_PORT', '5000'))
    app.run(debug=_debug, host=_host, port=_port)
