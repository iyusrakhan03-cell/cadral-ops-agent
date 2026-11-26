"""Self-improving loop.

1. A human approves, edits or rejects an AI reply in the dashboard -> one `feedback` row.
2. Retrieval side (`examples_for`): when a new question is similar to one a human corrected, the corrected
   answer is handed to the answering agent as an example, so the next reply follows the correction.
3. Training side (`export_dataset`): every correction can be exported as a JSONL re-training set
   (chat format for fine-tuning, plus router-label fixes for the classifier).
"""
from __future__ import annotations

import numpy as np
from sqlalchemy.orm import Session

from .models import Feedback

MIN_SIMILARITY = 0.80
MAX_EXAMPLES = 3


def _corrections(session: Session):
    """Edits, and rejections where the human wrote what the reply should have been."""
    return (session.query(Feedback)
            .filter(Feedback.action.in_(("edit", "reject")), Feedback.final_reply.isnot(None),
                    Feedback.final_reply != "", Feedback.agent == "support"))


def examples_for(session: Session, question: str) -> list[dict]:
    rows = _corrections(session).order_by(Feedback.created_at.desc()).limit(200).all()
    if not rows:
        return []
    from .rag.index import embed_passages, embed_query
    q = embed_query(question)
    sims = embed_passages([r.question for r in rows]) @ q
    best = [rows[i] for i in np.argsort(-sims)[:MAX_EXAMPLES] if sims[i] >= MIN_SIMILARITY]
    for r in best:
        r.times_used += 1
    return [{"question": r.question, "answer": r.final_reply} for r in best]


def export_dataset(session: Session) -> list[dict]:
    """One JSON object per human judgement, ready to append to a fine-tuning set."""
    out = []
    for f in session.query(Feedback).order_by(Feedback.id).all():
        good = f.final_reply if f.action in ("edit", "reject") and f.final_reply else (
            f.ai_reply if f.action == "approve" else None)
        row = {"feedback_id": f.id, "action": f.action, "agent": f.agent, "route_label": f.route_label,
               "corrected_label": f.corrected_label, "reason": f.reason, "created_at": f.created_at.isoformat()}
        if good:
            row["messages"] = [{"role": "user", "content": f.question}, {"role": "assistant", "content": good}]
        if f.action != "approve":
            row["rejected_reply"] = f.ai_reply
        out.append(row)
    return out
