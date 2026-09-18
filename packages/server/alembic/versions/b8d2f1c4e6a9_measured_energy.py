"""实测电量、订正系数与订正开关。docs/19 §三"""

import sqlalchemy as sa

from alembic import op

revision = "b8d2f1c4e6a9"
down_revision = "7c1e4d2b9a53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "measured_energy",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("station_id", sa.String(24), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("kwh", sa.Float(), nullable=False),
        sa.Column("basis", sa.String(16), nullable=False),
        sa.Column("model_kwh", sa.Float(), nullable=True),
        sa.Column("model_digest", sa.String(32), nullable=True),
        sa.Column("prior_kwh", sa.Float(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("station_id", "period_start", "period_end", name="uq_measured_period"),
    )
    op.create_index("ix_measured_energy_station_id", "measured_energy", ["station_id"])
    op.create_table(
        "station_correction",
        sa.Column("station_id", sa.String(24), primary_key=True),
        sa.Column("fitted_at", sa.DateTime(), nullable=False),
        sa.Column("method", sa.String(8), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("excluded_count", sa.Integer(), nullable=False),
        sa.Column("k", sa.Float(), nullable=True),
        sa.Column("error_before", sa.Float(), nullable=True),
        sa.Column("error_after", sa.Float(), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(128), nullable=True),
    )
    with op.batch_alter_table("stations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "correction_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("stations") as batch_op:
        batch_op.drop_column("correction_enabled")
    op.drop_table("station_correction")
    op.drop_index("ix_measured_energy_station_id", table_name="measured_energy")
    op.drop_table("measured_energy")
