"""create case management tables

Revision ID: 0001
Revises: 231a7e12e429
Create Date: 2025-04-15 09:00:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = "231a7e12e429"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE case_type AS ENUM (
            'writ_petition', 'pil', 'criminal_revision', 'criminal_appeal',
            'bail_application', 'anticipatory_bail', 'quashing', 'civil_revision',
            'first_appeal', 'second_appeal', 'contempt', 'other'
        )
    """
    )
    op.execute(
        "CREATE TYPE bench_type AS ENUM ('single_bench', 'division_bench', 'full_bench')"
    )
    op.execute(
        """
        CREATE TYPE case_stage AS ENUM (
            'filing', 'admission', 'notice', 'counter_affidavit', 'rejoinder',
            'arguments', 'judgment', 'disposed', 'transferred'
        )
    """
    )
    op.execute("CREATE TYPE case_status AS ENUM ('active', 'disposed', 'archived')")
    op.execute(
        """
        CREATE TYPE case_number_type AS ENUM (
            'lower_court', 'high_court', 'supreme_court', 'connected_matter', 'other'
        )
    """
    )
    op.execute(
        """
        CREATE TYPE party_type AS ENUM (
            'petitioner', 'respondent', 'intervener', 'amicus', 'witness', 'other'
        )
    """
    )

    op.execute(
        """
        CREATE TABLE cases (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            lawyer_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            case_title VARCHAR(500) NOT NULL,
            case_type case_type NOT NULL,
            bench_type bench_type NOT NULL DEFAULT 'single_bench',
            stage case_stage NOT NULL DEFAULT 'filing',
            status case_status NOT NULL DEFAULT 'active',
            court_number VARCHAR(20),
            petitioner_name VARCHAR(500) NOT NULL,
            respondent_name VARCHAR(500) NOT NULL,
            act_name VARCHAR(255),
            brief_facts TEXT,
            filing_date DATE,
            next_hearing_date DATE,
            lower_court_decision_date DATE,
            bail_rejection_date DATE,
            limitation_expiry_date DATE,
            notes TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """
    )
    op.execute("CREATE INDEX idx_cases_lawyer_id ON cases(lawyer_id)")
    op.execute("CREATE INDEX idx_cases_status ON cases(status)")
    op.execute(
        "CREATE INDEX idx_cases_next_hearing ON cases(next_hearing_date) WHERE status = 'active'"
    )

    op.execute(
        """
        CREATE TABLE case_numbers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
            number_type case_number_type NOT NULL,
            case_number VARCHAR(200) NOT NULL,
            court_name VARCHAR(255),
            year SMALLINT,
            is_primary BOOLEAN NOT NULL DEFAULT FALSE,
            notes VARCHAR(500),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """
    )
    op.execute("CREATE INDEX idx_case_numbers_case_id ON case_numbers(case_id)")
    op.execute("CREATE INDEX idx_case_numbers_number ON case_numbers(case_number)")

    op.execute(
        """
        CREATE TABLE case_sections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
            section VARCHAR(200) NOT NULL,
            act_name VARCHAR(200) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            added_by UUID NOT NULL REFERENCES users(id),
            added_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            removed_by UUID REFERENCES users(id),
            removed_at TIMESTAMP WITH TIME ZONE,
            notes VARCHAR(500)
        )
    """
    )
    op.execute("CREATE INDEX idx_case_sections_case_id ON case_sections(case_id)")
    op.execute(
        "CREATE INDEX idx_case_sections_active ON case_sections(case_id) WHERE is_active = TRUE"
    )

    op.execute(
        """
        CREATE TABLE case_access (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            granted_by UUID NOT NULL REFERENCES users(id),
            can_edit BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            CONSTRAINT uq_case_user UNIQUE(case_id, user_id)
        )
    """
    )
    op.execute("CREATE INDEX idx_case_access_user_id ON case_access(user_id)")
    op.execute("CREATE INDEX idx_case_access_case_id ON case_access(case_id)")

    op.execute(
        """
        CREATE TABLE parties (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
            name VARCHAR(500) NOT NULL,
            party_type party_type NOT NULL,
            address TEXT,
            phone VARCHAR(20),
            notes TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """
    )
    op.execute("CREATE INDEX idx_parties_case_id ON parties(case_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS parties")
    op.execute("DROP TABLE IF EXISTS case_access")
    op.execute("DROP TABLE IF EXISTS case_sections")
    op.execute("DROP TABLE IF EXISTS case_numbers")
    op.execute("DROP TABLE IF EXISTS cases")
    op.execute("DROP TYPE IF EXISTS party_type")
    op.execute("DROP TYPE IF EXISTS case_number_type")
    op.execute("DROP TYPE IF EXISTS case_status")
    op.execute("DROP TYPE IF EXISTS case_stage")
    op.execute("DROP TYPE IF EXISTS bench_type")
    op.execute("DROP TYPE IF EXISTS case_type")
