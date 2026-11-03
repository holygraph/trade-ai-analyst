"""Историческая частота для /analyst: stats/base_rates.json из сделок ГОТОВ варианта V2 этапа 9 (4 года).
Запуск: python research/base_rates.py [runs/backtest/trades9_V2_*.jsonl]
Для каждой исполненной сделки независимо по каждому уровню k R: гипотетический тейк entry ± k·R и стоп −1R
(собственная цель сделки не используется). Свеча входа лимитом: только стоп (порядок с тейком неизвестен);
вход по рынку: тейк и стоп с первой свечи. Тейк и стоп в одной свече — стоп. Ни то ни другое до конца данных —
сделка в уровень не входит. Доля дошедших, число сделок, 95% ДИ Уилсона; база случайного блуждания 1/(1+k).
Это частота похожих сетапов на истории, не прогноз."""
import bisect, collections, datetime as dt, json, math, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest as bt
import evaluate as ev
import fetch_candles as fc
import stage9 as s9
import i18n

LEVELS = (0.5, 1, 1.5, 2, 3)
FLAGS = (("стоп близко", "стоп ближе"), ("FTA < 1R", "FTA < 1R"), ("против BTC", ", против"),
         ("короткая история", "короткая история"))
OUT = ev.ROOT / "stats" / "base_rates.json"


def wilson(hit, n, z=1.96):
    """95% доверительный интервал доли (Уилсон)."""
    if not n:
        return None, None
    p = hit / n
    d = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, mid - half), min(1.0, mid + half)


def reach(fast, t, k):
    """Дошла ли цена до entry ± k·R раньше стопа: True, False или None (не решилось до конца данных)."""
    short, entry, stop = t["side"] == "down", t["entry"], t["stop"]
    risk = abs(entry - stop)
    level = entry - k * risk if short else entry + k * risk
    f = bisect.bisect_right(fast.t, t["t_in_ms"]) - 1
    start = f
    if t["entry_name"] != "рынок":
        if (fast.hmax[0][f] >= stop) if short else (fast.lmin[0][f] <= stop):
            return False
        start = f + 1
    if short:
        i_s, i_k = s9.first_ge(fast.hmax, start, fast.n, stop), s9.first_le(fast.lmin, start, fast.n, level)
    else:
        i_s, i_k = s9.first_le(fast.lmin, start, fast.n, stop), s9.first_ge(fast.hmax, start, fast.n, level)
    if i_k is None and i_s is None:
        return None
    return i_k is not None and (i_s is None or i_k < i_s)


def row_key(t):
    base = bt.strategy_of(t["strategy"]).replace(" продолжение", "").replace(" коррекция", "")
    return f"{base} {t['kind']}"


def summarize(results):
    """results: список {k: True/False/None}. Возвращает уровни с долей, ДИ и базой 1/(1+k)."""
    out = {}
    for k in LEVELS:
        vals = [r[k] for r in results if r[k] is not None]
        hit = sum(vals)
        lo, hi = wilson(hit, len(vals))
        out[str(k)] = dict(hit=hit, n=len(vals), rate=hit / len(vals) if vals else None, ci=[lo, hi],
                           random=1 / (1 + k), low_sample=len(vals) < bt.MIN_TRADES)
    return out


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else max((ev.ROOT / "runs" / "backtest").glob("trades9_V2_*.jsonl"))
    trades = [json.loads(x) for x in src.read_text(encoding="utf-8").splitlines() if x.strip()]
    trades = [t for t in trades if t["status"] == ev.READY and t["t_in_ms"] is not None]
    per = []
    for sym in sorted({t["symbol"] for t in trades}):
        bars = fc.load_history(sym, "15", ev.since("15", bt.day_ms(s9.START)))
        fast = s9.Fast(bars)
        for t in (x for x in trades if x["symbol"] == sym):
            per.append((t, {k: reach(fast, t, k) for k in LEVELS}))
        print(f"{sym}: готово", file=sys.stderr)
    groups = collections.OrderedDict([("все", [r for _, r in per])])
    for name in (f"{s} {k}" for s in ("basics", "a-to-b swing", "a-to-b intraweek", "poi-sweep-snr") for k in ("продолжение", "коррекция")):
        groups[name] = [r for t, r in per if row_key(t) == name]
    for name, needle in FLAGS:
        groups[f"флаг {name}: есть"] = [r for t, r in per if any(needle in f for f in t["flags"])]
        groups[f"флаг {name}: нет"] = [r for t, r in per if not any(needle in f for f in t["flags"])]
    data = dict(meta=dict(source=src.name, variant="V2 (этап 9)", period=f"{s9.START} – {s9.END}", levels=list(LEVELS),
                          generated=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), trades=len(per),
                          method=__doc__.split("\n", 2)[2].strip()),
                groups={name: dict(trades=len(rs), levels=summarize(rs)) for name, rs in groups.items()})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"записано {OUT} ({len(per)} сделок)")


if __name__ == "__main__":
    i18n.setup()
    main()
