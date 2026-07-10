import json
import logging
import random
import re
from datetime import datetime
from typing import Any, Dict

import requests

from .config import settings

logger = logging.getLogger("agent.llm")


def _extract_json(text: str) -> Dict[str, Any]:
    """Best-effort extraction of a JSON object from an LLM text response."""
    text = text.strip()
    # strip markdown code fences if present
    text = re.sub(r"^```(json)?", "", text.strip(), flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text.strip()).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start:end + 1]
        return json.loads(candidate)
    raise ValueError("Could not extract JSON from LLM response")


class LLMClient:
    def __init__(self):
        self.provider = settings.LLM_PROVIDER
        self.last_provider_used = self.provider

    
    def chat_json(self, system: str, user: str, *, task_type: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ask the configured LLM for a JSON object. Falls back to the mock
        generator on any failure so the agent never hard-crashes.
        """
        if self.provider != "mock":
            try:
                raw = self._call_llm(system, user)
                data = _extract_json(raw)
                self.last_provider_used = self.provider
                return data
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "LLM provider '%s' failed (%s). Falling back to offline mock generation.",
                    self.provider, exc,
                )
        self.last_provider_used = "mock"
        return _MockGenerator.generate(task_type, context)


    def _call_llm(self, system: str, user: str) -> str:
        if self.provider == "ollama":
            return self._call_ollama(system, user)
        if self.provider == "groq":
            return self._call_groq(system, user)
        if self.provider == "gemini":
            return self._call_gemini(system, user)
        raise ValueError(f"Unknown LLM_PROVIDER: {self.provider}")

    def _call_ollama(self, system: str, user: str) -> str:
        resp = requests.post(
            f"{settings.OLLAMA_HOST}/api/chat",
            json={
                "model": settings.OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.4},
            },
            timeout=settings.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    def _call_groq(self, system: str, user: str) -> str:
        if not settings.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY not set")
        resp = requests.post(
            settings.GROQ_BASE_URL,
            headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
            json={
                "model": settings.GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.4,
                "response_format": {"type": "json_object"},
            },
            timeout=settings.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def _call_gemini(self, system: str, user: str) -> str:
        if not settings.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not set")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.GEMINI_MODEL}:generateContent?key={settings.GEMINI_API_KEY}"
        )
        resp = requests.post(
            url,
            json={
                "contents": [{"parts": [{"text": f"{system}\n\n{user}"}]}],
                "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"},
            },
            timeout=settings.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]



class _MockGenerator:

    DOC_TYPE_KEYWORDS = [
        (("proposal", "pitch", "bid"), "Business Proposal"),
        (("meeting minutes", "minutes of meeting", "mom"), "Meeting Minutes"),
        (("project plan", "project timeline", "gantt"), "Project Plan"),
        (("sop", "standard operating procedure", "procedure"), "Standard Operating Procedure"),
        (("technical design", "design doc", "architecture"), "Technical Design Document"),
        (("product spec", "specification", "requirements doc", "prd"), "Product Specification"),
        (("business report", "quarterly report", "annual report", "report"), "Business Report"),
    ]

    SECTION_TEMPLATES = {
        "Business Proposal": [
            ("Executive Summary", "High-level overview of the proposal and its value proposition"),
            ("Problem Statement", "The business problem or opportunity being addressed"),
            ("Proposed Solution", "Description of the proposed solution or approach"),
            ("Scope of Work", "What is included and excluded from the engagement"),
            ("Timeline", "Key milestones and delivery schedule"),
            ("Budget & Pricing", "Cost breakdown and commercial terms"),
            ("Why Us", "Relevant experience and differentiators"),
            ("Next Steps", "Immediate actions to move forward"),
        ],
        "Meeting Minutes": [
            ("Meeting Details", "Date, time, location and attendees"),
            ("Agenda", "Topics planned for discussion"),
            ("Discussion Summary", "Key points raised during the meeting"),
            ("Decisions Made", "Decisions reached during the meeting"),
            ("Action Items", "Follow-up tasks, owners and due dates"),
            ("Next Meeting", "Date and focus of the next meeting"),
        ],
        "Project Plan": [
            ("Project Overview", "Objectives, background and success criteria"),
            ("Scope", "In-scope and out-of-scope items"),
            ("Milestones & Timeline", "Key phases and target dates"),
            ("Work Breakdown & Ownership", "Tasks, owners and deadlines"),
            ("Resources & Budget", "Team, tools and budget allocation"),
            ("Risks & Mitigation", "Key risks and mitigation strategies"),
            ("Communication Plan", "Reporting cadence and stakeholders"),
        ],
        "Standard Operating Procedure": [
            ("Purpose", "Why this procedure exists"),
            ("Scope", "Where and to whom this procedure applies"),
            ("Roles & Responsibilities", "Who is responsible for each step"),
            ("Procedure Steps", "Detailed step-by-step instructions"),
            ("Safety & Compliance Notes", "Relevant compliance or safety considerations"),
            ("Revision History", "Document version history"),
        ],
        "Technical Design Document": [
            ("Overview", "Summary of the system and design goals"),
            ("Background & Motivation", "Why this design is needed"),
            ("Architecture", "High-level architecture and components"),
            ("Detailed Design", "Component-level design details"),
            ("Data Model", "Key data structures and storage design"),
            ("API / Interfaces", "External and internal interfaces"),
            ("Risks & Trade-offs", "Alternatives considered and trade-offs"),
            ("Rollout Plan", "Deployment and migration approach"),
        ],
        "Product Specification": [
            ("Overview", "Product summary and goals"),
            ("User Problem", "The problem being solved for users"),
            ("Requirements", "Functional and non-functional requirements"),
            ("User Stories", "Representative user stories / use cases"),
            ("Success Metrics", "How success will be measured"),
            ("Timeline & Milestones", "Delivery schedule"),
            ("Open Questions", "Unresolved questions and risks"),
        ],
        "Business Report": [
            ("Executive Summary", "Top-level summary of findings"),
            ("Background", "Context for the report"),
            ("Key Findings", "Main findings from the analysis"),
            ("Data & Metrics", "Supporting data and metrics"),
            ("Recommendations", "Recommended actions"),
            ("Conclusion", "Closing summary"),
        ],
    }

    MOCK_NAMES = ["A. Sharma", "J. Patel", "M. Chen", "R. Gomez", "S. Khan", "L. Novak", "T. Wright"]
    MOCK_DEPTS = ["Product", "Engineering", "Operations", "Finance", "Sales", "Marketing"]

    @classmethod
    def generate(cls, task_type: str, context: Dict[str, Any]) -> Dict[str, Any]:
        if task_type == "outline":
            return cls._outline(context)
        if task_type == "section":
            return cls._section(context)
        if task_type == "summary":
            return cls._summary(context)
        if task_type == "reflection":
            return cls._reflection(context)
        raise ValueError(f"Unknown mock task_type: {task_type}")

    @classmethod
    def _infer_doc_type(cls, request: str) -> str:
        low = request.lower()
        for keywords, doc_type in cls.DOC_TYPE_KEYWORDS:
            if any(k in low for k in keywords):
                return doc_type
        return "Business Report"

    @classmethod
    def _outline(cls, context: Dict[str, Any]) -> Dict[str, Any]:
        request = context.get("request", "")
        doc_type = cls._infer_doc_type(request)
        sections = cls.SECTION_TEMPLATES.get(doc_type, cls.SECTION_TEMPLATES["Business Report"])

        # Derive a reasonably clean title from the request
        title = request.strip().rstrip(".")
        title = re.sub(r"^(create|write|draft|generate|produce|prepare)\s+(a|an|the)?\s*", "", title, flags=re.IGNORECASE)
        title = title[:1].upper() + title[1:] if title else doc_type
        if len(title) > 90:
            title = title[:87] + "..."

        return {
            "document_type": doc_type,
            "title": title,
            "audience": "Key stakeholders and decision makers",
            "sections": [{"heading": h, "purpose": p} for h, p in sections],
        }

    @classmethod
    def _section(cls, context: Dict[str, Any]) -> Dict[str, Any]:
        heading = context.get("heading", "Section")
        purpose = context.get("purpose", "")
        request = context.get("request", "")
        doc_type = context.get("document_type", "Business Report")
        low_heading = heading.lower()

        rng = random.Random(heading + request)  # deterministic per section

        paragraphs = [
            f"This section covers {purpose.lower()} in the context of: \"{request.strip()[:140]}\".",
            f"Based on the current scope of the {doc_type.lower()}, the {heading.lower()} reflects "
            f"the information available at the time of drafting and should be reviewed by the "
            f"relevant stakeholders before final sign-off.",
        ]

        bullets = []
        table = None

        if any(k in low_heading for k in ["action item", "task", "work breakdown", "step", "procedure"]):
            table = {
                "headers": ["#", "Item", "Owner", "Due Date", "Status"],
                "rows": [
                    [str(i + 1), item, rng.choice(cls.MOCK_NAMES), f"2026-0{rng.randint(7,9)}-{rng.randint(10,28)}", rng.choice(["Not Started", "In Progress", "Done"])]
                    for i, item in enumerate([
                        "Confirm requirements with stakeholders",
                        "Prepare draft deliverable",
                        "Review and incorporate feedback",
                        "Finalize and distribute",
                    ])
                ],
            }
        elif any(k in low_heading for k in ["attendee", "meeting details"]):
            table = {
                "headers": ["Name", "Role/Department"],
                "rows": [[n, rng.choice(cls.MOCK_DEPTS)] for n in rng.sample(cls.MOCK_NAMES, 4)],
            }
            paragraphs = [
                f"Date: 2026-07-{rng.randint(1,9):02d}    Time: {rng.choice(['10:00 AM','2:00 PM','4:00 PM'])}    "
                f"Location: Conference Room / Virtual Call",
            ]
        elif any(k in low_heading for k in ["milestone", "timeline", "schedule"]):
            table = {
                "headers": ["Milestone", "Target Date", "Owner"],
                "rows": [
                    ["Kickoff", "2026-07-15", rng.choice(cls.MOCK_NAMES)],
                    ["Draft Complete", "2026-08-01", rng.choice(cls.MOCK_NAMES)],
                    ["Review & Sign-off", "2026-08-15", rng.choice(cls.MOCK_NAMES)],
                    ["Final Delivery", "2026-08-30", rng.choice(cls.MOCK_NAMES)],
                ],
            }
        elif any(k in low_heading for k in ["budget", "pricing", "resources & budget", "cost"]):
            table = {
                "headers": ["Item", "Estimated Cost (USD)"],
                "rows": [
                    ["Labor / Professional Services", "$18,500"],
                    ["Tools & Licenses", "$2,300"],
                    ["Contingency (10%)", "$2,080"],
                    ["Total", "$22,880"],
                ],
            }
        else:
            bullets = [
                f"Aligns with the overall goal of: {request.strip()[:80]}",
                f"Owned by the {rng.choice(cls.MOCK_DEPTS)} team",
                "To be validated with subject-matter experts before finalization",
            ]

        return {"heading": heading, "paragraphs": paragraphs, "bullets": bullets, "table": table}

    _PLACEHOLDER_MARKERS = (
        "tbd", "lorem ipsum", "placeholder", "[insert", "n/a n/a", "xxx", "todo:",
        "as an ai", "i cannot", "i'm unable",
    )

    @classmethod
    def _reflection(cls, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Rule-based self-check used when no real LLM is configured. Real
        providers get an LLM-judged critique instead (see SECTION_REFLECTION
        prompt in agent.py); this mirrors the same criteria mechanically so
        the pipeline behaves consistently in mock mode.
        """
        heading = context.get("heading", "")
        purpose = (context.get("purpose") or "").lower()
        content = context.get("content") or {}
        paragraphs = content.get("paragraphs") or []
        bullets = content.get("bullets") or []
        table = content.get("table")

        issues = []
        combined_text = " ".join(paragraphs + bullets).lower()

        if not paragraphs and not bullets and not table:
            issues.append("Section has no content at all.")

        for marker in cls._PLACEHOLDER_MARKERS:
            if marker in combined_text:
                issues.append(f"Contains placeholder/unfinished language: '{marker}'.")
                break

        if paragraphs and all(len(p.strip()) < 15 for p in paragraphs):
            issues.append("Paragraph text is too thin to be useful.")

        wants_table = any(
            k in purpose or k in heading.lower()
            for k in ["action item", "attendee", "timeline", "milestone", "budget", "schedule", "task"]
        )
        if wants_table and not table:
            issues.append("Section purpose implies tabular data but no table was included.")

        return {"passes": len(issues) == 0, "issues": issues}

    @classmethod
    def _summary(cls, context: Dict[str, Any]) -> Dict[str, Any]:
        doc_type = context.get("document_type", "document")
        n_sections = context.get("n_sections", 0)
        return {
            "summary": (
                f"The agent analyzed the request, classified it as a '{doc_type}', autonomously "
                f"planned a {n_sections}-section outline, generated content for every section "
                f"(using mock data where real data was unavailable), and compiled a polished "
                f".docx document."
            )
        }


llm_client = LLMClient()
