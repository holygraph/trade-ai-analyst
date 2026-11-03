# trade-ai-analyst

A Claude Code slash command, `/analyst`, that checks one crypto trade idea against a fixed set of rule-based strategy checks.
It pulls closed candles from the public Bybit API (perpetual, otherwise spot), evaluates each strategy's conditions in code,
and prints statuses, levels and R:R. The model only relays the script's output; it does not recompute numbers.

> **Not financial advice.** The output is a rules check, not a recommendation. Historical frequencies from
> `stats/base_rates.json` come from a backtest and are not a forecast. Trade at your own risk.

What it does not do: it does not scan coin lists, run on a schedule, place orders, or ask for API keys.

## Requirements

- Python 3 (standard library only, no packages to install)
- Claude Code
- Internet access to `api.bybit.com` (public market data, no key)

## Install

1. Copy the scripts and stats into your data folder (default `~/trade-ai-analyst`, or set `TRADE_ANALYST_HOME`):

   ```bash
   mkdir -p ~/trade-ai-analyst/scripts ~/trade-ai-analyst/stats
   cp analyst.py evaluate.py fetch_candles.py journal.py i18n.py ~/trade-ai-analyst/scripts/
   cp stats/base_rates.json ~/trade-ai-analyst/stats/
   ```

2. Copy the command to your personal Claude Code commands:

   ```bash
   mkdir -p ~/.claude/commands
   cp commands/analyst.md ~/.claude/commands/analyst.md
   ```

   It runs `python "$HOME/trade-ai-analyst/scripts/analyst.py" $ARGUMENTS`.
   For Russian instructions and output, copy `commands/analyst.ru.md` instead, under the same name:

   ```bash
   cp commands/analyst.ru.md ~/.claude/commands/analyst.md
   ```

Alternatively, open this folder in Claude Code: the project-level command `.claude/commands/analyst.md` runs `python analyst.py` from the repo root
(`.claude/commands/analyst.ru.md` is the Russian variant, available there as `/analyst.ru`).

## Output language

Output is in English by default. Russian is available with `--lang ru` or the `ANALYST_LANG` environment variable.
Priority: `--lang`, then `ANALYST_LANG`, then `en`.

```bash
python analyst.py BTCUSDT long --lang ru
ANALYST_LANG=ru python analyst.py BTCUSDT long
ANALYST_LANG=ru python analyst.py BTCUSDT long --lang en   # --lang wins: English
```

In Claude Code: `/analyst BTCUSDT long --lang ru`. The same option works for `evaluate.py`, `fetch_candles.py`, `journal.py`,
`build_plugin.py` and the scripts in `research/`. All translations are in `i18n.py`.
Stored values stay as they are: journal fields (for example `--kind продолжение|коррекция`) and the keys in `stats/base_rates.json`.

## Usage

```
/analyst TICKER long|short [entry] [stop] [tp1 tp2 ...]
```

Example:

```
/analyst BTCUSDT long 100000 98000 104000
```

Without Claude Code:

```bash
python analyst.py BTCUSDT long 100000 98000 104000
```

Without a side, both sides are shown, one line each.

## Files

| Path | Purpose |
|---|---|
| `analyst.py` | Entry point: parses the trade, prints the rules check |
| `evaluate.py` | Strategy conditions and statuses |
| `fetch_candles.py` | Bybit public candles, levels, stop/target/R:R |
| `journal.py` | Optional trade journal (`add`, `close`, `cancel`, `stats`); writes only when you ask |
| `i18n.py` | Output language (`--lang`, `ANALYST_LANG`) and the English translations |
| `commands/analyst.md`, `commands/analyst.ru.md` | `/analyst` command for `~/.claude/commands/` (English / Russian) |
| `journal/README.md` | Journal schema (Russian) |
| `journal/journal.example.jsonl` | Fictional journal example |
| `stats/base_rates.json` | Historical frequencies from the backtest |
| `research/` | Backtest scripts that produced `base_rates.json` |
| `build_plugin.py` | Builds a local Claude Code plugin into `plugin/`. Needs a rules folder `.claude/skills/trading-analyst/` that is **not included**; create your own before running it |
| `test_*.py` | Tests, run as plain scripts, e.g. `python test_journal.py` |

The journal lives in `$TRADE_ANALYST_HOME/journal/journal.jsonl` (default `~/trade-ai-analyst`), outside the repo, and is ignored by git.
Settings: `TRADE_ANALYST_HOME` (see `.env.example`) and `ANALYST_LANG`.

## Customising the rules

Strategy conditions and thresholds are defined in `evaluate.py`. Change them there and re-run the tests.

## License

This project is licensed under the MIT License, see [LICENSE](LICENSE).
