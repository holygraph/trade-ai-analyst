"""Свечи Bybit (публичный API v5, без ключей) и короткая сводка уровней цифрами.
Запуск:
  python fetch_candles.py --check ТИКЕР [...]    проверить тикеры на Bybit (linear, spot)
  python fetch_candles.py ТИКЕР [...]            сводка уровней по монетам
  python fetch_candles.py --setup BTCUSDT D short НИЗ ВЕРХ [ТФ_СТОПА]  стоп, цель и R:R для зоны входа
ТФ в аргументах: M W D 240 60 15. Даты в UTC, по открытию свечи.
Считаются только закрытые свечи.
"""
import datetime as dt, json, sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).parent
API = "https://api.bybit.com/v5/market/"
LIMIT = 300
TFS = ["M", "W", "D", "240", "60", "15"]     # от старшего к младшему; M только контекст
NAMES = {"M": "M", "W": "W", "D": "D", "240": "H4", "60": "H1", "15": "M15"}
LOWER = {"M": "W", "W": "D", "D": "240", "240": "60", "60": "15"}   # x-1
MINUTES = {"D": 1440, "240": 240, "60": 60, "15": 15}
IMPULSE, WEAK = 1.5, 0.7                     # тело в ATR
MIN_RR = 1.5
STOP_BUFFER = 0.1                            # доля ATR за уровнем стопа
LOOKAHEAD = 20                               # сколько свечей ждать пробоя зоны SNR
FEES = None   # вариант бэктеста (maker, taker, проскальзывание стопа): R:R после комиссий; None — R:R по правилам
SHOW = 3                                     # сколько уровней каждого типа выше и ниже цены


def api(path, **params):
    with urlopen(API + path + "?" + urlencode(params), timeout=20) as r:
        data = json.load(r)
    if data.get("retCode") != 0:
        raise RuntimeError(data.get("retMsg"))
    return data["result"]


def instruments(symbol):
    """Где торгуется тикер: [(категория, статус)] — перпетуал linear, затем spot. Пусто — не найден."""
    found = []
    for cat in ("linear", "spot"):
        try:
            items = api("instruments-info", category=cat, symbol=symbol).get("list", [])
        except RuntimeError:            # Bybit отвечает ошибкой на несуществующий тикер
            items = []
        if items:
            found.append((cat, items[0]["status"]))
    return found


def check(symbols):
    for s in symbols:
        found = instruments(s)
        print(f"{s}: {', '.join(f'{c}:{st}' for c, st in found) if found else 'НЕ НАЙДЕН'}")


def closed_candles(symbol, tf):
    """Возвращает (закрытые свечи по возрастанию времени, открытая свеча или None)."""
    rows = []
    for cat in ("linear", "spot"):      # перпетуал, иначе спот
        try:
            rows = api("kline", category=cat, symbol=symbol, interval=tf, limit=LIMIT)["list"]
        except RuntimeError:
            rows = []
        if rows:
            break
    bars = [dict(t=int(r[0]), o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]))
            for r in reversed(rows)]
    if not bars:
        return [], None
    return bars[:-1], bars[-1]   # Bybit отдаёт текущую незакрытую свечу последней


CACHE = ROOT / "data" / "cache"
PAGE = 1000                                  # максимум свечей за запрос kline


def parse_rows(rows):
    return [dict(t=int(r[0]), o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4])) for r in rows]


def load_history(symbol, tf, since, cache=CACHE):
    """Все свечи с открытием от since (мс UTC) до сейчас, по возрастанию; последняя может быть незакрытой.
    Пагинация по PAGE назад от текущего момента, кэш на диск: data/cache/ТИКЕР_ТФ.json."""
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{symbol}_{tf}.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else dict(cat=None, bars=[], complete=False)
    bars = {b["t"]: b for b in saved["bars"]}
    cats = [saved["cat"]] if saved["cat"] else ["linear", "spot"]

    def pull(end, stop_at):
        """Страницы назад от end, пока не дошли до stop_at или до начала истории. Возвращает (кат., дошли до листинга)."""
        for cat in cats:
            got, first = 0, True
            while True:
                try:
                    rows = api("kline", category=cat, symbol=symbol, interval=tf, end=end, limit=PAGE)["list"]
                except RuntimeError:
                    rows = []
                if not rows:
                    if first:
                        break                       # у этой категории свечей нет — пробуем следующую
                    return cat, True
                first = False
                page = parse_rows(rows)
                for b in page:
                    bars[b["t"]] = b
                got += len(page)
                oldest = min(b["t"] for b in page)
                if oldest <= stop_at:
                    return cat, False
                if len(page) < PAGE:
                    return cat, True
                end = oldest - 1
            if got:
                return cat, True
        return saved["cat"], saved["complete"]

    now = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    if bars:
        cat, _ = pull(now, max(bars))               # дозагрузка новых свечей до последней из кэша
        saved["cat"] = saved["cat"] or cat
        if since < min(bars) and not saved["complete"]:
            cat, done = pull(min(bars) - 1, since)
            saved["complete"] = done
    else:
        cat, done = pull(now, since)
        saved.update(cat=cat, complete=done)
    saved["bars"] = sorted(bars.values(), key=lambda b: b["t"])
    path.write_text(json.dumps(saved), encoding="utf-8")
    return [b for b in saved["bars"] if b["t"] >= since]


def date(bars, i, tf):
    if i is None:
        return "нет"
    stamp = dt.datetime.fromtimestamp(bars[i]["t"] / 1000, dt.timezone.utc)
    return stamp.strftime("%Y-%m-%d" if tf in ("M", "W", "D") else "%Y-%m-%d %H:%M")


def atr(bars, n=14):
    out, prev = [None] * len(bars), None
    for i, b in enumerate(bars):
        tr = b["h"] - b["l"] if i == 0 else max(b["h"] - b["l"], abs(b["h"] - bars[i - 1]["c"]),
                                                abs(b["l"] - bars[i - 1]["c"]))
        if i < n - 1:
            prev = (prev or 0) + tr
        elif i == n - 1:
            prev = (prev + tr) / n
            out[i] = prev
        else:
            prev = (prev * (n - 1) + tr) / n
            out[i] = prev
    return out


def fractals(bars):
    """5 свечей: по 2 слева и справа не заходят за экстремум. Подтверждён на i+2."""
    out = []
    for i in range(2, len(bars) - 2):
        near = [bars[k] for k in (i - 2, i - 1, i + 1, i + 2)]
        if all(bars[i]["h"] > x["h"] for x in near):
            out.append(dict(side="high", i=i, p=bars[i]["h"], raids=[], broken=None))
        if all(bars[i]["l"] < x["l"] for x in near):
            out.append(dict(side="low", i=i, p=bars[i]["l"], raids=[], broken=None))
    return out


def mark_raids(bars, frs):
    """Рейд = фитиль за фракталом и закрытие обратно. Закрытие за фракталом = пробой."""
    for f in frs:
        for j in range(f["i"] + 3, len(bars)):
            b = bars[j]
            if f["side"] == "high" and b["h"] > f["p"]:
                if b["c"] > f["p"]:
                    f["broken"] = j
                    break
                f["raids"].append(j)
            if f["side"] == "low" and b["l"] < f["p"]:
                if b["c"] < f["p"]:
                    f["broken"] = j
                    break
                f["raids"].append(j)


def structure(bars, frs):
    """BOS/CHoCH по закрытиям. Ключевой фрактал (для dealing range) = точка, откуда начался слом."""
    trend, events, hi, lo, last_hi, last_lo = None, [], None, None, None, None
    by_conf = {}
    for f in frs:
        by_conf.setdefault(f["i"] + 2, []).append(f)
    for j, b in enumerate(bars):
        for f in by_conf.get(j, []):
            if f["side"] == "high":
                hi = last_hi = f
            else:
                lo = last_lo = f
        if hi and b["c"] > hi["p"]:
            kind = None if trend is None else ("BOS" if trend == "up" else "CHoCH")
            events.append(dict(kind=kind, dir="up", i=j, level=hi["p"], key=last_lo))
            trend, hi = "up", None
        elif lo and b["c"] < lo["p"]:
            kind = None if trend is None else ("BOS" if trend == "down" else "CHoCH")
            events.append(dict(kind=kind, dir="down", i=j, level=lo["p"], key=last_hi))
            trend, lo = "down", None
    return trend, events


def dealing_range(frs, trend, events):
    """От ключевого экстремума до самого дальнего фрактала после него."""
    if not events or not events[-1]["key"]:
        return None
    key = events[-1]["key"]
    after = [f for f in frs if f["i"] > key["i"] and f["side"] != key["side"]]
    if not after:
        return None
    if trend == "up":
        return key["p"], max(f["p"] for f in after)
    return min(f["p"] for f in after), key["p"]


def position(x, dr):
    """Число только внутри диапазона, иначе вне диапазона."""
    if x < dr[0]:
        return "вне диапазона (ниже)"
    if x > dr[1]:
        return "вне диапазона (выше)"
    pos = (x - dr[0]) / (dr[1] - dr[0])
    return f"{'premium' if pos > 0.5 else 'discount'} ({pos:.2f})"


def phase(frs):
    """HH+HL = up, LH+LL = down, иначе боковик — по двум последним фракталам каждого вида."""
    hs = [f["p"] for f in frs if f["side"] == "high"][-2:]
    ls = [f["p"] for f in frs if f["side"] == "low"][-2:]
    if len(hs) < 2 or len(ls) < 2:
        return "нет данных"
    if hs[1] > hs[0] and ls[1] > ls[0]:
        return "up (HH+HL)"
    if hs[1] < hs[0] and ls[1] < ls[0]:
        return "down (LH+LL)"
    return "боковик"


def zone_state(bars, z, start):
    """Тест = касание фитилём. Слом = закрытие за дальней границей."""
    z.update(tested=False, broken=None, first_test=None, last_test=None)
    for j in range(start, len(bars)):
        b = bars[j]
        touched = b["l"] <= z["hi"] if z["dir"] == "up" else b["h"] >= z["lo"]
        if touched:
            z["tested"] = True
            z["first_test"] = j if z["first_test"] is None else z["first_test"]
            z["last_test"] = j
        if (z["dir"] == "up" and b["c"] < z["lo"]) or (z["dir"] == "down" and b["c"] > z["hi"]):
            z["broken"] = j
            return z
    return z


def fvgs(bars):
    out = []
    for i in range(2, len(bars)):
        a, c = bars[i - 2], bars[i]
        if c["l"] > a["h"]:
            out.append(zone_state(bars, dict(dir="up", lo=a["h"], hi=c["l"], i=i), i + 1))
        elif c["h"] < a["l"]:
            out.append(zone_state(bars, dict(dir="down", lo=c["h"], hi=a["l"], i=i), i + 1))
    return out


def ifvgs(bars, fvg):
    """Сломанный FVG работает как зона обратной стороны; сформирована = дата слома."""
    out = []
    for z in fvg:
        if z["broken"] is not None:
            d = "down" if z["dir"] == "up" else "up"
            out.append(zone_state(bars, dict(dir=d, lo=z["lo"], hi=z["hi"], i=z["broken"]), z["broken"] + 1))
    return out


def rejection_blocks(bars, frs, fvg):
    """Две свечи: уровень снят фитилём любой, обе закрылись обратно, вторая по ходу реакции."""
    out = []
    for j in range(1, len(bars)):
        p, q = bars[j - 1], bars[j]
        bear = q["c"] < q["o"]
        if p["c"] == p["o"] or (p["c"] < p["o"]) == bear:
            continue                                # вторая свеча должна быть противоположной
        high, low = max(p["h"], q["h"]), min(p["l"], q["l"])
        levels = [f["p"] for f in frs if f["i"] + 2 < j - 1 and f["side"] == ("high" if bear else "low")
                  and (f["broken"] is None or f["broken"] >= j - 1)]
        levels += [z["lo"] if bear else z["hi"] for z in fvg
                   if z["i"] < j - 1 and z["dir"] == ("down" if bear else "up")
                   and (z["broken"] is None or z["broken"] >= j - 1)]
        if bear and not [x for x in levels if high > x and max(p["c"], q["c"]) < x]:
            continue
        if not bear and not [x for x in levels if low < x and min(p["c"], q["c"]) > x]:
            continue
        bodies = [max(p["o"], p["c"]), max(q["o"], q["c"])] if bear else [min(p["o"], p["c"]), min(q["o"], q["c"])]
        z = dict(dir="down", lo=min(bodies), hi=high, i=j) if bear else dict(dir="up", lo=low, hi=max(bodies), i=j)
        out.append(zone_state(bars, z, j + 1))
    return out


def snrs(bars, frs, fvg, lower_fvg, lower_tf_ms, lower_start=None):
    """Зона из двух свечей (по телам), пробитие закрытием; дальше зона обратной стороны."""
    out = []
    for i in range(1, len(bars)):
        a, b = bars[i - 1], bars[i]
        if a["c"] > a["o"] and b["c"] < b["o"] and b["c"] < a["o"]:
            res = True                              # buy-to-sell = resistance
        elif a["c"] < a["o"] and b["c"] > b["o"] and b["c"] > a["o"]:
            res = False                             # sell-to-buy = support
        else:
            continue
        lo = min(a["o"], a["c"], b["o"], b["c"])
        hi = max(a["o"], a["c"], b["o"], b["c"])
        for j in range(i + 1, min(i + 1 + LOOKAHEAD, len(bars))):
            c = bars[j]["c"]
            if lo <= c <= hi:
                continue
            if (res and c > hi) or (not res and c < lo):
                d = "up" if res else "down"
                z = zone_state(bars, dict(dir=d, lo=lo, hi=hi, i=j), j + 1)
                z["from_level"] = from_level(bars, frs, fvg, i, j, d)
                z["fvg_lower"] = fvg_inside(lower_fvg, bars[i]["t"], bars[j]["t"], d, lower_tf_ms, lower_start)
                out.append(z)
            break                                   # закрылись за другой границей — зона отработала
    return out


def from_level(bars, frs, fvg, i, j, d):
    """Условие 1 SNR: импульс начался со снятия фрактала или теста FVG."""
    want = "low" if d == "up" else "high"
    for f in frs:
        if f["side"] == want and any(i - 2 <= r <= j for r in f["raids"]):
            return True
    return any(z["dir"] == d and z["i"] < i and z_touch(bars, z, i - 2, j) for z in fvg)


def z_touch(bars, z, a, b):
    return any(bars[k]["l"] <= z["hi"] and bars[k]["h"] >= z["lo"] for k in range(max(a, z["i"] + 1), b + 1))


def fvg_inside(lower, t0, t1, d, step, lower_start=None):
    """Условие 2 SNR: FVG на x-1 внутри импульса. None = не проверено (нет ТФ или истории)."""
    if lower is None or lower_start is None or t0 < lower_start:
        return None
    return any(z["dir"] == d and t0 <= z["t"] <= t1 + step for z in lower)


def fmt(x):
    return f"{x:.2f}" if x >= 100 else f"{x:.5g}"


def near_zones(zones, price):
    """Активные зоны: цена внутри, затем до SHOW ближайших выше и ниже цены."""
    live = [z for z in zones if z["broken"] is None]
    inside = [z for z in live if z["lo"] <= price <= z["hi"]][:SHOW]
    above = sorted([z for z in live if z["lo"] > price], key=lambda z: z["lo"])[:SHOW]
    below = sorted([z for z in live if z["hi"] < price], key=lambda z: -z["hi"])[:SHOW]
    return [("внутри", z) for z in inside] + [("выше", z) for z in above] + [("ниже", z) for z in below]


def zone_txt(bars, z, tf):
    start = z["hi"] if z["dir"] == "up" else z["lo"]   # начало зоны: куда цена приходит первой
    mid = (z["lo"] + z["hi"]) / 2
    test = "нет"
    if z["tested"]:
        test = date(bars, z["first_test"], tf)
        if z["last_test"] != z["first_test"]:
            test += " .. " + date(bars, z["last_test"], tf)
    extra = ""
    if "from_level" in z:
        fl = {True: "да", False: "нет", None: "не проверено"}[z["fvg_lower"]]
        extra = f" от_уровня={'да' if z['from_level'] else 'нет'} fvg_x-1={fl}"
    return (f"{z['dir']} {fmt(z['lo'])}-{fmt(z['hi'])} начало={fmt(start)} 0.5={fmt(mid)} "
            f"сформирована={date(bars, z['i'], tf)} тест={test}{extra}")


def summary(symbol):
    print(f"# {symbol}")
    data = {tf: closed_candles(symbol, tf) for tf in TFS}
    if not data["15"][0]:
        print("нет данных")
        return
    price = data["15"][1]["c"] if data["15"][1] else data["15"][0][-1]["c"]
    month = data["M"][1]["o"] if data["M"][1] else None
    print(f"цена={fmt(price)} открытие_месяца={fmt(month) if month else 'нет данных'}")
    fvg_by_tf = {}
    for tf in reversed(TFS):                        # от младшего: нужен FVG на x-1
        bars = data[tf][0]
        z = fvgs(bars)
        for x in z:
            x["t"] = bars[x["i"]]["t"]
        fvg_by_tf[tf] = z
    starts = {tf: data[tf][0][0]["t"] for tf in TFS if data[tf][0]}
    for tf in TFS:
        tf_summary(tf, data[tf][0], price, fvg_by_tf, starts)


def tf_summary(tf, bars, price, fvg_by_tf, starts):
    print(f"\n## {NAMES[tf]} (закрытых свечей: {len(bars)})")
    if len(bars) < 50:              # как MIN_BARS в evaluate.py
        print("мало данных, не проверено")
        return
    a = atr(bars)
    frs = fractals(bars)
    mark_raids(bars, frs)
    trend, events = structure(bars, frs)
    fvg = fvg_by_tf[tf]
    low_tf = LOWER.get(tf)
    lower = fvg_by_tf.get(low_tf) if low_tf else None
    step = MINUTES.get(low_tf, 0) * 60000
    snr = snrs(bars, frs, fvg, lower, step, starts.get(low_tf))
    rb = rejection_blocks(bars, frs, fvg)
    ifvg = ifvgs(bars, fvg)
    last = bars[-1]
    body = abs(last["c"] - last["o"]) / a[-1] if a[-1] else 0
    kind = "импульс" if body >= IMPULSE else ("вяло" if body < WEAK else "нейтрально")
    print(f"ATR14={fmt(a[-1])} тело_последней={body:.2f}ATR ({kind})")
    print(f"фаза={phase(frs)} тренд_по_закрытиям={trend or 'нет данных'}")
    for e in events[-3:]:
        print(f"слом: {e['kind'] or 'старт'} {e['dir']} уровень={fmt(e['level'])} дата={date(bars, e['i'], tf)}")
    if events and events[-1]["key"]:
        k = events[-1]["key"]
        print(f"ключевой_фрактал={fmt(k['p'])} {k['side']} дата={date(bars, k['i'], tf)}")
    for side in ("high", "low"):
        k = last_fractal(frs, side)
        if k:
            print(f"последний_{side}_для_стопа={fmt(k['p'])} дата={date(bars, k['i'], tf)}")
    dr = dealing_range(frs, trend, events)
    if dr:
        print(f"диапазон={fmt(dr[0])}-{fmt(dr[1])} 0.5={fmt(sum(dr) / 2)} цена_в={position(price, dr)}")
    else:
        print("диапазон: не сформирован")
    live = [f for f in frs if f["broken"] is None]
    above = sorted([f for f in live if f["side"] == "high" and not f["raids"] and f["p"] > price], key=lambda f: f["p"])[:SHOW]
    below = sorted([f for f in live if f["side"] == "low" and not f["raids"] and f["p"] < price], key=lambda f: -f["p"])[:SHOW]
    print("неснятые_хаи_выше: " + (", ".join(f"{fmt(f['p'])} ({date(bars, f['i'], tf)})" for f in above) or "нет"))
    print("неснятые_лоу_ниже: " + (", ".join(f"{fmt(f['p'])} ({date(bars, f['i'], tf)})" for f in below) or "нет"))
    for f in sorted([f for f in live if f["raids"]], key=lambda f: f["raids"][-1])[-SHOW:]:
        tag = "ресвип" if len(f["raids"]) > 1 else "рейд"
        print(f"{tag} {f['side']} {fmt(f['p'])} даты={', '.join(date(bars, r, tf) for r in f['raids'][-SHOW:])}")
    for name, zones in (("FVG", fvg), ("IFVG", ifvg), ("SNR", snr), ("RB", rb)):
        for where, z in near_zones(zones, price):
            print(f"{name} {where} {zone_txt(bars, z, tf)}")


def last_fractal(frs, side):
    """Последний непробитый фрактал: закрытие за ним = CHoCH, за ним ставится стоп."""
    for f in reversed(frs):
        if f["side"] == side and f["broken"] is None:
            return f
    return None


def stop_price(frs, atr_value, short):
    """Последний фрактал против тренда плюс буфер. Возвращает (стоп, фрактал) или None."""
    f = last_fractal(frs, "high" if short else "low")
    if not f or atr_value is None:
        return None
    return (f["p"] + STOP_BUFFER * atr_value if short else f["p"] - STOP_BUFFER * atr_value), f


def far_stop(short, lo, hi, base, atr_value):
    """Стоп за дальней границей зоны входа плюс буфер; берётся более далёкий из двух уровней."""
    zone = hi + STOP_BUFFER * atr_value if short else lo - STOP_BUFFER * atr_value
    return max(zone, base) if short else min(zone, base)


def nearest_target(entry, short, tfs, extras=()):
    """Ближайший неснятый фрактал или незаполненный FVG противоположного направления.
    Незаполненный = нет закрытия за дальней границей; цель — ближняя к входу граница."""
    best = None
    for tf, bars, frs, fvg in tfs:
        side = "low" if short else "high"
        cands = [(f["p"], f"фрактал {side} {NAMES[tf]} {date(bars, f['i'], tf)}") for f in frs
                 if f["side"] == side and not f["raids"] and f["broken"] is None]
        want = "up" if short else "down"
        cands += [(z["hi"] if short else z["lo"], f"FVG {want} {NAMES[tf]} {fmt(z['lo'])}-{fmt(z['hi'])}")
                  for z in fvg if z["dir"] == want and z["broken"] is None]
        for p, label in list(cands) + list(extras):
            if (short and p < entry) or (not short and p > entry):
                if best is None or abs(entry - p) < abs(entry - best[0]):
                    best = (p, label)
    return best


def rr_value(entry, stop, target, risk, market, fees=None):
    """R:R по правилам. С комиссиями fees или вариантом бэктеста FEES (maker, taker, проскальзывание):
    (прибыль − комиссии) / (риск + комиссии), вход по рынку taker, лимит maker, тейк maker, стоп taker плюс проскальзывание."""
    gain = abs(entry - target)
    fees = fees or FEES
    if fees is None:
        return gain / risk
    maker, taker, slip = fees
    fee_in = (taker if market else maker) * entry
    return (gain - fee_in - maker * target) / (risk + fee_in + (taker + slip) * stop)


def plan(short, lo, hi, price, stop, tfs, extras=(), cancel=None):
    """R:R от начала зоны и от 0.5. Цена внутри зоны за началом — начало заменяется рынком,
    пройденный 0.5 пропускается. Цена за дальней границей — зона прошита, вариантов нет (beyond).
    Вариант входа за уровнем отмены cancel отпадает (dropped). Ход отработан: цена вне зоны
    и ближе к цели, чем ко входу, — вариант отпадает (worked)."""
    start, mid = (lo if short else hi), (lo + hi) / 2
    passed_start = price > start if short else price < start
    passed_mid = price > mid if short else price < mid
    in_zone = lo <= price <= hi
    beyond = price > hi if short else price < lo
    entries = [] if beyond else [("рынок" if passed_start else "начало", price if passed_start else start)]
    if not passed_mid and lo != hi:
        entries.append(("0.5", mid))
    options, dropped, worked, used = [], [], [], None
    for name, entry in entries:
        if cancel is not None and (entry >= cancel if short else entry <= cancel):
            dropped.append(dict(name=name, entry=entry))
            continue
        target = nearest_target(entry, short, tfs, extras)
        if target and not in_zone and abs(price - target[0]) < abs(price - entry):
            worked.append(dict(name=name, entry=entry, target=target))
            continue
        risk = stop - entry if short else entry - stop
        rr = rr_value(entry, stop, target[0], risk, name == "рынок") if target and risk > 0 else None
        options.append(dict(name=name, entry=entry, target=target, risk=risk, rr=rr))
        if used is None and rr is not None and rr >= MIN_RR:
            used = name
    return dict(stop=stop, options=options, dropped=dropped, worked=worked, used=used, cancel=cancel, price=price,
                beyond=beyond, far=hi if short else lo)


def plan_txt(p):
    rows = [f"цена {fmt(p['price'])} за дальней границей зоны {fmt(p['far'])}: зона прошита, входа нет"] if p.get("beyond") else []
    rows += [f"вход {o['name']}={fmt(o['entry'])}: за уровнем отмены {fmt(p['cancel'])}, отпал" for o in p["dropped"]]
    rows += [f"вход {o['name']}={fmt(o['entry'])}: ход отработан, цена {fmt(p['price'])} ближе к цели {fmt(o['target'][0])}, отпал"
             for o in p.get("worked", [])]
    for o in p["options"]:
        if o["rr"] is None:
            rows.append(f"вход {o['name']}={fmt(o['entry'])}: нет цели или стоп не за входом")
        else:
            rows.append(f"вход {o['name']}={fmt(o['entry'])} стоп={fmt(p['stop'])} цель={fmt(o['target'][0])} "
                        f"({o['target'][1]}) R:R={o['rr']:.2f}")
    rows.append(f"использован вход: {p['used']}" if p["used"] else f"R:R < {MIN_RR}: не проходит")
    return rows


def load_tf(symbol, tf):
    closed, _ = closed_candles(symbol, tf)
    frs = fractals(closed)
    mark_raids(closed, frs)
    trend, events = structure(closed, frs)
    return closed, frs, fvgs(closed), trend, events


def setup(symbol, tf, side, lo, hi, stop_tf=None):
    """Стоп, ближайшая цель и R:R для зоны входа: от начала зоны и от 0.5."""
    short = side == "short"
    stop_tf = stop_tf or tf
    bars, frs, fvg, trend, events = load_tf(symbol, tf)
    tfs = [(tf, bars, frs, fvg)]
    if tf in LOWER:
        b2, f2, z2 = load_tf(symbol, LOWER[tf])[:3]
        tfs.append((LOWER[tf], b2, f2, z2))
    sb, sf = (bars, frs) if stop_tf == tf else load_tf(symbol, stop_tf)[:2]
    print(f"# {symbol} {NAMES[tf]} {side} зона {fmt(lo)}-{fmt(hi)}")
    st = stop_price(sf, atr(sb)[-1], short)
    if not st:
        print("стоп: нет фрактала против тренда, нет позиции")
        return
    a = atr(sb)[-1]
    stop = far_stop(short, lo, hi, st[0], a)
    print(f"стоп={fmt(stop)} (фрактал {fmt(st[1]['p'])} {NAMES[stop_tf]} {date(sb, st[1]['i'], stop_tf)} "
          f"или дальняя граница зоны, буфер {STOP_BUFFER} ATR)")
    dr = dealing_range(frs, trend, events)
    print(f"диапазон {NAMES[tf]}: {fmt(dr[0])}-{fmt(dr[1])}" if dr else f"диапазон {NAMES[tf]}: не сформирован")
    now = closed_candles(symbol, "15")[1]
    for row in plan_txt(plan(short, lo, hi, now["c"] if now else bars[-1]["c"], stop, tfs, cancel=st[1]["p"])):
        print(row)


def main():
    args = sys.argv[1:]
    if not args or args == ["--check"]:
        print(__doc__)
        return
    if args[0] == "--check":
        check(args[1:])
        return
    if args[0] == "--setup":
        setup(args[1].upper(), args[2], args[3], float(args[4]), float(args[5]), args[6] if len(args) > 6 else None)
        return
    for i, s in enumerate(args):
        if i:
            print()
        summary(s.upper())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
