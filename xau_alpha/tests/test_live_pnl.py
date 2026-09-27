"""Regression: a stop-out must be reported as a LOSS from the balance, even if equity was refreshed mid-trade."""
import asyncio, sys
from pathlib import Path
X = Path(__file__).resolve().parents[1]
for p in (X / "lib", X / "cand", X / "live", X / "tests"):
    sys.path.insert(0, str(p))
from replay_parity import FakeGateway          # noqa: E402
from trader import AlphaTrader, TraderConfig   # noqa: E402


def test_stop_out_reports_realized_loss(tmp_path):
    async def run():
        g = FakeGateway(eq=22.02)
        tr = AlphaTrader(g, TraderConfig(module="shift_stack", mirror_start=13.0, state_dir=str(tmp_path)))
        g.set_quote(4171.38, 4171.60, 1.0)
        await tr.refresh_equity()
        o = {"t": 0, "d": 1, "kind": "mkt", "sl": 4169.71, "tmax": 3600_000, "tag": "shift1", "stack": True}
        await tr._open(o, 4171.38, 4171.60, 1.0)
        g.set_quote(4170.40, 4170.62, 20.0)            # mid-trade refresh: equity includes the floating loss
        await tr.refresh_equity()
        g.set_quote(4169.61, 4169.83, 46.0)            # broker SL fills
        await tr._close("sl", 4169.61, 4169.83, 46.0)
        return tr
    tr = asyncio.run(run())
    import json
    rec = [json.loads(l) for l in open(tmp_path / "xau_alpha_trades.jsonl")][-1]
    assert rec["ev"] == "close" and rec["pnl_balance"] < -1.9, rec
    assert tr.losses_today == 1
