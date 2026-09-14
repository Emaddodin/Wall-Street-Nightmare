import pathlib
p = pathlib.Path('/home/tbt/bot/papertrade.py')
L = p.read_text().split("\n")
i = next(k for k, l in enumerate(L) if "coin_reach(sym0)" in l)
pad = " " * (len(L[i]) - len(L[i].lstrip()))
L[i:i+1] = [
    pad + "# The coin this window is on, so its level is measured on its own",
    pad + "# scale. read_window works in TradingView's naming, so it is peeled",
    pad + "# back to the plain symbol the scanner uses.",
    pad + '_sym0 = str(r.get("symbol") or "").split(":")[-1].replace(".P", "")',
    pad + "_rr = coin_reach(_sym0)[0] if _sym0 else None",
]
p.write_text("\n".join(L))
print("  the window uses its own symbol")
