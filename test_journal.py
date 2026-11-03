"""Тесты journal.py во временной папке TRADE_ANALYST_HOME: python test_journal.py"""
import datetime as dt, os, tempfile
from pathlib import Path

os.environ["TRADE_ANALYST_HOME"] = tempfile.mkdtemp()
import evaluate as ev
import journal as jr

assert jr.data_dir() == Path(os.environ["TRADE_ANALYST_HOME"])
assert jr.journal_path() == jr.data_dir() / "journal" / "journal.jsonl"
assert jr.load() == []
assert jr.stats([]).endswith("Закрытых сделок нет: статистики нет.")

# запись: схема README плюс rules_verdict, taken, tags, entry_type; стоп проверяется
e = jr.new_entry("nearusdt", "long", "a-to-b intraweek", "продолжение", 100, 98, 104, verdict="ВХОД ДА",
                 tags=["тест"], flags=["FTA < 1R"])
assert all(k in e for k in jr.FIELDS + jr.EXTRA) and e["symbol"] == "NEARUSDT" and e["rr_planned"] == 2.0
assert e["status"] == "open" and e["taken"] is True and e["entry_type"] == "лимит"
for bad in (("long", 100, 101), ("short", 100, 99), ("вбок", 100, 98)):
    try:
        jr.new_entry("X", bad[0], "s", "продолжение", bad[1], bad[2], None)
        raise AssertionError(bad)
    except ValueError:
        pass

# результат в R после комиссий: цель — maker, стоп — taker + проскальзывание, рынок на входе — taker
win = jr.result_r(e, 104)
assert abs(win - (2 - (100 * 0.0002 + 104 * 0.0002) / 2)) < 1e-12
loss = jr.result_r(e, 98)
assert abs(loss - (-1 - (100 * 0.0002 + 98 * 0.00075) / 2)) < 1e-12
assert jr.result_r(dict(e, entry_type="рынок"), 104) < win
assert abs(jr.result_r(e, 101) - (0.5 - (100 * 0.0002 + 101 * 0.00055) / 2)) < 1e-12   # ручной выход — taker

# сохранение, закрытие, отмена; evaluate читает этот журнал и считает лимиты
entries = [e, jr.new_entry("ETHUSDT", "short", "poi-sweep-snr", "коррекция", 2000, 2020, 1950, verdict="НЕТ", taken=False)]
jr.save(entries)
assert len(jr.load()) == 2
closed = jr.close(entries, "nearusdt", 104, date="2026-11-02")
assert closed["status"] == "closed" and abs(closed["pnl_pct"] - closed["result_r"]) < 1e-12
jr.cancel(entries, "ETHUSDT")
try:
    jr.close(entries, "ETHUSDT", 1900)
    raise AssertionError("закрыта отменённая")
except ValueError:
    pass
jr.save(entries)
read = ev.read_journal(jr.data_dir() / "journal")
assert read is not None and len(read) == 2
asof = int(dt.datetime(2026, 11, 2, 12, tzinfo=dt.timezone.utc).timestamp() * 1000)
assert ev.c_limits(read, asof)[1] == ev.PASS

# статистика: группы по стратегии, тегам, флагам, вердикт × решение, мало данных
s = jr.stats(entries)
assert "все закрытые: сделок 1" in s and "мало данных" in s and "a-to-b intraweek продолжение" in s, s
assert "тест" in s and "FTA < 1R" in s and "правила ВХОД ДА, взял" in s, s

# CLI
assert jr.main(["add", "SOLUSDT", "long", "--strategy", "basics", "--kind", "коррекция", "--entry", "150", "--stop", "145",
                "--target", "160", "--verdict", "ЖДАТЬ", "--taken", "false", "--tags", "a,b"]) == 0
assert jr.load()[-1]["tags"] == ["a", "b"] and jr.load()[-1]["taken"] is False
assert jr.main(["close", "SOLUSDT", "145"]) == 0 and jr.load()[-1]["result_r"] < -1
assert jr.main(["close", "SOLUSDT", "145"]) == 1                      # открытой записи уже нет
print("OK")
