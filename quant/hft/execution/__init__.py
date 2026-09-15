"""
Execution module: Avellaneda-Stoikov market-making & Square-Root Law order splitting.
"""
from .avellaneda_stoikov import AvellanadaStoikov, ReservationQuote
from .order_splitter import SquareRootSplitter, OrderChunk

__all__ = [
    "AvellanadaStoikov",
    "ReservationQuote",
    "SquareRootSplitter",
    "OrderChunk",
]

AvellanedaStoikov = AvellanadaStoikov
