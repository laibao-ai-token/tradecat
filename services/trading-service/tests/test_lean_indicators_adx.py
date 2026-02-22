import warnings

import numpy as np

from src.indicators.batch.lean_indicators import calc_adx


def test_calc_adx_no_runtime_warning_on_flat_series():
    """Flat candles should not trigger divide-by-zero RuntimeWarning."""
    n = 60
    high = np.full(n, 100.0, dtype=float)
    low = np.full(n, 100.0, dtype=float)
    close = np.full(n, 100.0, dtype=float)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        res = calc_adx(high, low, close, period=14)

    assert set(res.keys()) == {"ADX", "正向DI", "负向DI"}
    assert np.isfinite(res["ADX"])
    assert np.isfinite(res["正向DI"])
    assert np.isfinite(res["负向DI"])
