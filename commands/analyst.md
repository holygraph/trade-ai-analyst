---
description: Check one trade against the user's rules (trade-ai-analyst)
argument-hint: TICKER long|short [entry] [stop] [tp1 tp2 ...]
allowed-tools: Bash(python:*), Bash(py:*), PowerShell(python:*), PowerShell(py:*)
---
Run: `python "$HOME/trade-ai-analyst/scripts/analyst.py" $ARGUMENTS`
($HOME expands in both bash and PowerShell; if `python` is not found, use `py "$HOME/trade-ai-analyst/scripts/analyst.py" $ARGUMENTS`).
The scripts are placed there by `python build_plugin.py` from the trade-ai-analyst project; if the file is missing, tell the user to run the build.
1. Show the script output as is: statuses, levels, R:R and frequencies are computed by the code; do not recompute them or add numbers.
2. If the output contains "For analyst review", you may briefly add inducement and strong high/low, only marked
   "analyst's judgement, less reliable". Do not suggest an entry from inducement without that mark.
3. Do not write a "trade success percentage". Do not open trades or write to the journal unless explicitly asked
   (journal: `python "$HOME/trade-ai-analyst/scripts/journal.py" stats|add|close|cancel`).
4. Keep the answer under 2500 characters.
