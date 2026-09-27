"""
xau_alpha/recon/microlot_ruin.py
Risk-of-ruin / growth math for a ~$13 XAUUSD account under discrete lot granularity.

Pure simulation under stated assumptions (no market data). Used by recon/web_research.md section C.
Run: python3 xau_alpha/recon/microlot_ruin.py   (single process, ~10 s)

Model per trade (per oz of exposure):
  win  (prob p): +b*D - c
  loss (prob 1-p): -D - c
  D = stop distance in $ (gold price units), b = reward/risk, c = round-trip cost in $/oz.
Sizing: target risk fraction f of equity; oz = floor(f*E/(D) / step) * step.
  If that is < 1 step, the account is FORCED to trade 1 step (what a micro "flip" account does).
Ruin: equity can no longer cover one stop at the minimum step (E < step*(D+c)).
Goal: equity >= TARGET.
"""
import numpy as np

E0, TARGET, PATHS, MAX_TRADES = 13.0, 100.0, 20000, 4000
rng = np.random.default_rng(7)


def simulate(p, b, D, c, step, f):
    E = np.full(PATHS, E0)
    alive = np.ones(PATHS, bool)
    done = np.zeros(PATHS, bool)
    n_trades = np.zeros(PATHS, np.int32)
    for _ in range(MAX_TRADES):
        act = alive & ~done
        if not act.any():
            break
        oz = np.floor(f * E / D / step) * step
        oz = np.maximum(oz, step)                    # forced minimum lot
        win = rng.random(PATHS) < p
        pnl = np.where(win, b * D - c, -D - c) * oz
        E = np.where(act, E + pnl, E)
        n_trades += act
        done |= act & (E >= TARGET)
        alive &= ~(act & (E < step * (D + c)))
    return (done.mean(), (~alive).mean(), float(np.median(n_trades[done])) if done.any() else np.nan)


def log_growth(p, b, D, c, frac):
    """Expected log growth per trade when risking `frac` of equity on a stop of D (costs included)."""
    w = frac * (b * D - c) / D
    l = frac * (D + c) / D
    if l >= 1:
        return -np.inf
    return p * np.log1p(w) + (1 - p) * np.log1p(-l)


def kelly(p, b, D, c):
    bw, bl = (b * D - c) / D, (D + c) / D          # net win/loss in units of stop
    return max(0.0, (p * bw - (1 - p) * bl) / (bw * bl))


if __name__ == "__main__":
    edges = [(0.45, 1.5), (0.55, 1.0)]              # gross EV +0.125R and +0.10R before costs
    stops = [2.0, 4.0]
    costs = {"ECN raw~0.15+comm0.05": 0.20, "Dukascopy-like 0.65": 0.65}
    steps = {"std 0.01lot=1oz": 1.0, "ProCent 0.1oz": 0.1, "cent 0.01oz": 0.01}
    print(f"E0=${E0}, target=${TARGET}, paths={PATHS}, max_trades={MAX_TRADES}")
    print("p,b | D | cost | step | f_forced(1 step) | Kelly f* | g(f_forced) | P(hit $100) | P(ruin) | median trades")
    for p, b in edges:
        for D in stops:
            for cname, c in costs.items():
                for sname, step in steps.items():
                    f_forced = step * (D + c) / E0
                    f_use = 0.02 if step < 1 else f_forced   # cent-type steps can size at 2% risk
                    k = kelly(p, b, D, c)
                    g = log_growth(p, b, D, c, max(f_forced, 0.02) if step < 1 else f_forced)
                    hit, ruin, med = simulate(p, b, D, c, step, f_use)
                    print(f"{p},{b} | {D} | {cname} | {sname} | {f_forced:.3f} | {k:.3f} | {g:+.4f} | "
                          f"{hit:.3f} | {ruin:.3f} | {med}")
