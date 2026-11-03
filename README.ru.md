# trade-ai-analyst

Команда Claude Code `/analyst` проверяет одну крипто-сделку по набору правил стратегий.
Скрипт берёт закрытые свечи из публичного API Bybit (перпетуал, иначе спот), проверяет условия каждой стратегии в коде
и выводит статусы, уровни и R:R. Модель только передаёт вывод скрипта и не пересчитывает цифры.

> **Не финансовый совет.** Вывод — проверка правил, а не рекомендация. Частоты из `stats/base_rates.json`
> получены бэктестом и не являются прогнозом. Решения о сделках вы принимаете сами и на свой риск.

Чего не делает: не сканирует списки монет, не запускается по расписанию, не открывает сделки, не просит API-ключи.

## Требования

- Python 3 (только стандартная библиотека, ставить пакеты не нужно)
- Claude Code
- Доступ к `api.bybit.com` (публичные данные, без ключа)

## Установка

1. Скопируйте скрипты и статистику в папку данных (по умолчанию `~/trade-ai-analyst`, либо задайте `TRADE_ANALYST_HOME`):

   ```bash
   mkdir -p ~/trade-ai-analyst/scripts ~/trade-ai-analyst/stats
   cp analyst.py evaluate.py fetch_candles.py journal.py ~/trade-ai-analyst/scripts/
   cp stats/base_rates.json ~/trade-ai-analyst/stats/
   ```

2. Положите команду в личные команды Claude Code:

   ```bash
   mkdir -p ~/.claude/commands
   cp commands/analyst.md ~/.claude/commands/analyst.md
   ```

   Команда запускает `python "$HOME/trade-ai-analyst/scripts/analyst.py" $ARGUMENTS`.

Другой вариант: откройте эту папку в Claude Code. Проектная команда `.claude/commands/analyst.md` запускает `python analyst.py` из корня репозитория.

## Использование

```
/analyst ТИКЕР long|short [вход] [стоп] [тейк1 тейк2 ...]
```

Пример:

```
/analyst BTCUSDT long 100000 98000 104000
```

Без Claude Code:

```bash
python analyst.py BTCUSDT long 100000 98000 104000
```

Без стороны выводятся обе стороны, по одной строке.

## Файлы

| Путь | Назначение |
|---|---|
| `analyst.py` | Точка входа: разбор сделки и проверка правил |
| `evaluate.py` | Условия стратегий и статусы |
| `fetch_candles.py` | Свечи Bybit, уровни, стоп, цель, R:R |
| `journal.py` | Необязательный журнал (`add`, `close`, `cancel`, `stats`), пишет только по просьбе |
| `journal/README.md` | Схема журнала |
| `journal/journal.example.jsonl` | Пример журнала с вымышленными данными |
| `stats/base_rates.json` | Частоты по истории из бэктеста |
| `research/` | Скрипты бэктеста, из которых получен `base_rates.json` |
| `build_plugin.py` | Сборка локального плагина Claude Code в `plugin/`. Нужна папка правил `.claude/skills/trading-analyst/`, её **нет в репозитории**; создайте свою перед запуском |
| `test_*.py` | Тесты, запускаются как обычные скрипты, например `python test_journal.py` |

Журнал хранится в `$TRADE_ANALYST_HOME/journal/journal.jsonl` (по умолчанию `~/trade-ai-analyst`), вне репозитория, и игнорируется git.
Единственная настройка описана в `.env.example`.

## Настройка правил

Условия и пороги стратегий заданы в `evaluate.py`. Меняйте их там и прогоняйте тесты.

## Лицензия

Проект распространяется по лицензии MIT, см. [LICENSE](LICENSE).
