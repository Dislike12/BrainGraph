from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from braingraph.database.models import CodeFile, Relation, Summary, Symbol
from braingraph.memory.vector_store import VectorMemory, tokenize
from braingraph.parser.utils import estimate_tokens


class RetrievalEngine:
    def __init__(self, session: Session, project_id: int, memory: VectorMemory) -> None:
        self.session = session
        self.project_id = project_id
        self.memory = memory

    def retrieve(self, query: str, limit: int = 8) -> dict:
        vector_hits = self.memory.search(query, limit=limit)
        file_scores: dict[str, float] = defaultdict(float)
        for index, hit in enumerate(vector_hits):
            path = str(hit["file_path"])
            if path:
                file_scores[path] += float(hit["score"]) + max(0.0, (limit - index) * 0.05)
        for path, score in self._keyword_scores(query).items():
            file_scores[path] += score
        file_paths = {
            path
            for path, _score in sorted(file_scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        }
        related = self._expand_related(file_paths)
        file_paths.update(related)
        summaries = self.session.scalars(
            select(Summary).join(CodeFile).where(
                Summary.project_id == self.project_id,
                CodeFile.path.in_(list(file_paths)) if file_paths else False,
            )
        ).all()
        summary_by_file = {summary.file.path: summary.summary for summary in summaries}
        raw_tokens = sum(
            file.token_estimate
            for file in self.session.scalars(
                select(CodeFile).where(CodeFile.project_id == self.project_id)
            )
        )
        budget = self._context_budget(raw_tokens, len(file_paths), len(vector_hits))
        context_parts: list[str] = []
        used_tokens = 0
        for path in sorted(file_paths):
            if path in summary_by_file:
                part = f"{path}: {summary_by_file[path]}"
                cost = estimate_tokens(part)
                if used_tokens + cost > budget and context_parts:
                    continue
                context_parts.append(part)
                used_tokens += cost
        include_snippets = raw_tokens >= 100 or not context_parts
        for hit in vector_hits[: max(1, min(3, limit // 2 or 1))]:
            if not include_snippets:
                break
            snippet = self._compact_chunk(str(hit["file_path"]), str(hit["content"]))
            cost = estimate_tokens(snippet)
            if used_tokens + cost > budget and context_parts:
                break
            context_parts.append(snippet)
            used_tokens += cost
        context = "\n".join(context_parts)
        return {
            "query": query,
            "files": sorted(file_paths),
            "chunks": vector_hits,
            "context": context,
            "raw_tokens": raw_tokens,
            "context_tokens": estimate_tokens(context) if context else 0,
        }

    def _keyword_scores(self, query: str) -> dict[str, float]:
        terms = [term for term in tokenize(query) if len(term) > 1]
        if not terms:
            return {}
        files = self.session.scalars(
            select(CodeFile).where(CodeFile.project_id == self.project_id)
        ).all()
        file_scores: dict[str, float] = {}
        for file in files:
            haystack = f"{file.path} {file.language}".lower()
            score = sum(1.2 for term in terms if term in haystack)
            if score:
                file_scores[file.path] = file_scores.get(file.path, 0.0) + score
        for summary in self.session.scalars(
            select(Summary).where(Summary.project_id == self.project_id)
        ):
            score = sum(0.9 for term in terms if term in summary.summary.lower())
            if score:
                file_scores[summary.file.path] = file_scores.get(summary.file.path, 0.0) + score
        symbols = self.session.scalars(
            select(Symbol).where(Symbol.project_id == self.project_id)
        ).all()
        for symbol in symbols:
            haystack = f"{symbol.name} {symbol.kind} {symbol.signature or ''}".lower()
            score = sum(1.1 for term in terms if term in haystack)
            if score:
                file_scores[symbol.file.path] = file_scores.get(symbol.file.path, 0.0) + score
        return file_scores

    def _context_budget(self, raw_tokens: int, file_count: int, hit_count: int) -> int:
        if raw_tokens <= 0:
            return 0
        base = max(24, min(220, raw_tokens // 2))
        return max(base, min(320, 18 * max(1, file_count) + 16 * max(1, hit_count)))

    def _compact_chunk(self, file_path: str, content: str) -> str:
        snippet = " ".join(line.strip() for line in content.splitlines() if line.strip())
        if len(snippet) > 140:
            snippet = snippet[:137].rstrip() + "..."
        return f"{file_path} snippet: {snippet}"

    def _expand_related(self, file_paths: set[str]) -> set[str]:
        if not file_paths:
            return set()
        relations = self.session.scalars(
            select(Relation).where(
                Relation.project_id == self.project_id,
                Relation.source_type == "file",
                Relation.source_key.in_(list(file_paths)),
            )
        ).all()
        related: set[str] = set()
        for relation in relations:
            if relation.target_type == "file":
                related.add(relation.target_key)
        return related
