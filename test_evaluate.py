"""Тесты условий evaluate на синтетических свечах: python test_evaluate.py"""
import datetime as dt

import fetch_candles as fc
import evaluate as ev


def B(o, h, l, c, t=0):
    return dict(t=t, o=o, h=h, l=l, c=c)


def event(kind, d):
    return dict(kind=kind, dir=d, i=0, level=0, key=None)


# статус
P, F, U, N = ("a", ev.PASS, ""), ("b", ev.FAIL, "ждём x"), ("c", ev.UNKNOWN, ""), ("d", ev.NA, "")
assert ev.status([P, P]) == ev.READY
assert ev.status([P, F]) == ev.ALMOST
assert ev.status([F, F]) == ev.NO
assert ev.status([P, U]) == ev.UNCHECKED
assert ev.status([F, U]) == ev.ALMOST

# п.3: НЕТ-ПОЧТИ только по R:R от 1.0, ожиданию триггера и условию позиции
assert ev.status([P, ("R:R", ev.FAIL, "", 1.0)]) == ev.ALMOST
assert ev.status([P, ("R:R", ev.FAIL, "", 0.99)]) == ev.NO
assert ev.status([P, ("R:R", ev.FAIL, "", None)]) == ev.NO
assert ev.status([F, N, N, N]) == ev.ALMOST
assert ev.status([P, N]) == ev.READY
assert ev.status([P, ("discount на D", ev.FAIL, "цена вне диапазона")]) == ev.ALMOST
assert ev.status([P, ("SNR на M15 после теста", ev.FAIL, "ждём M15 SNR")]) == ev.ALMOST
assert ev.status([P, ("точка A на W", ev.FAIL, "цена не в POI и нет свежего рейда")]) == ev.NO
assert ev.status([P, ev.c_swing_block("up", "down", "up", "2026-10-08")]) == ev.NO
assert ev.status([P, ("тренд D", ev.FAIL, "тренд не определён")]) == ev.NO
assert ev.status([P, (ev.GATE, ev.FAIL, "зона за уровнем отмены 5")]) == ev.NO
# неверный журнал не блокирует статус
assert ev.status([(ev.LIMITS, ev.UNKNOWN, ""), P]) == ev.READY

# направление
assert ev.c_direction("up", "W")[1] == ev.PASS
assert ev.c_direction(None, "W")[1] == ev.UNKNOWN

# запрет входа по W при сломанном D
assert ev.c_swing_block("up", "up", "up")[1] == ev.PASS
assert ev.c_swing_block("up", "down", "up")[1] == ev.FAIL
assert ev.c_swing_block("up", None, "up")[1] == ev.UNKNOWN

# premium/discount, вне диапазона не считается
assert ev.c_position(20, (10, 50), "up", "D")[1] == ev.PASS
assert ev.c_position(40, (10, 50), "up", "D")[1] == ev.FAIL
assert ev.c_position(40, (10, 50), "down", "D")[1] == ev.PASS
assert ev.c_position(20, (10, 50), "down", "D")[1] == ev.FAIL
assert ev.c_position(5, (10, 50), "up", "D")[1] == ev.FAIL
assert "вне диапазона" in ev.c_position(5, (10, 50), "up", "D")[2]
assert ev.c_position(60, (10, 50), "down", "D")[1] == ev.FAIL
assert ev.c_position(20, None, "up", "D")[1] == ev.UNKNOWN

# CHoCH
assert ev.has_choch([event("CHoCH", "up"), event("BOS", "up")], "up")
assert not ev.has_choch([event("CHoCH", "up"), event("BOS", "down")], "up")
assert not ev.has_choch([event("BOS", "up")], "up")
assert not ev.has_choch([event(None, "up")], "up")
tf = dict(tf="15", bars=[B(1, 1, 1, 1)], events=[dict(event("CHoCH", "up"))])
assert ev.c_choch([tf], "up")[1] == ev.PASS
assert ev.c_choch([tf], "down")[1] == ev.FAIL
assert ev.c_choch([None], "up")[1] == ev.UNKNOWN
assert ev.c_choch([None, tf], "down")[1] == ev.UNKNOWN
assert ev.c_choch([None, tf], "up")[1] == ev.PASS

# ближайшая зона
z1 = dict(dir="up", lo=10, hi=12, i=1, broken=None)
z2 = dict(dir="up", lo=20, hi=22, i=2, broken=None)
z3 = dict(dir="up", lo=26, hi=28, i=3, broken=5)
assert ev.nearest_zone([z1, z2, z3], "up", 25) is z2
assert ev.nearest_zone([z1, z2], "up", 21) is z2
assert ev.nearest_zone([z1, z2], "up", 25, need_inside=True) is None
assert ev.nearest_zone([z1, z2], "down", 25) is None

# точка A
bars = [B(10, 11, 9, 10)] * 25
t = dict(tf="D", bars=bars, frs=[], snr=[], rb=[], fvg=[z2], ifvg=[])
assert ev.point_a(t, "up", 25, ["fvg"])[0][1] == ev.PASS
assert ev.point_a(t, "up", 25, ["fvg"], need_inside=True)[0][1] == ev.FAIL
assert ev.point_a(t, "down", 25, ["fvg"])[0][1] == ev.FAIL
raid = dict(side="low", i=2, p=9, raids=[24], broken=None)
assert ev.point_a(dict(t, frs=[raid], fvg=[]), "up", 25, ["fvg"], need_inside=True)[0][1] == ev.PASS
assert ev.point_a(dict(t, frs=[dict(raid, raids=[5])], fvg=[]), "up", 25, ["fvg"], need_inside=True)[0][1] == ev.FAIL

# стоп: последний непробитый фрактал и буфер
frs = [dict(side="high", i=1, p=120, raids=[], broken=None), dict(side="high", i=5, p=110, raids=[], broken=None),
       dict(side="low", i=6, p=90, raids=[], broken=None)]
assert fc.stop_price(frs, 10, True)[0] == 111
assert fc.stop_price(frs, 10, False)[0] == 89
assert fc.stop_price(frs[:0], 10, True) is None
assert fc.last_fractal([dict(frs[1], broken=7), frs[0]][::-1], "high")["p"] == 120

# п.1: стоп за дальней границей зоны входа, берётся более далёкий из двух
assert fc.far_stop(True, 100, 110, 105, 10) == 111          # база внутри зоны — за зоной
assert fc.far_stop(True, 100, 110, 130, 10) == 130          # база дальше зоны — база
assert fc.far_stop(False, 100, 110, 105, 10) == 99
assert fc.far_stop(False, 100, 110, 80, 10) == 80

# цель: ближайшая, частично протестированный FVG подходит, далёкая не выбирается
tf_frs = [dict(side="low", i=1, p=70, raids=[], broken=None), dict(side="low", i=2, p=85, raids=[3], broken=None)]
fvg_partly = dict(dir="up", lo=76, hi=80, i=1, broken=None, tested=True)
fvg_broken = dict(dir="up", lo=88, hi=90, i=1, broken=2, tested=True)
bars_t = [B(1, 1, 1, 1)] * 5
got = fc.nearest_target(100, True, [("D", bars_t, tf_frs, [fvg_partly, fvg_broken])])
assert got[0] == 80, got
assert fc.nearest_target(100, True, [("D", bars_t, tf_frs, [])])[0] == 70
assert fc.nearest_target(100, True, [("D", bars_t, [], [])], [(95, "открытие месяца")])[0] == 95
assert fc.nearest_target(60, True, [("D", bars_t, tf_frs, [])]) is None

# R:R от начала и от 0.5
tfs = [("D", bars_t, [dict(side="low", i=1, p=70, raids=[], broken=None)], [])]
p = fc.plan(True, 100, 110, 90, 120, tfs)
assert abs(p["options"][0]["rr"] - 1.5) < 1e-9 and p["used"] == "начало", p
p = fc.plan(True, 100, 110, 90, 130, tfs)
assert abs(p["options"][0]["rr"] - 1.0) < 1e-9 and abs(p["options"][1]["rr"] - 1.4) < 1e-9 and p["used"] is None, p
p = fc.plan(True, 100, 110, 107, 120, tfs)
assert p["options"][0]["name"] == "рынок" and p["options"][0]["entry"] == 107 and len(p["options"]) == 1, p
p = fc.plan(True, 100, 110, 90, 95, tfs)
assert p["options"][0]["rr"] is None and p["used"] is None

# п.6: меньше 50 закрытых свечей — ТФ не используется, условия unknown
day = ev.DAY
d = [B(10, 11, 9, 10, t=i * day) for i in range(60)]
assert len(ev.build({"D": d}, 55 * day)["tfs"]["D"]["bars"]) == 55
assert ev.build({"D": d}, 49 * day)["tfs"]["D"] is None
assert ev.build({"D": d}, 50 * day)["tfs"]["D"] is not None
assert ev.duration("M", 0) == 31 * day
rows = ev.evaluate({"15": [B(10, 11, 9, 10, t=i * 900000) for i in range(30)]}, 30 * 900000)
assert all(r["status"] == ev.UNCHECKED for r in rows), rows
young = ev.evaluate({"15": [B(10, 11, 9, 10, t=i * 900000) for i in range(100)]}, 100 * 900000)
assert all(r["status"] == ev.UNCHECKED for r in young), young

# п.7: порядок событий внутри свечи старшего ТФ
H = 3600000
d_bar = [B(100, 110, 90, 100, t=0)]
m15_bars = [B(100, 101, 99, 100, t=i * 900000) for i in range(96)]
m15_bars[10] = B(100, 106, 99, 100, t=10 * 900000)
m15_bars[40] = B(100, 106, 99, 100, t=40 * 900000)
tf_ctx = {"D": dict(bars=d_bar), "240": None, "60": None, "15": dict(bars=m15_bars)}
assert ev.event_time(tf_ctx, "D", 0, 105, 107) == 40 * 900000
assert ev.event_time(tf_ctx, "D", 0, 105, 107, last=False) == 10 * 900000
# M15 не покрывает начало дня — берём H1
h1_bars = [B(100, 101, 99, 100, t=i * H) for i in range(24)]
h1_bars[5] = B(100, 106, 99, 100, t=5 * H)
cover = {"D": dict(bars=d_bar), "240": None, "60": dict(bars=h1_bars), "15": dict(bars=m15_bars[50:])}
assert ev.event_time(cover, "D", 0, 105, 107) == 5 * H
# младших ТФ нет — порядок не определён
assert ev.event_time({"D": dict(bars=d_bar), "240": None, "60": None, "15": None}, "D", 0, 105, 107) is None

# лимиты счёта по журналу
asof = int(dt.datetime(2026, 11, 2, tzinfo=dt.timezone.utc).timestamp() * 1000)
assert ev.c_limits([], asof)[1] == ev.PASS
assert ev.c_limits(None, asof)[1] == ev.UNKNOWN
assert ev.c_limits([dict(status="open", risk_pct=1), dict(status="open", risk_pct=1)], asof)[1] == ev.FAIL
assert ev.c_limits([dict(status="open", risk_pct=1)], asof)[1] == ev.PASS
assert ev.c_limits([dict(status="closed", date_close="2026-11-02", pnl_pct=-2)], asof)[1] == ev.FAIL
assert ev.c_limits([dict(status="closed", date_close="2026-11-01", pnl_pct=-2)], asof)[1] == ev.PASS
assert ev.c_limits([dict(status="closed", date_close="2026-11-02", pnl_pct=-1), dict(status="closed", date_close="2026-11-03", pnl_pct=-9)], asof)[1] == ev.PASS
assert ev.c_limits([dict(status="closed")], asof)[1] == ev.UNKNOWN
assert ev.c_limits([dict(status="cancelled", risk_pct=9)], asof)[1] == ev.PASS

# строка монеты с временем данных
rows = [ev.row("x", [P], info=dict(in_zone=True)), ev.row("y", [F]), ev.row("z", [F, F])]
assert ev.one_line("BTC", [rows[2]]) == "BTC: нет: z (ждём x)"           # у НЕТ только первый fail
beyond = (ev.BEYOND, ev.FAIL, "цена за зоной входа: 1 за дальней границей 2")
assert ev.cause(beyond) == beyond[2]                                      # без задвоения названия
assert ev.cause(("premium на D", ev.FAIL, "положение 0.3")) == "premium на D: положение 0.3"
full = ev.why("BTC", [ev.row("z", [F, beyond, U])])
assert "fail: ждём x" in full and "fail: цена за зоной входа" in full and "unknown: c" in full, full
# метка: в зоне A, но вне зоны входа
assert ev.ready_tag(ev.row("a", [P], info=dict(in_zone=True, in_entry=False))) == "в зоне A"
assert ev.ready_tag(ev.row("a", [P], info=dict(in_zone=True, in_entry=True))) == "цена в зоне"
assert ev.ready_tag(ev.row("a", [P], info=dict(in_zone=False, in_entry=False))) == "ожидание"
line = ev.one_line("BTC", rows, "2026-11-01 11:00")
assert "почти: y (ждём x)" in line and "ГОТОВ: x [цена в зоне]" in line and "[данные UTC 2026-11-01 11:00]" in line, line
assert ev.data_time({"15": [B(1, 1, 1, 1, t=0), B(1, 1, 1, 1, t=900000)]}, 900000) == "1970-01-01 00:15"

# п.4: не больше 8 карточек, порядок, «мало истории» без карточки
rd = ev.row("r", [P])
un = ev.row("u", [P, U])
hist = ev.missing("h", "мало истории")
near = ev.row("n", [P, ("R:R", ev.FAIL, "", 1.4)])
far_a = ev.row("f", [P, ("R:R", ev.FAIL, "", 1.0)])
cards, rest = ev.select_cards([("A", [far_a, near, hist, un, rd])] + [("B", [ev.row(f"r{i}", [P])]) for i in range(10)])
assert len(cards) == 8 and cards[0][1]["name"] == "r" and rest, cards
cards, rest = ev.select_cards([("A", [far_a, near, hist, un, rd])])
assert [c[1]["name"] for c in cards] == ["r", "u", "n", "f"] and not rest, cards

# п.5: флаги
fp = dict(options=[dict(name="начало", entry=100, rr=6)], used="начало", stop=101, stop_atr=10)
got = ev.flags(fp, None, "down", None, "up")
assert "стоп ближе 0.5 ATR" in got and "R:R > 5" in got and "BTC D up, против" in got, got
assert "BTC D down" in ev.flags(fp, None, "down", None, "down")
z_a = dict(zone=dict(t=0), side="down", flags=[])
z_b = dict(zone=dict(t=ev.DAY), side="down", flags=[])
z_c = dict(zone=dict(t=10 * ev.DAY), side="down", flags=[])
# вход вне диапазона в сторону верха (лонг) или низа (шорт) тоже даёт флаг
up_out = dict(options=[dict(name="начало", entry=120, rr=2)], used="начало", stop=110)
assert "верх диапазона: вход выше диапазона" in ev.flags(up_out, (10, 100), "up")
assert "верх диапазона" in ev.flags(dict(up_out, options=[dict(name="начало", entry=90, rr=2)]), (10, 100), "up")
dn_out = dict(options=[dict(name="начало", entry=5, rr=2)], used="начало", stop=8)
assert "низ диапазона: вход ниже диапазона" in ev.flags(dn_out, (10, 100), "down")
assert not any("диапазона" in f for f in ev.flags(dn_out, (10, 100), "up"))
# короткая история: меньше 100 закрытых свечей на ТФ точки A или W
assert "короткая история: W 60" in ev.flags(None, None, "up", hist=[("D", 299), ("W", 60)])
assert ev.flags(None, None, "up", hist=[("D", 299), ("W", 150)]) == []
assert "короткая история: W < 50" in ev.flags(None, None, "up", hist=[("W", None)])
ra, rb, rc = (ev.row(n, [P], info=i) for n, i in (("a", z_a), ("b", z_b), ("c", z_c)))
ev.mark_clusters([("X", [ra, rb, rc])])
assert ra["info"]["flags"] == ["одно движение: сигналов down 2"] and rc["info"]["flags"] == []

# локальный стоп: свип только между тестом POI и SNR; рейд после SNR не считается
m = [B(10, 11, 9, 10, t=i) for i in range(10)]
m[3] = B(10, 11, 7, 10, t=3)
m[8] = B(10, 11, 5, 10, t=8)
mf = dict(bars=m, atr=10, frs=[dict(side="low", i=0, p=8, raids=[3, 8], broken=None)])
assert ev.local_stop(mf, "up", 1, 6) == (6, True)
assert ev.local_stop(dict(mf, frs=[]), "up", 1, 6) == (6, False)
assert ev.local_stop(dict(mf, frs=[]), "up", 4, 6) == (8, False)
assert ev.local_stop(dict(mf, frs=[]), "up", 20, 30) == (None, False)

# c_plan: цель, стоп, R:R
low70 = dict(side="low", i=1, p=70, raids=[], broken=None)
high120 = dict(side="high", i=2, p=120, raids=[], broken=None)
high111 = dict(side="high", i=3, p=111, raids=[], broken=None)
bb = [B(100, 101, 99, 100, t=i) for i in range(30)]
tg = [("D", bb, [low70], [])]
zone = dict(dir="down", lo=100, hi=110, i=1, first_test=5, last_test=5, broken=None)
senior = dict(tf="D", bars=bb, frs=[high120], atr=0)
def cond(conds, name):
    return next(c for c in conds if c[0] == name)


conds, _ = ev.c_plan(True, senior, None, 90, senior, tg)        # зависимые не применимы
assert [c[1] for c in conds] == [ev.NA] * 6
conds, _ = ev.c_plan(True, senior, zone, 90, dict(senior, frs=[]), tg)
assert cond(conds, "стоп")[1] == ev.FAIL and cond(conds, "цель")[1] == ev.NA
conds, p = ev.c_plan(True, senior, zone, 90, senior, tg)
assert all(c[1] == ev.PASS for c in conds) and p["method"] == 1 and p["used"] == "начало", conds

# п.1: вход до уровня отмены, каждый вариант отдельно
p = fc.plan(True, 100, 110, 90, 120, tg, cancel=105)
assert [o["name"] for o in p["options"]] == ["начало"] and [o["name"] for o in p["dropped"]] == ["0.5"], p
p = fc.plan(True, 100, 110, 90, 120, tg, cancel=99)
assert p["options"] == [] and len(p["dropped"]) == 2 and p["used"] is None
assert ev.gate(p)[1] == ev.FAIL and "зона за уровнем отмены" in ev.gate(p)[2]
p = fc.plan(False, 100, 110, 120, 80, [("D", bb, [dict(high120, p=140)], [])], cancel=106)
assert [o["name"] for o in p["options"]] == ["начало"] and p["dropped"][0]["name"] == "0.5", p
# фрактал отмены ниже зоны: оба варианта отпали, статус НЕТ, а не почти
low_cancel = dict(senior, frs=[dict(high120, p=95)])
conds, _ = ev.c_plan(True, senior, zone, 90, low_cancel, tg)
assert cond(conds, ev.GATE)[1] == ev.FAIL and cond(conds, "R:R")[1] == ev.NA, conds
assert ev.status(conds) == ev.NO
# отпал вариант 0.5, но начало проходит
half = dict(senior, frs=[dict(high120, p=104)])
conds, p = ev.c_plan(True, senior, zone, 90, half, tg)
assert cond(conds, ev.GATE)[1] == ev.PASS and p["used"] == "начало", conds

# п.2: метод 2 считается от зоны подтверждения внутри зоны A, а не от зоны A
far = dict(senior, frs=[dict(high120, p=300)])
inner = dict(dir="down", lo=103, hi=105, i=10, broken=None, first_test=None, last_test=None)
low_tf = dict(tf="240", bars=bb, frs=[dict(high120, i=4, p=106), dict(high120, i=9, p=107)], atr=0, fvg=[], ifvg=[inner], snr=[], rb=[])
conds, p = ev.c_plan(True, senior, zone, 90, far, tg, (), (low_tf,))
assert p["method"] == 2 and cond(conds, "R:R")[1] == ev.PASS, conds
assert p["options"][0]["entry"] == 103 and p["options"][1]["entry"] == 104, p      # начало и 0.5 зоны подтверждения
assert all(o["entry"] not in (100, 105) for o in p["options"])                       # не от зоны A
assert p["stop"] == 106 and p["confirm"]["kind"] == "IFVG", p                        # ближайший фрактал за границей 105
assert cond(conds, "стоп")[2] == fc.fmt(p["stop"]), conds
# зона вне зоны A не считается подтверждением
outside = dict(inner, lo=108, hi=112)
tf_out = dict(low_tf, ifvg=[outside])
conds, _ = ev.c_plan(True, senior, zone, 90, far, tg, (), (tf_out,))
assert cond(conds, "подтверждение")[1] == ev.FAIL and cond(conds, "подтверждение")[2].startswith("ждём"), conds
assert ev.status(conds) == ev.ALMOST                                                 # зона A протестирована: ждём подтверждение
# зона сформирована до первого теста
before = dict(inner, i=2)
conds, _ = ev.c_plan(True, senior, zone, 90, far, tg, (), (dict(low_tf, ifvg=[before]),))
assert cond(conds, "подтверждение")[1] == ev.FAIL, conds
# зона против сделки не подтверждает
conds, _ = ev.c_plan(True, senior, zone, 90, far, tg, (), (dict(low_tf, ifvg=[dict(inner, dir="up")]),))
assert cond(conds, "подтверждение")[1] == ev.FAIL, conds
# зона A не протестирована: метод 2 не применяется
conds, _ = ev.c_plan(True, senior, dict(zone, first_test=None), 90, far, tg, (), (low_tf,))
assert cond(conds, "R:R")[1] == ev.FAIL and "не протестирована" in cond(conds, "R:R")[2], conds
# нет локального фрактала за зоной подтверждения
conds, _ = ev.c_plan(True, senior, zone, 90, far, tg, (), (dict(low_tf, frs=[dict(high120, p=104.5)]),))
assert "нет локального фрактала" in cond(conds, "R:R")[2], conds
# вход метода 2 за уровнем отмены младшего ТФ отпадает
conds, _ = ev.c_plan(True, senior, zone, 90, far, tg, (), (dict(low_tf, frs=[dict(high120, i=4, p=106), dict(high120, i=9, p=102)]),))
assert cond(conds, ev.GATE)[1] == ev.FAIL, conds
# нет младшего ТФ — R:R unknown
conds, _ = ev.c_plan(True, senior, zone, 90, far, tg, (), ())
assert cond(conds, "R:R")[1] == ev.UNKNOWN, conds

# ход отработан: цена вне зоны ближе к цели, чем ко входу; каждый вариант отдельно
p = fc.plan(True, 100, 110, 75, 120, tg)                         # цель 70, цена 75: оба варианта отработаны
assert p["options"] == [] and [o["name"] for o in p["worked"]] == ["начало", "0.5"], p
assert ev.gate(p)[1] == ev.PASS and ev.c_worked(p)[1] == ev.FAIL and "ход отработан" in ev.c_worked(p)[2]
two = [("D", bb, [low70, dict(low70, p=102)], [])]               # у 0.5 (105) цель 102, у начала (100) цель 70
p = fc.plan(True, 100, 110, 90, 120, two)
assert [o["name"] for o in p["options"]] == ["начало"] and [o["name"] for o in p["worked"]] == ["0.5"], p
assert ev.c_worked(p)[1] == ev.PASS
p = fc.plan(True, 100, 110, 102, 120, [("D", bb, [dict(low70, p=101)], [])])   # цена в зоне — не отработан
assert p["worked"] == [] and len(p["options"]) == 2, p
p = fc.plan(True, 100, 110, 90, 120, tg)                         # цена ближе ко входу
assert p["worked"] == [] and p["used"] == "начало", p
conds, _ = ev.c_plan(True, senior, zone, 75, senior, tg)
assert cond(conds, ev.WORKED)[1] == ev.FAIL and cond(conds, ev.GATE)[1] == ev.PASS, conds
assert ev.status(conds) == ev.NO, conds
conds, _ = ev.c_plan(True, senior, zone, 90, senior, tg)
assert cond(conds, ev.WORKED)[1] == ev.PASS, conds
# метод 1 отработан, у метода 2 входы за уровнем отмены — причина остаётся «ход отработан»
s_cut = dict(tf="240", bars=bb, atr=0, fvg=[], snr=[], rb=[], frs=[dict(high120, p=104), dict(high120, i=9, p=99)],
             ifvg=[dict(dir="down", lo=101, hi=103, i=10, broken=None, first_test=None, last_test=None)])
conds, _ = ev.c_plan(True, senior, zone, 75, senior, tg, (), (s_cut,))
assert cond(conds, ev.WORKED)[1] == ev.FAIL and cond(conds, ev.GATE)[1] == ev.PASS, conds

# метод 2 по ступеням: первая ступень с R:R ≥ 1.5, ниже не спускаемся; на ступени самая ранняя зона
def ztf(tf, zones, frs):
    return dict(tf=tf, bars=bb, frs=frs, atr=0, fvg=zones, ifvg=[], snr=[], rb=[])


inner_h4 = dict(dir="down", lo=101, hi=102, i=10, broken=None, first_test=None, last_test=None)
s_h4 = ztf("240", [inner_h4], [dict(high120, p=125)])           # R:R 1.29 и 1.34 — не проходит
early = dict(inner_h4, lo=103, hi=104, i=8)
late = dict(inner_h4, lo=105, hi=106, i=12)
s_h1 = ztf("60", [late, early], [dict(high120, p=107)])
s_m15 = ztf("15", [inner_h4], [dict(high120, p=103)])
conds, p = ev.c_plan(True, senior, zone, 90, far, tg, (), (s_h4, s_h1, s_m15))
assert p["method"] == 2 and p["step"] == "H1" and cond(conds, "R:R")[1] == ev.PASS, conds
assert p["options"][0]["entry"] == 103, p                       # самая ранняя зона после теста, не поздняя 105
assert "M15" not in cond(conds, "R:R")[2] and "H4 FVG" in cond(conds, "R:R")[2], conds
# первая ступень проходит — дальше не спускаемся
conds, p = ev.c_plan(True, senior, zone, 90, far, tg, (), (s_h1, s_m15))
assert p["step"] == "H1" and "M15" not in cond(conds, "R:R")[2], conds
# ни одна ступень не проходит — метод 2 не проходит
conds, p = ev.c_plan(True, senior, zone, 90, far, tg, (), (s_h4, ztf("60", [], [])))
assert cond(conds, "R:R")[1] == ev.FAIL and p["method"] == 1 and "H1: нет зоны" in cond(conds, "R:R")[2], conds
# ранняя зона до первого теста не берётся
s_old = ztf("60", [dict(early, i=2), late], [dict(high120, p=107)])
conds, p = ev.c_plan(True, senior, zone, 90, far, tg, (), (s_old,))
assert p["options"][0]["entry"] == 105, p

# минимум 50 закрытых свечей действует и на W, и на M
WEEK = 7 * ev.DAY
w = [B(10, 11, 9, 10, t=i * WEEK) for i in range(51)]
assert ev.build({"W": w}, 50 * WEEK)["tfs"]["W"] is not None                  # 50 закрытых — используется
c49 = ev.build({"W": w}, 49 * WEEK)
assert c49["tfs"]["W"] is None                                                # 49 — нет
assert all(r["status"] == ev.UNCHECKED and "мало истории" in r["conds"][0][2] for r in ev.swing(c49, "basics", ["fvg"]))
months = [B(10, 11, 9, 10, t=int(dt.datetime(2025, m, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)) for m in range(1, 13)]
assert ev.build({"M": months}, int(dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc).timestamp() * 1000))["tfs"]["M"] is None

# рынок только внутри зоны входа; цена за дальней границей — зона прошита
p = fc.plan(True, 100, 110, 112, 120, tg)                     # шорт, цена выше зоны
assert p["beyond"] and p["options"] == [] and p["worked"] == [] and p["dropped"] == [], p
assert ev.c_inside(p)[1] == ev.FAIL and "цена за зоной входа" in ev.c_inside(p)[2]
p = fc.plan(False, 100, 110, 95, 80, [("D", bb, [dict(high120, p=140)], [])])   # лонг, цена ниже зоны
assert p["beyond"] and p["options"] == [], p
p = fc.plan(False, 100, 110, 104, 80, [("D", bb, [dict(high120, p=140)], [])])  # лонг, цена в зоне за началом
assert p["options"][0]["name"] == "рынок" and p["options"][0]["entry"] == 104 and not p["beyond"], p
conds, p = ev.c_plan(True, senior, zone, 112, far, tg, (), (s_h1,))
assert cond(conds, ev.BEYOND)[1] == ev.FAIL and cond(conds, "R:R")[1] == ev.NA and p["method"] == 1, conds
assert ev.status(conds) == ev.NO
conds, _ = ev.c_plan(True, senior, zone, 90, senior, tg)
assert cond(conds, ev.BEYOND)[1] == ev.PASS, conds

# уровень отмены ТФ точки A первичен: зона A целиком за ним — метод 2 не применим
conds, p = ev.c_plan(True, senior, zone, 90, low_cancel, tg, (), (s_h1,))
assert cond(conds, ev.GATE)[1] == ev.FAIL and "зона A за уровнем отмены D" in cond(conds, ev.GATE)[2], conds
assert p["method"] == 1 and "alt" not in p and "метод 2 не применим" in cond(conds, "R:R")[2], conds
assert ev.status(conds) == ev.NO
# зона A частично за уровнем: метод 2 перебирается по ступеням
tg90 = [("D", bb, [dict(low70, p=90)], [])]
conds, p = ev.c_plan(True, senior, zone, 97, half, tg90, (), (s_h1,))
assert p["method"] == 2 and p["step"] == "H1" and cond(conds, "R:R")[1] == ev.PASS, conds

# FTA: ближайшая зона противоположного направления или неснятый фрактал между входом и целью, ближе 1R
p = fc.plan(True, 100, 110, 90, 120, tg)                         # вход 100, цель 70, R = 20
def fta_tf(**kw):
    return dict(dict(tf="240", bars=bb, frs=[], fvg=[], snr=[], rb=[], ifvg=[]), **kw)


snr_up = dict(dir="up", lo=95, hi=97, i=3, broken=None)
got = ev.flags(p, None, "down", fta_tfs=[fta_tf(snr=[snr_up])])
assert any(f.startswith("FTA < 1R: SNR H4 95-97") for f in got), got
assert not any("FTA" in f for f in ev.flags(p, None, "down", fta_tfs=[fta_tf(snr=[dict(snr_up, lo=75, hi=78)])]))  # 22 > 1R
assert not any("FTA" in f for f in ev.flags(p, None, "down", fta_tfs=[fta_tf(snr=[dict(snr_up, dir="down")])]))   # по ходу сделки
assert not any("FTA" in f for f in ev.flags(p, None, "down", fta_tfs=[fta_tf(snr=[dict(snr_up, broken=4)])]))     # пробита
got = ev.flags(p, None, "down", fta_tfs=[fta_tf(frs=[dict(low70, p=85), dict(low70, p=88, raids=[4])])])
assert any(f.startswith("FTA < 1R: фрактал H4 85") for f in got), got                                             # снятый 88 не считается

# журнал: схема и неверный формат
import json as _json, tempfile as _tf, pathlib as _pl
tmp = _pl.Path(_tf.mkdtemp())
(tmp / "README.md").write_text("x", encoding="utf-8")
assert ev.read_journal(tmp) == []
(tmp / "a.jsonl").write_text('{"id": 1}\n', encoding="utf-8")
assert ev.read_journal(tmp) is None
full = {k: None for k in ev.JOURNAL_FIELDS}
full.update(status="open", risk_pct=1)
(tmp / "a.jsonl").write_text(_json.dumps(full) + "\n", encoding="utf-8")
assert len(ev.read_journal(tmp)) == 1
(tmp / "a.jsonl").write_text("{сломано\n", encoding="utf-8")
assert ev.read_journal(tmp) is None

# сырой лог: все единичные fail без фильтра
run_dir = tmp / "runs"
ev.write_run(run_dir, 0, [("X", [ev.row("a", [("точка A на W", ev.FAIL, "цена не в POI")])])])
log = _json.loads(next(run_dir.glob("*.jsonl")).read_text(encoding="utf-8"))
assert log["status"] == ev.NO and log["single_fail"] == "точка A на W", log

# poi-sweep-snr: POI, SNR после теста, стоп
fvg_poi = dict(dir="down", lo=100, hi=105, i=2, broken=None, last_test=5)
h4 = dict(tf="240", bars=[B(100, 101, 99, 100, t=1000 + i) for i in range(30)], fvg=[fvg_poi], frs=[low70], range=None)
m15b = [B(100, 101 if i <= 6 else 99.5, 99, 99.2, t=1000 + i) for i in range(30)]
snr_z = dict(dir="down", lo=100, hi=102, i=20, broken=None, first_test=None, last_test=None)
m15 = dict(tf="15", bars=m15b, atr=1, frs=[], snr=[snr_z], fvg=[])
ctx = dict(tfs={"D": None, "240": h4, "60": dict(tf="60", bars=m15b, frs=[], fvg=[]), "15": m15}, price=99)
r = ev.poi_row(ctx, "down", "продолжение", h4, [])
assert r["status"] == ev.READY, r
assert r["plan"]["stop"] == 102.1 and r["plan"]["swept"] is False, r["plan"]      # п.1: за зоной, не за реакцией 101.1
none = dict(h4, fvg=[], frs=[])
assert ev.tested_poi(ctx["tfs"], none, "down") is None
r = ev.poi_row(dict(ctx, tfs=dict(ctx["tfs"], **{"240": none})), "down", "продолжение", none, [])
assert r["status"] == ev.NO and r["conds"][0][1] == ev.FAIL and r["conds"][1][1] == ev.NA, r    # нет POI — не «почти»
ctx2 = dict(ctx, tfs=dict(ctx["tfs"], **{"15": dict(m15, snr=[])}))
r = ev.poi_row(ctx2, "down", "продолжение", h4, [])
assert r["status"] == ev.ALMOST and ev.root_cause(r) == "ждём M15 SNR", r            # п.3
# п.7: порядок внутри свечи определить нельзя — unknown
no_lower = dict(ctx, tfs={"D": None, "240": h4, "60": None, "15": None})
assert ev.tested_poi(no_lower["tfs"], h4, "down")[0] is None
ctx3 = dict(ctx, tfs={"D": None, "240": h4, "60": None, "15": m15})
far_bars = [B(100, 101, 99, 100, t=10 ** 9 + i) for i in range(30)]
r = ev.poi_row(dict(ctx, tfs={"D": None, "240": h4, "60": None, "15": dict(m15, bars=far_bars)}), "down", "продолжение", h4, [])
assert r["status"] == ev.UNCHECKED and "порядок" in r["conds"][0][2], r
# вариант бэктеста V3: без свипа poi-sweep-snr не проходит; по умолчанию выключен
ev.VARIANT["need_sweep"] = True
r = ev.poi_row(ctx, "down", "продолжение", h4, [])
ev.VARIANT["need_sweep"] = False
assert r["status"] == ev.NO and ("свип перед SNR", ev.FAIL, "свипа не было (вариант бэктеста)") in r["conds"], r
assert ev.poi_row(ctx, "down", "продолжение", h4, [])["status"] == ev.READY

# флаги: верх и низ диапазона, против M
opt = dict(options=[dict(name="начало", entry=95, rr=2)], used="начало", stop=200)
assert ev.flags(opt, (0, 100), "up") == ["верх диапазона"]
assert ev.flags(opt, (0, 100), "down") == []
low_opt = dict(options=[dict(name="начало", entry=5, rr=2)], used="начало", stop=200)
assert ev.flags(low_opt, (0, 100), "down") == ["низ диапазона"]
assert ev.flags(opt, (0, 100), "down", "up") == ["против M"]

# карточка не длиннее лимита
big = ev.row("x", [("a" * 50, ev.PASS, "b" * 100)] * 30, info=dict(price=1))
assert len(ev.card("T", big)) <= ev.CARD_LIMIT

# этап 7: нет заглядывания в будущее. Синтетические M15 сводятся в старшие ТФ; результат на дату
# по полным данным совпадает с результатом по данным, обрезанным на эту дату (открытые свечи частичные).
import math, random
UTC = dt.timezone.utc


def synth_m15(days, seed):
    rnd, out, price, drift = random.Random(seed), [], 100.0, 0.0
    start = int(dt.datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)       # понедельник
    for i in range(days * 96):
        if i % 600 == 0:
            drift = rnd.choice((-1, 1)) * 0.0004
        o = price
        c = o * math.exp(rnd.gauss(drift, 0.003))
        out.append(dict(t=start + i * 900000, o=o, h=max(o, c) * (1 + abs(rnd.gauss(0, 0.0015))),
                        l=min(o, c) * (1 - abs(rnd.gauss(0, 0.0015))), c=c))
        price = c
    return out, start


def aggregate(m15, start):
    def month(t):
        d = dt.datetime.fromtimestamp(t / 1000, UTC)
        return int(dt.datetime(d.year, d.month, 1, tzinfo=UTC).timestamp() * 1000)
    keys = {"60": lambda t: t - t % 3600000, "240": lambda t: t - t % (4 * 3600000), "D": lambda t: t - t % ev.DAY,
            "W": lambda t: t - (t - start) % (7 * ev.DAY), "M": month}
    data = {"15": list(m15)}
    for tf, key in keys.items():
        groups = {}
        for b in m15:
            groups.setdefault(key(b["t"]), []).append(b)
        data[tf] = [dict(t=k, o=g[0]["o"], h=max(x["h"] for x in g), l=min(x["l"] for x in g), c=g[-1]["c"])
                    for k, g in sorted(groups.items())]
    return data


m15_all, t0 = synth_m15(420, seed=7)
full_data = aggregate(m15_all, t0)
for shift in (400 * ev.DAY, 405 * ev.DAY + 7 * 3600000 + 20 * 60000):
    at = t0 + shift
    cut_data = aggregate([b for b in m15_all if b["t"] + 900000 <= at], t0)   # будущего нет, открытые свечи частичные
    a = ev.evaluate(full_data, at, None, [], "up")
    b = ev.evaluate(cut_data, at, None, [], "up")
    assert _json.dumps(a, sort_keys=True, default=str) == _json.dumps(b, sort_keys=True, default=str), ev.utc(at)
    assert any(r["status"] != ev.UNCHECKED for r in a), a                       # проверка не на пустых строках
print("OK")
