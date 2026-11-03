"""Тесты analyst.py и base_rates без сети: python test_analyst.py"""
import datetime as dt, json, math, random, sys
from pathlib import Path

import analyst as an
import evaluate as ev
import fetch_candles as fc

sys.path.insert(0, str(Path(__file__).resolve().parent / "research"))
import base_rates as brs
import stage9 as s9

# разбор аргументов
u = an.parse_args(["nearusdt", "LONG", "4,06", "3.99", "4.15", "4.3"])
assert u == dict(symbol="NEARUSDT", side="up", entry=4.06, stop=3.99, takes=[4.15, 4.3]), u
assert an.parse_args(["BTCUSDT"])["side"] is None
assert an.parse_args(["BTCUSDT", "шорт", "100"]) == dict(symbol="BTCUSDT", side="down", entry=100.0, stop=None, takes=[])
def parse_error(args):
    try:
        an.parse_args(args)
    except ValueError as e:
        return str(e)
    raise AssertionError(args)


assert parse_error([]).startswith("Синтаксис")
assert "нужна сторона" in parse_error(["BTCUSDT", "100"]) or "сторона" in parse_error(["BTCUSDT", "100"])
assert "не число: abc" in parse_error(["BTCUSDT", "long", "abc"])
assert "больше нуля" in parse_error(["BTCUSDT", "long", "-1"])
assert "сторона «up»: нужно long или short" in parse_error(["BTCUSDT", "up", "100"])     # п.5: сторона не long/short
assert "сторона «вверх»" in parse_error(["BTCUSDT", "вверх"])
assert "не похож на тикер" in parse_error(["BTC/USDT", "long"])
assert "стоп 101 не по ходу сделки: для long ниже входа 100" in parse_error(["BTCUSDT", "long", "100", "101"])
assert "стоп 99 не по ходу сделки: для short выше входа 100" in parse_error(["BTCUSDT", "short", "100", "99"])
assert "тейк 2 (99) не по ходу сделки" in parse_error(["BTCUSDT", "long", "100", "98", "104", "99"])

# п.5: запуск без сети, неизвестный тикер, тикер только на споте — ясное сообщение без трассировки
from urllib.error import URLError
real_api, real_urlopen = fc.api, fc.urlopen


def no_network(*a, **k):
    raise URLError("getaddrinfo failed")


fc.urlopen = no_network
msg = an.run(["BTCUSDT", "long"])
fc.urlopen = real_urlopen
assert msg.startswith("BTCUSDT: нет данных от Bybit (api.bybit.com): URLError") and "Проверь интернет" in msg and "Traceback" not in msg, msg
fc.api = lambda path, **p: (_ for _ in ()).throw(RuntimeError("params error: symbol invalid"))
assert an.run(["NOSUCHCOINUSDT", "long"]).startswith("NOSUCHCOINUSDT: тикер не найден на Bybit (linear, spot)")


def spot_only(path, **p):
    if p.get("category") == "linear":
        raise RuntimeError("params error: symbol invalid")
    if path == "instruments-info":
        return {"list": [{"status": "Trading"}]}
    return {"list": [[str(i * 900000), "1", "1.1", "0.9", "1.05"] for i in range(5, 0, -1)]}


fc.api = spot_only
assert fc.instruments("SPOTONLY") == [("spot", "Trading")]
closed, current = fc.closed_candles("SPOTONLY", "15")
assert len(closed) == 4 and current["t"] == 5 * 900000, (closed, current)      # данные со спота
fc.api = real_api

# R:R после комиссий: (прибыль − комиссии) / (риск + комиссии)
net = an.net_rr(100, 98, 104, False)
assert abs(net - (4 - 100 * 0.0002 - 104 * 0.0002) / (2 + 100 * 0.0002 + 98 * 0.00075)) < 1e-12 and net < 2
assert an.net_rr(100, 98, 104, True) < net                              # рынок дороже лимита
assert an.net_rr(100, 100, 104, False) is None

# статус строки для стороны
row = lambda name, status, conds, side=None, plan=None: dict(name=name, status=status, conds=conds, plan=plan,
                                                            info=dict(side=side) if side else {})
assert an.line_status(row("a-to-b intraweek продолжение шорт", ev.READY, [], "down"), "up") == (an.NO, "по тренду только шорт")
assert an.line_status(row("a-to-b intraweek продолжение лонг", ev.READY, [], "up"), "up")[0] == an.PASS_
wait = row("poi-sweep-snr коррекция лонг", ev.ALMOST, [("SNR", ev.FAIL, "ждём M15 SNR")])
assert an.line_status(wait, "up") == (an.WAIT, "ждём M15 SNR") and an.line_status(wait, "down")[0] == an.NO   # сторона из названия
rr_almost = row("basics продолжение лонг", ev.ALMOST, [("R:R", ev.FAIL, "метод 1: начало 1.2", 1.2)], "up")
assert an.line_status(rr_almost, "up")[0] == an.NO                      # почти по R:R — не ЖДАТЬ
assert an.line_status(row("basics продолжение лонг", ev.UNCHECKED, [("x", ev.UNKNOWN, "нет данных")], "up"), "up")[0] == an.UNCHECKED

# поправки к плану: стоп ближе правила, снятие не отменяет идею, тейки в R, вне правил
bb = [dict(t=i, o=1, h=1, l=1, c=1) for i in range(30)]
c = dict(tfs={"D": dict(tf="D", bars=bb, frs=[], fvg=[], snr=[], rb=[], ifvg=[]), "240": None, "60": None})
params = dict(stop=9.5, target=11.0, cancel=9.6, lo=9.9, hi=10.1, advise=True, label="по правилам тест")
lines = an.user_plan(dict(entry=10.0, stop=9.7, takes=[10.2, 11.0, 11.5]), params, c, 10.0, False)
text = "\n".join(lines)
assert "Вход 10: внутри зоны 9.9-10.1; до уровня отмены 9.6" in text, text
assert "ближе правила 9.5 на 2.00% (0.40R)" in text and "не отменяет идею" in text and "вне зоны входа" in text, text
# п.1: R:R при стопе пользователя и при стопе по правилам, «ниже 1.5» прямо
assert "Т1 10.2: при вашем стопе R:R 0.67 /" in text and "при стопе по правилам R:R до Т1 0.40 /" in text and "— ниже 1.5" in text, text
assert "Т2 11: при вашем стопе R:R 3.33" in text and "при стопе по правилам R:R до Т2 2.00" in text, text
t2 = next(x for x in lines if x.startswith("Т2"))
assert "ниже 1.5" not in t2, t2
# п.2: положение тейка относительно цели
assert "ближе цели по правилам 11" in lines[3] and "на цели по правилам" in t2 and "дальше цели по правилам 11" in lines[5], lines
assert "в правилах" not in text
assert lines[-1].startswith("Итог: по правилам стоп 9.5, цель 11; ваш стоп 9.7 — поправка: перенести на 9.5"), lines[-1]
kill = "\n".join(an.user_plan(dict(entry=10.0, stop=9.4, takes=[]), params, c, 10.0, False))
assert "дальше правила 9.5" in kill and "снятие отменяет идею" in kill, kill
assert "ошибка" in "\n".join(an.user_plan(dict(entry=10.0, stop=10.1, takes=[]), params, c, 10.0, False))
assert "выше зоны 9.9-10.1 на 3.00%" in "\n".join(an.user_plan(dict(entry=10.4, stop=None, takes=[]), params, c, 10.0, False))
# п.1–2: без ПРОХОДИТ/ЖДАТЬ с планом — ориентир, без рекомендации переносить
orient = dict(params, advise=False, label="ориентир по глоссарию (стратегия не проходит)")
o_lines = an.user_plan(dict(entry=10.0, stop=9.7, takes=[10.2]), orient, c, 10.0, False)
o_text = "\n".join(o_lines)
assert "ближе ориентира 9.5" in o_text and "при стопе ориентира R:R до Т1 0.40" in o_text and "перенести" not in o_text, o_text
assert o_lines[-1] == "Итог: ориентир по глоссарию (стратегия не проходит): стоп 9.5, цель 11; рекомендации переносить стоп нет", o_lines[-1]
r_no = dict(name="basics продолжение лонг", plan=dict(stop=9.5, cancel=9.6, options=[dict(name="начало", entry=10, target=(11, ""), rr=2)],
                                                     used="начало"), info=dict(zone=dict(lo=9.9, hi=10.1)), status=ev.NO, conds=[])
assert an.row_params(r_no, an.NO)["advise"] is False and "стратегия не проходит" in an.row_params(r_no, an.NO)["label"]
assert an.row_params(r_no, an.WAIT)["advise"] is True and an.row_params(r_no, an.PASS_)["advise"] is True

# частота по истории: уровни, ближайший уровень не ниже R тейка, мало данных
lv = lambda rate, n: dict(hit=round(rate * n), n=n, rate=rate, ci=[rate - 0.05, rate + 0.05], random=0.5, low_sample=n < 30)
rates = dict(groups={"все": dict(trades=500, levels={"0.5": lv(0.7, 500), "1": lv(0.5, 500), "1.5": lv(0.4, 500),
                                                     "2": lv(0.33, 500), "3": lv(0.2, 500)}),
                     "basics продолжение": dict(trades=20, levels={"1": lv(0.6, 20), "2": lv(0.4, 20)})})
assert an.bucket(rates["groups"]["все"]["levels"], 1.2) == 1.5 and an.bucket(rates["groups"]["все"]["levels"], 3.5) is None
h = an.history_lines(rates, "все", [1.2, 4.0], [], None, "up")
# п.3: база случайного блуждания рядом и пометка относительно неё
assert "500 сделок, не прогноз" in h[0] and "до 1R 50% [45–55] (случайно 50%, в пределах случайного)" in h[0], h
assert "до 2R 33% [28–38] (случайно 33%, в пределах случайного)" in h[0], h
assert "Т1 1.20R → до 1.5R 40% [35–45] (случайно 40%, в пределах случайного)" in h[1] and "выше уровней таблицы" in h[2], h
assert len(h) == 3                                                       # без пометки «ориентировочная»
assert an.versus_random(lv(0.7, 500), 1) == "выше случайного" and an.versus_random(lv(0.3, 500), 1) == "ниже случайного"
h2 = an.history_lines(rates, "все", [1.2], [], None, "up", approximate=True)
assert h2[-1] == "  Таблица построена для стопов по правилам, оценка ориентировочная", h2
assert "мало данных" in an.history_lines(rates, "basics продолжение", [], [], None, "up")[0]
journal = [dict(status="closed", result_r=1.0, strategy="s", side="long")] * 30
assert an.history_lines(rates, "все", [], journal, "s", "up")[0].startswith("Журнал (30 похожих)")
assert not any("процент успеха" in x for x in h)

# stats/base_rates.json: строки стратегий, уровни, база 1/(1+k), доли внутри ДИ
data = json.loads((Path(__file__).resolve().parent / "stats" / "base_rates.json").read_text(encoding="utf-8"))
for name in ["все"] + [f"{s} {k}" for s in ("basics", "a-to-b swing", "a-to-b intraweek", "poi-sweep-snr") for k in ("продолжение", "коррекция")]:
    g = data["groups"][name]
    for k in ("0.5", "1", "1.5", "2", "3"):
        x = g["levels"][k]
        assert abs(x["random"] - 1 / (1 + float(k))) < 1e-12 and x["low_sample"] == (x["n"] < 30)
        assert x["n"] == 0 or x["ci"][0] <= x["rate"] <= x["ci"][1], (name, k, x)
assert data["groups"]["все"]["levels"]["1"]["n"] > 1000
# доля дошедших не растёт с уровнем
rates_all = [data["groups"]["все"]["levels"][k]["rate"] for k in ("0.5", "1", "1.5", "2", "3")]
assert rates_all == sorted(rates_all, reverse=True), rates_all
lo, hi = brs.wilson(50, 100)
assert lo < 0.5 < hi and brs.wilson(0, 0) == (None, None)
# reach: уровень k·R раньше стопа, стоп в одной свече — стоп
bars = [dict(t=i * 900000, o=100, h=100.5, l=99.5, c=100) for i in range(10)]
bars[3] = dict(t=3 * 900000, o=100, h=102.2, l=99.5, c=101)
bars[5] = dict(t=5 * 900000, o=100, h=100.5, l=97, c=99)
fast = s9.Fast(bars)
trade = dict(side="up", entry=100.0, stop=98.0, t_in_ms=900000, entry_name="0.5")
assert brs.reach(fast, trade, 1) is True and brs.reach(fast, trade, 2) is False and brs.reach(fast, trade, 0.5) is True

# формат вывода на синтетических свечах: заголовок, 8 строк стратегий, не длиннее 2500
UTC = dt.timezone.utc


def synth(days, seed):
    rnd, out, price, drift = random.Random(seed), [], 100.0, 0.0
    start = int(dt.datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    for i in range(days * 96):
        if i % 600 == 0:
            drift = rnd.choice((-1, 1)) * 0.0004
        o = price
        cl = o * math.exp(rnd.gauss(drift, 0.003))
        out.append(dict(t=start + i * 900000, o=o, h=max(o, cl) * 1.001, l=min(o, cl) * 0.999, c=cl))
        price = cl
    keys = {"60": lambda t: t - t % 3600000, "240": lambda t: t - t % 14400000, "D": lambda t: t - t % ev.DAY,
            "W": lambda t: t - (t - start) % (7 * ev.DAY),
            "M": lambda t: int(dt.datetime(*dt.datetime.fromtimestamp(t / 1000, UTC).timetuple()[:2], 1, tzinfo=UTC).timestamp() * 1000)}
    data = {"15": out}
    for tf, key in keys.items():
        groups = {}
        for b in out:
            groups.setdefault(key(b["t"]), []).append(b)
        data[tf] = [dict(t=k, o=g[0]["o"], h=max(x["h"] for x in g), l=min(x["l"] for x in g), c=g[-1]["c"]) for k, g in sorted(groups.items())]
    return data, start


data, start = synth(420, 11)
asof = start + 410 * ev.DAY
rows = ev.evaluate(data, asof, None, [], "up")
cc = ev.build(data, asof)
price = cc["price"]
for side in ("up", "down"):
    u = dict(symbol="TESTUSDT", side=side, entry=price, stop=price * (0.98 if side == "up" else 1.02),
             takes=[price * (1.03 if side == "up" else 0.97)])
    out = an.report(u, rows, cc, price, "2025-02-14 00:00", "linear:Trading", [], rates, asof)
    lines = out.split("\n")
    assert lines[0].startswith(f"TESTUSDT {'LONG' if side == 'up' else 'SHORT'} | цена ") and "| данные UTC 2025-02-14 00:00 |" in lines[0], lines[0]
    assert lines[1].split(" — ")[0] in ("Вердикт по правилам: ВХОД ДА", "Вердикт по правилам: ЖДАТЬ", "Вердикт по правилам: НЕТ"), lines[1]
    assert sum(1 for x in lines if x.startswith("  ") and any(x.strip().startswith(f"{b} {k}:") for b, k in an.LINES)) == 8, out
    assert "Ваш план:" in out and "Историческая частота похожих сетапов" in out and "процент успеха" not in out
    hist = next(x for x in lines if x.startswith("Историческая частота"))
    assert "(случайно 50%, " in hist and "случайного" in hist, hist
    assert lines[-1] == "Проверка правил, не рекомендация; проверь экономический календарь." and len(out) <= an.OUTPUT_LIMIT, len(out)
both = an.both_sides(dict(symbol="TESTUSDT"), rows, price, "2025-02-14 00:00", "linear:Trading")
assert both.split("\n")[1].startswith("LONG: ") and both.split("\n")[2].startswith("SHORT: ")
# ограничение длины: справочные строки уходят первыми, последняя строка сохраняется
long_lines = ["шапка"] + ["x" * 200] * 20 + ["Справочно, вне правил: " + "y" * 100, "Проверка правил, не рекомендация; проверь экономический календарь."]
cut = an.fit(long_lines)
assert len(cut) <= an.OUTPUT_LIMIT and cut.endswith("календарь.") and "Справочно" not in cut

# язык вывода: en по умолчанию, ru без изменений, --lang важнее ANALYST_LANG
import ast, re
import i18n

CYR = re.compile(r"[А-Яа-яЁё]")
argv = ["analyst.py", "BTCUSDT", "--lang", "ru", "long"]
assert i18n.pick(argv, {}) == "ru" and argv == ["analyst.py", "BTCUSDT", "long"]
assert i18n.pick(["a", "--lang=en"], {"ANALYST_LANG": "ru"}) == "en"
assert i18n.pick(["a"], {"ANALYST_LANG": "ru"}) == "ru" and i18n.pick(["a"], {}) == "en"
try:
    i18n.pick(["a", "--lang", "de"], {})
    raise AssertionError("de")
except SystemExit:
    pass
outputs = [an.report(dict(symbol="TESTUSDT", side=side, entry=price, stop=price * (0.98 if side == "up" else 1.02),
                          takes=[price * (1.03 if side == "up" else 0.97)]), rows, cc, price, "2025-02-14 00:00",
                     "linear:Trading", [], rates, asof) for side in ("up", "down")]
outputs += [both, ev.one_line("TESTUSDT", rows, "2025-02-14 00:00")] + [ev.card("TESTUSDT", r) for r in rows]
outputs += [parse_error(a) for a in ([], ["BTCUSDT", "100"], ["BTCUSDT", "long", "100", "101"], ["BTCUSDT", "short", "100", "99"],
                                     ["BTCUSDT", "long", "100", "98", "104", "99"], ["BTC/USDT", "long"])]
for text in outputs:
    assert i18n.tr(text, "ru") == text
    en = i18n.tr(text, "en")
    assert not CYR.search(en), [x for x in en.split("\n") if CYR.search(x)]
i18n.LANG = "en"                        # как после setup(): лимит 2500 считается по английскому тексту
for side in ("up", "down"):
    out = an.report(dict(symbol="TESTUSDT", side=side, entry=price, stop=price * (0.98 if side == "up" else 1.02),
                         takes=[price * (1.03 if side == "up" else 0.97)]), rows, cc, price, "2025-02-14 00:00",
                    "linear:Trading", [], rates, asof)
    assert not CYR.search(out) and len(out) <= an.OUTPUT_LIMIT, len(out)
    assert out.split("\n")[-1] == "Rules check, not financial advice; check the economic calendar.", out
cut = an.fit(long_lines)
assert len(cut) <= an.OUTPUT_LIMIT and cut.endswith("calendar.") and "For reference" not in cut
i18n.LANG = None
# каждый русский фрагмент строк вывода переводится (кроме частей слов, которые переводятся целой фразой)
PARTS = {" уровн", "я", "ем"}
root = Path(__file__).resolve().parent
for name in ("analyst.py", "evaluate.py", "fetch_candles.py", "journal.py",
             "research/backtest.py", "research/stage9.py", "research/base_rates.py"):
    tree = ast.parse((root / name).read_text(encoding="utf-8"))
    docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs and n.value not in PARTS:
            assert not CYR.search(i18n.tr(n.value, "en")), (name, n.lineno, n.value)
assert not CYR.search(i18n.tr("до уровня отмены 5; за уровнем отмены 6", "en"))
print("OK")
