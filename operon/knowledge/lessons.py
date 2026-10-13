"""Cross-run memory: operational lessons the operator learned the hard way.

When the operator recovers from a non-obvious failure (a date format a form
insists on, a page that needs a filter first), it records a short lesson. Future
runs retrieve relevant lessons during intake, so the same mistake is not repeated.
Lessons are plain JSONL, reviewable and editable by people.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .store import BM25, tokenize


class LessonStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def add(self, system: str, lesson: str, run_id: str) -> bool:
        lesson = lesson.strip()
        existing = {(_norm(item["lesson"])) for item in self.all()}
        if _norm(lesson) in existing:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"system": system, "lesson": lesson, "run_id": run_id,
                                 "recorded_at": datetime.now().isoformat(timespec="seconds")}) + "\n")
        return True

    def relevant(self, query: str, k: int = 6) -> list[dict]:
        lessons = self.all()
        if not lessons:
            return []
        index = BM25([tokenize(f"{item['system']} {item['lesson']}") for item in lessons])
        scores = index.scores(tokenize(query))
        ranked = sorted(range(len(lessons)), key=lambda i: scores[i], reverse=True)
        # Lessons are short and few; include weak matches too, best first.
        return [lessons[i] for i in ranked[:k] if scores[i] > 0] or lessons[-k:]


def _norm(text: str) -> str:
    return " ".join(text.lower().split())
