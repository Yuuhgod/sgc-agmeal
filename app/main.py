import base64
import binascii
import logging
import os
import re
import secrets
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    current_app,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import extract, inspect, text
from sqlalchemy.orm import selectinload
from weasyprint import HTML
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.datastructures import MultiDict
from werkzeug.utils import secure_filename

from database import (
    ACAO_ASSOCIADO_CRIAR,
    ACAO_ASSOCIADO_EDITAR,
    ACAO_ASSOCIADO_EXCLUIR,
    ACAO_ASSOCIADO_EXPORTAR,
    ACAO_ASSOCIADO_IMPORTAR,
    ACAO_AUTH_LOGIN,
    ACAO_AUTH_LOGIN_FALHOU,
    ACAO_AUTH_LOGOUT,
    ACAO_AUTH_RECUPERACAO,
    ACAO_AUTH_RECUPERACAO_FALHOU,
    ACAO_SISTEMA_BACKUP,
    ACAO_SISTEMA_RESTORE,
    ACAO_USUARIO_CRIAR,
    ACAO_USUARIO_EDITAR,
    ACAO_USUARIO_EXCLUIR,
    ACAO_USUARIO_SENHA_REDEFINIDA,
    ACAO_USUARIO_SENHA_TROCADA,
    ACAO_USUARIO_PALAVRA,
    ACAO_USUARIO_PERFIL,
    ACOES_ROTULOS,
    Associado,
    Auditoria,
    Dependente,
    PARENTESCO_NAO_INFORMADO,
    PARENTESCOS,
    ROLE_ADMIN,
    ROLE_USUARIO,
    ROLES_VALIDAS,
    SITUACAO_ATIVO,
    SITUACOES_ROTULOS,
    Usuario,
    db,
)

app = Flask(__name__)

# Corrige scheme/host/ip quando atrás do Nginx (X-Forwarded-*).
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

basedir = os.path.abspath(os.path.dirname(__file__))
# SGC_DATA_DIR permite apontar outra pasta de dados (ex.: pasta temporária nos testes).
data_dir = os.environ.get('SGC_DATA_DIR') or os.path.join(basedir, '..', 'data')
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
    MAX_CONTENT_LENGTH=max(
        10 * 1024 * 1024,
        int(os.environ.get('MAX_CONTENT_LENGTH_MB', '128')) * 1024 * 1024,
    ),
)

db_path = os.path.join(data_dir, 'sgc.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

backups_dir = os.path.join(data_dir, 'backups')
os.makedirs(backups_dir, exist_ok=True)
restore_pending_dir = os.path.join(data_dir, 'restore_pending')
os.makedirs(restore_pending_dir, exist_ok=True)

# Confirmação explícita na UI de restauração (evita substituição acidental).
RESTORE_CONFIRM_PHRASE = 'RESTAURAR'

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

# Bloqueio de força bruta partilhado entre workers: conta as falhas de login recentes na
# trilha de auditoria (o mesmo SQLite lido por todos os processos do Gunicorn). Ao contrário
# do rate limit em memory:// do Flask-Limiter — que conta por worker e fica ineficaz na
# instalação padrão (vários workers, sem Nginx à frente) —, este limite vale para toda a
# instalação, independentemente do número de workers.
LOGIN_MAX_FALHAS_IP = max(1, int(os.environ.get('LOGIN_MAX_FALHAS_IP', '10')))
LOGIN_JANELA_MINUTOS = max(1, int(os.environ.get('LOGIN_JANELA_MINUTOS', '5')))

# Sessão parada por mais tempo que isto é encerrada (além do limite absoluto de 8 horas).
SESSAO_INATIVIDADE_MINUTOS = max(1, int(os.environ.get('SESSAO_INATIVIDADE_MINUTOS', '30')))

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

import backup_agendador
from backup_service import criar_backup_zip, listar_backups_locais
from importacao_service import PlanilhaInvalida, gerar_modelo_xlsx, ler_planilha, separar_dependentes
from planilha_service import gerar_csv, gerar_xlsx
from restore_service import aplicar_restauracao, extrair_zip_seguro

migrate = Migrate(app, db)


def _backup_keep_local():
    try:
        return max(1, int(os.environ.get('BACKUP_KEEP_LOCAL', '14')))
    except ValueError:
        return 14


def _backup_keep_sync():
    try:
        return max(1, int(os.environ.get('BACKUP_KEEP_SYNC', '60')))
    except ValueError:
        return 60


def _backup_sync_dir():
    d = os.environ.get('BACKUP_SYNC_DIR', '').strip()
    return d or None


def _env_float(nome, padrao, minimo):
    try:
        return max(minimo, float(os.environ.get(nome, padrao)))
    except ValueError:
        return float(padrao)


# Backup automático: ligado por padrão; gera quando o último tiver mais que o intervalo.
BACKUP_AUTO = os.environ.get('BACKUP_AUTO', '1').lower() not in ('0', 'false', 'no', 'nao', 'não')
BACKUP_AUTO_INTERVALO_HORAS = _env_float('BACKUP_AUTO_INTERVALO_HORAS', '24', 1)
# O painel alerta os admins se o backup mais recente for mais velho que isto.
BACKUP_ALERTA_DIAS = _env_float('BACKUP_ALERTA_DIAS', '3', 1)


def _executar_backup_automatico():
    info = criar_backup_zip(
        data_dir=data_dir,
        upload_folder=UPLOAD_FOLDER,
        backups_dir=backups_dir,
        sync_dir=_backup_sync_dir(),
        keep_local=_backup_keep_local(),
        keep_sync=_backup_keep_sync(),
        log=app.logger,
    )
    # Fora de uma requisição: grava a auditoria direto (sem sessão/IP).
    db.session.add(Auditoria(
        usuario_id=None,
        usuario_username='backup-automático',
        acao=ACAO_SISTEMA_BACKUP,
        entidade='backup',
        descricao='Backup ZIP (automático)',
        detalhes=f"arquivo={info['zip_filename']}\ntamanho_bytes={info['size_bytes']}\n"
                 f"copia_nuvem={'sim' if info['sync_path'] else 'não'}",
    ))
    db.session.commit()
    return info


def _situacao_backup():
    """Resumo para a interface: idade do último backup, último erro e se merece alerta."""
    idade = backup_agendador.idade_ultimo_backup_horas(backups_dir, listar_backups_locais)
    status = backup_agendador.ler_status(backups_dir)
    falhou = bool(status.get('ultima_falha')) and (status.get('ultima_falha') or '') > (status.get('ultimo_sucesso') or '')
    return {
        'idade_horas': idade,
        'idade_dias': None if idade is None else idade / 24,
        'status': status,
        'falhou': falhou,
        'alerta': falhou or idade is None or idade > BACKUP_ALERTA_DIAS * 24,
        'automatico': BACKUP_AUTO,
        'intervalo_horas': BACKUP_AUTO_INTERVALO_HORAS,
        'alerta_dias': BACKUP_ALERTA_DIAS,
    }


def _exportar_pdf_max_sem_filtro() -> int:
    """Máximo de linhas permitidas em «Exportar resultados em PDF» sem nenhum filtro (nome, matrícula, ano)."""
    try:
        return max(1, int(os.environ.get('EXPORTAR_PDF_MAX_SEM_FILTRO', '400')))
    except ValueError:
        return 400


def _exportar_lista_simples_max() -> int:
    """Máximo de linhas na «lista simples» em PDF (só texto, bem mais leve que as fichas)."""
    try:
        return max(1, int(os.environ.get('EXPORTAR_LISTA_SIMPLES_MAX', '5000')))
    except ValueError:
        return 5000



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


# Colunas acrescentadas depois da primeira versão (bancos antigos não as têm).
# Também aplicadas pelas migrações Alembic equivalentes, para quem usa `flask db upgrade`.
COLUNAS_NOVAS = {
    'associados': (
        ('situacao', "VARCHAR(20) NOT NULL DEFAULT 'ativo'"),
        ('situacao_data', 'DATE'),
        ('situacao_motivo', 'VARCHAR(200)'),
    ),
    'usuarios': (
        ('ativo', 'BOOLEAN NOT NULL DEFAULT 1'),
        ('trocar_senha', 'BOOLEAN NOT NULL DEFAULT 0'),
    ),
}


def _garantir_colunas_novas():
    """Mini-migração idempotente das colunas acrescentadas depois da primeira versão.

    Registros existentes recebem o padrão (associado 'ativo', usuário ativo e sem troca
    de senha pendente). Tolerante a corrida entre workers."""
    inspector = inspect(db.engine)
    tabelas = set(inspector.get_table_names())
    for tabela, colunas in COLUNAS_NOVAS.items():
        if tabela not in tabelas:
            continue
        existentes = {c['name'] for c in inspector.get_columns(tabela)}
        for nome, ddl in colunas:
            if nome in existentes:
                continue
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE {tabela} ADD COLUMN {nome} {ddl}'))
                app.logger.info("Coluna '%s' adicionada em '%s'.", nome, tabela)
            except Exception as exc:
                if 'duplicate column' in str(exc).lower():
                    continue
                raise
    if 'associados' not in tabelas:
        return
    with db.engine.begin() as conn:
        conn.execute(text(
            'CREATE INDEX IF NOT EXISTS ix_associados_situacao ON associados (situacao)'
        ))


def _separar_nomes_legados(texto):
    """'Maria, João; Ana' -> ['Maria', 'João', 'Ana'] (limite de 100 caracteres por nome)."""
    return [n.strip()[:100] for n in re.split(r'[,;\n]+', texto or '') if n.strip()]


def _converter_dependentes_legados():
    """Converte, uma única vez, o texto livre de dependentes em registros da tabela nova.

    A marca em `sgc_meta` é gravada na mesma transação da conversão; o INSERT OR IGNORE
    serializa os workers do Gunicorn (só quem inserir a marca converte). Restaurar um
    backup antigo (sem a marca) converte os dados dele."""
    with db.engine.begin() as conn:
        conn.execute(text(
            'CREATE TABLE IF NOT EXISTS sgc_meta (chave VARCHAR(50) PRIMARY KEY, valor VARCHAR(200))'
        ))
        marcou = conn.execute(
            text("INSERT OR IGNORE INTO sgc_meta (chave, valor) VALUES ('dependentes_convertidos', :v)"),
            {'v': datetime.now().isoformat(timespec='seconds')},
        ).rowcount
        if not marcou:
            return
        linhas = conn.execute(text(
            "SELECT id, dependentes FROM associados WHERE dependentes IS NOT NULL AND TRIM(dependentes) != ''"
        )).all()
        total = 0
        for associado_id, texto_legado in linhas:
            for nome in _separar_nomes_legados(texto_legado):
                conn.execute(
                    text('INSERT INTO dependentes_associado (associado_id, nome, parentesco) '
                         'VALUES (:a, :n, :p)'),
                    {'a': associado_id, 'n': nome, 'p': PARENTESCO_NAO_INFORMADO},
                )
                total += 1
    if total:
        app.logger.info('Dependentes convertidos do texto antigo: %s registro(s).', total)


def _criar_tabelas():
    try:
        db.create_all()
    except Exception as exc:
        # Ignora erros de "table already exists" em corridas entre workers do Gunicorn.
        if 'already exists' in str(exc).lower():
            app.logger.info('Tabelas já existem (criadas por outro worker) — ignorando.')
        else:
            raise


def _garantir_schema():
    """Deixa o banco no formato atual. Idempotente: roda ao iniciar e após restaurar backup."""
    _criar_tabelas()
    _garantir_coluna_role()
    _garantir_colunas_novas()
    _converter_dependentes_legados()


with app.app_context():
    _garantir_schema()


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
def iniciar_agendador_backup():
    """Sobe o agendador na primeira requisição do processo (não em CLI/migrações/testes)."""
    if BACKUP_AUTO and not app.testing:
        backup_agendador.iniciar(
            app,
            data_dir=data_dir,
            backups_dir=backups_dir,
            executar_backup=_executar_backup_automatico,
            listar=listar_backups_locais,
            intervalo_horas=BACKUP_AUTO_INTERVALO_HORAS,
        )


@app.before_request
def verificar_primeiro_acesso():
    if request.endpoint in ['setup', 'static']:
        return
    if Usuario.query.count() == 0:
        return redirect(url_for('setup'))


@app.before_request
def sincronizar_sessao_com_banco():
    """Revalida a sessão a cada requisição contra o banco:

    - usuário excluído ou desativado: encerra a sessão;
    - sessão parada há mais de SESSAO_INATIVIDADE_MINUTOS: encerra a sessão;
    - papel e nome vêm do banco (um admin rebaixado perde o acesso na hora);
    - senha provisória pendente: só deixa acessar a troca de senha e o logout."""
    if request.endpoint == 'static' and not request.path.startswith('/static/uploads/'):
        return
    usuario_id = session.get('usuario_id')
    if usuario_id is None:
        return
    usuario = db.session.get(Usuario, usuario_id)
    if usuario is None or not usuario.ativo:
        session.clear()
        motivo = 'o usuário não existe mais' if usuario is None else 'a conta foi desativada'
        flash(f'Sua sessão foi encerrada porque {motivo}.', 'warning')
        return redirect(url_for('login'))

    agora = int(time.time())
    ultimo = session.get('ultimo_acesso', agora)
    if agora - ultimo > SESSAO_INATIVIDADE_MINUTOS * 60:
        session.clear()
        flash('Sua sessão expirou por inatividade. Entre novamente.', 'warning')
        return redirect(url_for('login'))
    # Atualiza no máximo uma vez por minuto (evita reenviar o cookie em toda resposta).
    if agora - ultimo >= 60 or 'ultimo_acesso' not in session:
        session['ultimo_acesso'] = agora

    # Só grava se mudou, para não reenviar o cookie de sessão em toda resposta.
    if session.get('role') != usuario.role:
        session['role'] = usuario.role
    if session.get('username') != usuario.username:
        session['username'] = usuario.username

    if usuario.trocar_senha and request.endpoint not in ('trocar_senha', 'logout', 'static'):
        return redirect(url_for('trocar_senha'))


@app.before_request
def proteger_uploads():
    """Impede acesso anônimo às fotos dos associados (dados pessoais) servidas em
    /static/uploads/. Nos PDFs as fotos são lidas do disco (file://), sem passar por aqui."""
    if request.path.startswith('/static/uploads/') and 'usuario_id' not in session:
        flash('Faça login para acessar este conteúdo.', 'warning')
        return redirect(url_for('login'))


@app.after_request
def add_header(response):
    """Cabeçalhos de segurança + impede cache de páginas autenticadas."""
    if request.path.startswith('/static/vendor/') and response.status_code in (200, 304):
        # Bibliotecas locais têm a versão no caminho (ex.: bootstrap-5.3.2): nunca mudam.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        response.headers.pop("Pragma", None)
        response.headers.pop("Expires", None)
    else:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'",
    )
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
    )
    # HSTS só vale sob HTTPS (navegadores ignoram em HTTP); ProxyFix lê X-Forwarded-Proto.
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
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
        'situacoes': SITUACOES_ROTULOS,
        'parentescos': PARENTESCOS,
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


def _ip_bloqueado(ip, acao_falha):
    """True se este IP excedeu o limite de falhas (`acao_falha`) na janela recente.

    Usa a trilha de auditoria como armazenamento partilhado entre workers, garantindo
    proteção contra força bruta mesmo na instalação padrão (sem Nginx)."""
    if not ip:
        return False
    desde = datetime.now() - timedelta(minutes=LOGIN_JANELA_MINUTOS)
    falhas = Auditoria.query.filter(
        Auditoria.acao == acao_falha,
        Auditoria.ip_origem == ip,
        Auditoria.data_hora >= desde,
    ).count()
    return falhas >= LOGIN_MAX_FALHAS_IP


def _login_bloqueado_por_ip(ip):
    return _ip_bloqueado(ip, ACAO_AUTH_LOGIN_FALHOU)


def _recuperacao_bloqueada_por_ip(ip):
    return _ip_bloqueado(ip, ACAO_AUTH_RECUPERACAO_FALHOU)


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if Usuario.query.count() > 0:
        return redirect(url_for('login'))

    if request.method == 'POST':
        username = request.form['username'].strip()
        senha = request.form['senha']
        palavra = request.form['palavra_recuperacao'].strip()

        if not username or not palavra:
            flash('Usuário e frase de segurança são obrigatórios.', 'danger')
            return render_template('setup.html')

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

        if _login_bloqueado_por_ip(request.remote_addr):
            app.logger.warning(
                'Login bloqueado por excesso de tentativas: ip=%s usuario=%s',
                request.remote_addr, username,
            )
            flash(
                'Muitas tentativas de login malsucedidas deste computador. '
                'Aguarde alguns minutos e tente novamente.',
                'danger',
            )
            return render_template('login.html')

        usuario = Usuario.query.filter_by(username=username).first()

        if usuario and usuario.check_senha(senha) and not usuario.ativo:
            # Só revelado a quem acertou a senha: não serve para descobrir contas.
            app.logger.warning('Login recusado (conta desativada): %s', username)
            registrar_auditoria(
                ACAO_AUTH_LOGIN_FALHOU,
                descricao=f'Conta desativada: "{username}"',
                usuario_id=usuario.id,
                usuario_username=usuario.username,
                commit=True,
            )
            flash('Esta conta está desativada. Procure um administrador.', 'danger')
            return render_template('login.html')

        if usuario and usuario.check_senha(senha):
            session.clear()
            session['usuario_id'] = usuario.id
            session['username'] = usuario.username
            session['role'] = usuario.role
            session['ultimo_acesso'] = int(time.time())
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

        if _recuperacao_bloqueada_por_ip(request.remote_addr):
            app.logger.warning(
                'Recuperação bloqueada por excesso de tentativas: ip=%s usuario=%s',
                request.remote_addr, username,
            )
            flash(
                'Muitas tentativas de recuperação malsucedidas deste computador. '
                'Aguarde alguns minutos e tente novamente.',
                'danger',
            )
            return render_template('esqueci_senha.html')

        usuario = Usuario.query.filter_by(username=username).first()

        # Frases novas são gravadas sem espaços nas pontas; as antigas podem tê-los.
        palavra_ok = usuario is not None and usuario.ativo and (
            usuario.verificar_palavra_recuperacao(palavra)
            or (palavra.strip() != palavra and usuario.verificar_palavra_recuperacao(palavra.strip()))
        )

        if palavra_ok:
            if usuario.check_senha(nova_senha):
                flash('A nova senha não pode ser igual à senha atual.', 'warning')
                return redirect(url_for('esqueci_senha'))

            usuario.migrar_palavra_recuperacao_se_legado(palavra)
            usuario.set_senha(nova_senha)
            usuario.trocar_senha = False
            registrar_auditoria(
                ACAO_AUTH_RECUPERACAO,
                entidade='usuario',
                entidade_id=usuario.id,
                descricao=f'{usuario.username} redefiniu a senha pela frase de segurança',
                usuario_id=usuario.id,
                usuario_username=usuario.username,
            )
            db.session.commit()
            app.logger.info('Senha redefinida via palavra de recuperação: %s', username)
            flash('Senha alterada com sucesso! Você já pode fazer login.', 'success')
            return redirect(url_for('login'))

        app.logger.warning('Recuperação falha para usuário: %s', username)
        registrar_auditoria(
            ACAO_AUTH_RECUPERACAO_FALHOU,
            descricao=f'Tentativa de recuperação com usuário "{username}"',
            usuario_id=None,
            usuario_username=username or '(vazio)',
            commit=True,
        )
        flash('Usuário ou Palavra de Recuperação incorretos.', 'danger')

    return render_template('esqueci_senha.html')


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
    for nome, parentesco, nascimento, cpf in zip(*colunas):
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
        query = query.filter(Associado.nome.ilike(f"%{filtros['nome']}%"))
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

    pdf = HTML(string=html_renderizado, base_url=request.url_root).write_pdf()

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

    pdf = HTML(string=html_renderizado, base_url=request.url_root).write_pdf()

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

            foto = request.files.get('foto_perfil')
            if foto and foto.filename and allowed_file(foto.filename):
                dados_foto = foto.read()
                if len(dados_foto) > MAX_FOTO_BYTES:
                    flash('A foto é muito grande (máximo 6 MB).', 'danger')
                    db.session.rollback()
                    return redirect(url_for('editar', id=id))
                if not _bytes_sao_imagem_png_ou_jpeg(dados_foto):
                    flash('Arquivo de foto inválido. Use apenas PNG ou JPEG.', 'danger')
                    db.session.rollback()
                    return redirect(url_for('editar', id=id))

                extensao = foto.filename.rsplit('.', 1)[1].lower()
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
            usuario.trocar_senha = False
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

        if not nova_palavra:
            flash('A frase de segurança não pode ficar em branco.', 'danger')
            return redirect(url_for('seguranca'))

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
        # Padrão: quem cria a conta conhece a senha, então o usuário troca no primeiro acesso.
        novo.trocar_senha = request.form.get('trocar_senha') == '1'
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


def _usuario_admin_ativo_unico(usuario):
    """True se `usuario` é o único administrador ativo (não pode perder o acesso de admin)."""
    if usuario.role != ROLE_ADMIN or not usuario.ativo:
        return False
    return Usuario.query.filter_by(role=ROLE_ADMIN, ativo=True).count() <= 1


@app.route('/usuarios/<int:id>/editar', methods=['GET', 'POST'])
@admin_required
def editar_usuario(id):
    usuario = db.session.get(Usuario, id)
    if not usuario:
        flash('Usuário não encontrado.', 'danger')
        return redirect(url_for('listar_usuarios'))
    eh_voce = usuario.id == session.get('usuario_id')

    if request.method == 'POST':
        if eh_voce:
            flash('Você não pode alterar o próprio perfil ou acesso. Peça a outro administrador.', 'warning')
            return redirect(url_for('editar_usuario', id=id))

        role = request.form.get('role', '').strip()
        ativo = request.form.get('ativo') == '1'
        if role not in ROLES_VALIDAS:
            flash('Perfil inválido.', 'danger')
            return redirect(url_for('editar_usuario', id=id))
        if _usuario_admin_ativo_unico(usuario) and (role != ROLE_ADMIN or not ativo):
            flash('Este é o único administrador ativo: não pode ser rebaixado nem desativado.', 'warning')
            return redirect(url_for('editar_usuario', id=id))

        mudancas = []
        if usuario.role != role:
            mudancas.append(f'perfil: "{usuario.role}" → "{role}"')
            usuario.role = role
        if usuario.ativo != ativo:
            mudancas.append('acesso: ' + ('reativado' if ativo else 'desativado'))
            usuario.ativo = ativo
        if not mudancas:
            flash('Nenhuma alteração para salvar.', 'info')
            return redirect(url_for('listar_usuarios'))

        registrar_auditoria(
            ACAO_USUARIO_EDITAR,
            entidade='usuario',
            entidade_id=usuario.id,
            descricao=f'{usuario.username}',
            detalhes='\n'.join(mudancas),
        )
        db.session.commit()
        app.logger.info('Usuário %s editado por %s: %s', usuario.username, session.get('username'), mudancas)
        flash(f'Usuário "{usuario.username}" atualizado.', 'success')
        return redirect(url_for('listar_usuarios'))

    return render_template(
        'usuario_editar.html',
        username=session.get('username'),
        usuario=usuario,
        eh_voce=eh_voce,
        unico_admin=_usuario_admin_ativo_unico(usuario),
    )


@app.route('/usuarios/<int:id>/redefinir_senha', methods=['POST'])
@admin_required
def redefinir_senha_usuario(id):
    usuario = db.session.get(Usuario, id)
    if not usuario:
        flash('Usuário não encontrado.', 'danger')
        return redirect(url_for('listar_usuarios'))
    if usuario.id == session.get('usuario_id'):
        flash('Para trocar a sua própria senha, use "Meu perfil".', 'warning')
        return redirect(url_for('editar_usuario', id=id))

    senha = request.form.get('senha_provisoria', '')
    if len(senha) < 8:
        flash('A senha provisória deve ter no mínimo 8 caracteres.', 'danger')
        return redirect(url_for('editar_usuario', id=id))
    if senha != request.form.get('senha_provisoria_confirmacao', ''):
        flash('A confirmação não confere com a senha provisória.', 'danger')
        return redirect(url_for('editar_usuario', id=id))

    usuario.set_senha(senha)
    usuario.trocar_senha = True
    registrar_auditoria(
        ACAO_USUARIO_SENHA_REDEFINIDA,
        entidade='usuario',
        entidade_id=usuario.id,
        descricao=f'Senha provisória definida para {usuario.username}',
    )
    db.session.commit()
    app.logger.info('Senha de %s redefinida por %s', usuario.username, session.get('username'))
    flash(
        f'Senha provisória definida para "{usuario.username}". Informe-a pessoalmente; '
        'no próximo acesso será obrigatório criar uma senha nova.',
        'success',
    )
    return redirect(url_for('listar_usuarios'))


@app.route('/trocar_senha', methods=['GET', 'POST'])
@login_required
def trocar_senha():
    """Troca obrigatória da senha provisória (definida por um admin ou na criação da conta)."""
    usuario = db.session.get(Usuario, session['usuario_id'])
    if not usuario.trocar_senha:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        nova = request.form.get('nova_senha', '')
        if len(nova) < 8:
            flash('A nova senha deve ter no mínimo 8 caracteres.', 'danger')
        elif nova != request.form.get('confirmacao', ''):
            flash('A confirmação não confere com a nova senha.', 'danger')
        elif usuario.check_senha(nova):
            flash('A nova senha não pode ser igual à senha provisória.', 'warning')
        else:
            usuario.set_senha(nova)
            usuario.trocar_senha = False
            registrar_auditoria(
                ACAO_USUARIO_SENHA_TROCADA,
                entidade='usuario',
                entidade_id=usuario.id,
                descricao=f'{usuario.username} criou a própria senha',
            )
            db.session.commit()
            flash('Senha criada com sucesso. Bem-vindo!', 'success')
            return redirect(url_for('dashboard'))

    return render_template('trocar_senha.html', username=session.get('username'))


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
        if _usuario_admin_ativo_unico(usuario) or Usuario.query.filter_by(role=ROLE_ADMIN).count() <= 1:
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

    pdf = HTML(string=html_renderizado, base_url=request.url_root).write_pdf()

    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'inline; filename=lista_associados.pdf'
    return response


@app.route('/admin/backup', methods=['GET'])
@admin_required
def admin_backup():
    recentes = listar_backups_locais(backups_dir)
    sync_dir = _backup_sync_dir()
    return render_template(
        'admin_backup.html',
        username=session.get('username'),
        sync_dir=sync_dir,
        recentes=recentes,
        situacao=_situacao_backup(),
        keep_local=_backup_keep_local(),
        keep_sync=_backup_keep_sync(),
    )


@app.route('/admin/backup/gerar', methods=['POST'])
@admin_required
@limiter.limit('12 per hour')
def admin_backup_gerar():
    copiar = request.form.get('copiar_drive') == '1'
    sync_dir = _backup_sync_dir() if copiar else None
    if copiar and not sync_dir:
        flash(
            'Para copiar automaticamente para a nuvem, defina BACKUP_SYNC_DIR no servidor '
            '(caminho da pasta sincronizada pelo Google Drive ou OneDrive).',
            'warning',
        )
        return redirect(url_for('admin_backup'))

    try:
        info = criar_backup_zip(
            data_dir=data_dir,
            upload_folder=UPLOAD_FOLDER,
            backups_dir=backups_dir,
            sync_dir=sync_dir,
            keep_local=_backup_keep_local(),
            keep_sync=_backup_keep_sync(),
            log=current_app.logger,
        )
    except Exception as exc:
        current_app.logger.exception('Falha ao gerar backup')
        agora_iso = datetime.now().isoformat(timespec='seconds')
        backup_agendador.gravar_status(backups_dir, ultima_tentativa=agora_iso, ultima_falha=agora_iso, erro=str(exc)[:300])
        flash('Não foi possível gerar o backup. Verifique permissões de pasta e o log do servidor.', 'danger')
        return redirect(url_for('admin_backup'))

    agora_iso = datetime.now().isoformat(timespec='seconds')
    backup_agendador.gravar_status(
        backups_dir, ultima_tentativa=agora_iso, ultimo_sucesso=agora_iso, erro=None,
        arquivo=info['zip_filename'], copia_nuvem=bool(info['sync_path']),
    )
    detalhes = (
        f"arquivo={info['zip_filename']}\n"
        f"tamanho_bytes={info['size_bytes']}\n"
        f"copia_nuvem={'sim' if info['sync_path'] else 'não'}"
    )
    registrar_auditoria(
        ACAO_SISTEMA_BACKUP,
        entidade='backup',
        descricao='Backup ZIP gerado',
        detalhes=detalhes,
        commit=True,
    )

    return send_file(
        info['zip_path'],
        mimetype='application/zip',
        as_attachment=True,
        download_name=info['zip_filename'],
    )


@app.route('/admin/restore', methods=['GET', 'POST'])
@admin_required
@limiter.limit('6 per hour', methods=['POST'])
def admin_restore():
    if request.method == 'GET':
        return render_template(
            'admin_restore.html',
            username=session.get('username'),
            confirm_phrase=RESTORE_CONFIRM_PHRASE,
        )

    if request.form.get('confirmar_texto', '').strip() != RESTORE_CONFIRM_PHRASE:
        flash(
            f'Digite exatamente a palavra {RESTORE_CONFIRM_PHRASE!r} no campo de confirmação.',
            'danger',
        )
        return redirect(url_for('admin_restore'))

    if request.form.get('confirmar_consciencia') != '1':
        flash('Marque a caixa confirmando que entende que os dados atuais serão substituídos.', 'warning')
        return redirect(url_for('admin_restore'))

    upload = request.files.get('arquivo')
    if not upload or not upload.filename:
        flash('Selecione o arquivo ZIP de backup.', 'warning')
        return redirect(url_for('admin_restore'))

    nome_original = secure_filename(upload.filename) or 'backup.zip'
    if not nome_original.lower().endswith('.zip'):
        flash('O arquivo deve ser um ZIP gerado pelo backup deste sistema.', 'danger')
        return redirect(url_for('admin_restore'))

    zip_path = os.path.join(restore_pending_dir, f'upload_{uuid.uuid4().hex}.zip')
    try:
        upload.save(zip_path)
    except OSError:
        current_app.logger.exception('Falha ao gravar ZIP de restauração')
        try:
            if os.path.isfile(zip_path):
                os.remove(zip_path)
        except OSError:
            pass
        flash('Não foi possível guardar o arquivo enviado. Verifique espaço em disco e permissões.', 'danger')
        return redirect(url_for('admin_restore'))

    auditoria_uid = session.get('usuario_id')
    auditoria_user = session.get('username') or '(anônimo)'

    try:
        with tempfile.TemporaryDirectory(dir=restore_pending_dir) as extract_root:
            extrair_zip_seguro(zip_path, extract_root, current_app.logger)

            try:
                criar_backup_zip(
                    data_dir=data_dir,
                    upload_folder=UPLOAD_FOLDER,
                    backups_dir=backups_dir,
                    sync_dir=None,
                    keep_local=_backup_keep_local(),
                    keep_sync=_backup_keep_sync(),
                    log=current_app.logger,
                )
            except Exception:
                current_app.logger.exception('Falha no backup de segurança antes da restauração')
                flash(
                    'Não foi possível criar um backup de segurança dos dados atuais. '
                    'A restauração foi cancelada; nada foi alterado.',
                    'danger',
                )
                return redirect(url_for('admin_restore'))

            db.session.remove()
            if db.engine is not None:
                db.engine.dispose()

            try:
                aplicar_restauracao(
                    extract_root=extract_root,
                    data_dir=data_dir,
                    upload_folder=UPLOAD_FOLDER,
                    log=current_app.logger,
                )
            except Exception:
                current_app.logger.exception('Falha ao aplicar restauração')
                flash(
                    'Ocorreu um erro ao aplicar o backup. Os dados podem estar inconsistentes; '
                    'use o ZIP de segurança mais recente em data/backups/ ou restaure manualmente '
                    '(veja LEIA-ME.txt dentro do ZIP).',
                    'danger',
                )
                return redirect(url_for('admin_restore'))

            db.session.remove()
            if db.engine is not None:
                db.engine.dispose()

            # Um backup antigo pode não ter as colunas acrescentadas depois.
            _garantir_schema()

            registrar_auditoria(
                ACAO_SISTEMA_RESTORE,
                entidade='restore',
                descricao='Restauração aplicada a partir de ZIP enviado na interface',
                detalhes=f'arquivo_enviado={nome_original}',
                usuario_id=auditoria_uid,
                usuario_username=auditoria_user,
                commit=True,
            )

    except ValueError as exc:
        current_app.logger.warning('ZIP de restauração rejeitado: %s', exc)
        flash(str(exc), 'danger')
        return redirect(url_for('admin_restore'))
    except Exception:
        current_app.logger.exception('Erro inesperado na restauração')
        flash('Não foi possível processar o ZIP. Verifique se é um backup válido deste sistema.', 'danger')
        return redirect(url_for('admin_restore'))
    finally:
        try:
            if os.path.isfile(zip_path):
                os.remove(zip_path)
        except OSError:
            current_app.logger.warning('Não foi possível remover ficheiro temporário: %s', zip_path)

    session.clear()
    flash(
        'Restauração concluída. Faça login novamente. '
        'Se o servidor usar Gunicorn com vários workers ou Docker sem reinício automático, '
        'reinicie o serviço para todos os processos carregarem o novo banco.',
        'success',
    )
    return redirect(url_for('login'))


if __name__ == '__main__':
    _debug = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    _host = os.environ.get('FLASK_HOST', '127.0.0.1')
    _port = int(os.environ.get('FLASK_PORT', '5000'))
    app.run(debug=_debug, host=_host, port=_port)
