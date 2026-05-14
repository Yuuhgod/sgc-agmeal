from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


def _agora_utc():
    return datetime.now(timezone.utc)


def _agora_local():
    """Hora local seguindo a TZ do contêiner (America/Maceio em produção)."""
    return datetime.now()


ROLE_ADMIN = 'admin'
ROLE_USUARIO = 'usuario'
ROLES_VALIDAS = (ROLE_ADMIN, ROLE_USUARIO)


class Usuario(db.Model):
    __tablename__ = 'usuarios'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    senha_hash = db.Column(db.String(256), nullable=False)

    # Frase de recuperação (hash; legado pode estar em texto plano até o próximo login/recuperação).
    palavra_recuperacao = db.Column(db.String(256), nullable=False)

    # Papel: 'admin' (gerencia usuários) ou 'usuario' (acesso padrão).
    role = db.Column(db.String(20), nullable=False, default=ROLE_USUARIO)

    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

    def set_palavra_recuperacao(self, palavra):
        self.palavra_recuperacao = generate_password_hash(palavra)

    def _palavra_recuperacao_eh_hash(self):
        s = self.palavra_recuperacao or ''
        return s.startswith('pbkdf2:') or s.startswith('scrypt:')

    def verificar_palavra_recuperacao(self, palavra):
        if self._palavra_recuperacao_eh_hash():
            return check_password_hash(self.palavra_recuperacao, palavra)
        return self.palavra_recuperacao == palavra

    def migrar_palavra_recuperacao_se_legado(self, palavra_plain):
        if palavra_plain and not self._palavra_recuperacao_eh_hash():
            if self.palavra_recuperacao == palavra_plain:
                self.set_palavra_recuperacao(palavra_plain)


class Associado(db.Model):
    __tablename__ = 'associados'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    matricula = db.Column(db.String(20), unique=True, nullable=False)
    rg = db.Column(db.String(20), nullable=False)
    cpf = db.Column(db.String(14), unique=True, nullable=False)
    telefone = db.Column(db.String(15), nullable=True)
    telefone_whatsapp = db.Column(db.String(15), nullable=True)
    foto_perfil = db.Column(db.String(255), nullable=True)
    endereco = db.Column(db.Text, nullable=False)
    data_nascimento = db.Column(db.Date, nullable=False)
    email = db.Column(db.String(100), nullable=False)
    data_admissao = db.Column(db.Date, nullable=False)
    dependentes = db.Column(db.Text, nullable=True)
    data_criacao = db.Column(db.DateTime, default=_agora_utc)


# Tipos de ações registradas na trilha de auditoria.
ACAO_ASSOCIADO_CRIAR = 'associado.criar'
ACAO_ASSOCIADO_EDITAR = 'associado.editar'
ACAO_ASSOCIADO_EXCLUIR = 'associado.excluir'
ACAO_USUARIO_CRIAR = 'usuario.criar'
ACAO_USUARIO_EXCLUIR = 'usuario.excluir'
ACAO_USUARIO_PERFIL = 'usuario.perfil_alterado'
ACAO_USUARIO_PALAVRA = 'usuario.palavra_alterada'
ACAO_AUTH_LOGIN = 'auth.login'
ACAO_AUTH_LOGOUT = 'auth.logout'
ACAO_AUTH_LOGIN_FALHOU = 'auth.login_falhou'
ACAO_SISTEMA_BACKUP = 'sistema.backup'
ACAO_SISTEMA_RESTORE = 'sistema.restore'

ACOES_ROTULOS = {
    ACAO_ASSOCIADO_CRIAR: 'Cadastrou associado',
    ACAO_ASSOCIADO_EDITAR: 'Editou associado',
    ACAO_ASSOCIADO_EXCLUIR: 'Excluiu associado',
    ACAO_USUARIO_CRIAR: 'Criou usuário',
    ACAO_USUARIO_EXCLUIR: 'Excluiu usuário',
    ACAO_USUARIO_PERFIL: 'Alterou perfil próprio',
    ACAO_USUARIO_PALAVRA: 'Alterou frase de segurança',
    ACAO_AUTH_LOGIN: 'Entrou no sistema',
    ACAO_AUTH_LOGOUT: 'Saiu do sistema',
    ACAO_AUTH_LOGIN_FALHOU: 'Tentativa de login (falhou)',
    ACAO_SISTEMA_BACKUP: 'Gerou backup do sistema',
    ACAO_SISTEMA_RESTORE: 'Restaurou backup (substituiu dados)',
}


class Auditoria(db.Model):
    """Trilha de auditoria. Independente de FK para preservar histórico após exclusões."""
    __tablename__ = 'auditoria'

    id = db.Column(db.Integer, primary_key=True)
    data_hora = db.Column(db.DateTime, default=_agora_local, nullable=False, index=True)

    # Snapshot do autor da ação (ID pode ficar nulo se o usuário for removido depois).
    usuario_id = db.Column(db.Integer, nullable=True, index=True)
    usuario_username = db.Column(db.String(80), nullable=False)

    acao = db.Column(db.String(40), nullable=False, index=True)
    entidade = db.Column(db.String(40), nullable=True)
    entidade_id = db.Column(db.Integer, nullable=True)
    descricao = db.Column(db.String(200), nullable=True)
    detalhes = db.Column(db.Text, nullable=True)
    ip_origem = db.Column(db.String(45), nullable=True)

    @property
    def rotulo(self):
        return ACOES_ROTULOS.get(self.acao, self.acao)

