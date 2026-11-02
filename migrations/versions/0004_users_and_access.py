"""Пользователи, разовые токены, владельцы документов и доступ по приглашению

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Документы, созданные до появления пользователей, переходят служебной записи без пароля.
LEGACY_EMAIL = "legacy@kb.invalid"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "auth_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_auth_tokens_user_id", "auth_tokens", ["user_id"])

    op.add_column("documents", sa.Column("owner_id", postgresql.UUID(as_uuid=False)))
    op.execute(
        sa.text(
            "INSERT INTO users (id, email, password_hash, consent_at) "
            "SELECT gen_random_uuid(), :email, '!', now() "
            "WHERE EXISTS (SELECT 1 FROM documents)"
        ).bindparams(email=LEGACY_EMAIL)
    )
    op.execute(
        sa.text(
            "UPDATE documents SET owner_id = (SELECT id FROM users WHERE email = :email)"
        ).bindparams(email=LEGACY_EMAIL)
    )
    op.alter_column("documents", "owner_id", nullable=False)
    op.create_foreign_key(
        "fk_documents_owner", "documents", "users", ["owner_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])
    op.drop_constraint("documents_source_name_key", "documents", type_="unique")
    op.create_unique_constraint(
        "uq_documents_owner_source", "documents", ["owner_id", "source_name"]
    )

    op.create_table(
        "document_shares",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("permission", sa.String(10), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("permission IN ('read', 'write')", name="ck_document_shares_permission"),
    )
    op.create_index("ix_document_shares_user_id", "document_shares", ["user_id"])


def downgrade() -> None:
    op.drop_table("document_shares")
    # Раньше имя файла было уникальным на всю базу: повторы у разных пользователей сбрасываются.
    op.execute(
        "UPDATE documents SET source_name = NULL WHERE id NOT IN "
        "(SELECT min(id::text)::uuid FROM documents GROUP BY source_name)"
    )
    op.drop_constraint("uq_documents_owner_source", "documents", type_="unique")
    op.create_unique_constraint("documents_source_name_key", "documents", ["source_name"])
    op.drop_index("ix_documents_owner_id", table_name="documents")
    op.drop_constraint("fk_documents_owner", "documents", type_="foreignkey")
    op.drop_column("documents", "owner_id")
    op.drop_table("auth_tokens")
    op.drop_table("users")
