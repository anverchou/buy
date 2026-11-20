from datetime import date, timedelta

from app.services.history import buy_signal, compute_stats, window_start

TODAY = date(2026, 11, 19)


def series(values, start):
    return {start + timedelta(days=i): v for i, v in enumerate(values)}


def test_window_starts():
    assert window_start("30d", TODAY) == date(2026, 10, 21)
    assert window_start("90d", TODAY) == date(2026, 8, 22)
    assert window_start("ytd", TODAY) == date(2026, 1, 1)


def test_stats_basic():
    start = TODAY - timedelta(days=29)
    s = series([100] * 10 + [80] * 10 + [90] * 10, start)
    st = compute_stats(s, start, TODAY, current=90)
    assert (st.low, st.high, st.days) == (80, 100, 30)
    assert round(st.avg, 2) == 90.0
    assert round(st.pct_above_low, 1) == 12.5
    assert st.low_day == start + timedelta(days=19)  # most recent day at the low
    assert not st.partial


def test_partial_coverage_label():
    first = date(2026, 11, 2)
    s = series([50] * 18, first)
    st = compute_stats(s, window_start("90d", TODAY), TODAY, current=50)
    assert st.partial and st.tracking_label == "Tracking since Nov 2"


def test_none_prices_are_ignored():
    start = TODAY - timedelta(days=9)
    s = series([None, 10, None, 12, 11, None, 10, 10, 10, 10], start)
    st = compute_stats(s, start, TODAY, current=10)
    assert st.days == 7 and st.low == 10 and st.high == 12


def _stats(current, low, avg, days=30):
    start = TODAY - timedelta(days=days - 1)
    from app.services.history import Stats
    return Stats(current, low, start, avg * 1.2, start, avg, (current - low) / low * 100, days, start, False, None)


def test_buy_signal_thresholds():
    assert buy_signal(_stats(101.9, 100, 120), "90d")["label"] == "At 90-day low"
    assert buy_signal(_stats(110, 100, 120), "90d")["kind"] == "below"
    assert buy_signal(_stats(121, 100, 120), "90d")["kind"] == "near"
    above = buy_signal(_stats(130, 100, 120), "30d")
    assert above["kind"] == "above" and above["label"] == "Above average – consider waiting"
    assert buy_signal(_stats(100, 100, 100), "ytd")["label"] == "At YTD low"


def test_buy_signal_needs_history():
    assert buy_signal(_stats(100, 100, 100, days=3), "90d")["kind"] == "insufficient"
    assert buy_signal(None, "90d")["kind"] == "none"
