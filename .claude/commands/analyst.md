---
description: Check one trade against the user's rules (trade-ai-analyst)
argument-hint: TICKER long|short [entry] [stop] [tp1 tp2 ...]
allowed-tools: Bash(python analyst.py:*), Bash(py analyst.py:*), PowerShell(python analyst.py:*), PowerShell(py analyst.py:*)
---
Run from the project root: `python analyst.py $ARGUMENTS` (if `python` is not found, use `py analyst.py $ARGUMENTS`).
1. Show the script output as is: statuses, levels, R:R and frequencies are computed by the code; do not recompute them or add numbers.
2. If the output contains "For analyst review", you may briefly add inducement and strong high/low, only marked
   "analyst's judgement, less reliable". Do not suggest an entry from inducement without that mark.
3. Do not write a "trade success percentage". Do not open trades or write to the journal unless explicitly asked (journal: `python journal.py`).
4. Do not read extracted/, runs/, data/cache/. Keep the answer under 2500 characters.
