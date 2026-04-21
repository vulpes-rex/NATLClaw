"""Structured handoff context for coordinator task delegation (Move B).

When the coordinator delegates a task to a persona, it can attach a
``HandoffContext`` payload so the receiving agent knows what the delegating
agent found, which files were touched, and what questions remain.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HandoffContext:
    """Structured context passed from one agent to another during task delegation.

    All fields are optional so partial handoffs round-trip cleanly.
    """

    summary: str = ""
    """Short summary of the work done so far (1–3 sentences)."""

    findings: list[str] = field(default_factory=list)
    """Bulleted observations or analysis results."""

    brain_note_ids: list[str] = field(default_factory=list)
    """IDs of brain notes relevant to the handoff (for retrieval context)."""

    files_touched: list[str] = field(default_factory=list)
    """Relative paths of files the delegating persona read or modified."""

    open_questions: list[str] = field(default_factory=list)
    """Unanswered questions the receiving persona should address."""

    recommendations: list[str] = field(default_factory=list)
    """Concrete suggestions from the delegating persona."""

    prior_task_id: str = ""
    """ID of the task this was split from (if any)."""

    prior_persona: str = ""
    """Name of the persona that built this handoff."""

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON persistence."""
        return {
            "summary": self.summary,
            "findings": list(self.findings),
            "brain_note_ids": list(self.brain_note_ids),
            "files_touched": list(self.files_touched),
            "open_questions": list(self.open_questions),
            "recommendations": list(self.recommendations),
            "prior_task_id": self.prior_task_id,
            "prior_persona": self.prior_persona,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HandoffContext":
        """Deserialize from a plain dict; unknown keys are silently ignored."""
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def to_prompt_block(self) -> str:
        """Format as a prompt block injected into the receiving persona's context."""
        if not any([
            self.summary, self.findings, self.brain_note_ids,
            self.files_touched, self.open_questions, self.recommendations,
        ]):
            return ""

        lines = ["== HANDOFF CONTEXT =="]
        if self.prior_persona:
            lines.append(f"From: @{self.prior_persona}")
        if self.prior_task_id:
            lines.append(f"Prior task: {self.prior_task_id}")

        if self.summary:
            lines.append(f"\nSummary: {self.summary}")

        if self.findings:
            lines.append("\nFindings:")
            for f in self.findings:
                lines.append(f"  - {f}")

        if self.files_touched:
            lines.append(f"\nFiles: {', '.join(self.files_touched)}")

        if self.brain_note_ids:
            lines.append(f"Brain notes: {', '.join(self.brain_note_ids)}")

        if self.open_questions:
            lines.append("\nOpen questions:")
            for q in self.open_questions:
                lines.append(f"  ? {q}")

        if self.recommendations:
            lines.append("\nRecommendations:")
            for r in self.recommendations:
                lines.append(f"  → {r}")

        lines.append("== END HANDOFF ==")
        return "\n".join(lines)


def build_handoff_from_delegation(delegation: dict) -> "HandoffContext | None":
    """Extract a HandoffContext from a coordinator delegation JSON block.

    The coordinator delegation JSON may contain any subset of HandoffContext
    fields under top-level keys.  Returns None when no meaningful context
    is present.
    """
    hc = HandoffContext(
        summary=delegation.get("summary", delegation.get("context", "")),
        findings=_as_list(delegation.get("findings")),
        brain_note_ids=_as_list(delegation.get("brain_note_ids")),
        files_touched=_as_list(delegation.get("files", delegation.get("files_touched"))),
        open_questions=_as_list(delegation.get("open_questions")),
        recommendations=_as_list(delegation.get("recommendations")),
        prior_task_id=delegation.get("prior_task_id", ""),
        prior_persona=delegation.get("prior_persona", delegation.get("assigned_to", "")),
    )
    # Only return if there's at least some content
    if any([
        hc.summary, hc.findings, hc.brain_note_ids, hc.files_touched,
        hc.open_questions, hc.recommendations,
    ]):
        return hc
    return None


def _as_list(value) -> list[str]:
    """Coerce None / str / list to a list[str]."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(v) for v in value if v]
