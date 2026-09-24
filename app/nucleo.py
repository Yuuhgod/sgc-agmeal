"""Núcleo do SGC-AGMEAL: app Flask, configuração, banco, hooks, auditoria e utilitários compartilhados pelos módulos de rotas."""

import logging
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from flask import (
    Flask,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import inspect, text
from weasyprint import HTML
from weasyprint.urls import URLFetcher
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

import backup_agendador
from backup_service import criar_backup_zip, listar_backups_locais
from database import (
    ACAO_AUTH_LOGIN_FALHOU,
    ACAO_AUTH_RECUPERACAO_FALHOU,
    ACAO_SISTEMA_BACKUP,
    PARENTESCO_NAO_INFORMADO,
    PARENTESCOS,
    ROLE_ADMIN,
    SITUACOES_ROTULOS,
    Auditoria,
    Usuario,
    db,
    normalizar_busca,
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

STATIC_DIR = os.path.join(basedir, 'static')
LOGO_PDF_URI = Path(STATIC_DIR, 'img', 'logo.jpeg').as_uri()
app.jinja_env.globals['LOGO_PDF_URI'] = LOGO_PDF_URI


class _BuscadorRecursosPDF(URLFetcher):
    """Recursos dos PDFs vêm só do disco (pastas static e de fotos) ou de data: URIs.

    Buscar por HTTP no próprio servidor travava o worker que gera o PDF: com os workers
    ocupados, o WeasyPrint esperava ~10 s e o PDF saía sem o logo."""

    def __init__(self):
        super().__init__(allowed_protocols=('file', 'data'), timeout=5)

    def fetch(self, url, headers=None):
        if url.startswith('file:'):
            caminho = os.path.realpath(url2pathname(urlparse(url).path))
            permitidos = (os.path.realpath(STATIC_DIR), os.path.realpath(UPLOAD_FOLDER))
            if not any(caminho.startswith(p + os.sep) for p in permitidos):
                raise ValueError(f'Arquivo fora das pastas permitidas no PDF: {caminho}')
        return super().fetch(url, headers)


def gerar_pdf(html):
    return HTML(string=html, base_url=Path(STATIC_DIR).as_uri() + '/', url_fetcher=_BuscadorRecursosPDF()).write_pdf()

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


BACKUP_SENHA_MIN = 10


def _arquivo_senha_backup():
    return os.path.join(data_dir, '.backup_senha')


def _senha_backup():
    """Senha dos backups: BACKUP_SENHA no ambiente ou a definida pelo admin (data/.backup_senha).
    O arquivo nunca entra no ZIP (o backup só leva sgc.db, .flask_secret e fotos)."""
    env = os.environ.get('BACKUP_SENHA', '').strip()
    if env:
        return env
    try:
        with open(_arquivo_senha_backup(), encoding='utf-8') as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _gravar_senha_backup(senha):
    caminho = _arquivo_senha_backup()
    tmp = caminho + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(senha)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, caminho)


def _executar_backup_automatico():
    info = criar_backup_zip(
        data_dir=data_dir,
        upload_folder=UPLOAD_FOLDER,
        backups_dir=backups_dir,
        sync_dir=_backup_sync_dir(),
        keep_local=_backup_keep_local(),
        keep_sync=_backup_keep_sync(),
        log=app.logger,
        senha=_senha_backup(),
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
        'criptografado': bool(_senha_backup()),
        'senha_via_ambiente': bool(os.environ.get('BACKUP_SENHA', '').strip()),
        'nuvem': bool(_backup_sync_dir()),
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
        ('consentimento_data', 'DATETIME'),
        ('consentimento_versao', 'VARCHAR(20)'),
        ('consentimento_por', 'VARCHAR(80)'),
        ('anonimizado_em', 'DATETIME'),
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
    if request.endpoint in ['setup', 'static', 'saude']:
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


# ---------------------------------------------------------------------------------------
# Páginas de erro e verificação de saúde
# ---------------------------------------------------------------------------------------

ERROS = {
    400: ('fa-circle-exclamation', 'Requisição inválida', 'O pedido não pôde ser processado. Volte e tente novamente.'),
    403: ('fa-lock', 'Acesso negado', 'Você não tem permissão para acessar esta página.'),
    404: ('fa-magnifying-glass', 'Página não encontrada', 'O endereço não existe ou o registro foi removido.'),
    405: ('fa-ban', 'Operação não permitida', 'Esta página não aceita este tipo de acesso.'),
    413: ('fa-file-circle-exclamation', 'Arquivo grande demais',
          'O arquivo enviado passa do limite aceito pelo sistema. Reduza o tamanho e tente de novo.'),
    429: ('fa-hourglass-half', 'Muitas tentativas', 'Aguarde alguns minutos antes de tentar novamente.'),
    500: ('fa-triangle-exclamation', 'Erro interno',
          'Algo deu errado do nosso lado. O problema foi registrado no log; tente novamente em instantes.'),
}


def _pagina_erro(codigo, mensagem=None):
    icone, titulo, padrao = ERROS.get(codigo, ERROS[500])
    return render_template('erro.html', codigo=codigo, icone=icone, titulo=titulo,
                           mensagem=mensagem or padrao), codigo


@app.errorhandler(CSRFError)
def erro_csrf(_exc):
    return _pagina_erro(400, 'O formulário expirou (a página ficou aberta por muito tempo). '
                             'Volte, recarregue a página e envie de novo.')


for _codigo in (400, 403, 404, 405, 413, 429):
    app.register_error_handler(_codigo, lambda exc, _c=_codigo: _pagina_erro(_c))


@app.errorhandler(500)
def erro_interno(_exc):
    db.session.rollback()
    return _pagina_erro(500)


@app.route('/saude')
@limiter.exempt
def saude():
    """Verificação para monitoramento: responde 200 se o app e o banco estão funcionando.
    Não expõe detalhes (é acessível sem login)."""
    try:
        db.session.execute(text('SELECT 1'))
        return {'status': 'ok'}, 200
    except Exception:  # noqa: BLE001
        app.logger.exception('Verificação de saúde: banco indisponível')
        return {'status': 'erro'}, 503


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


def _contem_texto(coluna, termo):
    """Filtro 'contém', sem diferenciar acentos nem maiúsculas; % e _ digitados são literais."""
    termo = normalizar_busca(termo.strip())
    termo = termo.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return db.func.sem_acento(coluna).like(f'%{termo}%', escape='\\')
