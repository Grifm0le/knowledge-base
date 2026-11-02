"""Полнотекстовый поиск PostgreSQL: вычисляемый столбец и индекс GIN

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('russian', title), 'A') || setweight(to_tsvector('russian', body), 'B')"
)


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(SEARCH_VECTOR_SQL, persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_documents_search_vector", "documents", ["search_vector"], postgresql_using="gin"
    )


def downgrade() -> None:
    op.drop_index("ix_documents_search_vector", table_name="documents")
    op.drop_column("documents", "search_vector")
