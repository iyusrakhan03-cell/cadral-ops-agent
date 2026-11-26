"""Self-improving loop, retrieval side: find human-corrected replies to questions similar to this one
and hand them to the answering agent as examples. (Day 3 adds the dashboard actions that create them.)"""
from __future__ import annotations

import numpy as np
from sqlalchemy.orm import Session

from .models import Feedback

MIN_SIMILARITY = 0.80
MAX_EXAMPLES = 3


def examples_for(session: Session, question: str) -> list[dict]:
    rows = (session.query(Feedback).filter(Feedback.action == "edit", Feedback.final_reply.isnot(None),
                                           Feedback.agent == "support")
            .order_by(Feedback.created_at.desc()).limit(200).all())
    if not rows:
        return []
    from .rag.index import embed_passages, embed_query
    q = embed_query(question)
    sims = embed_passages([r.question for r in rows]) @ q
    best = [rows[i] for i in np.argsort(-sims)[:MAX_EXAMPLES] if sims[i] >= MIN_SIMILARITY]
    for r in best:
        r.times_used += 1
    return [{"question": r.question, "answer": r.final_reply} for r in best]
