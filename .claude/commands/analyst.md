---
description: Разбор одной сделки по правилам пользователя (trade-ai-analyst)
argument-hint: ТИКЕР long|short [вход] [стоп] [тейк1 тейк2 ...]
allowed-tools: Bash(python analyst.py:*), Bash(py analyst.py:*), PowerShell(python analyst.py:*), PowerShell(py analyst.py:*)
---
Запусти из корня проекта: `python analyst.py $ARGUMENTS` (если `python` не найден — `py analyst.py $ARGUMENTS`).
1. Покажи вывод скрипта как есть: статусы, уровни, R:R и частоты считает код, не пересчитывай и не дополняй цифрами.
2. Если в выводе есть «К проверке аналитиком», можешь коротко добавить inducement и strong high/low только с пометкой
   «вывод аналитика, менее надёжно». Вход от inducement без этой пометки не предлагай.
3. Не пиши «процент успеха сделки». Не открывай сделки и не пиши в журнал без явной просьбы (журнал: `python journal.py`).
4. Не читай extracted/, runs/, data/cache/. Ответ не длиннее 2500 символов.
