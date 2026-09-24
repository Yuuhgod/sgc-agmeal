"""dependentes_em_tabela

Cria a tabela de dependentes. Idempotente: a aplicação também cria a tabela ao
iniciar. A conversão do texto antigo ("Maria, João") em registros é feita pela
aplicação na inicialização (uma única vez, marcada em `sgc_meta`).

Revision ID: c4e8f1a2b3d5
Revises: b7c3e2a91d40
Create Date: 2026-09-24 05:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4e8f1a2b3d5'
down_revision = 'b7c3e2a91d40'
branch_labels = None
depends_on = None


def _tabelas():
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade():
    if 'dependentes_associado' in _tabelas():
        return
    op.create_table(
        'dependentes_associado',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('associado_id', sa.Integer(),
                  sa.ForeignKey('associados.id', ondelete='CASCADE'), nullable=False),
        sa.Column('nome', sa.String(length=100), nullable=False),
        sa.Column('parentesco', sa.String(length=30), nullable=False),
        sa.Column('data_nascimento', sa.Date(), nullable=True),
        sa.Column('cpf', sa.String(length=14), nullable=True),
    )
    op.create_index('ix_dependentes_associado_associado_id', 'dependentes_associado', ['associado_id'])


def downgrade():
    # A coluna de texto antiga (associados.dependentes) não é apagada, então nada se perde
    # dos dados originais; os dependentes cadastrados depois da conversão, sim.
    tabelas = _tabelas()
    if 'dependentes_associado' in tabelas:
        op.drop_table('dependentes_associado')
    if 'sgc_meta' in tabelas:
        op.execute("DELETE FROM sgc_meta WHERE chave = 'dependentes_convertidos'")
