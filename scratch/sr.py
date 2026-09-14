import pathlib
p = pathlib.Path('/home/stratton-oakmont/bot/scout.py'); s = p.read_text()
s = s.replace('                        coiling, ripeness, _stop_room, BARS_JS)',
              '                        coiling, ripeness, coin_reach,\n'
              '                        _stop_room, BARS_JS)')
old = '''def read_break(c) -> dict | None:'''
new = '''def read_break(c, sym: str | None = None) -> dict | None:'''
assert s.count(old) == 1, 'read_break sig'
s = s.replace(old, new, 1)
old2 = '''    room = _stop_room()
    got = breakout(ohlc, t, max_stop=room)'''
new2 = '''    room = _stop_room()
    # The coin's own movement, so its level is judged on its own scale: a
    # fast coin keeps breaking and rebuilding levels, and a tolerance set for
    # a quiet one never sees them.
    rr = coin_reach(sym)[0] if sym else None
    got = breakout(ohlc, t, max_stop=room, reach=rr)'''
assert s.count(old2) == 1, 'read_break body'
s = s.replace(old2, new2, 1)
old3 = '''                    br = read_break(c)'''
new3 = '''                    br = read_break(c, sym)'''
assert s.count(old3) == 1, 'read_break call'
s = s.replace(old3, new3, 1)
p.write_text(s)
print('  the scout measures a level on the coin own scale')
