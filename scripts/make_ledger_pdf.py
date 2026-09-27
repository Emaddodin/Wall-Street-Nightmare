"""
Build the Apex daily compounding ledger PDF from a live-loop-fidelity backtest run (same columns as the original ledger).

    python3 scripts/make_ledger_pdf.py --tag rep60_atr15 --base rep60_base --out reports/apex_ledger_real.pdf --data-note "..."
"""
import argparse
import json
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
NAVY = colors.HexColor("#1b2a49")


def money(x):
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def load(tag):
    s = json.load(open(ROOT / f"data/{tag}_summary.json"))
    d = pd.read_csv(ROOT / f"data/{tag}_daily.csv")
    j = pd.read_csv(ROOT / f"data/{tag}_journal.csv")
    return s, d, j


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True); ap.add_argument("--base", default=None)
    ap.add_argument("--out", default="reports/apex_ledger_real.pdf")
    ap.add_argument("--title", default="STRATTON OAKMONT — APEX DAILY COMPOUNDING LEDGER (REAL CANDLES)")
    ap.add_argument("--data-note", default="")
    ap.add_argument("--filter-note", default="Apex production exit chain, entry ATR filter >= 1.5")
    a = ap.parse_args()
    s, d, j = load(a.tag)
    out = ROOT / a.out; out.parent.mkdir(parents=True, exist_ok=True)
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=17, textColor=NAVY, alignment=0, spaceAfter=4)
    sub = ParagraphStyle("sub", parent=ss["Normal"], fontSize=9, textColor=colors.HexColor("#555555"), spaceAfter=8)
    body = ParagraphStyle("body", parent=ss["Normal"], fontSize=8.6, leading=11.5)
    doc = SimpleDocTemplate(str(out), pagesize=landscape(letter), leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    el = []
    first, last = d.date.iloc[0], d.date.iloc[-1]
    final_bal, vault = s["final_retained_equity_usd"], s["total_cash_withdrawn_usd"]
    el += [Paragraph(a.title, h1), Paragraph(f"{first} to {last} · {len(d)} trading days · initial seed ${s['starting_capital']:,.2f} · {a.filter_note}", sub)]
    tiles = Table([[money(s["starting_capital"]), money(final_bal), money(vault), money(final_bal + vault)],
                   ["INITIAL CAPITAL", "FINAL BALANCE", "VAULTED CASH", "TOTAL WEALTH"]], colWidths=[2.4 * inch] * 4)
    tiles.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, 0), 15), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("TEXTCOLOR", (0, 0), (-1, 0), NAVY),
                               ("FONTSIZE", (0, 1), (-1, 1), 7.5), ("TEXTCOLOR", (0, 1), (-1, 1), colors.grey), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                               ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f2f4f8")), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9ced8"))]))
    el += [tiles, Spacer(1, 10)]

    rows = [["Metric", "Value"],
            ["Trades", f"{s['total_trades_logged']:,}"], ["Win rate", f"{s['overall_win_rate_pct']}%"], ["Profit factor", f"{s['profit_factor']}"],
            ["Expectancy / trade", money(s["expectancy_usd"])], ["Max drawdown", f"{s['max_drawdown_pct']}%"],
            ["Profitable days / losing days", f"{s['profitable_days']} / {s['losing_days']}"],
            ["Wealth multiple (retained + vaulted)", f"{(final_bal + vault) / s['starting_capital']:.2f}x"]]
    if a.base:
        b, _, _ = load(a.base)
        rows += [["", ""], ["Same engine WITHOUT the ATR filter", ""], ["  trades / win rate / PF", f"{b['total_trades_logged']} / {b['overall_win_rate_pct']}% / {b['profit_factor']}"],
                 ["  final + vaulted", f"{money(b['final_retained_equity_usd'])} + {money(b['total_cash_withdrawn_usd'])}"], ["  max drawdown", f"{b['max_drawdown_pct']}%"]]
    t = Table(rows, colWidths=[3.6 * inch, 3.2 * inch])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f7fa")]), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d5d9e2"))]))
    ex = ", ".join(f"{k} {v}" for k, v in sorted(s["exit_breakdown"].items(), key=lambda kv: -kv[1]))
    note = (f"<b>Exit mix:</b> {ex}.<br/><br/><b>Data:</b> {a.data_note}<br/><br/>"
            "<b>Method:</b> tests/backtest_live_loop_trump_regime.py replays the production Apex entry engine and the production exit chain (MicroExitController, "
            "breakeven and TP1 ratchets, structure invalidation, macro spike harvest, Friday flatten) on 1-minute candles, with a $0.30 spread and $0.10 slippage "
            "modelled, the live lot ladder, and the daily withdrawal rule (30% of a profitable day above $100 equity, 50% above $1,000, 70% above $5,000). "
            "It is not a guarantee: broker fills, latency and slippage on tight stops can be worse than modelled, and results depend on the data window.")
    el += [Table([[t, Paragraph(note, body)]], colWidths=[7.0 * inch, 3.6 * inch], style=[("VALIGN", (0, 0), (-1, -1), "TOP")]), PageBreak()]

    hdr = ["Day #", "Date", "Start Balance", "Day Realized PnL", "Vaulted Today", "Cumulative Vaulted", "Trades"]
    data = [hdr] + [[int(r.day_num), r.date, money(r.start_balance), money(r.day_pnl), money(r.withdrawn_today), money(r.cumulative_withdrawn), int(r.trades_count)] for r in d.itertuples()]
    tbl = Table(data, repeatRows=1, colWidths=[0.7 * inch, 1.1 * inch, 1.5 * inch, 1.7 * inch, 1.4 * inch, 1.8 * inch, 0.8 * inch])
    st = [("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
          ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d5d9e2"))]
    for i, r in enumerate(d.itertuples(), start=1):
        if r.day_pnl > 0:
            st.append(("BACKGROUND", (3, i), (3, i), colors.HexColor("#e2f0d9")))
        elif r.day_pnl < 0:
            st.append(("BACKGROUND", (3, i), (3, i), colors.HexColor("#fce4d6")))
    tbl.setStyle(TableStyle(st))
    el.append(tbl)
    doc.build(el)
    print("wrote", out)


if __name__ == "__main__":
    main()
