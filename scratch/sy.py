import pathlib
p = pathlib.Path('/home/tbt/bot/papertrade.py'); s = p.read_text()
old = '''              _rr = coin_reach(sym0)[0] if 'sym0' in dir() else None'''
new = '''              # The coin this window is on, so the level can be measured on
              # its own scale. read_window works in TradingView's naming, so
              # it is peeled back to the plain symbol the scanner uses.
              _sym0 = str(r.get("symbol") or "").split(":")[-1].replace(".P", "")
              _rr = coin_reach(_sym0)[0] if _sym0 else None'''
assert s.count(old) == 1, 'sym anchor'
p.write_text(s.replace(old, new, 1))
print('  it uses the window own symbol now')
