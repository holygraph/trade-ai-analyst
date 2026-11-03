"""Журнал сделок trade-ai-analyst. Записывает только по явной просьбе пользователя.
Хранится вне плагина: папка TRADE_ANALYST_HOME (по умолчанию ~/trade-ai-analyst), файл journal/journal.jsonl.
Схема: journal/README.md.
Запуск:
  python journal.py add ТИКЕР long|short --strategy S --kind продолжение|коррекция --entry E --stop S --target T
                    [--risk 1] [--verdict "ВХОД ДА|ЖДАТЬ|НЕТ"] [--taken true|false] [--market] [--sweep был|не был]
                    [--flags "a; b"] [--tags a,b] [--notes текст]
  python journal.py close ТИКЕР ВЫХОД [--id ID] [--date ГГГГ-ММ-ДД]   результат в R после комиссий
  python journal.py cancel ТИКЕР [--id ID]
  python journal.py stats
"""
import argparse, datetime as dt, json, os, random, sys
from pathlib import Path

import i18n

FIELDS = ("id", "symbol", "strategy", "side", "kind", "sweep", "date_open", "date_close", "entry", "stop",
          "target", "rr_planned", "risk_pct", "flags", "status", "result_r", "pnl_pct", "notes")
EXTRA = ("rules_verdict", "taken", "tags", "entry_type")
FEES = dict(maker=0.0002, taker=0.00055, slip=0.0002)     # как в бэктесте этапа 8
MIN_TRADES = 30
TOLERANCE = 1e-9


def data_dir():
    """Постоянная папка пользователя: TRADE_ANALYST_HOME или ~/trade-ai-analyst."""
    return Path(os.environ.get("TRADE_ANALYST_HOME") or Path.home() / "trade-ai-analyst")


def journal_path():
    return data_dir() / "journal" / "journal.jsonl"


def load(path=None):
    path = path or journal_path()
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def save(entries, path=None):
    path = path or journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8")


def today():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def side_of(text):
    t = text.lower()
    if t in ("long", "лонг", "buy"):
        return "long"
    if t in ("short", "шорт", "sell"):
        return "short"
    raise ValueError(f"сторона: long или short, а не {text}")


def new_entry(symbol, side, strategy, kind, entry, stop, target, risk=1.0, verdict=None, taken=True, market=False,
              sweep=None, flags=(), tags=(), notes="", date=None):
    """Запись по схеме journal/README.md плюс rules_verdict, taken, tags, entry_type."""
    side = side_of(side)
    risk_price = abs(entry - stop)
    if risk_price <= 0 or (side == "long" and stop >= entry) or (side == "short" and stop <= entry):
        raise ValueError("стоп должен быть за входом: ниже для long, выше для short")
    return dict(id=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S%f"), symbol=symbol.upper(), strategy=strategy,
                side=side, kind=kind, sweep=sweep, date_open=date or today(), date_close=None, entry=entry, stop=stop,
                target=target, rr_planned=abs(target - entry) / risk_price if target is not None else None,
                risk_pct=risk, flags=list(flags), status="open", result_r=None, pnl_pct=None, notes=notes,
                rules_verdict=verdict, taken=taken, tags=list(tags), entry_type="рынок" if market else "лимит")


def result_r(e, exit_price):
    """Результат в R после комиссий: вход maker (лимит) или taker (рынок); выход по цели maker,
    по стопу taker плюс проскальзывание, иначе taker."""
    risk = abs(e["entry"] - e["stop"])
    sign = 1 if e["side"] == "long" else -1
    gross = sign * (exit_price - e["entry"]) / risk
    cost = (FEES["taker"] if e.get("entry_type") == "рынок" else FEES["maker"]) * e["entry"]
    if e.get("target") is not None and abs(exit_price - e["target"]) <= TOLERANCE * e["entry"]:
        cost += FEES["maker"] * exit_price
    elif abs(exit_price - e["stop"]) <= TOLERANCE * e["entry"]:
        cost += (FEES["taker"] + FEES["slip"]) * exit_price
    else:
        cost += FEES["taker"] * exit_price
    return gross - cost / risk


def find_open(entries, symbol, entry_id=None):
    cands = [e for e in entries if e["status"] == "open" and e["symbol"] == symbol.upper()
             and (entry_id is None or e["id"] == entry_id)]
    if not cands:
        raise ValueError(f"открытой записи {symbol.upper()} нет")
    return cands[-1]


def close(entries, symbol, exit_price, entry_id=None, date=None):
    e = find_open(entries, symbol, entry_id)
    r = result_r(e, exit_price)
    e.update(status="closed", date_close=date or today(), result_r=r, pnl_pct=r * float(e["risk_pct"]), exit=exit_price)
    return e


def cancel(entries, symbol, entry_id=None):
    e = find_open(entries, symbol, entry_id)
    e.update(status="cancelled", date_close=today())
    return e


def boot_ci(xs, n=2000, seed=7):
    if len(xs) < 2:
        return None, None
    rnd, k = random.Random(seed), len(xs)
    means = sorted(sum(rnd.choices(xs, k=k)) / k for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def group_line(name, rs):
    lo, hi = boot_ci(rs)
    ci = "—" if lo is None else f"[{lo:.2f}; {hi:.2f}]"
    wins = sum(1 for r in rs if r > 0)
    note = "  мало данных" if len(rs) < MIN_TRADES else ""
    return f"  {name}: сделок {len(rs)}, побед {wins / len(rs) * 100:.1f}%, средний R {sum(rs) / len(rs):.2f}, ДИ95 {ci}{note}"


def stats(entries):
    """Закрытые записи по стратегии, тегам, флагам и вердикту правил против решения пользователя (taken)."""
    done = [e for e in entries if e["status"] == "closed" and e.get("result_r") is not None]
    opened = sum(1 for e in entries if e["status"] == "open")
    lines = [f"Журнал: {journal_path()}", f"записей {len(entries)}, открыто {opened}, закрыто {len(done)}"]
    if not done:
        lines.append("Закрытых сделок нет: статистики нет.")
        return "\n".join(lines)
    lines.append(group_line("все закрытые", [e["result_r"] for e in done]))
    for title, keys in (("по стратегии", lambda e: [f"{e['strategy']} {e['kind']}"]),
                        ("по тегам", lambda e: e.get("tags") or []),
                        ("по флагам", lambda e: e.get("flags") or []),
                        ("правила × решение", lambda e: [f"правила {e.get('rules_verdict') or '—'}, "
                                                         f"{'взял' if e.get('taken') else 'не брал'}"])):
        groups = {}
        for e in done:
            for k in keys(e):
                groups.setdefault(k, []).append(e["result_r"])
        if groups:
            lines.append(title + ":")
            lines += [group_line(k, rs) for k, rs in sorted(groups.items())]
    return "\n".join(lines)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Журнал trade-ai-analyst (только по просьбе пользователя)")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("symbol")
    a.add_argument("side")
    a.add_argument("--strategy", required=True)
    a.add_argument("--kind", required=True, choices=("продолжение", "коррекция"))
    a.add_argument("--entry", type=float, required=True)
    a.add_argument("--stop", type=float, required=True)
    a.add_argument("--target", type=float)
    a.add_argument("--risk", type=float, default=1.0)
    a.add_argument("--verdict")
    a.add_argument("--taken", default="true", choices=("true", "false"))
    a.add_argument("--market", action="store_true")
    a.add_argument("--sweep")
    a.add_argument("--flags", default="")
    a.add_argument("--tags", default="")
    a.add_argument("--notes", default="")
    c = sub.add_parser("close")
    c.add_argument("symbol")
    c.add_argument("exit", type=float)
    c.add_argument("--id")
    c.add_argument("--date")
    x = sub.add_parser("cancel")
    x.add_argument("symbol")
    x.add_argument("--id")
    sub.add_parser("stats")
    args = p.parse_args(argv)
    entries = load()
    try:
        if args.cmd == "add":
            e = new_entry(args.symbol, args.side, args.strategy, args.kind, args.entry, args.stop, args.target, args.risk,
                          args.verdict, args.taken == "true", args.market, args.sweep,
                          [f.strip() for f in args.flags.split(";") if f.strip()],
                          [t.strip() for t in args.tags.split(",") if t.strip()], args.notes)
            entries.append(e)
            save(entries)
            print(f"записано: {e['id']} {e['symbol']} {e['side']} {e['strategy']} {e['kind']}, вход {e['entry']}, стоп {e['stop']}")
        elif args.cmd == "close":
            e = close(entries, args.symbol, args.exit, args.id, args.date)
            save(entries)
            print(f"закрыто: {e['id']} {e['symbol']}, результат {e['result_r']:.2f}R после комиссий, {e['pnl_pct']:.2f}% счёта")
        elif args.cmd == "cancel":
            e = cancel(entries, args.symbol, args.id)
            save(entries)
            print(f"отменено: {e['id']} {e['symbol']}")
        else:
            print(stats(entries))
    except ValueError as err:
        print(f"ошибка: {err}")
        return 1
    return 0


if __name__ == "__main__":
    i18n.setup()
    sys.exit(main())
