"""Статусы стратегий считает код. Определения правил — в коде ниже.
Основной вход для пользователя — analyst.py (/analyst); evaluate.py — расчёт и отладка.
Запуск:
  python evaluate.py ТИКЕР [...]              одна строка на монету и список карточек
  python evaluate.py --cards [тикер ...]      то же и сами карточки, не больше 8 за прогон
  python evaluate.py --card ТИКЕР "стратегия" одна карточка до 2000 символов
  python evaluate.py --detail ТИКЕР           условия всех стратегий монеты
  python evaluate.py --why ТИКЕР [стратегия]  все fail и unknown по стратегиям (в строке монеты у НЕТ только первый)
  --asof "ГГГГ-ММ-ДД ЧЧ:ММ" к любой команде: проверка на дату (UTC) по свечам, закрытым к этому моменту;
                                              история из data/cache/ с дозагрузкой, монета без истории на дату пропускается
Прогон по списку пишет сырой лог в runs/ со всеми единичными fail без фильтра.
Функции расчёта получают данные и момент времени аргументами и не обращаются к «сейчас»:
время берётся только в main().
"""
import calendar, datetime as dt, json, sys, time
from pathlib import Path

import fetch_candles as fc
import i18n

PASS, FAIL, UNKNOWN, NA = "pass", "fail", "unknown", "не применимо"
READY, NO, ALMOST, UNCHECKED = "ГОТОВ", "НЕТ", "НЕТ-ПОЧТИ", "НЕ ПРОВЕРЕНО"
LIMITS, GATE, WORKED = "лимиты счёта", "вход до уровня отмены", "ход не отработан"
BEYOND = "цена не за зоной входа"
# Варианты бэктеста (backtest.py --variant). По умолчанию выключены: правила как в файлах.
VARIANT = dict(month_open_atr=None, no_m15_step=False, need_sweep=False, no_choch_swing_corr=False)
# Причины, понятные без названия условия: в строке монеты выводятся без него.
SELF_EXPLAINED = ("ждём", "тренд", "запрет", "зона", "ход отработан", "цена за зоной")
HISTORY_FLAG = 100  # флаг «короткая история»: меньше стольких закрытых свечей на ТФ точки A или W
CONFIRM_KINDS = ("ifvg", "snr", "rb", "fvg")
RECENT = 3          # рейд считается точкой A, если был не дальше стольких закрытых свечей (стартовое)
MIN_BARS = 50       # меньше закрытых свечей — ТФ не используется
RR_ALMOST = 1.0     # НЕТ-ПОЧТИ по R:R только от этого значения (стартовое)
TOP, BOTTOM = 0.85, 0.15
NEAR_STOP = 0.5     # флаг: стоп ближе стольких ATR от входа
HIGH_RR = 5         # флаг: R:R выше
CARD_LIMIT = 2000
MAX_CARDS = 8
TO_REVIEW = "к проверке: inducement, strong high/low"
DAY = 86400000
DURATION = {"W": 7 * DAY, "D": DAY, "240": 4 * 3600000, "60": 3600000, "15": 900000}
ROOT = Path(__file__).parent
JOURNAL = ROOT / "journal"
RUNS = ROOT / "runs"
JOURNAL_FIELDS = ("id", "symbol", "strategy", "side", "kind", "sweep", "date_open", "date_close", "entry", "stop",
                  "target", "rr_planned", "risk_pct", "flags", "status", "result_r", "pnl_pct", "notes")


def duration(tf, t):
    if tf == "M":
        d = dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc)
        return calendar.monthrange(d.year, d.month)[1] * DAY
    return DURATION[tf]


def fails_of(conds):
    return [c for c in conds if c[1] == FAIL]


def qualifies_almost(f):
    """НЕТ-ПОЧТИ: R:R ≥ 1.0, ждём триггер или подтверждение, проверяемое условие позиции."""
    if f[0] == "R:R":
        return len(f) > 3 and f[3] is not None and f[3] >= RR_ALMOST
    return f[2].startswith("ждём") or f[0].startswith(("discount", "premium"))


def status(conds):
    fails = fails_of(conds)
    if len(fails) == 1 and qualifies_almost(fails[0]):
        return ALMOST
    if fails:
        return NO
    return UNCHECKED if any(c[1] == UNKNOWN and c[0] != LIMITS for c in conds) else READY


def build_tf(tf, bars, lower_fvg=None, lower_start=None):
    """Все показатели одного ТФ по закрытым свечам. Меньше MIN_BARS — ТФ не используется."""
    if len(bars) < MIN_BARS:
        return None
    frs = fc.fractals(bars)
    fc.mark_raids(bars, frs)
    trend, events = fc.structure(bars, frs)
    fvg = fc.fvgs(bars)
    for z in fvg:
        z["t"] = bars[z["i"]]["t"]
    low = fc.LOWER.get(tf)
    step = fc.MINUTES.get(low, 0) * 60000
    return dict(tf=tf, bars=bars, frs=frs, trend=trend, events=events, fvg=fvg, ifvg=fc.ifvgs(bars, fvg),
                snr=fc.snrs(bars, frs, fvg, lower_fvg, step, lower_start), rb=fc.rejection_blocks(bars, frs, fvg),
                range=fc.dealing_range(frs, trend, events), atr=fc.atr(bars)[-1], phase=fc.phase(frs))


def build(data, asof, price=None):
    """data: {ТФ: все свечи}. Берём только свечи, закрытые к моменту asof (мс UTC), последние fc.LIMIT —
    столько же, сколько даёт живой запрос, чтобы история на дату совпадала с живым режимом."""
    ctx, lower, start = {}, None, None
    for tf in reversed(fc.TFS):
        bars = [b for b in data.get(tf, []) if b["t"] + duration(tf, b["t"]) <= asof][-fc.LIMIT:]
        ctx[tf] = build_tf(tf, bars, lower, start)
        lower = ctx[tf]["fvg"] if ctx[tf] else None
        start = bars[0]["t"] if ctx[tf] else None
    month = [b for b in data.get("M", []) if b["t"] <= asof < b["t"] + duration("M", b["t"])]
    if price is None:
        m15 = ctx["15"]
        price = m15["bars"][-1]["c"] if m15 else None
    return dict(tfs=ctx, price=price, month_open=month[0]["o"] if month else None)


def event_time(tfs, tf, i, lo, hi, last=True):
    """Время касания уровня внутри свечи tf по самому мелкому ТФ, что её покрывает. Нет — None."""
    bar = tfs[tf]["bars"][i]
    if tf == "15":
        return bar["t"]
    start, end = bar["t"], bar["t"] + duration(tf, bar["t"])
    order = fc.TFS[fc.TFS.index(tf) + 1:][::-1]      # от M15 к ближайшему младшему
    for finer in order:
        t = tfs.get(finer)
        if not t or t["bars"][0]["t"] > start:
            continue
        hits = [b["t"] for b in t["bars"] if start <= b["t"] < end and b["l"] <= hi and b["h"] >= lo]
        if hits:
            return max(hits) if last else min(hits)
    return None


def utc(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


# Условия: каждое возвращает (название, статус, причина[, лучший R:R]).

def c_limits(journal, asof):
    """Лимиты счёта по журналу: открытый риск 2%, убыток 2% в день и 3% в неделю. None = журнал не читается."""
    if journal is None:
        return (LIMITS, UNKNOWN, "журнал неверного формата")
    now = dt.datetime.fromtimestamp(asof / 1000, dt.timezone.utc).date()
    try:
        risk = sum(float(e["risk_pct"]) for e in journal if e["status"] == "open")
        closed = [(dt.date.fromisoformat(e["date_close"]), float(e["pnl_pct"])) for e in journal if e["status"] == "closed"]
    except (KeyError, ValueError, TypeError) as err:
        return (LIMITS, UNKNOWN, f"журнал неверного формата: {err}")
    closed = [c for c in closed if c[0] <= now]
    day = sum(p for d, p in closed if d == now)
    week = sum(p for d, p in closed if d.isocalendar()[:2] == now.isocalendar()[:2])
    if risk >= 2:
        return (LIMITS, FAIL, f"открытый риск {risk:g}%")
    if day <= -2:
        return (LIMITS, FAIL, f"убыток за день {day:g}%")
    if week <= -3:
        return (LIMITS, FAIL, f"убыток за неделю {week:g}%")
    return (LIMITS, PASS, f"сделок в журнале: {len(journal)}")


def last_event_date(t):
    return fc.date(t["bars"], t["events"][-1]["i"], t["tf"]) if t and t["events"] else "нет"


def c_direction(trend, name, when=""):
    if trend is None:
        return (f"направление {name}", UNKNOWN, "нет слома структуры")
    return (f"направление {name}", PASS, f"{trend} {when}".strip())


def c_swing_block(w_trend, d_trend, side, when=""):
    """Вход по W запрещён, пока D сломан против W."""
    if w_trend is None or d_trend is None:
        return ("D не против W", UNKNOWN, "нет данных")
    if d_trend != side:
        return ("D не против W", FAIL, f"запрет D: D {d_trend} против W {w_trend} с {when}")
    return ("D не против W", PASS, f"D {d_trend}")


def c_position(price, dr, side, name):
    """Коррекция: discount для лонга, premium для шорта. Вне диапазона не считается."""
    title = f"{'discount' if side == 'up' else 'premium'} на {name}"
    if dr is None or price is None:
        return (title, UNKNOWN, f"диапазон {name} не сформирован")
    if price < dr[0] or price > dr[1]:
        where = "ниже" if price < dr[0] else "выше"
        return (title, FAIL, f"цена вне диапазона {name} ({where}) {fc.fmt(dr[0])}-{fc.fmt(dr[1])}")
    pos = (price - dr[0]) / (dr[1] - dr[0])
    ok = pos <= 0.5 if side == "up" else pos > 0.5
    return (title, PASS if ok else FAIL, f"положение {pos:.2f}")


def has_choch(events, side):
    """Последний слом в нужную сторону и начался с CHoCH (дальше допустимы BOS)."""
    for e in reversed(events):
        if e["dir"] != side or e["kind"] is None:
            return False
        if e["kind"] == "CHoCH":
            return True
    return False


def choch_date(t, side):
    for e in reversed(t["events"]):
        if e["kind"] == "CHoCH" and e["dir"] == side:
            return fc.date(t["bars"], e["i"], t["tf"])
    return ""


def c_choch(tfs, side):
    names = "/".join(fc.NAMES[t["tf"]] for t in tfs if t) or "ТФ входа"
    title = f"CHoCH {side} на {names}"
    live = [t for t in tfs if t]
    hit = [t for t in live if has_choch(t["events"], side)]
    if hit:
        return (title, PASS, f"{fc.NAMES[hit[0]['tf']]} {choch_date(hit[0], side)}")
    if len(live) < len(tfs):
        return (title, UNKNOWN, "нет данных ТФ входа")
    return (title, FAIL, f"ждём CHoCH {side} на {names}")


def inside(z, price):
    return z["lo"] <= price <= z["hi"]


def nearest_zone(zones, side, price, need_inside=False):
    """Активная зона направления сделки: цена внутри, иначе ближайшая по ходу отката."""
    live = [z for z in zones if z["broken"] is None and z["dir"] == side]
    ins = [z for z in live if inside(z, price)]
    if ins:
        return max(ins, key=lambda z: z["i"])
    if need_inside:
        return None
    ahead = [z for z in live if (z["hi"] < price if side == "up" else z["lo"] > price)]
    if not ahead:
        return None
    return max(ahead, key=lambda z: z["hi"]) if side == "up" else min(ahead, key=lambda z: z["lo"])


def recent_raid(t, side):
    """Последний рейд фрактала против сделки не дальше RECENT закрытых свечей."""
    want = "low" if side == "up" else "high"
    last = len(t["bars"]) - 1
    raids = [(f["raids"][-1], f) for f in t["frs"] if f["side"] == want and f["raids"] and f["broken"] is None]
    raids = [r for r in raids if last - r[0] < RECENT]
    return max(raids, key=lambda r: r[0])[1] if raids else None


def confirm_zone(t, a, side, after_t):
    """Зона подтверждения метода 2 на одной ступени: IFVG, SNR, RB или FVG по ходу сделки,
    внутри зоны A, сформирована после первого теста. Несколько — самая ранняя; в одну свечу
    приоритет по порядку IFVG, SNR, RB, FVG. Возвращает (зона, тип) или None."""
    cands = [(t["bars"][z["i"]]["t"], n, z, kind) for n, kind in enumerate(CONFIRM_KINDS) for z in t[kind]
             if z["dir"] == side and z["broken"] is None and t["bars"][z["i"]]["t"] >= after_t
             and z["lo"] >= a["lo"] and z["hi"] <= a["hi"]]
    if not cands:
        return None
    best = min(cands, key=lambda c: c[:2])
    return best[2], best[3]


def local_fractal(frs, short, far):
    """Ближайший непробитый фрактал младшего ТФ за дальней границей зоны подтверждения."""
    side = "high" if short else "low"
    cands = [f for f in frs if f["side"] == side and f["broken"] is None and (f["p"] > far if short else f["p"] < far)]
    if not cands:
        return None
    return min(cands, key=lambda f: f["p"]) if short else max(cands, key=lambda f: f["p"])


def targets_ctx(*tfs):
    return [(t["tf"], t["bars"], t["frs"], t["fvg"]) for t in tfs if t]


def rr_text(p):
    rows = [f"{o['name']} {o['rr']:.2f}" if o["rr"] is not None else f"{o['name']} нет" for o in p["options"]]
    rows += [f"{o['name']} за уровнем отмены" for o in p["dropped"]]
    rows += [f"{o['name']} ход отработан" for o in p["worked"]]
    return "; ".join(rows)


def rr_best(*plans):
    values = [o["rr"] for p in plans if p for o in p["options"] if o["rr"] is not None]
    return max(values) if values else None


def na_rest(why, *names):
    return [(n, NA, why) for n in names]


def gate(p, why="зона за уровнем отмены"):
    """Условие «вход до уровня отмены»: отпали все варианты — fail."""
    level = fc.fmt(p["cancel"]) if p["cancel"] is not None else "нет"
    if p["options"] or p["worked"]:
        return (GATE, PASS, f"уровень {level}")
    return (GATE, FAIL, f"{why} {level}")


def c_inside(p):
    """Условие «цена не за зоной входа»: цена за дальней границей — зона прошита, рынок не подставляется."""
    if p["beyond"]:
        return (BEYOND, FAIL, f"цена за зоной входа: {fc.fmt(p['price'])} за дальней границей {fc.fmt(p['far'])}")
    return (BEYOND, PASS, f"дальняя граница {fc.fmt(p['far'])}")


def c_worked(p):
    """Условие «ход не отработан»: у всех вариантов цена вне зоны входа и ближе к цели, чем ко входу, — fail."""
    if p["options"]:
        gone = "; отработан: " + ", ".join(o["name"] for o in p["worked"]) if p["worked"] else ""
        return (WORKED, PASS, f"цена {fc.fmt(p['price'])}{gone}")
    if not p["worked"]:
        return (WORKED, NA, "вход за уровнем отмены")
    entries = ", ".join(f"{o['name']} {fc.fmt(o['entry'])}" for o in p["worked"])
    return (WORKED, FAIL, f"ход отработан: цена {fc.fmt(p['price'])} ближе к цели {fc.fmt(p['worked'][0]['target'][0])}, чем ко входу ({entries})")


def entry_conds(p, why="зона за уровнем отмены"):
    if p["beyond"]:
        return [c_inside(p)] + na_rest("цена за зоной входа", GATE, WORKED)
    return [c_inside(p), gate(p, why), c_worked(p)]


def step_plan(short, t, z, after_t, price, targets, extras):
    """Одна ступень метода 2: зона подтверждения, стоп за локальным фракталом этого ТФ, план.
    Возвращает (код, план): нет зоны, нет фрактала или план."""
    found = confirm_zone(t, z, z["dir"], after_t)
    if not found:
        return "no_zone", None
    cz, kind = found
    far = cz["hi"] if short else cz["lo"]
    f = local_fractal(t["frs"], short, far)
    if not f:
        return "no_fractal", None
    buf = fc.STOP_BUFFER * t["atr"]
    stop = f["p"] + buf if short else f["p"] - buf
    last = fc.last_fractal(t["frs"], "high" if short else "low")
    p = fc.plan(short, cz["lo"], cz["hi"], price, stop, targets, extras, cancel=last["p"] if last else None)
    p.update(method=2, step=fc.NAMES[t["tf"]], entry_tf=t["tf"], stop_atr=t["atr"], confirm=dict(zone_info(t, cz), kind=kind.upper()),
             stop_note=f"локальный фрактал {fc.fmt(f['p'])} ({fc.NAMES[t['tf']]} {fc.date(t['bars'], f['i'], t['tf'])}), буфер {fc.fmt(buf)}")
    return "plan", p


def method2(short, a_tf, z, price, targets, extras, steps, tfs):
    """Метод 2 по ступеням сверху вниз: первая ступень с R:R ≥ 1.5, ниже не спускаемся.
    Возвращает (код, ступени): код — нет ТФ, не протестирована, порядок или steps;
    ступени — [(ТФ, код ступени, план)], на выбранной остановка."""
    if not any(steps):
        return "no_tf", []
    first = z.get("first_test")
    if first is None:
        return "not_tested", []
    test_t = event_time(tfs, a_tf["tf"], first, z["lo"], z["hi"], last=False) if tfs else a_tf["bars"][first]["t"]
    if test_t is None:
        return "order", []
    out = []
    for t in steps:
        if not t:
            out.append(("?", "no_tf", None))
            continue
        code, p = step_plan(short, t, z, test_t, price, targets, extras)
        out.append((fc.NAMES[t["tf"]], code, p))
        if p and p["used"]:
            break
    return "steps", out


STEP_WHY = {"no_tf": "мало истории", "no_zone": "нет зоны", "no_fractal": "нет локального фрактала"}


def steps_text(steps):
    rows = []
    for name, code, p in steps:
        if p:
            rows.append(f"{name} {p['confirm']['kind']}: {rr_text(p)}")
        else:
            rows.append(f"{name}: {STEP_WHY[code]}")
    return "; ".join(rows)


def plan_conds(p, why="зона за уровнем отмены"):
    """Условия входа, цели и стопа одного плана; R:R добавляет вызывающий."""
    has_t = any(o["target"] for o in p["options"])
    goal = ("цель", PASS if has_t else (NA if not p["options"] else FAIL), "есть" if has_t else ("вход отпал" if not p["options"] else "нет цели"))
    return entry_conds(p, why) + [goal, ("стоп", PASS, fc.fmt(p["stop"]))]


def c_plan(short, a_tf, z, price, stop_ctx, targets, extras=(), steps=(), tfs=None):
    """Вход до уровня отмены, ход не отработан, цель, стоп и R:R: метод 1 от зоны A,
    при R:R < 1.5 метод 2 по ступеням от зоны подтверждения."""
    names = (BEYOND, GATE, WORKED, "цель", "стоп", "R:R")
    if z is None:
        return na_rest("нет точки A", *names), None
    st = fc.stop_price(stop_ctx["frs"], stop_ctx["atr"], short)
    if not st:
        return [("стоп", FAIL, "нет фрактала против тренда")] + na_rest("нет стопа", BEYOND, GATE, WORKED, "цель", "R:R"), None
    stop = fc.far_stop(short, z["lo"], z["hi"], st[0], stop_ctx["atr"])
    p = fc.plan(short, z["lo"], z["hi"], price, stop, targets, extras, cancel=st[1]["p"])
    p.update(method=1, stop_atr=stop_ctx["atr"],
             stop_note=f"фрактал {fc.fmt(st[1]['p'])} ({fc.date(stop_ctx['bars'], st[1]['i'], stop_ctx['tf'])}), дальняя граница зоны {fc.fmt(z['hi'] if short else z['lo'])}, буфер {fc.fmt(fc.STOP_BUFFER * stop_ctx['atr'])}")
    if p["used"]:
        return plan_conds(p) + [("R:R", PASS, f"метод 1: {rr_text(p)}")], p
    note = f"метод 1: {rr_text(p)}"
    if p["beyond"]:
        return plan_conds(p) + [("R:R", NA, f"{note}; зона A прошита, метод 2 не применим")], p
    if p["dropped"] and not p["options"] and not p["worked"]:
        # Уровень отмены ТФ точки A первичен: зона A целиком за ним — метод 2 не применим.
        why = f"зона A за уровнем отмены {fc.NAMES[stop_ctx['tf']]}"
        return plan_conds(p, why) + [("R:R", NA, f"{note}; метод 2 не применим")], p
    code, steps_out = method2(short, a_tf, z, price, targets, extras, steps, tfs)
    m1 = plan_conds(p)
    if code == "steps":
        note += f"; метод 2: {steps_text(steps_out)}"
        plans = [s[2] for s in steps_out if s[2]]
        chosen = next((x for x in plans if x["used"]), None)
        if chosen:
            return plan_conds(chosen) + [("R:R", PASS, note)], chosen
        live = [x for x in plans if x["options"]]
        if live:
            best = max(live, key=lambda x: rr_best(x) or 0)
            p["alt"] = best
            return plan_conds(best) + [("R:R", FAIL, note, rr_best(p, *live))], p
        if plans and p["options"]:
            # Зоны подтверждения есть, но все их входы отпали: причина по первой ступени с зоной.
            # Если отпали и входы метода 1, причина остаётся по методу 1.
            p["alt"] = plans[0]
            return plan_conds(plans[0]) + [("R:R", NA, note)], p
        if all(s[1] == "no_tf" for s in steps_out):
            code = "no_tf"
        elif any(s[1] == "no_zone" for s in steps_out):
            code = "no_zone"
        else:
            code = "no_fractal"
    if code == "no_tf":
        return m1 + [("R:R", NA if not p["options"] else UNKNOWN, f"{note}; младшего ТФ нет")], p
    if code == "order":
        return m1 + [("R:R", UNKNOWN, f"{note}; порядок событий не определён")], p
    if not p["options"]:
        return m1 + [("R:R", NA, note)], p
    if code == "no_zone":
        # Зона A протестирована, метод 1 не проходит: дальше только подтверждение.
        return m1 + [("подтверждение", FAIL, f"ждём зону подтверждения в зоне A ({note})"), ("R:R", NA, note)], p
    why = "зона A не протестирована" if code == "not_tested" else "нет локального фрактала"
    return m1 + [("R:R", FAIL, f"{note}; метод 2: {why}", rr_best(p))], p


def zone_info(t, z):
    """Границы и даты зоны для карточки."""
    if not t or "i" not in z:
        return {"lo": z["lo"], "hi": z["hi"], "text": "рейд, вход по цене"}
    b, tf = t["bars"], t["tf"]
    text = f"сформирована {fc.date(b, z['i'], tf)}"
    if "t" in z:
        text = f"свечи {fc.date(b, z['i'] - 2, tf)}, {fc.date(b, z['i'] - 1, tf)}, {fc.date(b, z['i'], tf)}"
    if z.get("first_test") is not None:
        text += f"; тест {fc.date(b, z['first_test'], tf)} .. {fc.date(b, z['last_test'], tf)}"
    else:
        text += "; не тестировалась"
    return {"lo": z["lo"], "hi": z["hi"], "tf": fc.NAMES[tf], "text": text, "t": b[z["i"]]["t"]}


def point_a(t, side, price, types, need_inside=False, need_test=False):
    """Точка A: активная зона нужных типов либо свежий рейд. Возвращает условие и зону."""
    name = fc.NAMES[t["tf"]]
    zones = [z for k in types for z in t[k]]
    z = nearest_zone(zones, side, price, need_inside)
    if z and need_test and z.get("first_test") is None and not inside(z, price):
        z = None
    if z:
        return (f"точка A на {name}", PASS, f"{fc.fmt(z['lo'])}-{fc.fmt(z['hi'])}"), z
    r = recent_raid(t, side)
    if r:
        when = fc.date(t["bars"], r["raids"][-1], t["tf"])
        return (f"точка A на {name}", PASS, f"рейд {fc.fmt(r['p'])} {when}"), dict(lo=price, hi=price, dir=side, first_test=None)
    return (f"точка A на {name}", FAIL, "цена не в POI и нет свежего рейда" if need_inside else "нет зоны и свежего рейда"), None


def opposite(side):
    return "down" if side == "up" else "up"


def row(name, conds, p=None, info=None):
    return dict(name=name, conds=conds, status=status(conds), plan=p, info=info or {})


def missing(name, why):
    return row(name, [("данные", UNKNOWN, why)], info=dict(no_history=True))


def entry_of(p):
    """Использованный вход или первый вариант. Нет вариантов — None."""
    for o in p["options"]:
        if o["name"] == p["used"]:
            return o
    return p["options"][0] if p["options"] else None


def fta(p, tfs, short):
    """Ближайшая к входу FTA между входом и целью: непробитая зона противоположного направления
    (FVG, SNR, RB, IFVG) или неснятый фрактал. Возвращает (расстояние, текст) или None."""
    o = entry_of(p)
    if not o or not o.get("target"):
        return None
    entry, target = o["entry"], o["target"][0]
    lo, hi = min(entry, target), max(entry, target)
    opp, side = ("up", "low") if short else ("down", "high")
    best, seen = None, set()
    for t in tfs:
        if not t or t["tf"] in seen:
            continue
        seen.add(t["tf"])
        name = fc.NAMES[t["tf"]]
        cands = [(z["hi"] if short else z["lo"], f"{kind.upper()} {name} {fc.fmt(z['lo'])}-{fc.fmt(z['hi'])} {fc.date(t['bars'], z['i'], t['tf'])}")
                 for kind in CONFIRM_KINDS for z in t.get(kind, []) if z["dir"] == opp and z["broken"] is None]
        cands += [(f["p"], f"фрактал {name} {fc.fmt(f['p'])} {fc.date(t['bars'], f['i'], t['tf'])}")
                  for f in t["frs"] if f["side"] == side and not f["raids"] and f["broken"] is None]
        for near, text in cands:
            if lo < near < hi and (best is None or abs(entry - near) < best[0]):
                best = (abs(entry - near), text)
    return best


def flags(p, dr, side, m_trend=None, btc_d=None, fta_tfs=(), hist=()):
    """Флаги считает код. Запретом не являются. Сигналы от одного движения добавляет mark_clusters.
    hist: [(ТФ, число закрытых свечей или None, если ТФ не построен)] для флага «короткая история»."""
    out = []
    o = entry_of(p) if p else None
    if o and dr and dr[1] > dr[0]:
        pos = (o["entry"] - dr[0]) / (dr[1] - dr[0])
        if side == "up" and pos >= TOP:
            out.append("верх диапазона" + (": вход выше диапазона" if pos > 1 else ""))
        if side == "down" and pos <= BOTTOM:
            out.append("низ диапазона" + (": вход ниже диапазона" if pos < 0 else ""))
    f = fta(p, fta_tfs, side == "down") if o else None
    if f and f[0] < abs(o["entry"] - p["stop"]):
        out.append(f"FTA < 1R: {f[1]}")
    short_hist = [f"{n} {k if k is not None else f'< {MIN_BARS}'}" for n, k in hist if k is None or k < HISTORY_FLAG]
    if short_hist:
        out.append("короткая история: " + ", ".join(short_hist))
    if o and p.get("stop_atr") and abs(o["entry"] - p["stop"]) < NEAR_STOP * p["stop_atr"]:
        out.append(f"стоп ближе {NEAR_STOP} ATR")
    if o and o["rr"] is not None and o["rr"] > HIGH_RR:
        out.append(f"R:R > {HIGH_RR}")
    if m_trend and m_trend != side:
        out.append("против M")
    if btc_d:
        out.append(f"BTC D {btc_d}" + ("" if btc_d == side else ", против"))
    return out


def history(c, a_tf):
    """Закрытые свечи на ТФ точки A и на W для флага «короткая история»; W меньше MIN_BARS не строится."""
    out = [(fc.NAMES[a_tf["tf"]], len(a_tf["bars"]))]
    W = c["tfs"].get("W")
    if a_tf["tf"] != "W":
        out.append(("W", len(W["bars"]) if W else None))
    return out


def make_row(c, name, conds, p, t, z, side, m_trend=None, fta_tfs=(), a_tf=None):
    """t — ТФ диапазона и зоны, a_tf — ТФ точки A (по умолчанию t), fta_tfs — ТФ поиска FTA."""
    dr = t["range"] if t else None
    price = c["price"]
    a_tf = a_tf or t
    entry_tf = c["tfs"].get(p.get("entry_tf")) if p else None
    hist = history(c, a_tf) if a_tf else []
    in_zone = bool(z) and inside(z, price)
    cz = p.get("confirm") if p else None
    info = dict(zone=zone_info(t, z) if z else None, side=side, range=dr,
                flags=flags(p, dr, side, m_trend, c.get("btc_d"), list(fta_tfs) + [entry_tf], hist),
                range_tf=fc.NAMES[t["tf"]] if t else None, atr=t["atr"] if t else None,
                in_zone=in_zone, in_entry=inside(cz, price) if cz else in_zone, price=price)
    return row(name, conds, p, info)


def swing(c, title, types, extras=()):
    """basics-system и a-to-b swing: старший W, рабочий D, вход H4."""
    W, D, H4, M = c["tfs"]["W"], c["tfs"]["D"], c["tfs"]["240"], c["tfs"]["M"]
    price = c["price"]
    if not (W and D and H4) or W["trend"] is None:
        why = f"мало истории: меньше {MIN_BARS} свечей на W, D или H4, либо нет слома W"
        return [missing(f"{title} продолжение", why), missing(f"{title} коррекция", why)]
    side = W["trend"]
    short = side == "down"
    m_trend = M["trend"] if M else None
    # Продолжение: точка A на D у basics, на старшем W у a-to-b.
    a_tf = D if title == "basics" else W
    ca, z = point_a(a_tf, side, price, types, need_test=title == "basics")
    lower = D if a_tf is W else H4
    tg = targets_ctx(a_tf, lower)
    conds = [c_direction(W["trend"], "W", last_event_date(W)), c_swing_block(W["trend"], D["trend"], side, last_event_date(D)), ca]
    steps = (D, H4) if a_tf is W else (H4,)      # ступени метода 2 от ТФ точки A до H4
    plan_c, p = c_plan(short, a_tf, z, price, a_tf, tg, extras, steps, c["tfs"])
    rows = [make_row(c, f"{title} продолжение {'шорт' if short else 'лонг'}", conds + plan_c, p, a_tf, z, side, m_trend, (a_tf, lower))]
    # Коррекция против W: POI на W, premium/discount, CHoCH на H4.
    cside = opposite(side)
    ca, z = point_a(W, cside, price, types, need_inside=True)
    conds = [ca, c_position(price, W["range"], cside, "W")]
    if not (VARIANT["no_choch_swing_corr"] and title == "a-to-b swing"):      # вариант V4 бэктеста
        conds.append(c_choch([H4], cside))
    plan_c, p = c_plan(cside == "down", W, z, price, W, targets_ctx(W, D), extras, (D, H4), c["tfs"])
    rows.append(make_row(c, f"{title} коррекция {'лонг' if cside == 'up' else 'шорт'}", conds + plan_c, p, W, z, cside, m_trend, (W, D)))
    return rows


def a_to_b_intraweek(c):
    """Старший D, рабочий H4, вход H1 или M15."""
    D, H4, H1, M15 = (c["tfs"][k] for k in ("D", "240", "60", "15"))
    price = c["price"]
    if not (D and H4) or D["trend"] is None:
        why = f"мало истории: меньше {MIN_BARS} свечей на D или H4, либо нет слома D"
        return [missing("a-to-b intraweek продолжение", why), missing("a-to-b intraweek коррекция", why)]
    side = D["trend"]
    short = side == "down"
    steps = (H4, H1) if VARIANT["no_m15_step"] else (H4, H1, M15)
    ca, z = point_a(D, side, price, ["fvg"])
    plan_c, p = c_plan(short, D, z, price, D, targets_ctx(D, H4), (), steps, c["tfs"])
    rows = [make_row(c, f"a-to-b intraweek продолжение {'шорт' if short else 'лонг'}",
                     [c_direction(D["trend"], "D", last_event_date(D)), ca] + plan_c, p, D, z, side, fta_tfs=(D, H4))]
    cside = opposite(side)
    ca, z = point_a(D, cside, price, ["fvg"], need_inside=True)
    conds = [ca, c_position(price, D["range"], cside, "D"), c_choch([H1, M15], cside)]
    plan_c, p = c_plan(cside == "down", D, z, price, D, targets_ctx(D, H4), (), steps, c["tfs"])
    rows.append(make_row(c, f"a-to-b intraweek коррекция {'лонг' if cside == 'up' else 'шорт'}", conds + plan_c, p, D, z, cside, fta_tfs=(D, H4)))
    return rows


def tested_poi(tfs, t, side):
    """POI poi-sweep-snr: последний протестированный активный FVG или рейд.
    Возвращает (время теста или None, текст, зона или None); нет POI — None."""
    best = None
    for z in t["fvg"]:
        if z["dir"] == side and z["broken"] is None and z["last_test"] is not None:
            if best is None or z["last_test"] > best[0]:
                best = (z["last_test"], z["lo"], z["hi"], f"FVG {fc.fmt(z['lo'])}-{fc.fmt(z['hi'])}", z)
    want = "low" if side == "up" else "high"
    for f in t["frs"]:
        if f["side"] == want and f["raids"] and f["broken"] is None:
            if best is None or f["raids"][-1] > best[0]:
                best = (f["raids"][-1], f["p"], f["p"], f"рейд {fc.fmt(f['p'])}", None)
    if best is None:
        return None
    return event_time(tfs, t["tf"], best[0], best[1], best[2]), best[3], best[4]


def local_stop(M15, side, after_t, until_t):
    """База стопа poi-sweep-snr: экстремум локального свипа между тестом POI и SNR, без него — реакция от POI."""
    bars, a = M15["bars"], M15["atr"]
    want = "low" if side == "up" else "high"
    raids = [r for f in M15["frs"] if f["side"] == want for r in f["raids"] if after_t <= bars[r]["t"] <= until_t]
    swept = bool(raids)
    window = [bars[r] for r in raids] if raids else [b for b in bars if after_t <= b["t"] <= until_t]
    if not window:
        return None, swept
    if side == "up":
        return min(b["l"] for b in window) - fc.STOP_BUFFER * a, swept
    return max(b["h"] for b in window) + fc.STOP_BUFFER * a, swept


def poi_row(c, side, label, poi_tf, extra_conds):
    M15 = c["tfs"]["15"]
    price = c["price"]
    short = side == "down"
    title = f"poi-sweep-snr {label} {'шорт' if short else 'лонг'}"
    poi = tested_poi(c["tfs"], poi_tf, side)
    name = fc.NAMES[poi_tf["tf"]]
    conds = list(extra_conds)
    rest = (BEYOND, GATE, WORKED, "цель", "стоп", "R:R")
    if not poi:
        conds.append((f"POI на {name} протестирована", FAIL, "нет"))
        return row(title, conds + na_rest("нет POI", "SNR на M15 после теста", *rest))
    when = poi[0]
    if when is None:
        conds.append((f"POI на {name} протестирована", UNKNOWN, f"{poi[1]}, порядок событий не определён"))
        return row(title, conds + na_rest("порядок не определён", "SNR на M15 после теста", *rest))
    conds.append((f"POI на {name} протестирована", PASS, f"{poi[1]}, тест {utc(when)}"))
    snr = [z for z in M15["snr"] if z["dir"] == side and z["broken"] is None and M15["bars"][z["i"]]["t"] >= when]
    if not snr:
        if when < M15["bars"][0]["t"]:
            conds.append(("SNR на M15 после теста", UNKNOWN, "тест старше истории M15"))
        else:
            conds.append(("SNR на M15 после теста", FAIL, "ждём M15 SNR"))
        return row(title, conds + na_rest("нет SNR", *rest))
    z = max(snr, key=lambda s: s["i"])
    conds.append(("SNR на M15 после теста", PASS, f"{fc.fmt(z['lo'])}-{fc.fmt(z['hi'])} {fc.date(M15['bars'], z['i'], '15')}"))
    base, swept = local_stop(M15, side, when, M15["bars"][z["i"]]["t"])
    if base is None:
        return row(title, conds + [("стоп", UNKNOWN, "нет свечей после теста")] + na_rest("нет стопа", BEYOND, GATE, WORKED, "цель", "R:R"))
    stop = fc.far_stop(short, z["lo"], z["hi"], base, M15["atr"])
    low = c["tfs"].get(fc.LOWER[poi_tf["tf"]])
    last = fc.last_fractal(M15["frs"], "high" if short else "low")
    p = fc.plan(short, z["lo"], z["hi"], price, stop, targets_ctx(poi_tf, low), cancel=last["p"] if last else None)
    p.update(swept=swept, stop_atr=M15["atr"], entry_tf="15",
             stop_note=f"{'свип' if swept else 'реакция'} {fc.fmt(base)}, дальняя граница зоны {fc.fmt(z['hi'] if short else z['lo'])}, буфер {fc.fmt(fc.STOP_BUFFER * M15['atr'])}")
    if VARIANT["need_sweep"]:
        conds.append(("свип перед SNR", PASS if swept else FAIL, "был" if swept else "свипа не было (вариант бэктеста)"))
    conds += entry_conds(p)
    if not p["options"]:
        return make_row(c, title, conds + [("стоп", PASS, fc.fmt(stop))] + na_rest("вход отпал", "цель", "R:R"), p, dict(M15, range=poi_tf["range"]), z, side, fta_tfs=(poi_tf, low), a_tf=poi_tf)
    has_t = any(o["target"] for o in p["options"])
    conds += [("цель", PASS if has_t else (UNKNOWN if low is None else FAIL), "есть" if has_t else "нет цели"),
              ("стоп", PASS, fc.fmt(stop))]
    if p["used"]:
        conds.append(("R:R", PASS, rr_text(p)))
    elif low is None:
        conds.append(("R:R", UNKNOWN, f"{rr_text(p)}; младшего ТФ нет"))
    else:
        conds.append(("R:R", FAIL, rr_text(p), rr_best(p)))
    return make_row(c, title, conds, p, dict(M15, range=poi_tf["range"]), z, side, fta_tfs=(poi_tf, low), a_tf=poi_tf)


def poi_sweep(c):
    D, H4, M15 = c["tfs"]["D"], c["tfs"]["240"], c["tfs"]["15"]
    if not (D and H4 and M15):
        return [missing("poi-sweep-snr", f"мало истории: меньше {MIN_BARS} свечей на D, H4 или M15")]
    if D["phase"] == "нет данных" or D["trend"] is None:
        return [missing("poi-sweep-snr", "мало истории: мало фракталов или нет слома на D")]
    phase_side = "up" if D["phase"].startswith("up") else "down" if D["phase"].startswith("down") else None
    if phase_side != D["trend"]:
        return [row("poi-sweep-snr", [("тренд D", FAIL, f"тренд не определён: фаза {D['phase']}, слом {D['trend']}")])]
    side = phase_side
    rows = [poi_row(c, side, "продолжение", H4, [("тренд D", PASS, D["phase"])])]
    cside = opposite(side)
    rows.append(poi_row(c, cside, "коррекция", D, [c_position(c["price"], D["range"], cside, "D"), c_choch([M15], cside)]))
    return rows


def evaluate(data, asof, price=None, journal=(), btc_d=None):
    """Статусы всех стратегий одной монеты на момент asof."""
    c = build(data, asof, price)
    c["btc_d"] = btc_d
    if c["price"] is None:
        rows = [missing("все стратегии", "мало истории: нет данных M15")]
    else:
        extras = [(c["month_open"], "открытие месяца")] if c["month_open"] else []
        k, D = VARIANT["month_open_atr"], c["tfs"]["D"]
        if extras and k and D and abs(c["price"] - c["month_open"]) < k * D["atr"]:
            extras = []      # вариант: открытие месяца не цель, пока цена от него ближе k ATR(D)
        rows = (swing(c, "basics", ["snr", "rb", "fvg"], extras) + swing(c, "a-to-b swing", ["fvg"]) +
                a_to_b_intraweek(c) + poi_sweep(c))
    lim = c_limits(journal, asof)
    for r in rows:
        r["conds"].insert(0, lim)
        r["status"] = status(r["conds"])
    return rows


def mark_clusters(all_rows):
    """Флаг: сколько сигналов ГОТОВ одного направления дало одно движение (зоны в пределах одного дня)."""
    ready = [r for _, rows in all_rows for r in rows
             if r["status"] == READY and (r["info"].get("zone") or {}).get("t") is not None]
    for side in ("up", "down"):
        group = sorted([r for r in ready if r["info"]["side"] == side], key=lambda r: r["info"]["zone"]["t"])
        cluster = []
        for r in group + [None]:
            if r and (not cluster or r["info"]["zone"]["t"] - cluster[-1]["info"]["zone"]["t"] <= DAY):
                cluster.append(r)
                continue
            for x in cluster if len(cluster) > 1 else []:
                x["info"]["flags"].append(f"одно движение: сигналов {side} {len(cluster)}")
            cluster = [r] if r else []


def root_cause(r):
    return cause(fails_of(r["conds"])[0])


def cause(f):
    if f[0] == "R:R":
        return f"R:R {f[2]}"
    return f[2] if f[2].startswith(SELF_EXPLAINED) else f"{f[0]}: {f[2]}"


def why(symbol, rows):
    """Полный список fail и unknown по стратегиям монеты (--why)."""
    out = []
    for r in rows:
        out.append(f"{symbol} {r['name']}: {r['status']}")
        out += [f"  fail: {cause(f)}" for f in fails_of(r["conds"])]
        out += [f"  unknown: {c[0]}: {c[2]}" for c in r["conds"] if c[1] == UNKNOWN]
    return "\n".join(out)


def unknown_cause(r):
    c = next(c for c in r["conds"] if c[1] == UNKNOWN and c[0] != LIMITS)
    return f"{c[0]}: {c[2]}"


def ready_tag(r):
    """Цена в зоне входа, внутри зоны A, но вне зоны входа, или ожидание."""
    i = r["info"]
    if i.get("in_entry", i.get("in_zone")):
        return "цена в зоне"
    return "в зоне A" if i.get("in_zone") else "ожидание"


def distance(r):
    """Расстояние от цены до использованного входа в ATR и %."""
    i, p = r["info"], r["plan"]
    d = abs(entry_of(p)["entry"] - i["price"])
    a = f"{d / i['atr']:.2f} ATR {i['range_tf']}, " if i.get("atr") else ""
    return f"{a}{d / i['price'] * 100:.2f}%"


def one_line(symbol, rows, stamp=""):
    ready = [f"{r['name']} [{ready_tag(r)}]" for r in rows if r["status"] == READY]
    almost = [f"{r['name']} ({root_cause(r)})" for r in rows if r["status"] == ALMOST]
    unknown = [f"{r['name']} ({unknown_cause(r)})" for r in rows if r["status"] == UNCHECKED]
    no = [f"{r['name']} ({root_cause(r)})" for r in rows if r["status"] == NO]
    parts = []
    if ready:
        parts.append("ГОТОВ: " + ", ".join(ready))
    if almost:
        parts.append("почти: " + ", ".join(almost))
    if unknown:
        parts.append("не проверено: " + ", ".join(unknown))
    if no:
        parts.append("нет: " + ", ".join(no))
    tail = f" [данные UTC {stamp}]" if stamp else ""
    return f"{symbol}: " + "; ".join(parts) + tail


def proximity(r):
    """Близость к порогу для порядка карточек НЕТ-ПОЧТИ: меньше — ближе."""
    f = fails_of(r["conds"])[0]
    if f[0] == "R:R":
        return fc.MIN_RR - f[3]
    if f[2].startswith("ждём"):
        return 0.0
    return 0.5


def select_cards(all_rows, limit=MAX_CARDS):
    """До limit карточек: ГОТОВ, НЕ ПРОВЕРЕНО кроме «мало истории», почти по близости. Возвращает (карточки, остальное)."""
    flat = [(s, r) for s, rows in all_rows for r in rows]
    ready = [x for x in flat if x[1]["status"] == READY]
    unknown = [x for x in flat if x[1]["status"] == UNCHECKED and not x[1]["info"].get("no_history")]
    almost = sorted([x for x in flat if x[1]["status"] == ALMOST], key=lambda x: proximity(x[1]))
    order = ready + unknown + almost
    return order[:limit], order[limit:]


def card(symbol, r):
    """Карточка сетапа: условия с числами и датами, уровни, флаги, список к проверке."""
    i, p = r["info"], r["plan"]
    head = f"# {symbol} {r['name']}: {r['status']}"
    if r["status"] == READY and p:
        tag = ready_tag(r)
        head += f" [в зоне A, до входа {distance(r)}]" if tag == "в зоне A" else f" [{tag}], до входа {distance(r)}"
    lines = [head]
    if i.get("price") is not None:
        lines.append(f"цена {fc.fmt(i['price'])}")
    for c in r["conds"]:
        lines.append(f"{c[1]}: {c[0]}: {c[2]}")
    z = i.get("zone")
    if z:
        label = "зона входа" if p and "swept" in p else "зона A"
        lines.append(f"{label} {z.get('tf', '')} {fc.fmt(z['lo'])}-{fc.fmt(z['hi'])}: {z['text']}")
    if p:
        cz = p.get("confirm")
        if cz:
            lines.append(f"зона подтверждения {cz['tf']} {cz['kind']} {fc.fmt(cz['lo'])}-{fc.fmt(cz['hi'])}: {cz['text']}")
        level = f"; уровень отмены {fc.fmt(p['cancel'])}" if p.get("cancel") is not None else ""
        step = f", ступень {p['step']}" if p.get("step") else ""
        lines.append(f"метод {p.get('method', 'локальный')}{step}{level}; стоп: {p.get('stop_note', '')}")
        lines += fc.plan_txt(p)
        if "swept" in p:
            lines.append(f"свип {'был' if p['swept'] else 'не был'}")
    dr = i.get("range")
    o = entry_of(p) if p else None
    if dr and o:
        lines.append(f"диапазон {fc.fmt(dr[0])}-{fc.fmt(dr[1])}; положение входа {fc.position(o['entry'], dr)}")
    if i.get("flags"):
        lines.append("флаги: " + ", ".join(i["flags"]))
    lines.append(TO_REVIEW)
    text = "\n".join(lines)
    return text if len(text) <= CARD_LIMIT else text[:CARD_LIMIT - 14] + "\n(сокращено)"


def read_journal(folder=JOURNAL):
    """Записи журнала из *.jsonl по схеме journal/README.md. Неверный формат = None (unknown)."""
    entries = []
    try:
        for path in sorted(Path(folder).glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    e = json.loads(line)
                    if any(k not in e for k in JOURNAL_FIELDS) or e["status"] not in ("open", "closed", "cancelled"):
                        return None
                    entries.append(e)
    except (OSError, ValueError, TypeError):
        return None
    return entries


def data_time(data, asof):
    """Время закрытия последней свечи M15 к моменту asof."""
    closed = [b["t"] + DURATION["15"] for b in data.get("15", []) if b["t"] + DURATION["15"] <= asof]
    return utc(max(closed)) if closed else "нет данных"


def write_run(folder, asof, all_rows):
    """Сырой лог прогона для этапа 7: все стратегии, единичные fail без фильтра."""
    folder.mkdir(exist_ok=True)
    name = dt.datetime.fromtimestamp(asof / 1000, dt.timezone.utc).strftime("run_%Y%m%d_%H%M%S.jsonl")
    with (folder / name).open("w", encoding="utf-8") as out:
        for symbol, rows in all_rows:
            for r in rows:
                fails = fails_of(r["conds"])
                rec = dict(asof=asof, symbol=symbol, strategy=r["name"], status=r["status"],
                           single_fail=fails[0][0] if len(fails) == 1 else None,
                           conds=[list(c[:3]) for c in r["conds"]], flags=r["info"].get("flags", []))
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")


def fetch(symbol):
    """Все свечи и живая цена. Только эта функция ходит в сеть."""
    data, price = {}, None
    for tf in fc.TFS:
        closed, current = fc.closed_candles(symbol, tf)
        data[tf] = closed + ([current] if current else [])
        if tf == "15" and current:
            price = current["c"]
    return data, price


def btc_direction():
    """Направление BTCUSDT на D для флага."""
    try:
        t = build_tf("D", fc.closed_candles("BTCUSDT", "D")[0])
    except Exception:
        return None
    return t["trend"] if t else None


def since(tf, asof):
    """Начало истории, нужной для fc.LIMIT закрытых свечей ТФ до asof (с запасом в 2 свечи)."""
    return asof - (fc.LIMIT + 2) * (31 * DAY if tf == "M" else DURATION[tf])


def fetch_asof(symbol, asof):
    """Свечи для режима --asof из кэша data/cache/ с дозагрузкой. Живой цены нет: цена — закрытие последней M15."""
    return {tf: fc.load_history(symbol, tf, since(tf, asof)) for tf in fc.TFS}


def listed(data, asof):
    """Есть ли у монеты история на дату: первая M15 открылась раньше asof."""
    m15 = data.get("15") or []
    return bool(m15) and m15[0]["t"] < asof


def btc_at(data, asof):
    """Направление BTCUSDT на D на момент asof по закрытым свечам."""
    bars = [b for b in data.get("D", []) if b["t"] + DAY <= asof][-fc.LIMIT:]
    t = build_tf("D", bars)
    return t["trend"] if t else None


def parse_utc(text):
    return int(dt.datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    asof, live = int(time.time() * 1000), True
    if "--asof" in args:
        k = args.index("--asof")
        asof, live = parse_utc(args[k + 1]), False
        del args[k:k + 2]
    journal = read_journal()
    if live:
        get, btc = fetch, btc_direction()
    else:
        get = lambda s: (fetch_asof(s, asof), None)
        btc = btc_at(fetch_asof("BTCUSDT", asof), asof)
        print(f"режим --asof {utc(asof)} UTC: только свечи, закрытые к этому моменту")
    if args and args[0] in ("--detail", "--card", "--why"):
        s = args[1].upper()
        data, price = get(s)
        if not live and not listed(data, asof):
            print(f"{s}: нет истории на дату (листинг позже)")
            return
        rows = evaluate(data, asof, price, journal, btc)
        if len(args) > 2:
            rows = [r for r in rows if args[2].lower() in r["name"].lower()]
            if not rows:
                print(f"{s}: стратегия не найдена: {args[2]}")
        if args[0] == "--why":
            print(why(s, rows))
        else:
            print("\n\n".join(card(s, r) for r in rows))
        return
    show = bool(args) and args[0] == "--cards"
    symbols = [a.upper() for a in args if not a.startswith("--")]
    if not symbols:
        print(i18n.block("evaluate_usage", __doc__))    # списка монет по умолчанию нет: только явные тикеры
        return
    all_rows, lines = [], []
    for s in symbols:
        try:
            data, price = get(s)
            if not live and not listed(data, asof):
                lines.append((s, None, "нет истории на дату (листинг позже)"))
                continue
            rows = evaluate(data, asof, price, journal, btc)
            all_rows.append((s, rows))
            lines.append((s, rows, data_time(data, asof)))
        except Exception as e:      # сбой одной монеты не должен скрывать остальные
            lines.append((s, None, str(e)))
    mark_clusters(all_rows)
    for s, rows, info in lines:
        if rows is not None:
            print(one_line(s, rows, info))
        else:
            print(f"{s}: пропущена, {info}" if info.startswith("нет истории") else f"{s}: не проверено (ошибка данных: {info})")
    write_run(RUNS, asof, all_rows)
    cards, rest = select_cards(all_rows)
    print(f"\nкарточек: {len(cards)} из {MAX_CARDS}")
    if rest:
        print("вне лимита: " + "; ".join(f"{s} {r['name']}" for s, r in rest))
    if show:
        for s, r in cards:
            print("\n" + card(s, r))


if __name__ == "__main__":
    i18n.setup()
    main()
