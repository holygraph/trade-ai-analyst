"""trade-ai-analyst: разбор одной сделки по правилам пользователя по запросу (/analyst). Скан списка монет не делает.
Запуск:
  python analyst.py ТИКЕР [long|short] [вход] [стоп] [тейк1 тейк2 ...]
Без стороны — обе стороны по одной строке. Статусы и уровни считает evaluate.py (правила не меняются);
комиссии: лимитный вход и тейк maker 0.02%, рыночный вход и стоп taker 0.055%, проскальзывание стопа 0.02%.
Частота по истории — stats/base_rates.json (бэктест 4 года, не прогноз). Вывод не длиннее 2500 символов."""
import datetime as dt, json, sys, time
from pathlib import Path

import evaluate as ev
import fetch_candles as fc
import journal as jr

FEES = (0.0002, 0.00055, 0.0002)        # maker, taker, проскальзывание стопа
MARKET_TOL = 0.0005                     # вход ближе 0.05% к цене считается рыночным (решение пользователя, стартовое)
STOP_DIFF_NOTE = 0.25                   # стоп пользователя дальше от правила больше чем на 25% расстояния — оценка ориентировочная
OUTPUT_LIMIT = 2500
LINES = [(s, k) for s in ("basics", "a-to-b swing", "a-to-b intraweek", "poi-sweep-snr") for k in ("продолжение", "коррекция")]
TF_CODE = {"M15": "15", "H1": "60", "H4": "240", "D": "D", "W": "W"}
PASS_, WAIT, NO, UNCHECKED = "ПРОХОДИТ", "ЖДАТЬ", "НЕТ", "НЕ ПРОВЕРЕНО"
SIDES = {"long": "up", "лонг": "up", "buy": "up", "short": "down", "шорт": "down", "sell": "down"}
USAGE = "Синтаксис: /analyst ТИКЕР long|short [вход] [стоп] [тейк1 тейк2 ...]"


# ---------- аргументы ----------

def number(text):
    return float(text.replace(",", ".").replace(" ", ""))


def parse_args(argv):
    """ТИКЕР, сторона (необязательна), затем числа: вход, стоп, тейки. Ошибка — ValueError с подсказкой."""
    if not argv:
        raise ValueError(USAGE)
    symbol, rest = argv[0].upper(), list(argv[1:])
    if not symbol.replace("-", "").isalnum():
        raise ValueError(f"тикер «{argv[0]}» не похож на тикер Bybit (например BTCUSDT). {USAGE}")
    side = SIDES.get(rest[0].lower()) if rest else None
    if rest and side:
        rest = rest[1:]
    elif rest:
        try:
            number(rest[0])
        except ValueError:
            raise ValueError(f"сторона «{rest[0]}»: нужно long или short. {USAGE}")
    nums = []
    for x in rest:
        try:
            nums.append(number(x))
        except ValueError:
            raise ValueError(f"не число: {x}. {USAGE}")
    if nums and side is None:
        raise ValueError(f"с уровнями нужна сторона long или short. {USAGE}")
    if any(n <= 0 for n in nums):
        raise ValueError("уровни должны быть больше нуля")
    entry, stop, takes = (nums[0] if nums else None), (nums[1] if len(nums) > 1 else None), nums[2:]
    long_ = side == "up"
    if stop is not None and (stop >= entry if long_ else stop <= entry):
        raise ValueError(f"стоп {stop:g} не по ходу сделки: для {'long ниже' if long_ else 'short выше'} входа {entry:g}")
    for i, t in enumerate(takes, 1):
        if t <= entry if long_ else t >= entry:
            raise ValueError(f"тейк {i} ({t:g}) не по ходу сделки: для {'long выше' if long_ else 'short ниже'} входа {entry:g}")
    return dict(symbol=symbol, side=side, entry=entry, stop=stop, takes=takes)


# ---------- строки стратегий ----------

def find_row(rows, base, kind):
    for r in rows:
        if r["name"].startswith(f"{base} {kind}"):
            return r
    for r in rows:
        if r["name"] in (base, "все стратегии") or r["name"].startswith(base) and kind not in r["name"]:
            return r
    return None


def short_text(text, n=90):
    return text if len(text) <= n else text[:n - 1] + "…"


def row_side(r):
    """Сторона строки: из info, иначе из названия (у строк без плана info пустой)."""
    side = r["info"].get("side")
    if side:
        return side
    words = r["name"].split()
    return "up" if "лонг" in words else "down" if "шорт" in words else None


def line_status(r, side):
    """(статус, причина) строки для стороны side: ПРОХОДИТ, ЖДАТЬ, НЕТ, НЕ ПРОВЕРЕНО."""
    if r is None:
        return NO, "строки нет"
    rside = row_side(r)
    if rside and rside != side:
        other = "лонг" if rside == "up" else "шорт"
        return NO, f"по тренду только {other}"
    if r["status"] == ev.READY:
        return PASS_, "все условия выполнены"
    if r["status"] == ev.UNCHECKED:
        return UNCHECKED, short_text(ev.unknown_cause(r))
    cause = ev.root_cause(r)
    if r["status"] == ev.ALMOST and cause.startswith("ждём"):
        return WAIT, short_text(cause)
    return NO, short_text(cause)


def entry_zone(r):
    p = r["plan"]
    z = (p or {}).get("confirm") or r["info"].get("zone") or {}
    return z.get("lo"), z.get("hi"), z.get("tf", "")


def net_rr(entry, stop, target, market):
    risk = abs(entry - stop)
    return fc.rr_value(entry, stop, target, risk, market, fees=FEES) if risk > 0 else None


def wait_level(r, c):
    """Условие и уровень для ЖДАТЬ: CHoCH — закрытие за последним фракталом ТФ; SNR — после теста POI; зона подтверждения — зона A."""
    cause = ev.root_cause(r)
    if cause.startswith("ждём CHoCH"):
        _, _, d, _, names = cause.split(" ", 4)
        out = []
        for name in names.split("/"):
            t = c["tfs"].get(TF_CODE.get(name, ""))
            f = fc.last_fractal(t["frs"], "high" if d == "up" else "low") if t else None
            if f:
                out.append(f"{name} закрытие {'выше' if d == 'up' else 'ниже'} {fc.fmt(f['p'])}")
        return "CHoCH: " + ("; ".join(out) or "уровень не найден")
    if "SNR" in cause:
        poi = next((c2[2] for c2 in r["conds"] if c2[0].startswith("POI на")), "POI")
        return f"SNR на M15 после теста {short_text(poi, 60)}"
    z = r["info"].get("zone")
    if z:
        return f"зона подтверждения внутри зоны A {fc.fmt(z['lo'])}-{fc.fmt(z['hi'])}"
    return short_text(cause)


def further_targets(c, entry, target, short, n=3):
    """Следующие неснятые фракталы и незаполненные FVG за целью по ходу сделки (W, D, H4) — справочно, вне правил."""
    cands = {}
    for tf in ("W", "D", "240"):
        t = c["tfs"].get(tf)
        if not t:
            continue
        side, want = ("low", "up") if short else ("high", "down")
        for f in t["frs"]:
            if f["side"] == side and not f["raids"] and f["broken"] is None:
                cands.setdefault(f["p"], f"фрактал {fc.NAMES[tf]}")
        for z in t["fvg"]:
            if z["dir"] == want and z["broken"] is None:
                cands.setdefault(z["hi"] if short else z["lo"], f"FVG {fc.NAMES[tf]}")
    beyond = [(p, lbl) for p, lbl in cands.items() if (p < target if short else p > target)]
    beyond.sort(key=lambda x: abs(x[0] - entry))
    return beyond[:n]


# ---------- вердикт ----------

def decide(rows, side, user_entry, price):
    """(вердикт, причина, опорная строка). Опорная — первая ПРОХОДИТ, затем ЖДАТЬ с планом, затем ближайшая НЕТ с планом."""
    lines = [(base, kind, find_row(rows, base, kind)) for base, kind in LINES]
    stats = [(b, k, r, *line_status(r, side)) for b, k, r in lines]
    passing = [s for s in stats if s[3] == PASS_]
    for b, k, r, st, why in passing:
        lo, hi, _ = entry_zone(r)
        o = ev.entry_of(r["plan"])
        if user_entry is not None:
            if lo is not None and lo <= user_entry <= hi:
                return "ВХОД ДА", f"{b} {k}: ваш вход внутри зоны входа", r, stats
        elif r["info"].get("in_entry"):
            return "ВХОД ДА", f"{b} {k}: цена в зоне входа, рынок допустим", r, stats
    if passing:
        b, k, r = passing[0][:3]
        o = ev.entry_of(r["plan"])
        lo, hi, _ = entry_zone(r)
        where = "ваш вход вне зоны входа; " if user_entry is not None else ""
        return "ЖДАТЬ", f"{b} {k}: {where}лимит {o['name']} {fc.fmt(o['entry'])} (зона {fc.fmt(lo)}-{fc.fmt(hi)})", r, stats
    waiting = [s for s in stats if s[3] == WAIT]
    if waiting:
        b, k, r = waiting[0][:3]
        return "ЖДАТЬ", f"{b} {k}: {short_text(ev.root_cause(r), 60)}", r, stats
    own = [s for s in stats if s[2] is not None and row_side(s[2]) == side and s[2]["status"] != ev.UNCHECKED]
    pool = sorted(own, key=lambda s: (len(ev.fails_of(s[2]["conds"])), s[2]["plan"] is None))
    if pool:
        b, k, r = pool[0][:3]
        return "НЕТ", f"ближе всех {b} {k}: не хватает — {short_text(ev.root_cause(r), 70)}", r if r["plan"] else None, stats
    unchecked = [s for s in stats if s[3] == UNCHECKED]
    if unchecked:
        return "НЕТ", f"не проверено: {short_text(unchecked[0][4], 80)}", None, stats
    return "НЕТ", "ни одна стратегия не подходит для этой стороны", None, stats


# ---------- план пользователя ----------

def row_params(r, status):
    """Параметры по правилам строки: стоп, цель, уровень отмены, зона входа. Поправки («перенести стоп»)
    только для ПРОХОДИТ или ЖДАТЬ с планом; для НЕТ — ориентир (решение пользователя, стартовое)."""
    p = r["plan"]
    o = ev.entry_of(p)
    lo, hi, _ = entry_zone(r)
    advise = status in (PASS_, WAIT)
    return dict(stop=p["stop"], target=o["target"][0] if o and o.get("target") else None, cancel=p.get("cancel"),
                lo=lo, hi=hi, advise=advise,
                label=f"по правилам {r['name']}" if advise else f"ориентир по правилам {r['name']} (стратегия не проходит)")


def generic_params(c, entry, short):
    """Без опорной стратегии: ориентир по определениям глоссария для точки A на D (intraweek):
    стоп за последним фракталом против тренда на D плюс 0.1 ATR(D), цель — ближайшая на D/H4 (решение пользователя, стартовое)."""
    D, H4 = c["tfs"].get("D"), c["tfs"].get("240")
    if not D or entry is None:
        return None
    st = fc.stop_price(D["frs"], D["atr"], short)
    tg = fc.nearest_target(entry, short, ev.targets_ctx(D, H4))
    if not st:
        return None
    return dict(stop=st[0], target=tg[0] if tg else None, cancel=st[1]["p"], lo=None, hi=None, advise=False,
                label="ориентир по глоссарию (стратегия не проходит)")


def target_place(T, target, short):
    """Тейк относительно цели по правилам: на цели, ближе или дальше."""
    if target is None:
        return "цели по правилам нет"
    if abs(T - target) <= 1e-9 * max(abs(target), 1):
        return "на цели по правилам"
    closer = (T > target) if short else (T < target)
    return f"{'ближе' if closer else 'дальше'} цели по правилам {fc.fmt(target)}"


def user_plan(u, p, c, price, short):
    """Поправки к уровням пользователя относительно параметров p (row_params или generic_params).
    Для каждого тейка R:R до и после комиссий при стопе пользователя и при стопе по правилам или ориентиру."""
    advise = p.get("advise", False)
    ref_word = "правила" if advise else "ориентира"
    out = [f"Сравнение с: {p['label']}"]
    E, S = u["entry"], u["stop"]
    lo, hi = p["lo"], p["hi"]
    cancel = p.get("cancel")
    if E is not None:
        if lo is not None and lo <= E <= hi:
            where = f"внутри зоны {fc.fmt(lo)}-{fc.fmt(hi)}"
        elif lo is not None:
            edge = hi if E > hi else lo
            where = f"{'выше' if E > hi else 'ниже'} зоны {fc.fmt(lo)}-{fc.fmt(hi)} на {abs(E - edge) / price * 100:.2f}%"
        else:
            where = "зоны нет"
        ok = cancel is None or (E < cancel if short else E > cancel)
        out.append(f"Вход {fc.fmt(E)}: {where}; {'до' if ok else 'за'} уровн{'я' if ok else 'ем'} отмены {fc.fmt(cancel) if cancel else '—'}")
    if S is not None and E is not None:
        if (S <= E) if short else (S >= E):
            out.append(f"Стоп {fc.fmt(S)}: ошибка — для {'short выше' if short else 'long ниже'} входа")
            return out
        rule_risk = abs(E - p["stop"])
        closer = (S < p["stop"]) if short else (S > p["stop"])
        diff = abs(S - p["stop"])
        kills = cancel is not None and ((S > cancel) if short else (S < cancel))
        inside = lo is not None and lo <= S <= hi
        out.append(f"Стоп {fc.fmt(S)}: {'ближе' if closer else 'дальше'} {ref_word} {fc.fmt(p['stop'])} на {diff / price * 100:.2f}% "
                   f"({diff / rule_risk:.2f}R); снятие {'отменяет идею' if kills else 'не отменяет идею (до уровня отмены)'}; "
                   f"{'внутри зоны входа' if inside else 'вне зоны входа'}")
        market = abs(E - price) / price <= MARKET_TOL
        target = p["target"]
        tfs = [c["tfs"].get(k) for k in ("D", "240", "60")]
        ref_stop = "по правилам" if advise else "ориентира"
        for i, T in enumerate(u["takes"], 1):
            if (T >= E) if short else (T <= E):
                out.append(f"Т{i} {fc.fmt(T)}: ошибка — не по ходу сделки")
                continue
            gross, net = abs(T - E) / abs(E - S), net_rr(E, S, T, market)
            g_rule, n_rule = abs(T - E) / rule_risk, net_rr(E, p["stop"], T, market)
            low = f" — ниже {fc.MIN_RR:g}" if g_rule < fc.MIN_RR else ""
            fake = dict(options=[dict(name="план", entry=E, target=(T, ""), rr=gross)], used="план", stop=S)
            f = ev.fta(fake, tfs, short)
            path = f"на пути FTA {short_text(f[1], 45)} ({f[0] / abs(E - S):.2f}R)" if f else "на пути FTA нет"
            out.append(f"Т{i} {fc.fmt(T)}: при вашем стопе R:R {gross:.2f} / {net:.2f} после комиссий; при стопе {ref_stop} "
                       f"R:R до Т{i} {g_rule:.2f} / {n_rule:.2f}{low}; {target_place(T, target, short)}; {path}")
        tgt = fc.fmt(target) if target else "—"
        if advise:
            fix = "совпадает" if diff <= 1e-12 else f"перенести на {fc.fmt(p['stop'])}"
            out.append(f"Итог: по правилам стоп {fc.fmt(p['stop'])}, цель {tgt}; ваш стоп {fc.fmt(S)} — поправка: {fix}")
        else:
            out.append(f"Итог: {p['label']}: стоп {fc.fmt(p['stop'])}, цель {tgt}; рекомендации переносить стоп нет")
    return out


# ---------- частота по истории и журнал ----------

def base_rates_path():
    for path in (jr.data_dir() / "stats" / "base_rates.json", Path(__file__).resolve().parent / "stats" / "base_rates.json",
                 Path(__file__).resolve().parent.parent / "stats" / "base_rates.json"):
        if path.exists():
            return path
    return None


def load_base_rates(path=None):
    path = path or base_rates_path()
    return json.loads(path.read_text(encoding="utf-8")) if path else None


def versus_random(lv, k):
    """Сравнение со случайным блужданием 1/(1+k): ДИ включает базу — «в пределах случайного»."""
    base = 1 / (1 + k)
    if lv["ci"][0] is not None and lv["ci"][0] > base:
        return "выше случайного"
    if lv["ci"][1] is not None and lv["ci"][1] < base:
        return "ниже случайного"
    return "в пределах случайного"


def level_text(lv, k):
    if not lv or lv["rate"] is None:
        return f"до {k:g}R нет данных"
    note = ", мало данных" if lv["low_sample"] else ""
    return (f"до {k:g}R {lv['rate'] * 100:.0f}% [{lv['ci'][0] * 100:.0f}–{lv['ci'][1] * 100:.0f}] "
            f"(случайно {100 / (1 + k):.0f}%, {versus_random(lv, k)}{note})")


def bucket(levels, r):
    """Наименьший уровень таблицы не ниже R тейка (консервативно; решение пользователя, стартовое)."""
    ks = sorted(float(k) for k in levels)
    return next((k for k in ks if k >= r - 1e-9), None)


def history_lines(rates, key, takes_r, entries, strategy, side, approximate=False):
    """Частота по истории; approximate — стоп пользователя далеко от стопа по правилам, таблица для стопов по правилам."""
    out = []
    similar = [e for e in entries if e.get("status") == "closed" and e.get("result_r") is not None
               and e.get("strategy") == strategy and e.get("side") == ("long" if side == "up" else "short")]
    if len(similar) >= 30:
        rs = [e["result_r"] for e in similar]
        out.append(f"Журнал ({len(rs)} похожих): побед {sum(r > 0 for r in rs) / len(rs) * 100:.0f}%, средний R {sum(rs) / len(rs):.2f} — приоритет")
    if not rates:
        return out + ["Частота по истории: stats/base_rates.json не найден"]
    g = rates["groups"].get(key) or rates["groups"]["все"]
    name = key if key in rates["groups"] else "все"
    lv = g["levels"]
    out.append(f"Историческая частота похожих сетапов ({name}; бэктест 4 года, {g['trades']} сделок, не прогноз): "
               f"{level_text(lv.get('1'), 1)}, {level_text(lv.get('2'), 2)}")
    for i, r in enumerate(takes_r, 1):
        k = bucket(lv, r)
        if k is None:
            out.append(f"  Т{i} {r:.2f}R: выше уровней таблицы (до 3R)")
        else:
            out.append(f"  Т{i} {r:.2f}R → {level_text(lv.get(f'{k:g}'), k)}")
    if takes_r and approximate:
        out.append("  Таблица построена для стопов по правилам, оценка ориентировочная")
    return out


def account_lines(entries, symbol, side, asof):
    lim = ev.c_limits(entries, asof)
    opened = [e for e in entries if e.get("status") == "open"]
    risk = sum(float(e.get("risk_pct") or 0) for e in opened)
    want = "long" if side == "up" else "short"
    same = [e["symbol"] for e in opened if e["symbol"] == symbol]
    corr = [e["symbol"] for e in opened if e["symbol"] != symbol and e.get("side") == want]
    text = f"Счёт: открыто {len(opened)}/2, риск {risk:g}%, лимиты {lim[1]} ({lim[2]})"
    if same:
        text += f"; по {symbol} уже есть позиция — одна позиция на идею"
    if corr:
        text += f"; в ту же сторону открыто: {', '.join(corr)} (крипта коррелирует с BTC)"
    return [text]


# ---------- вывод ----------

def rules_lines(r, c, price, short):
    p = r["plan"]
    o = ev.entry_of(p)
    if not o or not o.get("target"):
        return ["По правилам: параметров нет (" + short_text(ev.root_cause(r) if ev.fails_of(r["conds"]) else "нет плана", 60) + ")"]
    lo, hi, ztf = entry_zone(r)
    market = o["name"] == "рынок"
    E, S, T = o["entry"], p["stop"], o["target"][0]
    risk = abs(E - S)
    gross, net = abs(T - E) / risk, net_rr(E, S, T, market)
    method = f"метод {p.get('method', 'локальный')}" + (f", ступень {p['step']}" if p.get("step") else "")
    kind = "рынок (цена в зоне)" if market else f"лимит {o['name']}"
    out = [f"По правилам ({r['name']}, {method}): вход {kind} {fc.fmt(E)} в зоне {ztf} {fc.fmt(lo)}-{fc.fmt(hi)}; "
           f"стоп {fc.fmt(S)} ({risk / E * 100:.2f}%, 1R={fc.fmt(risk)}; отмена {fc.fmt(p['cancel']) if p.get('cancel') else '—'}); "
           f"цель {fc.fmt(T)} ({short_text(o['target'][1], 40)}); R:R {gross:.2f} / {net:.2f} после комиссий"]
    far = further_targets(c, E, T, short)
    if far:
        out.append("Справочно, вне правил: дальше " + ", ".join(f"{fc.fmt(x)} {lbl} ({abs(x - E) / risk:.1f}R)" for x, lbl in far))
    return out


def report(u, rows, c, price, stamp, cat, entries, rates, asof):
    side = u["side"]
    short = side == "down"
    head = f"{u['symbol']} {'SHORT' if short else 'LONG'} | цена {fc.fmt(price)} | данные UTC {stamp} | {cat}"
    verdict, why, ref, stats = decide(rows, side, u["entry"], price)
    L = [head, f"Вердикт по правилам: {verdict} — {why}", "Стратегии:"]
    L += [f"  {b} {k}: {st} — {reason}" for b, k, r, st, reason in stats]
    if ref is not None and ref["status"] == ev.ALMOST and ev.root_cause(ref).startswith("ждём"):
        L.append("Ждём: " + wait_level(ref, c))
    if ref is not None and ref["plan"]:
        L += rules_lines(ref, c, price, short)
    params = None
    if u["entry"] is not None:
        status = next((st for b, k, r, st, _ in stats if r is ref), None)
        params = row_params(ref, status) if ref is not None and ref["plan"] else generic_params(c, u["entry"], short)
        if params:
            L.append("Ваш план:")
            L += ["  " + x for x in user_plan(u, params, c, price, short)]
        else:
            L.append("Ваш план: параметров по правилам нет — сравнить не с чем")
    takes_r, approximate = [], False
    if u["entry"] is not None and u["stop"] is not None:
        takes_r = [abs(t - u["entry"]) / abs(u["entry"] - u["stop"]) for t in u["takes"]]
        if params:
            approximate = abs(u["stop"] - params["stop"]) > STOP_DIFF_NOTE * abs(u["entry"] - params["stop"])
    key = next((f"{b} {k}" for b, k, r, *_ in stats if r is ref), "все") if ref is not None else "все"
    L += history_lines(rates, key, takes_r, entries, ref["name"] if ref else None, side, approximate)
    flags = ref["info"].get("flags", []) if ref is not None else []
    L.append("Флаги: " + (", ".join(flags) if flags else "нет"))
    L.append("К проверке аналитиком: inducement, strong high/low (вывод аналитика, менее надёжно)")
    L += account_lines(entries, u["symbol"], side, asof)
    L.append("Проверка правил, не рекомендация; проверь экономический календарь.")
    return fit(L)


def both_sides(u, rows, price, stamp, cat):
    L = [f"{u['symbol']} | цена {fc.fmt(price)} | данные UTC {stamp} | {cat}"]
    for side, name in (("up", "LONG"), ("down", "SHORT")):
        verdict, why, _, _ = decide(rows, side, None, price)
        L.append(f"{name}: {verdict} — {why}")
    L.append("Подробно: /analyst ТИКЕР long|short. Проверка правил, не рекомендация; проверь экономический календарь.")
    return fit(L)


def fit(lines, limit=OUTPUT_LIMIT):
    """Не длиннее limit символов: сначала убираются справочные строки, затем строки обрезаются."""
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    lines = [x for x in lines if not x.startswith("Справочно")]
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    lines = [short_text(x, 160) for x in lines]
    text = "\n".join(lines)
    tail = lines[-1]
    return text if len(text) <= limit else text[:limit - len(tail) - 2] + "…\n" + tail


def run(argv):
    try:
        u = parse_args(argv)
    except ValueError as e:
        return str(e)
    try:
        found = fc.instruments(u["symbol"])
        if not found:
            return f"{u['symbol']}: тикер не найден на Bybit (linear, spot). {USAGE}"
        asof = int(time.time() * 1000)
        data, price = ev.fetch(u["symbol"])
        btc = ev.btc_direction()
    except (OSError, RuntimeError, ValueError) as e:       # нет сети, таймаут, ошибка ответа Bybit
        return (f"{u['symbol']}: нет данных от Bybit (api.bybit.com): {type(e).__name__}: {short_text(str(e), 120)}. "
                "Проверь интернет и повтори; без данных разбор не делается.")
    cat = ", ".join(f"{c}:{s}" for c, s in found)
    try:
        entries = jr.load()
    except (OSError, ValueError):
        entries = None                                      # журнал не читается — лимиты unknown, статус не блокирует
    rows = ev.evaluate(data, asof, price, entries, btc)
    entries = entries or []
    price = price if price is not None else (data["15"][-1]["c"] if data.get("15") else None)
    if price is None:
        return f"{u['symbol']}: Bybit не отдал свечей M15 — разбор невозможен"
    stamp = ev.data_time(data, asof)
    if u["side"] is None:
        return both_sides(u, rows, price, stamp, cat)
    c = ev.build(data, asof, price)
    return report(u, rows, c, price, stamp, cat, entries, load_base_rates(), asof)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    print(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
