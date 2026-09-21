from backtest_1month_challenge import simulate_single_day, datetime, timedelta

end_date = datetime(2026, 9, 18)
curr = end_date
trading_days = []
while len(trading_days) < 22:
    if curr.weekday() < 5:
        trading_days.append(curr.strftime("%Y-%m-%d"))
    curr -= timedelta(days=1)
trading_days.reverse()

print("=" * 75)
print("🎯 EXACT WEDNESDAY ENGINE APPLIED TO ALL 22 TRADING DAYS")
print("Setup: $50 Starting Capital Daily, Stacking 5-10 orders of 0.10-0.25 Lots")
print("Intraday Compounding: Scale scales up to 10x as balance grows within the day")
print("=" * 75)

results = []
for i, d in enumerate(trading_days, 1):
    r = simulate_single_day(d, start_balance=50.0)
    results.append(r)
    print(f"{i:02d} | {d} | Start: $50.00 -> End: ${r['end_balance']:>10,.2f} | Daily PnL: ${r['net_pnl']:>+10,.2f} | Trades: {r['trade_count']:>2d} (WR: {r['win_rate']:>4.1f}%)")

total_pnl = sum(r['net_pnl'] for r in results)
avg_daily = total_pnl / len(results)
best = max(results, key=lambda x: x['net_pnl'])
worst = min(results, key=lambda x: x['net_pnl'])

print("=" * 75)
print("📊 MONTHLY HARVEST WITH THE EXACT WEDNESDAY ENGINE:")
print("=" * 75)
print(f"Total Profits Harvested in 1 Month:  ${total_pnl:,.2f} USD")
print(f"Average Profit Generated PER DAY:    ${avg_daily:,.2f} / day")
print(f"Best Day:                            {best['date']} (+${best['net_pnl']:,.2f})")
print(f"Worst Day:                           {worst['date']} (${worst['net_pnl']:,.2f})")
print("=" * 75)
