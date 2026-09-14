import pathlib
import re
p = pathlib.Path('/home/tbt/bot/boom2.py'); s = p.read_text()

# the watchlist filtered on a field that was never measured, so it was always
# empty and the scout has been falling back to the short list all along
old = '''             if r.get("bars", 0) >= 300 and r.get("typical", 0) >= 0.30]'''
new = '''             if r.get("shapes", 0) >= 0.5 and r.get("typical", 0) >= 0.30]'''
assert s.count(old) == 1, 'watchlist filter'
s = s.replace(old, new, 1)

# the table promised columns it never printed
h_old = re.search(r'f"\{\'to tp\':>7\}.*?\n.*?\n', s)
print("  header found:", bool(h_old))
s = re.sub(r"f\"\{'to tp':>7\}[^\n]*\n(\s*f\"[^\n]*\n)?",
           "f\"{'to tp':>7}{'reach':>7}{'line':>6}{'shapes':>8}{'force':>7}\"\n"
           "          f\"{'wick':>7}{'lev':>5}{'score':>7}\")\n", s, count=1)
row_old = re.search(r'f"\{r\[\'lost\'\]:>6\}[^\n]*\n(\s*f"[^\n]*\n)*', s)
print("  row found:", bool(row_old))
if row_old:
    s = (s[:row_old.start()]
         + "f\"{r['lost']:>6}{r['med_min']:>6.0f}m\"\n"
           "              f\"{r.get('reach', 0):>6.1f}%{r.get('smooth', 0):>6.2f}\"\n"
           "              f\"{r.get('strong', 0):>8.1f}{r.get('force', 0):>7.1f}\"\n"
           "              f\"{r['wick']:>6.0f}%{r.get('lev', 0):>5.0f}{r['score']:>7.1f}\")\n"
         + s[row_old.end():])
p.write_text(s)
print('  watchlist filter and table columns fixed')
