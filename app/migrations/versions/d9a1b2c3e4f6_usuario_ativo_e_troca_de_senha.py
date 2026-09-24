"""usuario_ativo_e_troca_de_senha

Adiciona usuarios.ativo e usuarios.trocar_senha. Idempotente: a aplicação também
cria estas colunas ao iniciar.

Revision ID: d9a1b2c3e4f6
Revises: c4e8f1a2b3d5
Create Date: 2026-09-24 06:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd9a1b2c3e4f6'
down_revision = 'c4e8f1a2b3d5'
branch_labels = None
depends_on = None


def _colunas():
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns('usuarios')}


def upgrade():
    existentes = _colunas()
    with op.batch_alter_table('usuarios') as batch_op:
        if 'ativo' not in existentes:
            batch_op.add_column(sa.Column('ativo', sa.Boolean(), nullable=False, server_default='1'))
        if 'trocar_senha' not in existentes:
            batch_op.add_column(sa.Column('trocar_senha', sa.Boolean(), nullable=False, server_default='0'))


def downgrade():
    existentes = _colunas()
    with op.batch_alter_table('usuarios') as batch_op:
        for nome in ('trocar_senha', 'ativo'):
            if nome in existentes:
                batch_op.drop_column(nome)
