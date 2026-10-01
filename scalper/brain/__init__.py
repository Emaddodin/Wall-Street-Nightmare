"""
scalper.brain
=============
Intelligence layer for Stratton Oakmont XAUUSD Scalping Engine:
- Laya Non-Autoregressive System 1 Decision Oracle
- Semantic ICT Knowledge RAG Indexer (288 concepts)
- Real-Time Macro & Economic News Watchdog
"""

from scalper.brain.laya_oracle import LayaOracle, get_laya_oracle, LayaDecision
from scalper.brain.ict_rag import ICTKnowledgeRAG, get_ict_rag
from scalper.brain.macro_watchdog import MacroWatchdog, get_macro_watchdog

__all__ = [
    "LayaOracle",
    "get_laya_oracle",
    "LayaDecision",
    "ICTKnowledgeRAG",
    "get_ict_rag",
    "MacroWatchdog",
    "get_macro_watchdog",
]
