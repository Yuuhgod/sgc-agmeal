from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# Tabela de Administradores
class Usuario(db.Model):
    __tablename__ = 'usuarios'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    senha_hash = db.Column(db.String(256), nullable=False)
    
    # NOVO CAMPO: Frase secreta para recuperar a conta
    palavra_recuperacao = db.Column(db.String(100), nullable=False)

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

# Tabela de Associados (já existia)
class Associado(db.Model):
    __tablename__ = 'associados'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    matricula = db.Column(db.String(20), unique=True, nullable=False)
    rg = db.Column(db.String(20), nullable=False)
    cpf = db.Column(db.String(14), unique=True, nullable=False)
    telefone = db.Column(db.String(15), nullable=True)
    telefone_whatsapp = db.Column(db.String(15), nullable=True)
    foto_perfil = db.Column(db.String(255), nullable=True) # Guarda o nome da imagem salva
    endereco = db.Column(db.Text, nullable=False)
    data_nascimento = db.Column(db.Date, nullable=False)
    email = db.Column(db.String(100), nullable=False)
    data_admissao = db.Column(db.Date, nullable=False)
    dependentes = db.Column(db.Text, nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)