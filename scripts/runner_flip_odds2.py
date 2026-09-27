import sys
sys.argv = ["x", "data/candles/real_bt2"]
import numpy as np, pandas as pd
import scripts.runner_flip as R
from scripts.runner_flip_odds import sim  # noqa (re-runs prints; ignore)
