"""Pandas' default NA parsing treats "None" (no preemption) as missing.
We disable keep_default_na and use na_values=[""] to preserve literal answers
while marking only empty cells as missing.
"""

import pandas as pd


def read_table(path):
    return pd.read_csv(path, keep_default_na=False, na_values=[""])
