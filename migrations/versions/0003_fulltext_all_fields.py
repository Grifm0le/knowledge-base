"""Полнотекстовый поиск по всем полям: заголовки, теги и код

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from kb.parser import parse_document

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

OLD_VECTOR_SQL = (
    "setweight(to_tsvector('russian', title), 'A') || setweight(to_tsvector('russian', body), 'B')"
)
NEW_VECTOR_SQL = (
    "setweight(to_tsvector('russian', title), 'A') || "
    "setweight(to_tsvector('russian', keywords), 'B') || "
    "setweight(to_tsvector('russian', body), 'C') || "
    "setweight(to_tsvector('russian', code), 'D')"
)


def _replace_vector(expression: str) -> None:
    # Выражение вычисляемого столбца нельзя изменить, столбец создаётся заново.
    op.drop_index("ix_documents_search_vector", table_name="documents")
    op.drop_column("documents", "search_vector")
    op.add_column(
        "documents",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(expression, persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_documents_search_vector", "documents", ["search_vector"], postgresql_using="gin"
    )


def upgrade() -> None:
    op.add_column("documents", sa.Column("keywords", sa.Text(), nullable=False, server_default=""))
    op.add_column("documents", sa.Column("code", sa.Text(), nullable=False, server_default=""))

    # Новые поля уже сохранённых документов заполняются разбором текущей версии.
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT d.id, v.content FROM documents d JOIN document_versions v "
            "ON v.document_id = d.id AND v.version = d.current_version"
        )
    )
    for doc_id, content in rows.all():
        fields = parse_document(content).index_fields()
        connection.execute(
            sa.text("UPDATE documents SET keywords = :keywords, code = :code WHERE id = :id"),
            {
                "id": doc_id,
                "keywords": f"{fields['headings']} {fields['tags']}".strip(),
                "code": fields["code"],
            },
        )
    _replace_vector(NEW_VECTOR_SQL)


def downgrade() -> None:
    _replace_vector(OLD_VECTOR_SQL)
    op.drop_column("documents", "code")
    op.drop_column("documents", "keywords")
