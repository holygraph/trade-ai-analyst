"""Тесты симуляции, комиссий, портфеля и вариантов бэктеста: python test_backtest.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "research"))   # backtest.py и stage9.py в research/
import backtest as bt
import evaluate as ev
import fetch_candles as fc


def bars(*hlc, t0=0, step=bt.M15):
    out = [dict(t=t0 + i * step, o=c, h=h, l=l, c=c) for i, (h, l, c) in enumerate(hlc)]
    return out, [b["t"] for b in out]


# шорт: вход 105, стоп 110, цель 95, уровень отмены 108
def sim(b, market=False, cancel=108, m1=None):
    return bt.simulate(b[0], b[1], 0, True, 105, 110, 95, cancel, market, m1)["outcome"]


assert sim(bars((104, 100, 102), (106, 103, 104), (104, 94, 95))) == "цель"
assert sim(bars((106, 103, 104), (104, 99, 100), (111, 99, 100))) == "стоп"
assert sim(bars((111, 103, 104))) == "стоп"                             # вход и стоп в одной свече
assert sim(bars((106, 103, 104), (111, 94, 100))) == "стоп"            # стоп и цель, M1 нет — стоп
assert sim(bars((104, 94, 95), (106, 100, 104))) == "отменён"          # цель до касания входа
assert sim(bars((106, 94, 100))) == "отменён"                          # вход и цель, M1 нет — отмена
assert sim(bars((104.9, 100, 104), (104.9, 100, 108.5))) == "отменён"  # закрытие телом за уровнем отмены
assert sim(bars((104.9, 100, 104), (104.9, 100, 108.5)), cancel=None) == "открыт"
assert sim(bars(*[(104, 100, 102)] * (14 * 96 + 5))) == "не исполнен"
assert sim(bars((104, 100, 102))) == "открыт"                          # данные кончились, ордер жив
out = bt.simulate(*bars((104, 94, 100)), 0, True, 102, 110, 95, 108, True)   # рынок: исполнен на дату
assert out["outcome"] == "цель" and out["t_in"] == 0, out
assert bt.simulate(*bars((99, 94, 97), (106, 96, 105)), 0, False, 95, 90, 105, 92, False)["outcome"] == "цель"

# п.2: порядок по M1
m1_entry_first = lambda t: bars((106, 103, 105), (104, 94, 95), t0=t, step=60000)[0]   # вход, потом цель
m1_target_first = lambda t: bars((104, 94, 95), (106, 103, 105), t0=t, step=60000)[0]
assert sim(bars((106, 94, 100)), m1=m1_entry_first) == "цель"
assert sim(bars((106, 94, 100)), m1=m1_target_first) == "отменён"
m1_target_then_stop = lambda t: bars((104, 94, 95), (111, 100, 110), t0=t, step=60000)[0]
m1_stop_then_target = lambda t: bars((111, 100, 110), (104, 94, 95), t0=t, step=60000)[0]
m1_same_minute = lambda t: bars((111, 94, 100), t0=t, step=60000)[0]
seq = bars((106, 103, 104), (111, 94, 100))
assert sim(seq, m1=m1_target_then_stop) == "цель"
assert sim(seq, m1=m1_stop_then_target) == "стоп"
assert sim(seq, m1=m1_same_minute) == "стоп"                           # оба в одной минуте — стоп
r = bt.simulate(*seq, 0, True, 105, 110, 95, 108, False, m1_target_then_stop)
assert r["m1_used"] == 1 and r["m1_failed"] == 0 and abs(r["mfe"] - 10) < 1e-9, r
# MFE: ход к цели после входа, без свечи входа
r = bt.simulate(*bars((106, 103, 104), (105, 101, 102), (111, 102, 110)), 0, True, 105, 110, 95, 108, False)
assert r["outcome"] == "стоп" and abs(r["mfe"] - 4) < 1e-9, r

# п.1: комиссии в R, новая и старая модель
g, n = bt.result_r("цель", 100, 110, 80, False, bt.OLD)
assert g == 2 and abs(n - (2 - (100 + 80) * 0.00055 / 10)) < 1e-12
g, n = bt.result_r("цель", 100, 110, 80, False, bt.NEW)               # лимит maker, тейк maker
assert abs(n - (2 - (100 + 80) * 0.0002 / 10)) < 1e-12
g, n = bt.result_r("стоп", 100, 110, 80, True, bt.NEW)                # рынок taker, стоп taker + проскальзывание
assert g == -1 and abs(n - (-1 - (100 * 0.00055 + 110 * (0.00055 + 0.0002)) / 10)) < 1e-12
assert bt.result_r("открыт", 100, 110, 80) == (None, None)

# п.3: безубыточный % побед по фактическим R:R набора
ts = [dict(win_new=1.0, loss_new=-1.0), dict(win_new=3.0, loss_new=-1.0)]
assert abs(bt.breakeven(ts) - 100 / 3) < 1e-9                         # p·2 − (1−p) = 0 → p = 1/3

# п.4: портфель — слоты, одна идея, лимит дня, риск от баланса
def tr(sym, t_in, t_out, r_new, zone=(1, 2), side="down"):
    return dict(status=ev.READY, symbol=sym, side=side, entry_zone=[zone[0], zone[1]], t_in_ms=t_in, t_out_ms=t_out,
                asof_ms=t_in, r_new=r_new)


H = 3600000
day0 = 10 * ev.DAY                                                    # не понедельник — неделя началась раньше
m = bt.portfolio([tr("A", day0, day0 + H, 2.0), tr("B", day0 + 2 * H, day0 + 3 * H, -1.0)], "pct")
assert abs(m["balance"] - (10000 + 200 - 102)) < 1e-9 and m["trades"] == 2, m      # 1% от текущего баланса
m = bt.portfolio([tr("A", day0, day0 + 9 * H, 1), tr("B", day0 + H, day0 + 9 * H, 1), tr("C", day0 + 2 * H, day0 + 3 * H, 1)], "fixed")
assert m["skipped"] == {"нет свободного слота": 1} and m["trades"] == 2, m
m = bt.portfolio([tr("A", day0, day0 + 9 * H, 1), tr("A", day0 + H, day0 + 2 * H, 1, zone=(1.5, 3))], "fixed")
assert m["skipped"] == {"та же идея": 1}, m
m = bt.portfolio([tr("A", day0, day0 + H, 1), tr("A", day0 + 2 * H, day0 + 3 * H, 1, side="up")], "fixed")
assert m["trades"] == 2                                               # другая сторона — другая идея
loss = [tr("A", day0, day0 + H, -1.0), tr("B", day0 + H, day0 + 2 * H, -1.1), tr("C", day0 + 3 * H, day0 + 4 * H, 1.0)]
m = bt.portfolio(loss, "fixed")
assert m["skipped"] == {"лимит убытка дня": 1}, m                    # −$210 ≥ 2% от $10 000
m = bt.portfolio(loss[:2] + [tr("C", day0 + ev.DAY, day0 + ev.DAY + H, 1.0)], "fixed")
assert m["trades"] == 3, m                                            # на следующий день вход разрешён
assert m["streak"] == 2 and abs(m["pf"] - 100 / 210) < 1e-9, m
assert bt.portfolio([tr("A", day0, None, 1)], "pct")["still_open"] == 1

# доверительный интервал
lo, hi = bt.boot_ci([1.0, -1.0] * 50)
assert lo < 0 < hi

# варианты: R:R после комиссий, открытие месяца, свип, ступень M15, CHoCH в swing-коррекции; по умолчанию выключены
DEFAULT = dict(month_open_atr=None, no_m15_step=False, need_sweep=False, no_choch_swing_corr=False)
assert fc.FEES is None and ev.VARIANT == DEFAULT
assert fc.rr_value(100, 110, 85, 10, False) == 1.5
bt.set_variant("V1")
net = fc.rr_value(100, 110, 85, 10, False)
assert abs(net - (15 - 100 * 0.0002 - 85 * 0.0002) / (10 + 100 * 0.0002 + 110 * 0.00075)) < 1e-12 and net < 1.5
assert ev.VARIANT["month_open_atr"] == 0.5
bt.set_variant("V2")
assert ev.VARIANT["no_m15_step"] and fc.FEES is not None
bt.set_variant("V3")
assert ev.VARIANT["need_sweep"] and not ev.VARIANT["no_m15_step"]
bt.set_variant("V4")
assert ev.VARIANT["no_choch_swing_corr"] and ev.VARIANT["no_m15_step"] and fc.FEES is not None
bt.set_variant("V0")
assert fc.FEES is None and ev.VARIANT == DEFAULT
print("OK")
