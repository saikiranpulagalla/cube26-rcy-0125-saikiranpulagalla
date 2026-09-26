"""Store canonical quantities as fixed-precision decimal values.

Revision ID: 0005_v02_quantity
Revises: 0004_v02_sources
"""

import sqlalchemy as sa

from alembic import op

revision = "0005_v02_quantity"
down_revision = "0004_v02_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "financial_event",
        "quantity",
        existing_type=sa.Float(),
        type_=sa.Numeric(18, 6),
        postgresql_using="quantity::numeric(18, 6)",
    )
    op.alter_column(
        "evidence_record",
        "coverage_quantity",
        existing_type=sa.Float(),
        type_=sa.Numeric(18, 6),
        postgresql_using="coverage_quantity::numeric(18, 6)",
    )


def downgrade() -> None:
    op.alter_column("evidence_record", "coverage_quantity", existing_type=sa.Numeric(18, 6), type_=sa.Float())
    op.alter_column("financial_event", "quantity", existing_type=sa.Numeric(18, 6), type_=sa.Float())
