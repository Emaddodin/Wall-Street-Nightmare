import json,sys
d=json.load(open(sys.argv[1]))
print(sys.argv[2],{k:d[k] for k in ['total_trades_logged','overall_win_rate_pct','profit_factor','expectancy_usd','max_drawdown_pct','final_retained_equity_usd','total_cash_withdrawn_usd','wealth_multiplier']})
print("   exits",d['exit_breakdown'])
