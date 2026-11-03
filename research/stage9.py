"""Этап 9: проверка правил на 4 годах. Правила в файлах не меняет, решения не принимает, модель не вызывает.
Запуск: python research/stage9.py [тикер ...]   по умолчанию backtest.COINS_4Y; золото отдельной таблицей.
Правило решений (зафиксировано пользователем до прогона): стратегия или вариант принимается, только если
на каждом из 4 лет средний R после комиссий > 0 и 95% ДИ среднего R по объединённой выборке целиком выше 0;
иначе «преимущество не доказано».
Отчёт: runs/backtest/report_stage9_*.txt; сделки: runs/backtest/trades9_*.jsonl."""
import bisect, collections, datetime as dt, json, random, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest as bt
import evaluate as ev
import fetch_candles as fc
import i18n

START, END = "2022-11-02", "2026-11-01"
YEARS = (("2022-11-02", "2023-11-01"), ("2023-11-02", "2024-11-01"),
         ("2024-11-02", "2025-11-01"), ("2025-11-02", "2026-11-01"))
SKIP = {"JCTUSDT"}                 # листинг короткий (решение пользователя)
GOLD = ("PAXGUSDT", "XAUTUSDT", "XAUUSDT")
RUN = ("V0", "V2", "V4")
CONTROL_VARIANTS = ("V0", "V2")
BOOT, CONTROLS, SLICES = 10000, 1000, 8
HOURS = ("00–06", "06–12", "12–18", "18–24")
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def year_of(t):
    """Номер года периода 0..3 по времени (мс), вне периода — None."""
    for i, (a, b) in enumerate(YEARS):
        if bt.day_ms(a) <= t < bt.day_ms(b) + ev.DAY:
            return i
    return None


# ---------- статистика ----------

def boot_ci(xs, level=0.95, n=BOOT, seed=7):
    """Bootstrap-ДИ среднего: n повторов, фиксированный seed."""
    if len(xs) < 2:
        return None, None
    rnd, k = random.Random(seed), len(xs)
    means = sorted(sum(rnd.choices(xs, k=k)) / k for _ in range(n))
    a = (1 - level) / 2
    return means[int(a * n)], means[min(n - 1, int((1 - a) * n))]


def closed(ts):
    return [t for t in ts if t["status"] == ev.READY and t["r_new"] is not None]


def stats_row(ts, level=0.95):
    rs = [t["r_new"] for t in ts]
    wins = sum(1 for t in ts if t["outcome"] == "цель")
    lo, hi = boot_ci(rs, level)
    return dict(n=len(ts), win=wins / len(ts) * 100 if ts else None, mean=bt.mean(rs), ci=(lo, hi))


def row_text(s):
    note = "  мало данных" if s["n"] < bt.MIN_TRADES else ""
    return f"n={s['n']}, побед {bt.fmt(s['win'], 1)}%, средний R {bt.fmt(s['mean'])}, ДИ {bt.ci_text(s['ci'])}{note}"


def decision(ts):
    """Правило решений: все 4 года средний R > 0 и ДИ95 объединённой выборки целиком > 0."""
    years = [[t for t in ts if t["year"] == i] for i in range(len(YEARS))]
    means = [bt.mean([t["r_new"] for t in y]) for y in years]
    pooled = stats_row(ts)
    ok = all(m is not None and m > 0 for m in means) and pooled["ci"][0] is not None and pooled["ci"][0] > 0
    return ok, means, pooled


# ---------- быстрая симуляция для контрольных входов ----------

def sparse(a, f):
    """Таблица агрегатов f по блокам 2^k для поиска первого касания за O(log n)."""
    tab, k = [a], 1
    while (1 << k) <= len(a):
        prev, half = tab[-1], 1 << (k - 1)
        tab.append([f(prev[i], prev[i + half]) for i in range(len(a) - (1 << k) + 1)])
        k += 1
    return tab


def first_ge(tab, s, end, level):
    pos = s
    for k in range(len(tab) - 1, -1, -1):
        if pos + (1 << k) <= end and tab[k][pos] < level:
            pos += 1 << k
    return pos if pos < end and tab[0][pos] >= level else None


def first_le(tab, s, end, level):
    pos = s
    for k in range(len(tab) - 1, -1, -1):
        if pos + (1 << k) <= end and tab[k][pos] > level:
            pos += 1 << k
    return pos if pos < end and tab[0][pos] <= level else None


class Fast:
    """Свечи M15 монеты для контрольных входов: те же правила исполнения, что в bt.simulate без M1."""

    def __init__(self, bars):
        self.t = [b["t"] for b in bars]
        self.c = [b["c"] for b in bars]
        self.n = len(bars)
        self.hmax = sparse([b["h"] for b in bars], max)
        self.lmin = sparse([b["l"] for b in bars], min)
        self.cmax = sparse(self.c, max)
        self.cmin = sparse(self.c, min)

    def sim(self, s, short, entry, stop, target, cancel, market):
        """Исход с открытия свечи s: цель, стоп, отменён, не исполнен, открыт. Спорная свеча — консервативно."""
        n, INF = self.n, self.n
        if market:
            f, start = s, s
        else:
            ttl = bisect.bisect_left(self.t, self.t[s] + bt.TTL)
            if short:
                ie = first_ge(self.hmax, s, n, entry)
                it = first_le(self.lmin, s, n, target)
                ic = first_ge(self.cmax, s, n, cancel * (1 + 1e-12)) if cancel is not None else None
            else:
                ie = first_le(self.lmin, s, n, entry)
                it = first_ge(self.hmax, s, n, target)
                ic = first_le(self.cmin, s, n, cancel * (1 - 1e-12)) if cancel is not None else None
            ie, it, ic = (INF if x is None else x for x in (ie, it, ic))
            if ie < it and ie <= ic:
                if ie >= ttl:
                    return "не исполнен" if ttl < n else "открыт"
                f = ie
                hit = self.hmax[0][f] >= stop if short else self.lmin[0][f] <= stop
                if hit:
                    return "стоп"
                start = f + 1
            else:
                c = min(it, ic)
                if c < ttl and c < n:
                    return "отменён"
                return "не исполнен" if ttl < n else "открыт"
        if short:
            i_s, i_t = first_ge(self.hmax, start, n, stop), first_le(self.lmin, start, n, target)
        else:
            i_s, i_t = first_le(self.lmin, start, n, stop), first_ge(self.hmax, start, n, target)
        if i_s is None and i_t is None:
            return "открыт"
        if i_t is None or (i_s is not None and i_s <= i_t):
            return "стоп"
        return "цель"


def control_pass(fast, trades, rnd, acc, variant):
    """Для каждой закрытой сделки ГОТОВ CONTROLS контрольных: тот же символ и сторона, те же расстояния до входа,
    стопа, цели и уровня отмены в % от цены, случайная свеча M15 в том же году, тот же способ входа и комиссии.
    acc[(вариант, k)] — суммы R и число закрытых контрольных k-го набора; acc[(вариант, "rules")] — R правил той же симуляцией."""
    for t in closed(trades):
        y = t["year"]
        lo = max(1, bisect.bisect_left(fast.t, bt.day_ms(YEARS[y][0])))
        hi = bisect.bisect_left(fast.t, bt.day_ms(YEARS[y][1]) + ev.DAY)
        s0 = bisect.bisect_left(fast.t, t["asof_ms"])
        if hi - lo < 2 or s0 >= fast.n or s0 < 1:
            continue
        p0 = fast.c[s0 - 1]
        market, short = t["entry_name"] == "рынок", t["side"] == "down"
        rel = [None if x is None else x / p0 for x in (t["entry"], t["stop"], t["target"], t["cancel"])]
        out = fast.sim(s0, short, t["entry"], t["stop"], t["target"], t["cancel"], market)
        r = bt.result_r(out, t["entry"], t["stop"], t["target"], market, bt.NEW)[1]
        if r is not None:
            acc[variant, "rules"].append(r)
        for k in range(CONTROLS):
            s = rnd.randrange(lo, hi)
            p = fast.c[s - 1]
            e, st, tg, cn = (None if x is None else x * p for x in rel)
            if market:
                e = p
            out = fast.sim(s, short, e, st, tg, cn, market)
            r = bt.result_r(out, e, st, tg, market, bt.NEW)[1]
            if r is not None:
                a = acc[variant, k]
                a[0] += r
                a[1] += 1


# ---------- прогон ----------

def btc_phases(dates):
    """Фаза BTCUSDT на D (вверх/вниз/боковик) на каждую дату по закрытым свечам."""
    d = fc.load_history("BTCUSDT", "D", ev.since("D", dates[0]))
    out = {}
    for asof in dates:
        t = ev.build_tf("D", [b for b in d if b["t"] + ev.DAY <= asof][-fc.LIMIT:])
        ph = t["phase"] if t else ""
        out[asof] = "вверх" if ph.startswith("up") else "вниз" if ph.startswith("down") else "боковик"
    return out, ({"D": d}, {"D": [b["t"] for b in d]})


def coin_dates(data, dates):
    """Даты, на которые у монеты есть нужная история: не меньше ev.MIN_BARS закрытых свечей D."""
    d = data["D"]
    if len(d) < ev.MIN_BARS:
        return []
    first = d[ev.MIN_BARS - 1]["t"] + ev.DAY
    return [x for x in dates if x >= first]


def run_coin(s, dates, btc, phases, variants, acc=None, rnd=None):
    data, times = bt.load(s, dates[0])
    mine = coin_dates(data, dates)
    out = {}
    minutes = {s: bt.Minutes(s)}
    fast = Fast(data["15"]) if acc is not None and mine else None
    for v in variants:
        bt.set_variant(v)
        if not mine:
            out[v] = ([], [], 0, len(dates))
            continue
        records, trades, evaluated, skipped = bt.run_period(mine, [s], {s: (data, times)}, btc, minutes)
        for t in trades:
            t["year"] = year_of(t["asof_ms"])
            t["btc_phase"] = phases[t["asof_ms"]]
            t["variant"] = v
        out[v] = (records, trades, evaluated, skipped + len(dates) - len(mine))
        if fast and v in CONTROL_VARIANTS:
            control_pass(fast, trades, rnd, acc, v)
    bt.set_variant("V0")
    return out


def table(L, title, ts):
    L.append(title)
    for i, (a, b) in enumerate(YEARS):
        L.append(f"  год {i + 1} ({a} – {b}): {row_text(stats_row([t for t in ts if t['year'] == i]))}")
    L.append(f"  все 4 года: {row_text(stats_row(ts))}")


def verdict(name, ts):
    ok, means, pooled = decision(ts)
    years = ", ".join(bt.fmt(m) for m in means)
    return (f"  {name}: средний R по годам [{years}], ДИ95 объединённой выборки {bt.ci_text(pooled['ci'])} → "
            + ("ПРИНЯТ по правилу решений" if ok else "преимущество не доказано"))


def month_blocks(m, first, last):
    """Возвраты сделок портфеля по месяцам выхода, включая месяцы без сделок."""
    blocks = collections.OrderedDict()
    d = dt.date.fromisoformat(first).replace(day=1)
    end = dt.date.fromisoformat(last)
    while d <= end:
        blocks[d.strftime("%Y-%m")] = []
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    for t, r in m["returns"]:
        k = dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m")
        if k in blocks:
            blocks[k].append(r)
    return blocks


def path(returns):
    """Итог и максимальная просадка пути по долям баланса."""
    eq = peak = 1.0
    dd = 0.0
    for r in returns:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    return eq - 1, dd


def month_bootstrap(blocks, n=BOOT, seed=7):
    """Bootstrap по блокам-месяцам: 12 случайных месяцев с возвращением, порядок сделок внутри месяца сохранён."""
    rnd, keys = random.Random(seed), list(blocks)
    finals, dds, months8 = [], [], 0
    for _ in range(n):
        seq = []
        for k in rnd.choices(keys, k=12):
            ret = path(blocks[k])[0]
            months8 += ret >= 0.08
            seq += blocks[k]
        f, d = path(seq)
        finals.append(f)
        dds.append(d)
    finals.sort()
    q = lambda p: finals[min(n - 1, int(p * n))] * 100
    return dict(p5=q(0.05), p25=q(0.25), p50=q(0.5), p75=q(0.75), p95=q(0.95),
                loss=sum(1 for f in finals if f < 0) / n * 100, dd30=sum(1 for d in dds if d > 0.30) / n * 100,
                month8=months8 / (12 * n) * 100)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    coins = [a.upper() for a in sys.argv[1:]] or [s for s in bt.COINS_4Y if s not in SKIP]
    bt.OUT.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    dates = bt.dates_between(START, END)
    t0 = time.time()
    phases, btc = btc_phases(dates)
    acc = collections.defaultdict(lambda: [0.0, 0])
    acc.update({(v, "rules"): [] for v in CONTROL_VARIANTS})
    rnd = random.Random(7)
    trades = {v: [] for v in RUN}
    counts = {v: collections.Counter() for v in RUN}
    for s in coins:
        res = run_coin(s, dates, btc, phases, RUN, acc, rnd)
        for v, (records, ts, evaluated, skipped) in res.items():
            trades[v] += ts
            counts[v].update(evaluated=evaluated, skipped=skipped, records=len(records))
        print(f"{s}: {time.time() - t0:.0f} с, сделок V0 {len(res['V0'][1])}", file=sys.stderr)
    gold = {}
    for s in GOLD:
        res = run_coin(s, dates, btc, phases, ("V0", "V2"))
        gold[s] = {v: (r[1], r[2], r[3]) for v, r in res.items()}
        print(f"{s}: {time.time() - t0:.0f} с", file=sys.stderr)
    for v in RUN:
        bt.write(f"trades9_{v}_{stamp}.jsonl", trades[v])
    L = [f"ЭТАП 9. Время: {time.time() - t0:.0f} с. Период {START} – {END}, ежедневно 00:00 UTC.",
         f"Монеты ({len(coins)}): {', '.join(coins)}. JCTUSDT пропущена. Даты без 50 закрытых свечей D пропускаются.",
         "Комиссии как в этапе 8: лимит и тейк maker 0.02%, рынок и стоп taker 0.055%, проскальзывание стопа 0.02%. Funding не учитывается.",
         "V2 — как в этапе 8: V1 (R:R после комиссий ≥ 1.5, открытие месяца не цель ближе 0.5 ATR D) + метод 2 не ниже H1.",
         "V4 — V2 + a-to-b swing коррекция без CHoCH на H4; вход только на дату, когда остальные условия выполнены.",
         "Не вывод о прибыльности. Журнал не меняется. Правила в файлах не менялись."]
    for v in RUN:
        c = counts[v]
        L.append(f"{v}: пар монета-дата {c['evaluated']}, пропущено дат без истории {c['skipped']}, строк стратегий {c['records']}")
    comparisons = []

    # 2. варианты по годам
    L.append("\n===== 2. Варианты: сделки ГОТОВ после дедупликации, закрытые; R после комиссий; ДИ95 bootstrap 10 000")
    for v in RUN:
        table(L, f"{v}:", closed(trades[v]))

    # правило решений
    L.append("\n===== ВЕРДИКТ по правилу решений (каждый год средний R > 0 и ДИ95 объединённой выборки > 0)")
    for v in RUN:
        L.append(verdict(v, closed(trades[v])))
        comparisons.append(f"правило решений {v}")

    # 3. контроль случайных входов
    L.append(f"\n===== 3. Контроль случайных входов ({CONTROLS} наборов: на каждую закрытую сделку одна контрольная в каждом наборе)")
    L.append("  Средний R правил пересчитан той же быстрой симуляцией без M1, что и контрольные.")
    for v in CONTROL_VARIANTS:
        rule = bt.mean(acc[v, "rules"])
        means = [acc[v, k][0] / acc[v, k][1] for k in range(CONTROLS) if acc[v, k][1]]
        if rule is None or not means:
            L.append(f"  {v}: нет данных")
            continue
        below = sum(1 for m in means if m < rule)
        p = (1 + sum(1 for m in means if m >= rule)) / (1 + len(means))
        means.sort()
        L.append(f"  {v}: средний R правил {rule:.3f} (сделок {len(acc[v, 'rules'])}); контрольные: медиана {means[len(means) // 2]:.3f}, "
                 f"5–95% [{means[int(0.05 * len(means))]:.3f}; {means[int(0.95 * len(means)) - 1]:.3f}]; "
                 f"процентиль правил {below / len(means) * 100:.1f}, p = {p:.3f}")
        comparisons.append(f"контроль случайных входов {v}")

    # 4. срезы V2 с Bonferroni
    L.append(f"\n===== 4. V2 по стратегии × типу; ДИ95 и ДИ с поправкой Bonferroni на {SLICES} сравнений (уровень {1 - 0.05 / SLICES:.4f})")
    groups = collections.defaultdict(list)
    for t in closed(trades["V2"]):
        base = bt.strategy_of(t["strategy"]).replace(" продолжение", "").replace(" коррекция", "")
        groups[f"{base} {t['kind']}"].append(t)
    names = [f"{s} {k}" for s in ("basics", "a-to-b swing", "a-to-b intraweek", "poi-sweep-snr") for k in ("продолжение", "коррекция")]
    for name in names:
        ts = groups.get(name, [])
        s95 = stats_row(ts)
        lo, hi = boot_ci([t["r_new"] for t in ts], 1 - 0.05 / SLICES)
        passed = lo is not None and lo > 0
        L.append(f"  {name}: {row_text(s95)}; ДИ Bonferroni {bt.ci_text((lo, hi))} → {'проходит' if passed else 'не проходит'}")
        comparisons.append(f"срез V2 {name}")
    L.append("  Правило решений для срезов V2:")
    for name in names:
        L.append(verdict(name, groups.get(name, [])))
        comparisons.append(f"правило решений, срез {name}")

    # 5. золото
    L.append("\n===== 5. Золото и форекс")
    L.append("  Форекс на Bybit: EURUSD(T), GBPUSD(T), USDJPY, AUDUSD(T) — не найдены (fetch_candles.py --check).")
    L.append("  Золото: XAUUSDT (linear, с 2026-03-09), XAUTUSDT (linear с 2025-03-31), PAXGUSDT (linear с 2022-03-14). "
             "PAXG и XAUT — токены на золото, не спот-золото эталонного рынка; ликвидность ниже основных монет.")
    for s, res in gold.items():
        for v, (ts, evaluated, skipped) in res.items():
            L.append(f"  {s} {v}: пар монета-дата {evaluated}, пропущено {skipped}")
            table(L, f"  {s} {v}:", closed(ts))
            comparisons.append(f"золото {s} {v}")

    # 6. диагностика V2
    L.append("\n===== 6. Диагностика V2 (без правил): по времени входа UTC, дню недели входа, фазе BTC на D на дату сигнала")
    v2 = closed(trades["V2"])
    for title, key, order in (("час входа", lambda t: HOURS[int(t["filled"][11:13]) // 6], HOURS),
                              ("день недели входа", lambda t: WEEKDAYS[dt.datetime.fromtimestamp(t["t_in_ms"] / 1000, dt.timezone.utc).weekday()], WEEKDAYS),
                              ("фаза BTC D", lambda t: t["btc_phase"], ("вверх", "вниз", "боковик"))):
        g = collections.defaultdict(list)
        for t in v2:
            g[key(t)].append(t)
        for k in order:
            L.append(f"  {title} {k}: {row_text(stats_row(g.get(k, [])))}")
            comparisons.append(f"диагностика {title} {k}")

    # 7. портфель V2
    L.append("\n===== 7. Портфель V2: $10 000, риск 1% от баланса, 2 позиции, лимиты 2%/день и 3%/неделю; каждый прогон с $10 000")
    for i, (a, b) in enumerate(YEARS):
        m = bt.portfolio([t for t in trades["V2"] if t["year"] == i], "pct")
        L.append(bt.portfolio_text(f"год {i + 1} ({a} – {b})", m))
    m = bt.portfolio(trades["V2"], "pct")
    L.append(bt.portfolio_text("все 4 года", m))
    blocks = month_blocks(m, START, END)
    month_rets = [path(r)[0] for r in blocks.values()]
    b = month_bootstrap(blocks)
    L.append(f"  Bootstrap по месяцам ({BOOT} повторов, 12 месяцев, блоки — календарные месяцы из {len(blocks)}):")
    L.append(f"    результат за 12 месяцев: 5% {b['p5']:+.1f}%, 25% {b['p25']:+.1f}%, медиана {b['p50']:+.1f}%, "
             f"75% {b['p75']:+.1f}%, 95% {b['p95']:+.1f}%; вероятность убытка за год {b['loss']:.1f}%")
    L.append(f"    вероятность месяца ≥ +8%: {b['month8']:.1f}%; вероятность просадки > 30% за 12 месяцев: {b['dd30']:.1f}%")
    L.append(f"    по факту месяцев ≥ +8%: {sum(1 for r in month_rets if r >= 0.08)} из {len(month_rets)} "
             f"({bt.pct(sum(1 for r in month_rets if r >= 0.08), len(month_rets))})")

    # 8. число сравнений
    L.insert(9, f"\nВыполнено сравнений: {len(comparisons)} (правило решений для вариантов и срезов, контроль случайных входов, "
                f"срезы с Bonferroni, золото, диагностика). Результаты, не прошедшие правило решений, нельзя считать "
                f"подтверждением; при таком числе сравнений часть «хороших» срезов ожидаема случайно.")
    text = "\n".join(L)
    (bt.OUT / f"report_stage9_{stamp}.txt").write_text(i18n.tr(text), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    i18n.setup()
    main()
