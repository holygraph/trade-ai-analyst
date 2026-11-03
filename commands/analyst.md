---
description: Разбор одной сделки по правилам пользователя (trade-ai-analyst)
argument-hint: ТИКЕР long|short [вход] [стоп] [тейк1 тейк2 ...]
allowed-tools: Bash(python:*), Bash(py:*), PowerShell(python:*), PowerShell(py:*)
---
Запусти: `python "$HOME/trade-ai-analyst/scripts/analyst.py" $ARGUMENTS`
($HOME раскрывается и в bash, и в PowerShell; если `python` не найден — `py "$HOME/trade-ai-analyst/scripts/analyst.py" $ARGUMENTS`).
Скрипты кладёт туда `python build_plugin.py` из проекта trade-ai-analyst; если файла нет — скажи пользователю запустить сборку.
1. Покажи вывод скрипта как есть: статусы, уровни, R:R и частоты считает код, не пересчитывай и не дополняй цифрами.
2. Если в выводе есть «К проверке аналитиком», можешь коротко добавить inducement и strong high/low только с пометкой
   «вывод аналитика, менее надёжно». Вход от inducement без этой пометки не предлагай.
3. Не пиши «процент успеха сделки». Не открывай сделки и не пиши в журнал без явной просьбы
   (журнал: `python "$HOME/trade-ai-analyst/scripts/journal.py" stats|add|close|cancel`).
4. Ответ не длиннее 2500 символов.
