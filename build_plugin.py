"""Сборка плагина Claude Code trade-ai-analyst из файлов проекта (единственный источник — корень проекта).
Запуск: python build_plugin.py
Создаёт plugin/ — локальный маркетплейс trade-ai-analyst-local с плагином trade-ai-analyst:
  plugin/.claude-plugin/marketplace.json
  plugin/trade-ai-analyst/.claude-plugin/plugin.json, commands/analyst.md, skills/trading-analyst/, scripts/, stats/
и копирует stats/base_rates.json и scripts/ в постоянную папку пользователя (TRADE_ANALYST_HOME, по умолчанию
~/trade-ai-analyst/stats/ и ~/trade-ai-analyst/scripts/ — для личной команды /analyst).
Журнал не трогает. Плагин из локального маркетплейса загружается из этой папки: после сборки — /reload-plugins."""
import json, shutil
from pathlib import Path

import i18n
import journal as jr

ROOT = Path(__file__).resolve().parent
MARKET = ROOT / "plugin"
PLUGIN = MARKET / "trade-ai-analyst"
VERSION = "1.0.0"
SCRIPTS = ("analyst.py", "evaluate.py", "fetch_candles.py", "journal.py", "i18n.py")
README = """# trade-ai-analyst
Личный плагин Claude Code: аналитик одной сделки по правилам пользователя, по запросу, без сканирования списка монет.
Команда: `/trade-ai-analyst:analyst ТИКЕР long|short [вход] [стоп] [тейк1 тейк2 ...]`. Данные — публичный API Bybit
(перпетуал, иначе спот), ключи не нужны. Скрипты на стандартной библиотеке Python: scripts/analyst.py, evaluate.py,
fetch_candles.py, journal.py. Правила и глоссарий: skills/trading-analyst/.
Журнал и статистика лежат вне плагина: TRADE_ANALYST_HOME (по умолчанию ~/trade-ai-analyst): journal/journal.jsonl,
stats/base_rates.json. Журнал пишется только по просьбе пользователя. Вывод — проверка правил, не рекомендация.
Собирается из проекта командой `python build_plugin.py`; файлы в этой папке руками не править.

"""
INSTALL = """Установка и переустановка (в том числе после переименования папки проекта):
1. Переименовать папку проекта (если нужно).
2. В новой папке: `python build_plugin.py` (собирает plugin/, копирует scripts/ и stats/ в ~/trade-ai-analyst).
3. Добавить маркетплейс по новому пути: `claude plugin marketplace add "<папка проекта>\\plugin"`
   (если старый маркетплейс trade-ai-analyst-local уже добавлен — удалить его в /plugin → Marketplaces и добавить заново),
   затем `claude plugin install trade-ai-analyst@trade-ai-analyst-local --scope user` и /reload-plugins.
Короткая личная команда /analyst (~/.claude/commands/analyst.md) запускает ~/trade-ai-analyst/scripts/analyst.py
и от пути проекта не зависит; после правок кода — снова `python build_plugin.py`.
"""
README += INSTALL
COMMAND = """---
description: Разбор одной сделки по правилам пользователя (trade-ai-analyst)
argument-hint: ТИКЕР long|short [вход] [стоп] [тейк1 тейк2 ...]
allowed-tools: Bash(python:*), Bash(py:*), PowerShell(python:*), PowerShell(py:*)
---
Запусти скрипт плагина: `python "${CLAUDE_PLUGIN_ROOT}/scripts/analyst.py" $ARGUMENTS`
(если `python` не найден — `py "${CLAUDE_PLUGIN_ROOT}/scripts/analyst.py" $ARGUMENTS`).
1. Покажи вывод скрипта как есть: статусы, уровни, R:R и частоты считает код, не пересчитывай и не дополняй цифрами.
2. Если в выводе есть «К проверке аналитиком», можешь коротко добавить inducement и strong high/low только с пометкой
   «вывод аналитика, менее надёжно». Вход от inducement без этой пометки не предлагай.
3. Не пиши «процент успеха сделки». Не открывай сделки и не пиши в журнал без явной просьбы
   (журнал: `python "${CLAUDE_PLUGIN_ROOT}/scripts/journal.py" stats|add|close|cancel`).
4. Правила и глоссарий: ${CLAUDE_PLUGIN_ROOT}/skills/trading-analyst/. Ответ не длиннее 2500 символов.
"""


def main():
    if PLUGIN.exists():
        shutil.rmtree(PLUGIN)
    (PLUGIN / ".claude-plugin").mkdir(parents=True)
    (MARKET / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (PLUGIN / ".claude-plugin" / "plugin.json").write_text(json.dumps(dict(
        name="trade-ai-analyst", version=VERSION, author=dict(name="trade-ai-analyst"),
        description=i18n.block("plugin_description",
                               "Аналитик сделок по правилам пользователя: /trade-ai-analyst:analyst ТИКЕР long|short [вход] [стоп] [тейки]")),
        ensure_ascii=False, indent=2), encoding="utf-8")
    (MARKET / ".claude-plugin" / "marketplace.json").write_text(json.dumps(dict(
        name="trade-ai-analyst-local", owner=dict(name="trade-ai-analyst"),
        description=i18n.block("market_description", "Личный локальный маркетплейс: плагин trade-ai-analyst, без публикации"),
        plugins=[dict(name="trade-ai-analyst", source="./trade-ai-analyst",
                      description=i18n.block("market_plugin_description", "Аналитик сделок по правилам пользователя"))]),
        ensure_ascii=False, indent=2), encoding="utf-8")
    (PLUGIN / "README.md").write_text(i18n.block("plugin_readme", README), encoding="utf-8")
    (PLUGIN / "commands").mkdir()
    (PLUGIN / "commands" / "analyst.md").write_text(i18n.block("plugin_command", COMMAND), encoding="utf-8")
    shutil.copytree(ROOT / ".claude" / "skills" / "trading-analyst", PLUGIN / "skills" / "trading-analyst",
                    ignore=shutil.ignore_patterns("_template.md"))
    (PLUGIN / "scripts").mkdir()
    for s in SCRIPTS:
        shutil.copy2(ROOT / s, PLUGIN / "scripts" / s)
    (PLUGIN / "stats").mkdir()
    shutil.copy2(ROOT / "stats" / "base_rates.json", PLUGIN / "stats" / "base_rates.json")
    user_stats = jr.data_dir() / "stats"
    user_stats.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "stats" / "base_rates.json", user_stats / "base_rates.json")
    user_scripts = jr.data_dir() / "scripts"           # для личной команды /analyst; источник — проект
    user_scripts.mkdir(parents=True, exist_ok=True)
    for s in SCRIPTS:
        shutil.copy2(ROOT / s, user_scripts / s)
    print(f"скрипты → {user_scripts}")
    files = sorted(p.relative_to(MARKET).as_posix() for p in MARKET.rglob("*") if p.is_file())
    print(f"плагин собран: {PLUGIN} ({len(files)} файлов); base_rates → {user_stats}")
    for f in files:
        print("  " + f)


if __name__ == "__main__":
    i18n.setup()
    main()
