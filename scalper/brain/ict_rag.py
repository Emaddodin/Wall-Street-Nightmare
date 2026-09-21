"""
scalper/brain/ict_rag.py
========================
Semantic ICT & Price Action Knowledge RAG Engine.
Indexes and retrieves concepts from the 33-chapter ICT Knowledge Library
(FVG, Order Blocks, Liquidity Sweeps, Killzones, Judas Swings, AMD Cycles, CRT, etc.)
to provide deep institutional context for Laya System 1 decision evaluations.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ict_rag")

ROOT_DIR = Path(__file__).resolve().parents[2]
CONCEPTS_DIR = ROOT_DIR / "scalper" / "learn" / "ict-knowledge-library" / "concepts"


@dataclass
class ICTConcept:
    concept_id: str
    title: str
    category: str
    tags: List[str]
    definition: str
    criteria: str
    machine_criteria: List[Dict[str, str]]
    source_path: str


class ICTKnowledgeRAG:
    """
    In-memory semantic indexer and retriever for ICT concepts.
    Provides sub-millisecond retrieval of matching playbook rules for any market geometry.
    """

    def __init__(self, concepts_dir: Path = CONCEPTS_DIR):
        self.concepts_dir = concepts_dir
        self.concepts: Dict[str, ICTConcept] = {}
        self._index: List[Dict[str, Any]] = []
        self._load_library()

    def _load_library(self) -> None:
        """Parses all markdown concept files in the library."""
        if not self.concepts_dir.exists():
            logger.warning("ICT concepts directory not found at %s", self.concepts_dir)
            return

        count = 0
        for md_file in self.concepts_dir.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8", errors="ignore")
                concept = self._parse_concept(md_file.stem, md_file, content)
                if concept:
                    self.concepts[concept.concept_id] = concept
                    self._index.append({
                        "id": concept.concept_id,
                        "title": concept.title,
                        "category": concept.category,
                        "tags": set(concept.tags),
                        "text_blob": f"{concept.title} {' '.join(concept.tags)} {concept.definition} {concept.criteria}".lower(),
                        "concept": concept,
                    })
                    count += 1
            except Exception as e:
                logger.debug("Failed to parse %s: %s", md_file, e)

        logger.info("📚 Indexed %d institutional ICT concepts into RAG memory.", count)

    def _parse_concept(self, cid: str, path: Path, text: str) -> Optional[ICTConcept]:
        lines = text.splitlines()
        title = cid.replace("-", " ").title()
        category = path.parent.name
        tags = []
        definition = ""
        criteria = ""

        # Extract title from # Header
        for line in lines:
            if line.startswith("# "):
                title = line.replace("# ", "").strip()
                break

        # Extract metadata
        m_tags = re.search(r"\*\*Tags:\*\*\s*(.+)", text, re.IGNORECASE)
        if m_tags:
            tags = [t.strip().lower() for t in m_tags.group(1).split(",") if t.strip()]

        # Extract Definition section
        m_def = re.search(r"## Definition\s*(.*?)(?=##|\Z)", text, re.DOTALL)
        if m_def:
            definition = m_def.group(1).strip()
            # Clean markdown links
            definition = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", definition)
            definition = " ".join(definition.split()[:80])  # keep concise

        # Extract Criteria section
        m_crit = re.search(r"## Formal Criteria\s*(.*?)(?=##|\Z)", text, re.DOTALL)
        if m_crit:
            criteria = m_crit.group(1).strip()
            criteria = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", criteria)
            criteria = " ".join(criteria.split()[:100])

        return ICTConcept(
            concept_id=cid,
            title=title,
            category=category,
            tags=tags,
            definition=definition or title,
            criteria=criteria or "Institutional market geometry validation.",
            machine_criteria=[],
            source_path=str(path),
        )

    def retrieve_context(self, market_state: Dict[str, Any], top_k: int = 3) -> Dict[str, Any]:
        """
        Matches current market geometry against the ICT concept library.
        Returns top relevant concepts and a synthesized context prompt for Laya.
        """
        if not self._index:
            return {"concepts": [], "summary": "Standard S&R Breakout and Retest Geometry"}

        direction = str(market_state.get("direction", "BUY")).upper()
        wick_ratio = float(market_state.get("wick_ratio", 0.50))
        session = str(market_state.get("session", "")).lower()

        # Build query keywords from market state
        keywords = set()
        if direction == "BUY":
            keywords.update(["bullish", "support", "discount", "order-block", "rejection", "fvg", "mss"])
        else:
            keywords.update(["bearish", "resistance", "premium", "order-block", "rejection", "fvg", "mss"])

        if wick_ratio >= 0.45:
            keywords.update(["rejection", "pin", "wick", "sweep", "turtle-soup"])

        if "london" in session:
            keywords.update(["london", "killzone", "judas"])
        elif "ny" in session or "new york" in session:
            keywords.update(["silver-bullet", "ny", "expansion"])

        scored: List[tuple[float, ICTConcept]] = []
        for item in self._index:
            score = 0.0
            item_tags = item["tags"]
            text_blob = item["text_blob"]

            # Tag overlap
            score += len(item_tags.intersection(keywords)) * 3.0

            # Keyword presence in text blob
            for kw in keywords:
                if kw in text_blob:
                    score += 1.0

            # Specific category bonuses
            if "rejection" in item["id"] or "mss" in item["id"] or "fvg" in item["id"]:
                score += 2.0
            if "judas" in item["id"] and "london" in session:
                score += 3.0

            scored.append((score, item["concept"]))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_concepts = [c for s, c in scored[:top_k]]

        # Synthesize concise institutional rule context
        rules = []
        for c in top_concepts:
            rules.append(f"[{c.title}]: {c.definition}")

        summary = " | ".join(rules)

        return {
            "top_concept_ids": [c.concept_id for c in top_concepts],
            "top_concept_titles": [c.title for c in top_concepts],
            "rules_summary": summary,
        }


# Singleton instance
_rag_instance: Optional[ICTKnowledgeRAG] = None


def get_ict_rag() -> ICTKnowledgeRAG:
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = ICTKnowledgeRAG()
    return _rag_instance
