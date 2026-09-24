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

    # Conta desativada não entra no sistema, mas o usuário e o histórico são preservados.
    ativo = db.Column(db.Boolean, nullable=False, default=True, server_default='1')
    # Senha provisória (definida por um admin ou na criação): obriga a troca no próximo acesso.
    trocar_senha = db.Column(db.Boolean, nullable=False, default=False, server_default='0')

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


SITUACAO_ATIVO = 'ativo'
SITUACAO_INATIVO = 'inativo'
SITUACAO_DESLIGADO = 'desligado'
SITUACOES_ROTULOS = {
    SITUACAO_ATIVO: 'Ativo',
    SITUACAO_INATIVO: 'Inativo',
    SITUACAO_DESLIGADO: 'Desligado',
}


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
    # Texto livre da versão antiga ("Maria, João"). Convertido uma vez para a tabela de
    # dependentes na inicialização e mantido só como cópia de segurança (não é mais editado).
    dependentes_texto_legado = db.Column('dependentes', db.Text, nullable=True)
    data_criacao = db.Column(db.DateTime, default=_agora_utc)

    # Situação cadastral: desligar/inativar preserva o histórico (em vez de excluir).
    situacao = db.Column(
        db.String(20), nullable=False, default=SITUACAO_ATIVO,
        server_default=SITUACAO_ATIVO, index=True,
    )
    situacao_data = db.Column(db.Date, nullable=True)
    situacao_motivo = db.Column(db.String(200), nullable=True)

    # LGPD: consentimento para o tratamento dos dados (versão do termo aceito) e anonimização.
    consentimento_data = db.Column(db.DateTime, nullable=True)
    consentimento_versao = db.Column(db.String(20), nullable=True)
    consentimento_por = db.Column(db.String(80), nullable=True)
    anonimizado_em = db.Column(db.DateTime, nullable=True)

    dependentes = db.relationship(
        'Dependente',
        backref='titular',
        cascade='all, delete-orphan',
        order_by='Dependente.nome',
    )

    @property
    def situacao_rotulo(self):
        return SITUACOES_ROTULOS.get(self.situacao, self.situacao)

    @property
    def dependentes_resumo(self):
        """Texto curto para tabelas/planilhas: "Maria (Filho(a)); João (Cônjuge)"."""
        return '; '.join(f'{d.nome} ({d.parentesco})' for d in self.dependentes)


PARENTESCO_NAO_INFORMADO = 'Não informado'
PARENTESCOS = (
    'Cônjuge', 'Companheiro(a)', 'Filho(a)', 'Enteado(a)', 'Pai', 'Mãe',
    'Irmão(ã)', 'Neto(a)', 'Outro', PARENTESCO_NAO_INFORMADO,
)


class Dependente(db.Model):
    __tablename__ = 'dependentes_associado'

    id = db.Column(db.Integer, primary_key=True)
    associado_id = db.Column(
        db.Integer, db.ForeignKey('associados.id', ondelete='CASCADE'), nullable=False, index=True,
    )
    nome = db.Column(db.String(100), nullable=False)
    parentesco = db.Column(db.String(30), nullable=False, default=PARENTESCO_NAO_INFORMADO)
    data_nascimento = db.Column(db.Date, nullable=True)
    cpf = db.Column(db.String(14), nullable=True)

    def resumo(self):
        partes = [self.parentesco]
        if self.data_nascimento:
            partes.append(f'nasc. {self.data_nascimento.strftime("%d/%m/%Y")}')
        if self.cpf:
            partes.append(f'CPF {self.cpf}')
        return f'{self.nome} ({", ".join(partes)})'


# Tipos de ações registradas na trilha de auditoria.
ACAO_ASSOCIADO_CRIAR = 'associado.criar'
ACAO_ASSOCIADO_EDITAR = 'associado.editar'
ACAO_ASSOCIADO_EXCLUIR = 'associado.excluir'
ACAO_USUARIO_CRIAR = 'usuario.criar'
ACAO_USUARIO_EXCLUIR = 'usuario.excluir'
ACAO_USUARIO_PERFIL = 'usuario.perfil_alterado'
ACAO_USUARIO_PALAVRA = 'usuario.palavra_alterada'
ACAO_USUARIO_EDITAR = 'usuario.editado'
ACAO_USUARIO_SENHA_REDEFINIDA = 'usuario.senha_redefinida'
ACAO_USUARIO_SENHA_TROCADA = 'usuario.senha_trocada'
ACAO_AUTH_LOGIN = 'auth.login'
ACAO_AUTH_LOGOUT = 'auth.logout'
ACAO_AUTH_LOGIN_FALHOU = 'auth.login_falhou'
ACAO_AUTH_RECUPERACAO = 'auth.senha_recuperada'
ACAO_AUTH_RECUPERACAO_FALHOU = 'auth.recuperacao_falhou'
ACAO_ASSOCIADO_EXPORTAR = 'associado.exportar'
ACAO_ASSOCIADO_IMPORTAR = 'associado.importar'
ACAO_ASSOCIADO_CARTEIRINHA = 'associado.carteirinha'
ACAO_ASSOCIADO_CONSENTIMENTO = 'associado.consentimento'
ACAO_ASSOCIADO_DADOS_TITULAR = 'associado.dados_titular'
ACAO_ASSOCIADO_ANONIMIZAR = 'associado.anonimizar'
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
    ACAO_USUARIO_EDITAR: 'Editou usuário (perfil/acesso)',
    ACAO_USUARIO_SENHA_REDEFINIDA: 'Redefiniu senha de outro usuário',
    ACAO_USUARIO_SENHA_TROCADA: 'Trocou a senha provisória',
    ACAO_AUTH_LOGIN: 'Entrou no sistema',
    ACAO_AUTH_LOGOUT: 'Saiu do sistema',
    ACAO_AUTH_LOGIN_FALHOU: 'Tentativa de login (falhou)',
    ACAO_AUTH_RECUPERACAO: 'Redefiniu a senha pela frase de segurança',
    ACAO_AUTH_RECUPERACAO_FALHOU: 'Tentativa de recuperação de senha (falhou)',
    ACAO_ASSOCIADO_EXPORTAR: 'Exportou planilha de associados',
    ACAO_ASSOCIADO_IMPORTAR: 'Importou associados de planilha',
    ACAO_ASSOCIADO_CARTEIRINHA: 'Emitiu carteirinha',
    ACAO_ASSOCIADO_CONSENTIMENTO: 'Registrou/revogou consentimento (LGPD)',
    ACAO_ASSOCIADO_DADOS_TITULAR: 'Exportou dados do titular (LGPD)',
    ACAO_ASSOCIADO_ANONIMIZAR: 'Anonimizou associado (LGPD)',
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

