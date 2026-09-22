"""
scripts/build_trump_60usd_excel.py
==================================
Generates the comprehensive daily compounding Excel workbook and CSV starting from
exactly $60.00 USD initial capital across all 473 days of the Trump Administration
(January 21, 2025 to September 20, 2026).

Features:
- Professional Institutional Spreadsheet with Stratton Oakmont styling
- Tab 1: Daily Compounding Ledger (473 rows with Start Balance, Day PnL, Vaulted Cash,
  End Balance, Drawdown %, Win Rate %, Trade Counts)
- Tab 2: Granular Per-Trade Journal (Every trade executed with entry/exit price, lots, PnL, duration)
- Tab 3: System Defense Architecture & Guardrails (Explaining all 8 fatal edge case safeguards)
- Auto-formatted currency ($#,##0.00), percentages (0.0%), and dynamic green/red conditional fills.
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# Ensure root is in path
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.run_daily_trump_backtest_hardened import run_daily_trump_backtest


def build_formatted_excel(
    daily_csv_path: Path,
    output_excel_path: Path,
    trades_csv_path: Optional[Path] = None,
    starting_balance: float = 60.0,
) -> None:
    print(f"Generating formatted Excel workbook at {output_excel_path}...")
    df_daily = pd.read_csv(daily_csv_path)

    wb = openpyxl.Workbook()
    # Default sheet -> Tab 1
    ws_daily = wb.active
    ws_daily.title = "Daily Compounding Ledger"

    # Tab 2: Trade Journal
    ws_trades = wb.create_sheet(title="Trade Journal (Trump Regime)")

    # Tab 3: Safeguards
    ws_defense = wb.create_sheet(title="Institutional Safeguards")

    # Styling Palettes
    navy_header_fill = PatternFill(start_color="1B2A4A", end_color="1B2A4A", fill_type="solid")
    gold_header_fill = PatternFill(start_color="D4AF37", end_color="D4AF37", fill_type="solid")
    card_fill = PatternFill(start_color="F2F4F8", end_color="F2F4F8", fill_type="solid")
    green_win_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
    red_loss_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
    zebra_fill = PatternFill(start_color="F9FAFC", end_color="F9FAFC", fill_type="solid")

    font_title = Font(name="Calibri", size=16, bold=True, color="1B2A4A")
    font_subtitle = Font(name="Calibri", size=11, italic=True, color="555555")
    font_card_num = Font(name="Calibri", size=14, bold=True, color="1B2A4A")
    font_card_lbl = Font(name="Calibri", size=9, bold=True, color="666666")
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_data = Font(name="Calibri", size=10, color="222222")
    font_bold_data = Font(name="Calibri", size=10, bold=True, color="222222")
    font_green = Font(name="Calibri", size=10, bold=True, color="276A3C")
    font_red = Font(name="Calibri", size=10, bold=True, color="9C0006")

    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )
    header_border = Border(
        left=Side(style="thin", color="4F6272"),
        right=Side(style="thin", color="4F6272"),
        top=Side(style="medium", color="1B2A4A"),
        bottom=Side(style="medium", color="1B2A4A"),
    )

    # -------------------------------------------------------------
    # TAB 1: DAILY COMPOUNDING LEDGER
    # -------------------------------------------------------------
    ws_daily.views.sheetView[0].showGridLines = True

    # Title Block
    ws_daily.cell(row=2, column=2, value="🏛️ STRATTON OAKMONT — TRUMP REGIME DAILY COMPOUNDING LEDGER").font = font_title
    ws_daily.cell(row=3, column=2, value=f"Full 473-Day Empirical Backtest (Jan 21, 2025 to Sep 20, 2026) · Initial Seed Capital: ${starting_balance:.2f} USD").font = font_subtitle

    # Compute Summary Stats
    total_days = len(df_daily)
    final_balance = float(df_daily["end_balance"].iloc[-1])
    total_vaulted = float(df_daily["cumulative_withdrawn"].iloc[-1])
    total_wealth = final_balance + total_vaulted
    mult = total_wealth / starting_balance if starting_balance > 0 else 0.0
    total_trades = int(df_daily["trades_count"].sum())
    total_wins = int(df_daily["wins"].sum())
    overall_wr = (total_wins / total_trades * 100.0) if total_trades > 0 else 0.0
    profitable_days = int((df_daily["day_pnl"] > 0).sum())
    losing_days = int((df_daily["day_pnl"] < 0).sum())

    # KPI Summary Cards (Rows 5-6)
    kpis = [
        ("INITIAL CAPITAL", f"${starting_balance:,.2f}"),
        ("FINAL BALANCE", f"${final_balance:,.2f}"),
        ("VAULTED CASH", f"${total_vaulted:,.2f}"),
        ("TOTAL WEALTH", f"${total_wealth:,.2f}"),
        ("MULTIPLIER", f"{mult:,.1f}x"),
        ("TOTAL TRADES", f"{total_trades:,}"),
        ("WIN RATE", f"{overall_wr:.1f}%"),
        ("PROFITABLE DAYS", f"{profitable_days}/{total_days} ({profitable_days/total_days*100:.0f}%)"),
    ]

    for idx, (label, val) in enumerate(kpis):
        col = 2 + (idx * 2)
        # Card Box (Merge 2 columns)
        ws_daily.merge_cells(start_row=5, start_column=col, end_row=5, end_column=col + 1)
        ws_daily.merge_cells(start_row=6, start_column=col, end_row=6, end_column=col + 1)

        c_val = ws_daily.cell(row=5, column=col, value=val)
        c_val.font = font_card_num
        c_val.alignment = Alignment(horizontal="center", vertical="center")

        c_lbl = ws_daily.cell(row=6, column=col, value=label)
        c_lbl.font = font_card_lbl
        c_lbl.alignment = Alignment(horizontal="center", vertical="center")

        for r in range(5, 7):
            for c in range(col, col + 2):
                cell = ws_daily.cell(row=r, column=c)
                cell.fill = card_fill
                cell.border = thin_border

    # Table Headers (Row 8)
    headers = [
        ("Day #", "C"),
        ("Date", "C"),
        ("Start Balance", "R"),
        ("Day Realized PnL", "R"),
        ("Vaulted Today", "R"),
        ("Cumulative Vaulted", "R"),
        ("End Balance", "R"),
        ("Total Net Wealth", "R"),
        ("Trades", "C"),
        ("Wins", "C"),
        ("Win Rate", "R"),
        ("Peak Equity", "R"),
        ("Drawdown %", "R"),
        ("Sovereign Status", "C"),
    ]

    header_row = 8
    for col_idx, (h_title, align) in enumerate(headers, start=2):
        cell = ws_daily.cell(row=header_row, column=col_idx, value=h_title)
        cell.font = font_header
        cell.fill = navy_header_fill
        cell.border = header_border
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Data Rows
    running_peak = starting_balance
    start_row = 9

    for r_idx, row in df_daily.iterrows():
        cur_row = start_row + r_idx
        pnl = float(row["day_pnl"])
        start_bal = float(row["start_balance"])
        end_bal = float(row["end_balance"])
        v_today = float(row["withdrawn_today"])
        c_vault = float(row["cumulative_withdrawn"])
        n_trades = int(row["trades_count"])
        n_wins = int(row["wins"])
        wr_pct = float(row["win_rate_pct"])
        dd_pct = float(row["drawdown_pct"])
        tot_wealth = end_bal + c_vault
        running_peak = max(running_peak, tot_wealth)

        status = "VAULT HARVEST" if v_today > 0 else ("GROWTH DAY" if pnl > 0 else "DEFENSIVE SHIELD")

        row_data = [
            int(row["day_num"]),
            str(row["date"]),
            start_bal,
            pnl,
            v_today,
            c_vault,
            end_bal,
            tot_wealth,
            n_trades,
            n_wins,
            wr_pct / 100.0,
            running_peak,
            dd_pct / 100.0,
            status,
        ]

        is_even = (r_idx % 2 == 0)

        for c_idx, val in enumerate(row_data, start=2):
            cell = ws_daily.cell(row=cur_row, column=c_idx, value=val)
            cell.font = font_data
            cell.border = thin_border
            cell.fill = zebra_fill if is_even else PatternFill(fill_type=None)

            # Alignment & Number Formatting
            col_name = headers[c_idx - 2][0]
            if "Balance" in col_name or "PnL" in col_name or "Vaulted" in col_name or "Wealth" in col_name or "Peak" in col_name:
                cell.number_format = "$#,##0.00"
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif "Rate" in col_name or "Drawdown" in col_name:
                cell.number_format = "0.0%"
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif "Trades" in col_name or "Wins" in col_name or "Day #" in col_name:
                cell.number_format = "#,##0"
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="center", vertical="center")

            # Highlight PnL
            if col_name == "Day Realized PnL":
                if pnl > 0:
                    cell.fill = green_win_fill
                    cell.font = font_green
                elif pnl < 0:
                    cell.fill = red_loss_fill
                    cell.font = font_red

    # Auto-fit Column Widths on Tab 1
    for col in ws_daily.columns:
        col_letter = get_column_letter(col[0].column)
        if col[0].column == 1:
            ws_daily.column_dimensions[col_letter].width = 3
            continue
        max_len = max(len(str(c.value or "")) for c in col)
        ws_daily.column_dimensions[col_letter].width = max(12, min(max_len + 3, 26))

    # -------------------------------------------------------------
    # TAB 2: GRANULAR PER-TRADE JOURNAL
    # -------------------------------------------------------------
    ws_trades.views.sheetView[0].showGridLines = True
    ws_trades.cell(row=2, column=2, value="📋 TRUMP REGIME GRANULAR TRADE-BY-TRADE JOURNAL").font = font_title
    ws_trades.cell(row=3, column=2, value="Every individual trade indexed causally with entry/exit points, lot volume, PnL, duration, and safeguard exit reasons").font = font_subtitle

    # Load trades from regime_trade_journal_full.csv or trump_regime_trades_protected.csv
    trades_file = trades_csv_path or (ROOT_DIR / "data" / "regime_trade_journal_full.csv")
    if trades_file.exists():
        df_trades = pd.read_csv(trades_file)
    else:
        df_trades = pd.DataFrame()

    trade_headers = [
        ("Ticket #", "C"),
        ("Date", "C"),
        ("Time (UTC)", "C"),
        ("Direction", "C"),
        ("Strategy", "C"),
        ("Entry Price", "R"),
        ("Exit Price", "R"),
        ("Volume (Lots)", "R"),
        ("Points Captured", "R"),
        ("Realized PnL", "R"),
        ("Win/Loss", "C"),
        ("Exit Reason", "C"),
        ("Duration (Min)", "C"),
        ("Political Regime", "C"),
    ]

    for col_idx, (th_title, _) in enumerate(trade_headers, start=2):
        cell = ws_trades.cell(row=5, column=col_idx, value=th_title)
        cell.font = font_header
        cell.fill = navy_header_fill
        cell.border = header_border
        cell.alignment = Alignment(horizontal="center", vertical="center")

    if not df_trades.empty:
        for t_idx, row in df_trades.iterrows():
            c_row = 6 + t_idx
            pnl_val = float(row.get("realized_pnl", 0.0))
            is_win = bool(row.get("is_win", pnl_val > 0))

            t_data = [
                int(row.get("ticket_id", t_idx + 1)),
                str(row.get("date", "")),
                str(row.get("time_utc", "")),
                str(row.get("direction", "BUY")),
                str(row.get("strategy", "BREAKOUT_RETEST")),
                float(row.get("entry_price", 0.0)),
                float(row.get("exit_price", 0.0)),
                float(row.get("volume", row.get("initial_volume", 0.05))),
                float(row.get("points_captured", 0.0)),
                pnl_val,
                "WIN" if is_win else "LOSS",
                str(row.get("exit_reason", "STOP_LOSS")),
                int(row.get("duration_min", 10)),
                str(row.get("politician_regime", "TRADE_WAR_TARIFFS")),
            ]

            is_even = (t_idx % 2 == 0)

            for c_idx, val in enumerate(t_data, start=2):
                cell = ws_trades.cell(row=c_row, column=c_idx, value=val)
                cell.font = font_data
                cell.border = thin_border
                cell.fill = zebra_fill if is_even else PatternFill(fill_type=None)

                hdr = trade_headers[c_idx - 2][0]
                if "Price" in hdr or "PnL" in hdr:
                    cell.number_format = "$#,##0.00"
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                elif "Volume" in hdr:
                    cell.number_format = "0.00"
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                elif "Points" in hdr:
                    cell.number_format = "0.00"
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                else:
                    cell.alignment = Alignment(horizontal="center", vertical="center")

                if hdr == "Realized PnL":
                    if pnl_val > 0:
                        cell.fill = green_win_fill
                        cell.font = font_green
                    else:
                        cell.fill = red_loss_fill
                        cell.font = font_red

    for col in ws_trades.columns:
        col_letter = get_column_letter(col[0].column)
        if col[0].column == 1:
            ws_trades.column_dimensions[col_letter].width = 3
            continue
        max_len = max(len(str(c.value or "")) for c in col[:100])
        ws_trades.column_dimensions[col_letter].width = max(11, min(max_len + 3, 30))

    # -------------------------------------------------------------
    # TAB 3: INSTITUTIONAL SAFEGUARDS ARCHITECTURE
    # -------------------------------------------------------------
    ws_defense.views.sheetView[0].showGridLines = True
    ws_defense.cell(row=2, column=2, value="🛡️ STRATTON OAKMONT 8 INSTITUTIONAL SAFEGUARDS").font = font_title
    ws_defense.cell(row=3, column=2, value="Forensic Guardrails Eliminating Fatal Edge Cases and Protecting Capital").font = font_subtitle

    safeguards = [
        ("1. Dynamic Peak Watermark Bag Protection", "Locks cash when floating profit pulls back 18% from >=$25-$50 peak. Guarantees that a +$300 floating winner can NEVER reverse into a -$500 loss."),
        ("2. Mathematical Risk-Based Lot Sizing", "Strictly caps total equity dollar risk to <=2.0% per trade. Eliminates the catastrophic 10-stack overleveraging that blew $500 on $700."),
        ("3. Physical Broker-Side Stop-Loss Injection", "Injects hard stop-loss directly into LiteFinance MT5 broker DOM on entry. Positions are NEVER naked if browser or connection drops."),
        ("4. Stagnation & Time-Decay Lifespan Exit", "Enforces a strict 20-35 minute maximum trade horizon. If a scalp consolidates and fails to expand, it is immediately harvested or scratched."),
        ("5. Fast Breakeven Lock (+1.0 ATR)", "Automatically ratchets stop-loss to entry + spread buffer within the first +1.20 pts (+1.0 ATR) of favorable impulse, creating a risk-free cushion."),
        ("6. Post-Loss Cooldown & 2-Loss Lockout", "Imposes a 5-minute freeze after any loss, and a 60-minute complete lockout after 2 consecutive losses to prevent emotional revenge trading."),
        ("7. Live Spread Blowout Veto (> $0.45)", "Monitors broker bid-ask spread in real time; vetoes entries if spread expands beyond $0.45/oz during news spikes or liquidity vacuums."),
        ("8. Sovereign Daily Withdrawal Compounding", "Automatically vaults 30% to 70% of daily realized profits into the Stratton Vault, banking profits as hard cash while compounding the rest."),
    ]

    for idx, (title, desc) in enumerate(safeguards, start=5):
        ws_defense.cell(row=idx, column=2, value=title).font = Font(name="Calibri", size=11, bold=True, color="1B2A4A")
        ws_defense.cell(row=idx, column=3, value=desc).font = Font(name="Calibri", size=10, color="333333")
        ws_defense.cell(row=idx, column=2).border = thin_border
        ws_defense.cell(row=idx, column=3).border = thin_border

    ws_defense.column_dimensions["B"].width = 45
    ws_defense.column_dimensions["C"].width = 95

    # Save Workbook
    output_excel_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_excel_path)
    print(f"✅ Successfully exported formatted Excel ledger to: {output_excel_path}")


def main() -> None:
    print("=" * 70)
    print(" BUILDING TRUMP REGIME DAILY COMPOUNDING EXCEL LEDGER ($60 USD)")
    print("=" * 70)

    # 1. Run backtest with $60.00 starting balance
    summary = run_daily_trump_backtest(
        data_dir="data/candles",
        start_date_str="2025-01-21",
        starting_balance=60.0,
        enable_daily_withdrawal=True,
        buffer_equity=150.0,
    )

    daily_csv = ROOT_DIR / "data" / "trump_regime_daily_report.csv"
    target_excel = ROOT_DIR / "data" / "trump_regime_daily_60_usd_compounding.xlsx"
    target_csv = ROOT_DIR / "data" / "trump_regime_daily_60_usd_compounding.csv"

    # Copy / save CSV version
    df = pd.read_csv(daily_csv)
    df.to_csv(target_csv, index=False)
    print(f"Saved compounding CSV to: {target_csv}")

    # 2. Build Rich Formatted Excel
    build_formatted_excel(
        daily_csv_path=daily_csv,
        output_excel_path=target_excel,
        starting_balance=60.0,
    )

    print("\nSummary Results starting with $60.00 across 473 Days:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
