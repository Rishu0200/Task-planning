import logging
import os
import re
import uuid
from typing import Any, Dict, List

from .config import settings
from .doc_builder import build_document
from .llm_client import llm_client
from .schemas import Task

logger = logging.getLogger("agent.core")

PLANNER_SYSTEM_PROMPT = """You are an autonomous business-document planning agent.
Given a user's natural language request, decide:
1. The most appropriate document type. Choose one of:
   "Business Proposal", "Meeting Minutes", "Project Plan", "Standard Operating Procedure",
   "Technical Design Document", "Product Specification", "Business Report".
2. A concise, professional document title.
3. The intended audience.
4. An ordered list of 5-9 sections that this document type should contain, each with a short
   one-sentence "purpose" describing what that section should cover.

Respond with ONLY a JSON object, no prose, no markdown fences, in exactly this shape:
{
  "document_type": "...",
  "title": "...",
  "audience": "...",
  "sections": [{"heading": "...", "purpose": "..."}, ...]
}
"""

SECTION_SYSTEM_PROMPT = """You are an autonomous business-document writing agent.
Write the content for ONE section of a larger document. Use professional business language.
Where real data is not available (figures, names, dates), invent clearly plausible mock data
rather than leaving placeholders like "[TBD]".

If the section is naturally tabular (e.g. action items, attendees, timeline, budget), include a
"table" object with "headers" and "rows". Otherwise set "table" to null.

Respond with ONLY a JSON object, no prose, no markdown fences, in exactly this shape:
{
  "heading": "...",
  "paragraphs": ["...", "..."],
  "bullets": ["...", "..."],
  "table": {"headers": ["..."], "rows": [["...", "..."]]} or null
}
"""

SECTION_REFLECTION_SYSTEM_PROMPT = """You are a strict editor reviewing ONE section of a business document before
it ships. Check the drafted content against its stated purpose. Flag it as failing if it:
- is empty, trivially short, or generic filler that doesn't address the stated purpose
- contains placeholder text such as "TBD", "[insert ...]", "lorem ipsum", or similar
- should obviously be a table (action items, attendees, timeline, budget, schedule) but has no table
- is off-topic relative to the section's purpose and the overall document

Respond with ONLY a JSON object, no prose, no markdown fences, in exactly this shape:
{"passes": true or false, "issues": ["short issue 1", "short issue 2"]}
If there are no issues, return {"passes": true, "issues": []}.
"""

SUMMARY_SYSTEM_PROMPT = """You are an autonomous agent reporting back to the user on a completed task.
Write a concise 2-4 sentence summary of the document you produced, in a professional tone.
Respond with ONLY a JSON object: {"summary": "..."}
"""


class Agent:
    def __init__(self):
        self.tasks: List[Task] = []
        self._next_id = 1

    
    def _new_task(self, name: str) -> Task:
        t = Task(id=self._next_id, name=name, status="pending")
        self._next_id += 1
        self.tasks.append(t)
        return t

    def _set(self, task: Task, status: str, detail: str = None):
        task.status = status
        if detail:
            task.detail = detail

    
    def run(self, request: str) -> Dict[str, Any]:
        plan_task = self._new_task("Analyze request and generate document outline")
        self._set(plan_task, "in_progress")
        try:
            outline = llm_client.chat_json(
                PLANNER_SYSTEM_PROMPT,
                f"User request: {request}",
                task_type="outline",
                context={"request": request},
            )
            document_type = outline.get("document_type", "Business Report")
            title = outline.get("title") or document_type
            audience = outline.get("audience", "Key stakeholders")
            sections_plan = outline.get("sections") or []
            if not sections_plan:
                raise ValueError("Planner returned no sections")
            self._set(
                plan_task, "done",
                detail=f"Classified as '{document_type}' with {len(sections_plan)} planned sections.",
            )
        except Exception as exc:  # noqa: BLE001
            self._set(plan_task, "failed", detail=str(exc))
            logger.error("Planning failed: %s", exc)
            # last-resort minimal plan so the agent still produces *something*
            document_type = "Business Report"
            title = request[:80] or "Untitled Document"
            audience = "Key stakeholders"
            sections_plan = [
                {"heading": "Overview", "purpose": "Summary of the request"},
                {"heading": "Details", "purpose": "Details relevant to the request"},
                {"heading": "Next Steps", "purpose": "Recommended next steps"},
            ]

        
        section_tasks: List[Task] = []
        for s in sections_plan:
            t = self._new_task(f"Draft section: {s.get('heading', 'Section')}")
            section_tasks.append(t)

        generated_sections: List[Dict[str, Any]] = []
        for s, task in zip(sections_plan, section_tasks):
            self._set(task, "in_progress")
            heading = s.get("heading", "Section")
            purpose = s.get("purpose", "")
            base_user_prompt = (
                f"Document type: {document_type}\n"
                f"Document title: {title}\n"
                f"Original user request: {request}\n"
                f"Section heading: {heading}\n"
                f"Section purpose: {purpose}\n"
            )
            try:
                content = llm_client.chat_json(
                    SECTION_SYSTEM_PROMPT,
                    base_user_prompt,
                    task_type="section",
                    context={"request": request, "document_type": document_type, "heading": heading, "purpose": purpose},
                )
                content.setdefault("heading", heading)

                review = llm_client.chat_json(
                    SECTION_REFLECTION_SYSTEM_PROMPT,
                    f"{base_user_prompt}\nDrafted content:\n{content}\n",
                    task_type="reflection",
                    context={"heading": heading, "purpose": purpose, "content": content},
                )
                if not review.get("passes", True):
                    issues = review.get("issues", []) or ["Did not meet quality bar."]
                    logger.info("Self-check flagged section '%s': %s -> revising once.", heading, issues)
                    revised = llm_client.chat_json(
                        SECTION_SYSTEM_PROMPT,
                        base_user_prompt
                        + "\nA reviewer flagged the previous draft with these issues, fix them:\n- "
                        + "\n- ".join(issues),
                        task_type="section",
                        context={"request": request, "document_type": document_type, "heading": heading, "purpose": purpose},
                    )
                    revised.setdefault("heading", heading)
                    content = revised
                    self._set(task, "done", detail=f"Revised after self-check ({'; '.join(issues)})")
                else:
                    self._set(task, "done", detail="Self-check passed on first draft")

                generated_sections.append(content)
            except Exception as exc:  # noqa: BLE001
                logger.error("Section '%s' failed: %s", heading, exc)
                self._set(task, "failed", detail=str(exc))
                # degrade gracefully: still include a minimal section
                generated_sections.append({
                    "heading": heading,
                    "paragraphs": [f"Content for this section could not be generated automatically ({purpose})."],
                    "bullets": [],
                    "table": None,
                })

        assemble_task = self._new_task("Assemble content into Word document")
        self._set(assemble_task, "in_progress")
        filename = f"{uuid.uuid4().hex[:10]}_{_slugify(title)}.docx"
        output_path = os.path.join(settings.OUTPUT_DIR, filename)
        try:
            build_document(
                title=title,
                document_type=document_type,
                audience=audience,
                sections=generated_sections,
                output_path=output_path,
            )
            self._set(assemble_task, "done", detail=filename)
        except Exception as exc:  # noqa: BLE001
            self._set(assemble_task, "failed", detail=str(exc))
            logger.error("Document assembly failed: %s", exc)
            raise

        summary_task = self._new_task("Generate final summary")
        self._set(summary_task, "in_progress")
        try:
            summary_data = llm_client.chat_json(
                SUMMARY_SYSTEM_PROMPT,
                f"Document type: {document_type}\nTitle: {title}\nSections: "
                f"{[sec.get('heading') for sec in generated_sections]}",
                task_type="summary",
                context={"document_type": document_type, "n_sections": len(generated_sections)},
            )
            summary = summary_data.get("summary", "Document generated successfully.")
            self._set(summary_task, "done")
        except Exception as exc:  # noqa: BLE001
            self._set(summary_task, "failed", detail=str(exc))
            summary = (
                f"Generated a '{document_type}' titled '{title}' with "
                f"{len(generated_sections)} sections."
            )

        failed_sections = [t for t in section_tasks if t.status == "failed"]
        overall_status = "completed_with_errors" if failed_sections or plan_task.status == "failed" else "completed"

        return {
            "status": overall_status,
            "document_type": document_type,
            "title": title,
            "plan": self.tasks,
            "summary": summary,
            "llm_provider_used": llm_client.last_provider_used,
            "filename": filename,
            "output_path": output_path,
        }


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return slug[:40] or "document"
