"""Тесты этапа 9: python test_stage9.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "research"))   # backtest.py и stage9.py в research/
import random

import backtest as bt
import stage9 as s9

rnd = random.Random(3)
# поиск первого касания по таблицам совпадает с перебором
a = [rnd.uniform(0, 100) for _ in range(500)]
tmax, tmin = s9.sparse(a, max), s9.sparse(a, min)
for _ in range(2000):
    s, level = rnd.randrange(500), rnd.uniform(0, 100)
    assert s9.first_ge(tmax, s, 500, level) == next((j for j in range(s, 500) if a[j] >= level), None)
    assert s9.first_le(tmin, s, 500, level) == next((j for j in range(s, 500) if a[j] <= level), None)

# быстрая симуляция совпадает с bt.simulate без M1 на случайных свечах
price, bars = 100.0, []
for i in range(4000):
    o = price
    c = o * (1 + rnd.gauss(0, 0.004))
    bars.append(dict(t=i * bt.M15, o=o, h=max(o, c) * (1 + abs(rnd.gauss(0, 0.002))), l=min(o, c) * (1 - abs(rnd.gauss(0, 0.002))), c=c))
    price = c
fast = s9.Fast(bars)
times = [b["t"] for b in bars]
outcomes = set()
for _ in range(3000):
    s = rnd.randrange(1, 3500)
    p = bars[s - 1]["c"]
    short, market = rnd.random() < 0.5, rnd.random() < 0.3
    sign = 1 if short else -1
    entry = p if market else p * (1 + sign * rnd.uniform(0, 0.02))
    stop = entry * (1 + sign * rnd.uniform(0.003, 0.03))
    target = entry * (1 - sign * rnd.uniform(0.003, 0.06))
    cancel = None if rnd.random() < 0.2 else entry * (1 + sign * rnd.uniform(0.001, 0.02))
    ref = bt.simulate(bars, times, bars[s]["t"], short, entry, stop, target, cancel, market)["outcome"]
    got = fast.sim(s, short, entry, stop, target, cancel, market)
    assert got == ref, (s, short, market, entry, stop, target, cancel, got, ref)
    outcomes.add(got)
assert outcomes >= {"цель", "стоп", "отменён"}, outcomes

# правило решений: все годы > 0 и ДИ целиком > 0
def tr(year, r):
    return dict(year=year, r_new=r, outcome="цель" if r > 0 else "стоп", status="ГОТОВ")


good = [tr(y, r) for y in range(4) for r in [1.0, 0.5] * 30]
ok, means, pooled = s9.decision(good)
assert ok and all(m > 0 for m in means)
bad_year = good + [tr(2, -5.0)] * 40
assert not s9.decision(bad_year)[0]
noisy = [tr(y, r) for y in range(4) for r in [3.0, -1.0, -1.0] * 3]       # средний > 0, но ДИ задевает 0
assert not s9.decision(noisy)[0]

# bootstrap по месяцам: путь и вероятности
assert s9.path([0.1, -0.5]) == (1.1 * 0.5 - 1, 0.5)
b = s9.month_bootstrap({"a": [0.1], "b": [-0.01]}, n=2000)
assert 0 < b["month8"] < 100 and b["p5"] <= b["p50"] <= b["p95"]
print("OK")
