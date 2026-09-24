"""situacao_do_associado

Adiciona a situação cadastral (ativo/inativo/desligado), a data e o motivo.
Idempotente: a aplicação também cria estas colunas ao iniciar, então a migração
só adiciona o que ainda faltar.

Revision ID: b7c3e2a91d40
Revises: 649aca2b1243
Create Date: 2026-09-24 04:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7c3e2a91d40'
down_revision = '649aca2b1243'
branch_labels = None
depends_on = None


def _colunas_existentes():
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns('associados')}


def _indices_existentes():
    return {i['name'] for i in sa.inspect(op.get_bind()).get_indexes('associados')}


def upgrade():
    existentes = _colunas_existentes()
    with op.batch_alter_table('associados') as batch_op:
        if 'situacao' not in existentes:
            batch_op.add_column(sa.Column(
                'situacao', sa.String(length=20), nullable=False, server_default='ativo',
            ))
        if 'situacao_data' not in existentes:
            batch_op.add_column(sa.Column('situacao_data', sa.Date(), nullable=True))
        if 'situacao_motivo' not in existentes:
            batch_op.add_column(sa.Column('situacao_motivo', sa.String(length=200), nullable=True))
    if 'ix_associados_situacao' not in _indices_existentes():
        op.create_index('ix_associados_situacao', 'associados', ['situacao'])


def downgrade():
    if 'ix_associados_situacao' in _indices_existentes():
        op.drop_index('ix_associados_situacao', table_name='associados')
    existentes = _colunas_existentes()
    with op.batch_alter_table('associados') as batch_op:
        for nome in ('situacao_motivo', 'situacao_data', 'situacao'):
            if nome in existentes:
                batch_op.drop_column(nome)
