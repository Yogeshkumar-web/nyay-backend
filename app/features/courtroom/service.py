import asyncio
import json
import logging
import queue
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, List, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.exceptions import NotFoundError, ForbiddenError, ValidationError
from app.features.courtroom.models import CourtroomSession, SessionType, SessionStatus
from app.features.context.models import CaseContext, CaseSummary

logger = logging.getLogger(__name__)

MAX_TURNS = 100
MAX_CONTEXT_MESSAGES = 50

_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "prompts" / "courtroom"


# ============================================================
# PROMPT LOADER
# ============================================================

_PROMPT_CACHE: Dict[str, str] = {}


def _load_prompt(name: str) -> str:
    if name in _PROMPT_CACHE:
        return _PROMPT_CACHE[name]
    path = _PROMPTS_DIR / name
    if not path.exists():
        raise RuntimeError(f"Prompt file not found: {path}")
    content = path.read_text(encoding="utf-8")
    _PROMPT_CACHE[name] = content
    return content


# ============================================================
# AI PROVIDER ABSTRACTION
# ============================================================


async def _stream_gemini_courtroom(
    system_prompt: str,
    messages: List[Dict[str, str]],
) -> AsyncGenerator[str, None]:
    """Stream tokens from Gemini for courtroom (sync SDK in thread)."""
    from google import genai
    from google.genai import types as genai_types

    model = settings.GEMINI_MODEL or "gemini-2.5-flash"
    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    # Build a single user turn from the conversation history
    conversation_text = "\n\n".join(
        f"{'Lawyer' if m['role'] == 'user' else 'AI'}: {m['content']}"
        for m in messages[:-1]
    )
    last_message = messages[-1]["content"]
    user_content = (
        f"Previous conversation:\n{conversation_text}\n\nLawyer: {last_message}"
        if len(messages) > 1
        else last_message
    )

    q: queue.Queue = queue.Queue()
    _DONE = object()

    def _run():
        try:
            response = client.models.generate_content_stream(
                model=model,
                contents=user_content,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=1000,
                ),
            )
            for chunk in response:
                if chunk.text:
                    q.put(chunk.text)
        except Exception as exc:
            q.put(exc)
        finally:
            q.put(_DONE)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    loop = asyncio.get_running_loop()
    while True:
        item = await loop.run_in_executor(None, q.get)
        if item is _DONE:
            break
        if isinstance(item, Exception):
            raise item
        yield item


async def _stream_claude_courtroom(
    system_prompt: str,
    messages: List[Dict[str, str]],
) -> AsyncGenerator[str, None]:
    """Stream tokens from Anthropic Claude for courtroom."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    async with client.messages.stream(
        model=settings.ANTHROPIC_MODEL or "claude-haiku-4-5",
        max_tokens=1000,
        system=system_prompt,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield text


def _get_courtroom_stream(
    system_prompt: str,
    messages: List[Dict[str, str]],
) -> AsyncGenerator[str, None]:
    if settings.AI_PROVIDER == "gemini":
        return _stream_gemini_courtroom(system_prompt, messages)
    return _stream_claude_courtroom(system_prompt, messages)


async def _call_ai_sync(system_prompt: str, user_message: str) -> str:
    """Non-streaming AI call — used for weak-points analysis after session ends."""
    if settings.AI_PROVIDER == "gemini":
        return await _call_gemini_sync(system_prompt, user_message)
    return await _call_claude_sync(system_prompt, user_message)


async def _call_gemini_sync(system_prompt: str, user_message: str) -> str:
    from google import genai
    from google.genai import types as genai_types

    model = settings.GEMINI_MODEL or "gemini-2.5-flash"
    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    loop = asyncio.get_running_loop()

    def _run():
        resp = client.models.generate_content(
            model=model,
            contents=user_message,
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=1500,
            ),
        )
        return resp.text or ""

    return await loop.run_in_executor(None, _run)


async def _call_claude_sync(system_prompt: str, user_message: str) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = await client.messages.create(
        model=settings.ANTHROPIC_MODEL or "claude-haiku-4-5",
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


# ============================================================
# SERVICE
# ============================================================


class CourtroomService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------
    # ACCESS HELPERS
    # ------------------------------------------------------------

    async def _get_session(self, session_id: uuid.UUID) -> CourtroomSession:
        stmt = select(CourtroomSession).where(CourtroomSession.id == session_id)
        session = (await self.db.execute(stmt)).scalar_one_or_none()
        if not session:
            raise NotFoundError("Session not found")
        return session

    def _require_owner(self, session: CourtroomSession, user_id: uuid.UUID):
        if session.lawyer_id != user_id:
            raise ForbiddenError("Not authorized")

    # ------------------------------------------------------------
    # CREATE
    # ------------------------------------------------------------

    async def create_session(
        self,
        case_id: uuid.UUID,
        lawyer_id: uuid.UUID,
        session_type: SessionType,
        title: str = None,
    ) -> CourtroomSession:
        session = CourtroomSession(
            case_id=case_id,
            lawyer_id=lawyer_id,
            session_type=session_type,
            title=title
            or (
                "Practice with Judge"
                if session_type == SessionType.judge
                else "Practice with Opposing Counsel"
            ),
            messages=[],
            turn_count=0,
            status=SessionStatus.active,
        )
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    # ------------------------------------------------------------
    # READ
    # ------------------------------------------------------------

    async def get_session(self, session_id: uuid.UUID, user_id: uuid.UUID):
        session = await self._get_session(session_id)
        self._require_owner(session, user_id)
        return session

    async def get_sessions_for_case(
        self,
        case_id: uuid.UUID,
        user_id: uuid.UUID,
        page: int = 1,
        limit: int = 20,
    ):
        stmt = (
            select(CourtroomSession)
            .where(
                CourtroomSession.case_id == case_id,
                CourtroomSession.lawyer_id == user_id,
            )
            .order_by(CourtroomSession.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars())

    # ------------------------------------------------------------
    # STREAM MESSAGE
    # ------------------------------------------------------------

    async def send_message_stream(
        self,
        session_id: uuid.UUID,
        content: str,
        user_id: uuid.UUID,
    ) -> AsyncGenerator[str, None]:
        session = await self._get_session(session_id)
        self._require_owner(session, user_id)

        if session.status != SessionStatus.active:
            raise ValidationError("Session is not active")

        if session.turn_count >= MAX_TURNS:
            raise ValidationError("Session turn limit reached (100 turns max)")

        # Append user message
        messages = list(session.messages)
        messages.append(
            {
                "role": "lawyer",
                "content": content,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        session.messages = list(messages)  # new list — break any shared reference
        flag_modified(session, "messages")
        session.turn_count += 1
        await self.db.flush()  # persist user message before AI call

        # Build system prompt
        prompt_file = (
            "judge.txt"
            if session.session_type == SessionType.judge
            else "opposing_counsel.txt"
        )
        system_template = _load_prompt(prompt_file)

        summary = (
            await self.db.execute(
                select(CaseSummary).where(CaseSummary.case_id == session.case_id)
            )
        ).scalar_one_or_none()

        context = (
            await self.db.execute(
                select(CaseContext).where(CaseContext.case_id == session.case_id)
            )
        ).scalar_one_or_none()

        system_prompt = system_template.replace(
            "{{case_summary}}",
            summary.summary_text if summary else "No summary available.",
        ).replace(
            "{{case_context}}",
            json.dumps(context.context_json, ensure_ascii=False) if context else "{}",
        )

        # Build Anthropic-format messages (last MAX_CONTEXT_MESSAGES only)
        anthropic_messages = [
            {
                "role": "user" if m["role"] == "lawyer" else "assistant",
                "content": m["content"],
            }
            for m in messages[-MAX_CONTEXT_MESSAGES:]
        ]

        full_response = ""

        try:
            async for text in _get_courtroom_stream(system_prompt, anthropic_messages):
                full_response += text
                yield f"data: {json.dumps({'text': text})}\n\n"

        except Exception:
            logger.exception("Courtroom streaming failed")
            await self.db.rollback()
            yield "event: error\ndata: Stream failed\n\n"
            return

        # Persist AI reply — use a fresh list to ensure SQLAlchemy detects the change
        messages.append(
            {
                "role": "ai",
                "content": full_response,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        session.messages = list(messages[-MAX_CONTEXT_MESSAGES:])
        flag_modified(session, "messages")
        await self.db.commit()

        yield "data: [DONE]\n\n"

    # ------------------------------------------------------------
    # END SESSION + WEAK POINTS
    # ------------------------------------------------------------

    async def end_session(self, session_id: uuid.UUID, user_id: uuid.UUID):
        session = await self._get_session(session_id)
        self._require_owner(session, user_id)

        if session.status != SessionStatus.active:
            return session  # already ended — idempotent

        session.status = SessionStatus.completed

        # Build conversation transcript
        transcript = "\n\n".join(
            f"{'LAWYER' if m['role'] == 'lawyer' else 'AI OPPONENT'}: {m['content']}"
            for m in session.messages
        )

        # Fetch case context for better analysis
        summary = (
            await self.db.execute(
                select(CaseSummary).where(CaseSummary.case_id == session.case_id)
            )
        ).scalar_one_or_none()

        system_prompt = (
            "You are a senior advocate and legal coach at the Allahabad High Court.\n"
            "Analyze the following argument practice session transcript.\n"
            "Identify 3–6 specific weak points in the LAWYER's arguments or submissions.\n"
            "For each weak point, provide a concrete improvement suggestion.\n\n"
            "Case Summary:\n"
            f"{summary.summary_text if summary else 'Not available.'}\n\n"
            "Return ONLY a valid JSON array (no markdown, no explanation) in this exact format:\n"
            '[{"point": "...", "suggestion": "..."}, ...]'
        )

        try:
            raw = await _call_ai_sync(
                system_prompt, f"Session Transcript:\n\n{transcript}"
            )

            # Strip any markdown code fences the AI might add
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            parsed: List[Dict[str, Any]] = json.loads(raw)
            for wp in parsed:
                wp["identified_at"] = datetime.utcnow().isoformat()

            session.weak_points_identified = parsed

        except Exception:
            logger.exception("Weak-point generation failed")
            session.weak_points_identified = []

        await self.db.commit()
        await self.db.refresh(session)
        return session
