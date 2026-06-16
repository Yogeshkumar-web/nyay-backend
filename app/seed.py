"""
Dev seed data. Run with:
    python -m app.seed
"""
import asyncio
import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.security import hash_password
from app.features.users.models import User, UserRole
from app.features.cases.models import (
    Case, CaseAccess, CaseNumber, CaseSection, Party,
    BenchType, CaseNumberType, CaseStage, CaseStatus, CaseType, PartyType,
)


LAWYER_EMAIL = "adv.sharma@vakilsuite.dev"
MUNSHI_EMAIL = "munshi.rajesh@vakilsuite.dev"
PASSWORD = "Password123!"

today = date.today()


async def seed(session: AsyncSession) -> None:
    # ── Users ──────────────────────────────────────────────────────────────────
    lawyer = User(
        id=uuid.uuid4(),
        full_name="Adv. Suresh Sharma",
        email=LAWYER_EMAIL,
        hashed_password=hash_password(PASSWORD),
        role=UserRole.lawyer,
        bar_enrollment_no="AHC/2010/1234",
        phone="9876543210",
    )
    munshi = User(
        id=uuid.uuid4(),
        full_name="Rajesh Kumar",
        email=MUNSHI_EMAIL,
        hashed_password=hash_password(PASSWORD),
        role=UserRole.munshi,
        phone="9123456789",
    )
    session.add_all([lawyer, munshi])
    await session.flush()

    print(f"  Lawyer : {LAWYER_EMAIL} / {PASSWORD}")
    print(f"  Munshi : {MUNSHI_EMAIL} / {PASSWORD}")

    # ── Cases ──────────────────────────────────────────────────────────────────
    cases_data = [
        dict(
            case_title="Ram Prakash Yadav vs State of UP",
            case_type=CaseType.bail_application,
            bench_type=BenchType.single_bench,
            stage=CaseStage.arguments,
            petitioner_name="Ram Prakash Yadav",
            respondent_name="State of Uttar Pradesh",
            act_name="IPC",
            filing_date=today - timedelta(days=45),
            next_hearing_date=today + timedelta(days=7),
            bail_rejection_date=today - timedelta(days=60),
            limitation_expiry_date=today + timedelta(days=30),
            numbers=[
                dict(type=CaseNumberType.high_court, number="12345/2024",
                     court="Allahabad High Court", year=2024, primary=True),
                dict(type=CaseNumberType.lower_court, number="SC/234/2024",
                     court="ACJM Lucknow", year=2024, primary=False),
            ],
            sections=[("302", "IPC"), ("307", "IPC")],
            parties=[
                ("Ram Prakash Yadav", PartyType.petitioner, "9876500001"),
                ("State of Uttar Pradesh", PartyType.respondent, None),
            ],
            give_munshi_access=True,
        ),
        dict(
            case_title="Priya Devi vs Union of India & Ors",
            case_type=CaseType.writ_petition,
            bench_type=BenchType.division_bench,
            stage=CaseStage.counter_affidavit,
            petitioner_name="Priya Devi",
            respondent_name="Union of India & Ors",
            act_name="Constitution of India",
            filing_date=today - timedelta(days=90),
            next_hearing_date=today + timedelta(days=14),
            numbers=[
                dict(type=CaseNumberType.high_court, number="4321/2024",
                     court="Allahabad High Court", year=2024, primary=True),
            ],
            sections=[
                ("21", "Constitution of India"),
                ("14", "Constitution of India"),
            ],
            parties=[
                ("Priya Devi", PartyType.petitioner, "9876500002"),
                ("Union of India", PartyType.respondent, None),
                ("State of UP", PartyType.respondent, None),
            ],
            give_munshi_access=True,
        ),
        dict(
            case_title="Mohd. Aslam vs State of UP — Quashing",
            case_type=CaseType.quashing,
            bench_type=BenchType.single_bench,
            stage=CaseStage.admission,
            petitioner_name="Mohd. Aslam",
            respondent_name="State of Uttar Pradesh",
            act_name="CrPC",
            filing_date=today - timedelta(days=15),
            next_hearing_date=today + timedelta(days=3),
            numbers=[
                dict(type=CaseNumberType.high_court, number="9876/2025",
                     court="Allahabad High Court", year=2025, primary=True),
                dict(type=CaseNumberType.lower_court, number="FIR/456/2024",
                     court="PS Hazratganj", year=2024, primary=False),
            ],
            sections=[("420", "IPC"), ("406", "IPC")],
            parties=[
                ("Mohd. Aslam", PartyType.petitioner, "9876500003"),
                ("State of Uttar Pradesh", PartyType.respondent, None),
            ],
            give_munshi_access=True,
        ),
        dict(
            case_title="Shiv Shankar Gupta vs State — Criminal Revision",
            case_type=CaseType.criminal_revision,
            bench_type=BenchType.single_bench,
            stage=CaseStage.notice,
            petitioner_name="Shiv Shankar Gupta",
            respondent_name="State of Uttar Pradesh",
            act_name="CrPC",
            filing_date=today - timedelta(days=30),
            next_hearing_date=today + timedelta(days=21),
            numbers=[
                dict(type=CaseNumberType.high_court, number="5555/2025",
                     court="Allahabad High Court", year=2025, primary=True),
            ],
            sections=[("138", "Negotiable Instruments Act")],
            parties=[
                ("Shiv Shankar Gupta", PartyType.petitioner, "9876500004"),
                ("State of UP", PartyType.respondent, None),
            ],
            give_munshi_access=False,
        ),
        dict(
            case_title="Citizens Welfare Forum vs UP Pollution Control Board — PIL",
            case_type=CaseType.pil,
            bench_type=BenchType.division_bench,
            stage=CaseStage.filing,
            petitioner_name="Citizens Welfare Forum",
            respondent_name="UP Pollution Control Board",
            act_name="Environment Protection Act",
            filing_date=today - timedelta(days=5),
            next_hearing_date=today + timedelta(days=45),
            numbers=[
                dict(type=CaseNumberType.high_court, number="PIL/123/2025",
                     court="Allahabad High Court", year=2025, primary=True),
            ],
            sections=[
                ("3", "Environment Protection Act"),
                ("5", "Environment Protection Act"),
            ],
            parties=[
                ("Citizens Welfare Forum", PartyType.petitioner, None),
                ("UP Pollution Control Board", PartyType.respondent, None),
                ("State of UP", PartyType.respondent, None),
            ],
            give_munshi_access=False,
        ),
    ]

    for cd in cases_data:
        case = Case(
            id=uuid.uuid4(),
            lawyer_id=lawyer.id,
            case_title=cd["case_title"],
            case_type=cd["case_type"],
            bench_type=cd["bench_type"],
            stage=cd["stage"],
            status=CaseStatus.active,
            petitioner_name=cd["petitioner_name"],
            respondent_name=cd["respondent_name"],
            act_name=cd.get("act_name"),
            filing_date=cd.get("filing_date"),
            next_hearing_date=cd.get("next_hearing_date"),
            bail_rejection_date=cd.get("bail_rejection_date"),
            limitation_expiry_date=cd.get("limitation_expiry_date"),
        )
        session.add(case)
        await session.flush()

        for n in cd.get("numbers", []):
            session.add(CaseNumber(
                id=uuid.uuid4(),
                case_id=case.id,
                number_type=n["type"],
                case_number=n["number"],
                court_name=n["court"],
                year=n["year"],
                is_primary=n["primary"],
            ))

        for section, act in cd.get("sections", []):
            session.add(CaseSection(
                id=uuid.uuid4(),
                case_id=case.id,
                section=section,
                act_name=act,
                is_active=True,
                added_by=lawyer.id,
            ))

        for name, ptype, phone in cd.get("parties", []):
            session.add(Party(
                id=uuid.uuid4(),
                case_id=case.id,
                name=name,
                party_type=ptype,
                phone=phone,
            ))

        if cd.get("give_munshi_access"):
            session.add(CaseAccess(
                id=uuid.uuid4(),
                case_id=case.id,
                user_id=munshi.id,
                granted_by=lawyer.id,
                can_edit=True,
            ))

        print(f"  Case   : {case.case_title}")

    await session.commit()
    print("\nSeed complete ✅")


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with AsyncSessionLocal() as session:
        print("\nSeeding database...")
        await seed(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())