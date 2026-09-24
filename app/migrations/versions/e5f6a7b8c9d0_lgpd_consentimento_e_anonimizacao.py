"""lgpd_consentimento_e_anonimizacao

Adiciona em associados: consentimento_data, consentimento_versao,
consentimento_por e anonimizado_em. Idempotente: a aplicação também cria
estas colunas ao iniciar.

Revision ID: e5f6a7b8c9d0
Revises: d9a1b2c3e4f6
Create Date: 2026-09-24 07:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5f6a7b8c9d0'
down_revision = 'd9a1b2c3e4f6'
branch_labels = None
depends_on = None

COLUNAS = (
    ('consentimento_data', sa.DateTime()),
    ('consentimento_versao', sa.String(length=20)),
    ('consentimento_por', sa.String(length=80)),
    ('anonimizado_em', sa.DateTime()),
)


def _colunas():
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns('associados')}


def upgrade():
    existentes = _colunas()
    with op.batch_alter_table('associados') as batch_op:
        for nome, tipo in COLUNAS:
            if nome not in existentes:
                batch_op.add_column(sa.Column(nome, tipo, nullable=True))


def downgrade():
    existentes = _colunas()
    with op.batch_alter_table('associados') as batch_op:
        for nome, _ in reversed(COLUNAS):
            if nome in existentes:
                batch_op.drop_column(nome)
