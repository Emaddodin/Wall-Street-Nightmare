import pathlib
p = pathlib.Path('/home/tbt/bot/papertrade.py'); s = p.read_text()
old = '''                        else:
                            _ctp, _csl = geo_leg(sym, ohlc, s["t"])
                        if a.min_expansion > 0:'''
new = '''                        elif s.get("kind") != "break":
                            _ctp, _csl = geo_leg(sym, ohlc, s["t"])
                        # A shape's stop is the level it broke, and that was
                        # settled fifty lines up. This branch used to run for
                        # it as well and quietly put the fixed 2.1% back --
                        # which is past the liquidation line at fifty times, so
                        # the whole "the stop comes from the level" design was
                        # never actually reaching a single trade.
                        if a.min_expansion > 0:'''
assert s.count(old) == 1, 'overwrite anchor'
p.write_text(s.replace(old, new, 1))
print('  the level keeps its stop; nothing downstream overwrites it')
