"""Этапы 7–8: проверка правил на истории. Правила в файлах не меняет, пороги не подстраивает, модель не вызывает.
Запуск:
  python backtest.py [дней=365] [--variant V0] [тикер ...]   один период до вчера, отчёт и портфель
  python backtest.py --stage8                                 все варианты на рабочем и контрольном периодах
Варианты (решение пользователя, правила в файлах не меняются):
  V0 — текущие правила; V1 — V0 + R:R после комиссий ≥ 1.5 и открытие месяца не цель ближе 0.5 ATR(D);
  V2 — V1 + метод 2 не ниже H1; V3 — V1 + свип обязателен в poi-sweep-snr;
  V4 — V2 + a-to-b swing коррекция без CHoCH на H4 (этап 9, stage9.py).
Даты — каждый день в 00:00 UTC. Свечи из data/cache/ с дозагрузкой, M1 — только для спорных свечей M15.
Логи и отчёты: runs/backtest/."""
import bisect, collections, datetime as dt, json, random, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # evaluate.py и fetch_candles.py в корне проекта
import evaluate as ev
import fetch_candles as fc

# Конфигурация монет бэктеста (watchlist удалён на этапе 10): этапы 7–8 и этап 9 (бывший watchlist без JCTUSDT).
COINS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT", "ARBUSDT")
COINS_4Y = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "AAVEUSDT", "ADAUSDT", "HYPEUSDT", "LDOUSDT",
            "OPUSDT", "ENAUSDT", "WIFUSDT", "ARBUSDT")
OUT = ev.ROOT / "runs" / "backtest"
TTL = 14 * ev.DAY          # срок жизни лимитного ордера
MIN_TRADES = 30            # меньше сделок — «мало данных»
M15 = 900000
# Модели комиссий: новая — лимит и тейк maker, рынок и стоп taker, проскальзывание стопа (стартовое); старая — taker везде.
NEW = dict(maker=0.0002, taker=0.00055, slip=0.0002)
OLD = dict(maker=0.00055, taker=0.00055, slip=0.0)
DEPOSIT, SLOTS, DAY_LIMIT, WEEK_LIMIT = 10000.0, 2, 0.02, 0.03
V0 = dict(fees=False, month_open_atr=None, no_m15_step=False, need_sweep=False, no_choch_swing_corr=False)
V1 = dict(V0, fees=True, month_open_atr=0.5)
V2 = dict(V1, no_m15_step=True)
VARIANTS = {"V0": V0, "V1": V1, "V2": V2, "V3": dict(V1, need_sweep=True),
            "V4": dict(V2, no_choch_swing_corr=True)}          # V4 (этап 9): V2 + a-to-b swing коррекция без CHoCH на H4
WORK = ("2025-11-02", "2026-11-01")
CONTROL = ("2024-11-02", "2025-11-01")
LAST120 = ("2026-07-05", "2026-11-01")


def set_variant(name):
    v = VARIANTS[name]
    fc.FEES = (NEW["maker"], NEW["taker"], NEW["slip"]) if v["fees"] else None
    ev.VARIANT.update(month_open_atr=v["month_open_atr"], no_m15_step=v["no_m15_step"], need_sweep=v["need_sweep"],
                      no_choch_swing_corr=v["no_choch_swing_corr"])


def day_ms(text):
    return int(dt.datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def dates_between(first, last):
    return list(range(day_ms(first), day_ms(last) + 1, ev.DAY))


# ---------- данные ----------

def load(symbol, first):
    """Вся история монеты для дат от first; списки по возрастанию и времена открытия для bisect."""
    data = {tf: fc.load_history(symbol, tf, ev.since(tf, first)) for tf in fc.TFS}
    return data, {tf: [b["t"] for b in bars] for tf, bars in data.items()}


def window(data, times, asof):
    """Срез свечей, открытых до asof, с запасом под fc.LIMIT закрытых; остальное отрежет evaluate.build."""
    out = {}
    for tf, bars in data.items():
        k = bisect.bisect_left(times[tf], asof)
        out[tf] = bars[max(0, k - fc.LIMIT - 2):k]
    return out


class Minutes:
    """Свечи M1 внутри одной свечи M15 для спорного порядка событий; кэш data/cache/ТИКЕР_1m.json."""

    def __init__(self, symbol):
        self.symbol, self.path = symbol, fc.CACHE / f"{symbol}_1m.json"
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.cat, self.dirty = None, False

    def __call__(self, t):
        key = str(t)
        if key not in self.data:
            rows = []
            for cat in [self.cat] if self.cat else ["linear", "spot"]:
                try:
                    rows = fc.api("kline", category=cat, symbol=self.symbol, interval="1", start=t, end=t + M15 - 1, limit=15)["list"]
                except Exception:
                    rows = []
                if rows:
                    self.cat = cat
                    break
            self.data[key] = sorted(fc.parse_rows(rows), key=lambda b: b["t"])
            self.dirty = True
        return self.data[key]

    def save(self):
        if self.dirty:
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
            self.dirty = False


# ---------- описание сетапа ----------

def kind_of(name):
    return "коррекция" if "коррекция" in name else "продолжение"


def strategy_of(name):
    """Название стратегии без стороны сделки."""
    return " ".join(w for w in name.split() if w not in ("лонг", "шорт"))


def sim_option(r):
    """План и вариант входа для симуляции: ГОТОВ — использованный вход; НЕТ-ПОЧТИ — лучший R:R ≥ 1.0
    из плана метода 1 и метода 2. Нет — None."""
    p = r["plan"]
    if not p:
        return None
    if r["status"] == ev.READY:
        o = ev.entry_of(p)
        return (p, o) if o and o.get("target") else None
    if r["status"] != ev.ALMOST:
        return None
    best = None
    for q in (p, p.get("alt")):
        for o in (q or {}).get("options", []):
            if o.get("rr") is not None and o["rr"] >= ev.RR_ALMOST and (best is None or o["rr"] > best[1]["rr"]):
                best = (q, o)
    return best


def entry_zone(r, p):
    """Зона входа: зона подтверждения метода 2, иначе зона из строки (зона A или SNR poi-sweep-snr)."""
    z = p.get("confirm") or r["info"].get("zone") or {}
    return z.get("lo"), z.get("hi"), z.get("tf"), z.get("text", "")


def record(symbol, asof, r):
    p = r["plan"]
    fails = ev.fails_of(r["conds"])
    o = ev.entry_of(p) if p else None
    rec = dict(symbol=symbol, asof=ev.utc(asof), strategy=r["name"], status=r["status"],
               single_fail=fails[0][0] if len(fails) == 1 else None,
               single_fail_text=fails[0][2] if len(fails) == 1 else None,
               single_fail_rr=fails[0][3] if len(fails) == 1 and fails[0][0] == "R:R" and len(fails[0]) > 3 else None,
               conds=[list(c[:3]) for c in r["conds"]], flags=r["info"].get("flags", []))
    if p:
        rec.update(method=p.get("method", "локальный"), step=p.get("step"), stop=p["stop"], cancel=p.get("cancel"),
                   entry=o["entry"] if o else None, entry_name=o["name"] if o else None,
                   target=o["target"][0] if o and o.get("target") else None, rr=o["rr"] if o else None,
                   swept=p.get("swept"))
    return rec


# ---------- симуляция ----------

def simulate(bars, times, asof, short, entry, stop, target, cancel, market, m1=None):
    """Исход по свечам M15 с открытием от asof.
    Лимит исполняется при касании входа; отменён, если до касания входа цена коснулась цели или свеча
    закрылась телом за уровнем отмены. После входа: стоп при касании стопа, цель при касании цели.
    Касание входа и цели или стопа и цели в одной свече M15 — порядок по M1 (m1(t) → свечи M1);
    M1 нет или в одной минуте оба события — консервативно: отмена до входа, стоп после входа.
    Ордер живёт 14 дней, потом «не исполнен». Данные кончились — «открыт».
    Возвращает dict(outcome, t_in, t_out, mfe — макс. ход к цели после входа в цене, m1_used, m1_failed)."""
    hit_e = (lambda b: b["h"] >= entry) if short else (lambda b: b["l"] <= entry)
    hit_s = (lambda b: b["h"] >= stop) if short else (lambda b: b["l"] <= stop)
    hit_t = (lambda b: b["l"] <= target) if short else (lambda b: b["h"] >= target)
    fav = (lambda b: entry - b["l"]) if short else (lambda b: b["h"] - entry)
    st = dict(outcome="открыт", t_in=asof if market else None, t_out=None, mfe=0.0, m1_used=0, m1_failed=0)

    def done(outcome, t):
        st.update(outcome=outcome, t_out=t)
        if outcome == "цель":
            st["mfe"] = max(st["mfe"], abs(target - entry))
        return st

    def walk(b):
        """Порядок событий внутри свечи M15 по M1: (исход, время), ("", None) — без исхода, None — M1 нет."""
        ms = m1(b["t"]) if m1 else []
        if not ms:
            st["m1_failed"] += 1
            return None
        st["m1_used"] += 1
        for m in ms:
            if st["t_in"] is None:
                if hit_t(m):
                    return "отменён", m["t"]           # цель раньше входа или в той же минуте
                if hit_e(m):
                    st["t_in"] = m["t"]
                    if hit_s(m):
                        return "стоп", m["t"]
                continue
            if hit_s(m):
                return "стоп", m["t"]
            if hit_t(m):
                return "цель", m["t"]
            st["mfe"] = max(st["mfe"], fav(m))
        return "", None

    for b in bars[bisect.bisect_left(times, asof):]:
        if st["t_in"] is None:
            if b["t"] >= asof + TTL:
                return done("не исполнен", asof + TTL)
            e, t = hit_e(b), hit_t(b)
            if e and t:
                r = walk(b)
                if r is None or (not r[0] and st["t_in"] is None):
                    return done("отменён", b["t"])
                if r[0]:
                    return done(*r)
                continue
            if e:
                st["t_in"] = b["t"]
                if hit_s(b):
                    return done("стоп", b["t"])
                continue
            if t or (cancel is not None and (b["c"] > cancel if short else b["c"] < cancel)):
                return done("отменён", b["t"])
            continue
        s, t = hit_s(b), hit_t(b)
        if s and t:
            r = walk(b)
            return done(*r) if r and r[0] else done("стоп", b["t"])
        if s:
            return done("стоп", b["t"])
        if t:
            return done("цель", b["t"])
        st["mfe"] = max(st["mfe"], fav(b))
    return st


def result_r(outcome, entry, stop, target, market=False, fees=OLD):
    """Результат в R по ценам плана: до комиссий и после комиссий модели fees. Не закрыта — (None, None).
    Комиссии: вход taker (рынок) или maker (лимит), выход по цели maker, по стопу taker плюс проскальзывание."""
    risk = abs(entry - stop)
    if outcome not in ("цель", "стоп") or risk <= 0:
        return None, None
    win = outcome == "цель"
    gross = abs(target - entry) / risk if win else -1.0
    cost = (fees["taker"] if market else fees["maker"]) * entry
    cost += fees["maker"] * target if win else (fees["taker"] + fees["slip"]) * stop
    return gross, gross - cost / risk


def setup_key(symbol, r, p, o):
    lo, hi, _, _ = entry_zone(r, p)
    entry = None if o["name"] == "рынок" else round(o["entry"], 10)
    return (symbol, r["name"], r["info"].get("side"), lo, hi, o["name"], entry)


# ---------- прогон периода ----------

def run_period(dates, coins, store, btc, minutes, log=None):
    """Статусы и сделки за даты. Возвращает (записи, сделки, пары монета-дата, пропущено без истории)."""
    records, trades, alive = [], [], {}
    evaluated = skipped = 0
    for asof in dates:
        btc_d = ev.btc_at(window(*btc, asof), asof)
        for s in coins:
            data, times = store[s]
            win = window(data, times, asof)
            if not ev.listed(win, asof):
                skipped += 1
                continue
            evaluated += 1
            for r in ev.evaluate(win, asof, None, [], btc_d):
                rec = record(s, asof, r)
                records.append(rec)
                if log:
                    log.write(json.dumps(rec, ensure_ascii=False) + "\n")
                pick = sim_option(r)
                if not pick:
                    continue
                p, o = pick
                key = (r["status"],) + setup_key(s, r, p, o)
                if alive.get(key, -1) > asof:
                    continue                 # тот же сетап ещё жив — не новая сделка
                trades.append(make_trade(s, asof, r, rec, p, o, data, times, minutes[s]))
                t = trades[-1]
                alive[key] = t["t_out_ms"] if t["t_out_ms"] is not None else float("inf")
    for m in minutes.values():
        m.save()
    return records, trades, evaluated, skipped


def make_trade(s, asof, r, rec, p, o, data, times, m1):
    short = r["info"]["side"] == "down"
    market = o["name"] == "рынок"
    entry, stop, target = o["entry"], p["stop"], o["target"][0]
    sim = simulate(data["15"], times["15"], asof, short, entry, stop, target, p.get("cancel"), market, m1)
    gross, r_new = result_r(sim["outcome"], entry, stop, target, market, NEW)
    _, r_old = result_r(sim["outcome"], entry, stop, target, market, OLD)
    risk = abs(entry - stop)
    lo, hi, ztf, ztext = entry_zone(r, p)
    a_zone = r["info"].get("zone") or {}
    return dict(rec, outcome=sim["outcome"], t_in_ms=sim["t_in"], t_out_ms=sim["t_out"], asof_ms=asof,
                filled=ev.utc(sim["t_in"]) if sim["t_in"] else None, closed=ev.utc(sim["t_out"]) if sim["t_out"] else None,
                entry=entry, entry_name=o["name"], stop=stop, target=target, target_text=o["target"][1], rr=o["rr"],
                r_gross=gross, r_new=r_new, r_old=r_old,
                win_new=result_r("цель", entry, stop, target, market, NEW)[1],
                loss_new=result_r("стоп", entry, stop, target, market, NEW)[1],
                mfe_r=sim["mfe"] / risk if risk else None, m1_used=sim["m1_used"], m1_failed=sim["m1_failed"],
                side=r["info"]["side"], kind=kind_of(r["name"]), entry_zone=[lo, hi, ztf, ztext],
                zone_a=[a_zone.get("lo"), a_zone.get("hi"), a_zone.get("tf"), a_zone.get("text")],
                entry_tf=fc.NAMES[p["entry_tf"]] if p.get("entry_tf") else ztf,
                method=p.get("method", "локальный"), step=p.get("step"), swept=p.get("swept"),
                poi=next((f"{c[0]}: {c[2]}" for c in r["conds"] if c[0].startswith("POI на")), None),
                quarter=quarter(asof))


def quarter(asof):
    d = dt.datetime.fromtimestamp(asof / 1000, dt.timezone.utc)
    return f"{d.year} Q{(d.month - 1) // 3 + 1}"


# ---------- портфель ----------

def overlaps(a, b):
    return a[0] is not None and b[0] is not None and a[0] <= b[1] and b[0] <= a[1]


def portfolio(trades, mode):
    """Портфель в долларах по сделкам ГОТОВ: депозит $10 000, риск 1% от баланса (mode="pct") или $100 ("fixed").
    Не больше 2 открытых позиций; лимиты убытка 2% в день и 3% в неделю (UTC) от баланса на начало дня/недели;
    одна идея (монета, сторона, пересекающиеся зоны входа) — одна позиция. Очередь — по времени исполнения.
    Результат позиции — r_new × риск в $. Открытые на конец — без нереализованного результата. Funding не учитывается."""
    fills = sorted((t for t in trades if t["status"] == ev.READY and t["t_in_ms"] is not None),
                   key=lambda t: (t["t_in_ms"], t["asof_ms"]))
    balance, open_, realized, taken = DEPOSIT, [], [], []
    skipped = collections.Counter()

    def close_until(t):
        nonlocal balance
        for pos in sorted([p for p in open_ if p["t_out_ms"] is not None and p["t_out_ms"] <= t], key=lambda p: p["t_out_ms"]):
            open_.remove(pos)
            pnl = pos["r_new"] * pos["risk_usd"]
            realized.append((pos["t_out_ms"], pnl, pos, balance))
            balance += pnl

    def loss_since(start):
        return sum(x[1] for x in realized if x[0] >= start)

    def balance_at(start):
        return DEPOSIT + sum(x[1] for x in realized if x[0] < start)

    for t in fills:
        close_until(t["t_in_ms"])
        day = t["t_in_ms"] - t["t_in_ms"] % ev.DAY
        week = day - dt.datetime.fromtimestamp(day / 1000, dt.timezone.utc).weekday() * ev.DAY
        idea = (t["symbol"], t["side"], t["entry_zone"][:2])
        if any(p["symbol"] == idea[0] and p["side"] == idea[1] and overlaps(p["entry_zone"][:2], idea[2]) for p in open_):
            skipped["та же идея"] += 1
        elif len(open_) >= SLOTS:
            skipped["нет свободного слота"] += 1
        elif loss_since(day) <= -DAY_LIMIT * balance_at(day):
            skipped["лимит убытка дня"] += 1
        elif loss_since(week) <= -WEEK_LIMIT * balance_at(week):
            skipped["лимит убытка недели"] += 1
        else:
            pos = dict(t, risk_usd=balance * 0.01 if mode == "pct" else 100.0)
            open_.append(pos)
            taken.append(pos)
    close_until(float("inf"))
    return portfolio_metrics(realized, taken, skipped, open_)


def portfolio_metrics(realized, taken, skipped, still_open):
    realized = sorted(realized, key=lambda x: x[0])
    peak, dd, dd_pct, bal = DEPOSIT, 0.0, 0.0, DEPOSIT
    for x in realized:
        bal += x[1]
        peak = max(peak, bal)
        dd, dd_pct = max(dd, peak - bal), max(dd_pct, (peak - bal) / peak)
    pnls = [x[1] for x in realized]
    rs = [x[2]["r_new"] for x in realized]
    streak = best = 0
    for r in rs:
        streak = streak + 1 if r < 0 else 0
        best = max(best, streak)
    monthly = collections.defaultdict(float)
    for x in realized:
        monthly[dt.datetime.fromtimestamp(x[0] / 1000, dt.timezone.utc).strftime("%Y-%m")] += x[1]
    gain, loss = sum(p for p in pnls if p > 0), -sum(p for p in pnls if p < 0)
    return dict(balance=bal, pct=(bal / DEPOSIT - 1) * 100, dd=dd, dd_pct=dd_pct * 100, trades=len(realized),
                opened=len(taken), still_open=len(still_open), wins=sum(1 for r in rs if r > 0),
                pf=gain / loss if loss else None, mean_r=mean(rs), ci=boot_ci(rs), streak=best,
                monthly=dict(sorted(monthly.items())), skipped=dict(skipped),
                returns=[(x[0], x[1] / x[3]) for x in realized])   # (время выхода, доля баланса до сделки)


# ---------- статистика ----------

def mean(xs):
    return sum(xs) / len(xs) if xs else None


def boot_ci(xs, n=2000, seed=7):
    """95% доверительный интервал среднего (bootstrap, n повторов, фиксированный seed)."""
    if len(xs) < 2:
        return None, None
    rnd = random.Random(seed)
    means = sorted(sum(rnd.choice(xs) for _ in xs) / len(xs) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def breakeven(ts):
    """Безубыточный % побед по фактическим R:R набора: p, при котором p·mean(W) + (1−p)·mean(L) = 0,
    W и L — результат каждой сделки в R при выигрыше и проигрыше после комиссий (новая модель)."""
    w, l = mean([t["win_new"] for t in ts]), mean([t["loss_new"] for t in ts])
    return -l / (w - l) * 100 if w is not None and w != l else None


def pct(a, b):
    return f"{a / b * 100:.1f}%" if b else "—"


def fmt(x, d=2):
    return "—" if x is None else f"{x:.{d}f}"


def ci_text(ci):
    return "—" if ci[0] is None else f"[{ci[0]:.2f}; {ci[1]:.2f}]"


def trade_stats(ts):
    """Строка статистики группы сделок."""
    n = len(ts)
    c = collections.Counter(t["outcome"] for t in ts)
    closed = [t for t in ts if t["r_gross"] is not None]
    actual = c["цель"] / len(closed) * 100 if closed else None
    note = "  мало данных" if len(closed) < MIN_TRADES else ""
    return (f"n={n} цель {pct(c['цель'], n)} стоп {pct(c['стоп'], n)} не исполнен {pct(c['не исполнен'], n)} "
            f"отменён {pct(c['отменён'], n)} открыт {pct(c['открыт'], n)} | закрыто {len(closed)}, средний R до "
            f"{fmt(mean([t['r_gross'] for t in closed]))}, после: новая {fmt(mean([t['r_new'] for t in closed]))} "
            f"старая {fmt(mean([t['r_old'] for t in closed]))} | безубыточный % побед {fmt(breakeven(closed), 1)}, "
            f"фактический {fmt(actual, 1)}{note}")


FLAG_GROUPS = (("стоп близко", "стоп ближе"), ("R:R > 5", "R:R > 5"), ("FTA < 1R", "FTA < 1R"),
               ("короткая история", "короткая история"), ("против BTC", ", против"))


def breakdown(lines, title, ts):
    lines.append(f"\n{title}")
    groups = [
        ("метод", lambda t: str(t["method"])),
        ("ступень метода 2", lambda t: t["step"] if t["method"] == 2 else None),
        ("коррекция/продолжение", lambda t: t["kind"]),
        ("свип (poi-sweep-snr)", lambda t: None if t["swept"] is None else ("был" if t["swept"] else "не был")),
        ("квартал", lambda t: t["quarter"]),
        ("стратегия", lambda t: strategy_of(t["strategy"])),
    ]
    lines.append(f"  все: {trade_stats(ts)}")
    for name, key in groups:
        vals = collections.defaultdict(list)
        for t in ts:
            k = key(t)
            if k is not None:
                vals[k].append(t)
        for k in sorted(vals):
            lines.append(f"  {name} = {k}: {trade_stats(vals[k])}")
    for name, needle in FLAG_GROUPS:
        with_f = [t for t in ts if any(needle in f for f in t["flags"])]
        without = [t for t in ts if not any(needle in f for f in t["flags"])]
        lines.append(f"  флаг {name}: есть — {trade_stats(with_f)}")
        lines.append(f"  флаг {name}: нет — {trade_stats(without)}")


def portfolio_text(title, m):
    note = "  мало данных" if m["trades"] < MIN_TRADES else ""
    month = ", ".join(f"{k} {v:+.0f}" for k, v in m["monthly"].items()) or "нет"
    skipped = ", ".join(f"{k} {v}" for k, v in m["skipped"].items()) or "нет"
    return (f"  {title}: баланс ${m['balance']:,.0f} ({m['pct']:+.1f}%), макс. просадка ${m['dd']:,.0f} ({m['dd_pct']:.1f}%), "
            f"сделок {m['trades']} (открыто на конец {m['still_open']}), побед {pct(m['wins'], m['trades'])}, "
            f"profit factor {fmt(m['pf'])}, средний R {fmt(m['mean_r'])} ДИ95 {ci_text(m['ci'])}, "
            f"серия убытков {m['streak']}{note}\n    помесячно $: {month}\n    пропущено: {skipped}")


def m1_text(trades):
    used, failed = sum(t["m1_used"] for t in trades), sum(t["m1_failed"] for t in trades)
    return f"спорных свечей M15: разобрано по M1 {used}, M1 нет (консервативно) {failed}"


def period_report(title, records, trades, evaluated, skipped, coins, dates):
    L = [f"\n===== {title}: даты {ev.utc(dates[0])} .. {ev.utc(dates[-1])} UTC",
         f"дат {len(dates)}, монет {len(coins)}, пар монета-дата {evaluated}, пропущено без истории {skipped}, "
         f"строк стратегий {len(records)}, стратегий {len({strategy_of(r['strategy']) for r in records})}",
         m1_text(trades), "Частота статусов по стратегиям (доля от пар монета-дата):"]
    by = collections.defaultdict(collections.Counter)
    for r in records:
        by[strategy_of(r["strategy"])][r["status"]] += 1
    for s in sorted(by):
        c = by[s]
        L.append(f"  {s}: ГОТОВ {c[ev.READY]} ({pct(c[ev.READY], evaluated)}), почти {c[ev.ALMOST]} ({pct(c[ev.ALMOST], evaluated)}), "
                 f"НЕТ {c[ev.NO]}, НЕ ПРОВЕРЕНО {c[ev.UNCHECKED]}")
    only_rr = [r for r in records if r["single_fail"] == "R:R"]
    buckets = collections.Counter()
    for r in only_rr:
        x = r["single_fail_rr"]
        buckets["нет R:R" if x is None else "< 0.5" if x < 0.5 else "0.5–1.0" if x < 1.0 else "1.0–1.5"] += 1
    L.append(f"Упали только по R:R: {len(only_rr)} из {len(records)} ({pct(len(only_rr), len(records))}); "
             + ", ".join(f"{k}: {buckets[k]}" for k in ("нет R:R", "< 0.5", "0.5–1.0", "1.0–1.5")))
    ready = [t for t in trades if t["status"] == ev.READY]
    almost = [t for t in trades if t["status"] == ev.ALMOST]
    L.append(f"Сделки после дедупликации: ГОТОВ {len(ready)}, НЕТ-ПОЧТИ с R:R ≥ 1.0 {len(almost)}")
    for tf in ("M15", "H1"):
        sig = [t for t in ready if t["entry_tf"] == tf]
        L.append(f"  сигналы ГОТОВ с зоной входа {tf}: {len(sig)}, дожили до исполнения "
                 f"{sum(1 for t in sig if t['filled'])} ({pct(sum(1 for t in sig if t['filled']), len(sig))})")
    breakdown(L, "ГОТОВ:", ready)
    breakdown(L, "НЕТ-ПОЧТИ с R:R ≥ 1.0 (симуляция того же входа):", almost)
    return L


# ---------- диагностика ----------

def diagnostics(trades):
    L = ["\n===== Диагностика (V0, рабочий период)"]
    swing = [t for t in trades if t["status"] == ev.READY and strategy_of(t["strategy"]) == "a-to-b swing продолжение"
             and t["r_gross"] is not None]
    L.append(f"a-to-b swing продолжение, закрытые ГОТОВ: {len(swing)}" + ("  мало данных" if len(swing) < MIN_TRADES else ""))
    for t in swing:
        L.append(f"  {t['symbol']} {t['asof']} {t['strategy']}: вход {t['entry_name']} {fc.fmt(t['entry'])} ({t['filled']}), "
                 f"стоп {fc.fmt(t['stop'])}, цель {fc.fmt(t['target'])} ({t['target_text']}), R:R {fmt(t['rr'])}, "
                 f"исход {t['outcome']} {t['closed']}, MFE {fmt(t['mfe_r'])}R")
    corr = [t for t in trades if t["status"] == ev.ALMOST and strategy_of(t["strategy"]) == "a-to-b swing коррекция"
            and t["r_gross"] is not None]
    L.append(f"\na-to-b swing коррекция «почти», закрытые: {len(corr)}, по невыполненному условию:")
    groups = collections.defaultdict(list)
    for t in corr:
        f = t["single_fail"] or "?"
        if f == "R:R":
            x = t["single_fail_rr"]
            f = "R:R " + ("нет" if x is None else "1.0–1.5" if x >= 1.0 else "< 1.0")
        elif (t["single_fail_text"] or "").startswith("ждём"):
            f = f"{f} ({t['single_fail_text']})"
        groups[f].append(t)
    for k in sorted(groups, key=lambda k: -len(groups[k])):
        L.append(f"  {k}: {trade_stats(groups[k])}")
    closed = [t for t in trades if t["status"] == ev.READY and t["r_gross"] is not None and t["mfe_r"] is not None]
    stops = [t for t in closed if t["outcome"] == "стоп"]
    L.append(f"\nMFE закрытых ГОТОВ (макс. ход к цели после входа до выхода, в R): {len(closed)} сделок, из них стопов {len(stops)}")
    for k in (0.5, 1, 2, 3):
        L.append(f"  дошли до {k}R: все {pct(sum(1 for t in closed if t['mfe_r'] >= k), len(closed))}, "
                 f"среди стопов {pct(sum(1 for t in stops if t['mfe_r'] >= k), len(stops))}")
    L.append("  MFE считается по свечам после свечи входа (минутам после входа, если разобрано по M1); свеча выхода по стопу не входит.")
    return L


def samples(trades, k=5, seed=7):
    ready = [t for t in trades if t["status"] == ev.READY]
    L = [f"\n{k} случайных сделок ГОТОВ для сверки с TradingView (seed {seed}):"]
    for t in random.Random(seed).sample(ready, min(k, len(ready))):
        z, e = t["zone_a"], t["entry_zone"]
        where = t["poi"] if t.get("poi") else f"зона A {z[2]} {fc.fmt(z[0]) if z[0] else ''}-{fc.fmt(z[1]) if z[1] else ''}: {z[3]}"
        L.append(f"  {t['symbol']} {t['asof']} {t['strategy']} (метод {t['method']}{', ступень ' + t['step'] if t['step'] else ''})\n"
                 f"    {where}\n    зона входа {e[2]} {fc.fmt(e[0]) if e[0] else ''}-{fc.fmt(e[1]) if e[1] else ''}: {e[3]}\n"
                 f"    вход {t['entry_name']} {fc.fmt(t['entry'])}, стоп {fc.fmt(t['stop'])}, цель {fc.fmt(t['target'])} "
                 f"({t['target_text']}), R:R {fmt(t['rr'])}\n    исход: {t['outcome']}, вход {t['filled']}, закрыт {t['closed']}, "
                 f"R до {fmt(t['r_gross'])} после {fmt(t['r_new'])}; флаги: {', '.join(t['flags']) or 'нет'}")
    return L


# ---------- запуск ----------

def prepare(coins, first):
    t0 = time.time()
    store = {s: load(s, first) for s in coins}
    btc = store.get("BTCUSDT") or load("BTCUSDT", first)
    return store, btc, {s: Minutes(s) for s in coins}, time.time() - t0


def write(name, rows):
    with (OUT / name).open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def run(days, coins, variant):
    """Один период до вчера: отчёт, портфель, образцы."""
    set_variant(variant)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    today = int(time.time() * 1000) // ev.DAY * ev.DAY
    dates = [today - d * ev.DAY for d in range(days, 0, -1)]
    store, btc, minutes, t_load = prepare(coins, dates[0])
    t0 = time.time()
    with (OUT / f"log_{variant}_{days}d_{stamp}.jsonl").open("w", encoding="utf-8") as log:
        records, trades, evaluated, skipped = run_period(dates, coins, store, btc, minutes, log)
    write(f"trades_{variant}_{days}d_{stamp}.jsonl", trades)
    L = [f"Бэктест правил, вариант {variant}, {days} дней; время: загрузка {t_load:.0f} с, расчёт {time.time() - t0:.0f} с",
         "Не вывод о прибыльности: проверка правил на истории. Журнал не меняется. Funding не учитывается."]
    L += period_report(f"{variant}", records, trades, evaluated, skipped, coins, dates)
    L.append("\nПортфель (ГОТОВ, депозит $10 000):")
    L.append(portfolio_text("риск 1% от баланса", portfolio(trades, "pct")))
    L.append(portfolio_text("фиксированно $100", portfolio(trades, "fixed")))
    L += samples(trades)
    text = "\n".join(L)
    (OUT / f"report_{variant}_{days}d_{stamp}.txt").write_text(text, encoding="utf-8")
    print(text)


def stage8(coins):
    """Этап 8: V0–V3 на рабочем и контрольном периодах, портфель V0 на 365 и 120 днях, диагностика."""
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    store, btc, minutes, t_load = prepare(coins, day_ms(CONTROL[0]))
    t0 = time.time()
    periods = {"рабочий": dates_between(*WORK), "контрольный": dates_between(*CONTROL)}
    results = {}
    for v in VARIANTS:
        set_variant(v)
        for pname, dates in periods.items():
            records, trades, evaluated, skipped = run_period(dates, coins, store, btc, minutes)
            write(f"trades8_{v}_{pname}_{stamp}.jsonl", trades)
            results[v, pname] = (records, trades, evaluated, skipped, dates)
            print(f"{v} {pname}: строк {len(records)}, сделок {len(trades)}, {time.time() - t0:.0f} с", file=sys.stderr)
    set_variant("V0")
    last = run_period(dates_between(*LAST120), coins, store, btc, minutes)
    set_variant("V0")
    L = [f"ЭТАП 8. Время: загрузка {t_load:.0f} с, расчёт {time.time() - t0:.0f} с",
         "Не вывод о прибыльности: проверка правил на истории. Правила в файлах не менялись. Журнал не меняется.",
         "Комиссии: новая модель — лимит и тейк maker 0.02%, рынок и стоп taker 0.055%, проскальзывание стопа 0.02%; "
         "старая — taker 0.055% везде. Funding не учитывается."]
    # часть 2: портфель V0
    L.append("\n===== Портфель V0 (ГОТОВ), депозит $10 000 на каждый прогон")
    for title, trades in (("365 дней 02.11.2025–01.11.2026", results["V0", "рабочий"][1]),
                          ("120 дней 05.07.2026–01.11.2026", last[1])):
        L.append(f"{title}:")
        L.append(portfolio_text("риск 1% от баланса", portfolio(trades, "pct")))
        L.append(portfolio_text("фиксированно $100", portfolio(trades, "fixed")))
    # часть 4: сводная таблица
    L.append("\n===== Варианты × периоды (ГОТОВ после дедупликации; портфель при риске 1% от баланса)")
    L.append("вариант | период | сделок закрыто | % побед | средний R после комиссий (новая) | ДИ95 | портфель $ | макс. просадка")
    for (v, pname), (records, trades, *_rest) in results.items():
        closed = [t for t in trades if t["status"] == ev.READY and t["r_new"] is not None]
        rs = [t["r_new"] for t in closed]
        m = portfolio(trades, "pct")
        note = " мало данных" if len(closed) < MIN_TRADES else ""
        L.append(f"{v} | {pname} | {len(closed)}{note} | {pct(sum(1 for t in closed if t['outcome'] == 'цель'), len(closed))} | "
                 f"{fmt(mean(rs))} | {ci_text(boot_ci(rs))} | ${m['balance']:,.0f} ({m['pct']:+.1f}%) | "
                 f"${m['dd']:,.0f} ({m['dd_pct']:.1f}%)")
    # часть 3: диагностика и подробности V0
    L += diagnostics(results["V0", "рабочий"][1])
    for pname in periods:
        records, trades, evaluated, skipped, dates = results["V0", pname]
        L += period_report(f"V0, {pname} период", records, trades, evaluated, skipped, coins, dates)
    L += samples(results["V0", "рабочий"][1])
    text = "\n".join(L)
    (OUT / f"report_stage8_{stamp}.txt").write_text(text, encoding="utf-8")
    print(text)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    variant = "V0"
    if "--variant" in args:
        k = args.index("--variant")
        variant = args[k + 1].upper()
        del args[k:k + 2]
    if "--stage8" in args:
        args.remove("--stage8")
        stage8([a.upper() for a in args] or list(COINS))
        return
    days = int(args[0]) if args and args[0].isdigit() else 365
    run(days, [a.upper() for a in args if not a.isdigit()] or list(COINS), variant)


if __name__ == "__main__":
    main()
