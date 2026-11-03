"""Язык вывода trade-ai-analyst: en (по умолчанию) или ru.
Выбор: параметр --lang en|ru, затем переменная окружения ANALYST_LANG, затем en.
Расчёты и внутренние строки остаются на русском (это данные: статусы, ключи stats/base_rates.json, поля журнала);
переводится только то, что видит пользователь: строки вывода — по таблице фраз PHRASES, целые тексты — BLOCKS.
При ru вывод не меняется ни на символ.
"""
import os, re, sys

LANGS = ("en", "ru")
DEFAULT = "en"
LANG = None         # задаёт setup() при запуске скрипта; до этого (импорт, тесты) текст не переводится

# Правила с подстановкой: применяются до таблицы фраз
RULES = [
    (r"(\d) с(?![А-Яа-яЁё])", r"\1 s"),                                   # секунды: «12 с»
    (r"(?<![А-Яа-яЁё])на (?=[\d.,]+\s?%)", "by "),                         # «на 0.53%»
    (r"открытой записи (\S+) нет", r"no open entry for \1"),
    (r"; по (\S+) уже есть позиция — одна позиция на идею", r"; \1 already has a position — one position per idea"),
    (r"R до (\S+) после", r"R gross \1, net"),
]

PHRASES = {
    # analyst.py
    "синтаксис": "usage", "тикер": "ticker", "тейк": "TP", "тейки": "TPs", "т": "TP",
    "вход": "entry", "входа": "entry", "стоп": "stop", "при стопе": "with the stop", "зоны": "zone", "отмены": "invalidation",
    "уже есть позиция — одна позиция на идею": "already has a position — one position per idea",
    "открытой записи": "no open entry for", "итог: по правилам стоп": "summary: rule stop",
    "проходит": "pass", "ждать": "wait", "нет": "no", "не проверено": "not checked", "вход да": "enter",
    "лонг": "long", "шорт": "short",
    "в пределах случайного": "within random", "выше случайного": "above random", "ниже случайного": "below random",
    "случайно": "random", "мало данных": "low sample", "нет данных": "no data",
    "ждём": "waiting for",
    "ни одна стратегия не подходит для этой стороны": "no strategy fits this side",
    "цели по правилам нет": "no rule target", "на цели по правилам": "at the rule target",
    "ближе цели по правилам": "closer than the rule target", "дальше цели по правилам": "beyond the rule target",
    "цели по правилам": "the rule target",
    "ближе правила": "closer than the rule stop", "дальше правила": "farther than the rule stop",
    "ближе ориентира": "closer than the reference stop", "дальше ориентира": "farther than the reference stop",
    "при стопе по правилам": "with the rule stop", "при стопе ориентира": "with the reference stop",
    "правила": "rules", "ориентира": "reference", "ориентир": "reference",
    "счёт": "account", "открыто": "open", "риск": "risk", "лимиты": "limits",
    "рынок": "market", "цена": "price", "данные": "data", "стратегии": "strategies",
    "к проверке аналитиком": "for analyst review", "вывод аналитика, менее надёжно": "analyst's judgement, less reliable",
    "проверка правил, не рекомендация": "rules check, not financial advice",
    "проверь экономический календарь": "check the economic calendar",
    "подробно": "details",
    "продолжение": "continuation", "коррекция": "pullback",
    "уровни должны быть больше нуля": "levels must be greater than zero",
    "строки нет": "no row", "все условия выполнены": "all conditions met",
    "snr на m15 после теста": "SNR on M15 after the test of",
    "зона подтверждения внутри зоны a": "confirmation zone inside zone A",
    "ваш вход вне зоны входа": "your entry is outside the entry zone",
    "ваш вход внутри зоны входа": "your entry is inside the entry zone",
    "цена в зоне входа, рынок допустим": "price is in the entry zone, market entry allowed",
    "ориентир по глоссарию": "reference by glossary definitions", "стратегия не проходит": "strategy does not pass",
    "ориентир по правилам": "reference per rules",
    "сравнение с": "compared with", "по правилам": "per rules", "вердикт по правилам": "rules verdict",
    "историческая частота похожих сетапов": "historical frequency of similar setups",
    "бэктест 4 года": "4-year backtest", "не прогноз": "not a forecast", "сделок": "trades",
    "таблица построена для стопов по правилам, оценка ориентировочная": "table built for rule stops, estimate is approximate",
    "в ту же сторону открыто": "open in the same direction", "крипта коррелирует с btc": "crypto correlates with BTC",
    "метод": "method", "лимит": "limit", "в зоне": "in zone", "отмена": "invalidation", "цель": "target",
    "после комиссий": "after fees", "флаги": "flags", "флаги: нет": "flags: none",
    "bybit не отдал свечей m15 — разбор невозможен": "Bybit returned no M15 candles — analysis is not possible",
    "не похож на тикер bybit (например btcusdt)": "does not look like a Bybit ticker (e.g. BTCUSDT)",
    "с уровнями нужна сторона long или short": "levels require side long or short",
    "не по ходу сделки": "on the wrong side of the trade",
    "для long ниже входа": "for long it must be below entry", "для short выше входа": "for short it must be above entry",
    "для long выше входа": "for long it must be above entry", "для short ниже входа": "for short it must be below entry",
    "по тренду только": "trend allows only",
    "уровень не найден": "level not found", "зона": "zone", "ближе всех": "closest", "не хватает": "missing",
    "ближе": "closer", "дальше": "further", "внутри зоны": "inside zone", "зоны нет": "no zone",
    "до уровня отмены": "before the invalidation level", "за уровнем отмены": "beyond the invalidation level",
    "уровень отмены": "invalidation level", "уровня отмены": "invalidation level",
    "снятие": "a raid of this stop", "отменяет идею": "invalidates the idea", "не отменяет идею": "does not invalidate the idea",
    "внутри зоны входа": "inside the entry zone", "вне зоны входа": "outside the entry zone",
    "на пути fta нет": "no FTA on the path", "на пути fta": "FTA on the path",
    "совпадает": "matches", "журнал": "journal", "похожих": "similar", "побед": "wins", "средний r": "average R",
    "приоритет": "priority", "частота по истории": "historical frequency", "не найден": "not found", "ступень": "step",
    "справочно, вне правил": "for reference, outside the rules", "справочно": "for reference",
    "ваш план": "your plan", "параметров по правилам нет — сравнить не с чем": "no rule parameters — nothing to compare with",
    "тикер не найден на bybit": "ticker not found on Bybit", "нет данных от bybit": "no data from Bybit",
    "проверь интернет и повтори; без данных разбор не делается": "check your internet connection and retry; no analysis without data",
    "все стратегии": "all strategies", "все": "all", "план": "plan", "ниже": "below", "выше": "above",
    "при вашем стопе": "with your stop", "перенести на": "move to", "итог": "summary", "ваш стоп": "your stop",
    "поправка": "adjustment", "рекомендации переносить стоп нет": "no recommendation to move the stop",
    "выше уровней таблицы": "above table levels", "до 3r": "up to 3R",
    "параметров нет": "no parameters", "нет плана": "no plan", "локальный": "local", "не число": "not a number",
    "закрытие": "close", "poi на": "POI on", "фрактал": "fractal", "ошибка": "error", "сторона": "side",
    "нужно long или short": "must be long or short",
    # evaluate.py
    "цена не за зоной входа": "price not beyond the entry zone", "к проверке": "for review", "не применимо": "not applicable",
    "не применим": "not applicable", "готов": "ready", "нет-почти": "near-miss", "почти": "almost",
    "лимиты счёта": "account limits", "вход до уровня отмены": "entry before the invalidation level",
    "ход не отработан": "move not played out", "ход отработан": "move played out", "отработан": "played out",
    "тренд": "trend", "запрет": "block", "цена за зоной": "price beyond the zone", "цена за зоной входа": "price beyond the entry zone",
    "зона за уровнем отмены": "zone beyond the invalidation level", "мало истории": "short history", "нет зоны": "no zone",
    "нет локального фрактала": "no local fractal", "d не против w": "D not against W", "тф входа": "entry TF", "тф": "TF",
    "зона a не протестирована": "zone A not tested", "сформирована": "formed", "не тестировалась": "not tested",
    "цена в зоне": "price in zone", "в зоне a": "in zone A", "ожидание": "waiting",
    "журнал неверного формата": "journal has an invalid format", "сделок в журнале": "trades in journal",
    "нет слома структуры": "no structure break", "направление": "direction", "положение": "position",
    "нет данных тф входа": "no entry TF data", "дальняя граница": "far edge", "дальняя граница зоны": "far edge of zone",
    "вход за уровнем отмены": "entry beyond the invalidation level", "ближе к цели": "closer to target",
    "чем ко входу": "than to entry", "есть": "yes", "зона a за уровнем отмены": "zone A beyond the invalidation level",
    "рейд, вход по цене": "raid, entry at price", "свечи": "candles", "тест": "test", "против": "against",
    "меньше": "fewer than", "свечей на w, d или h4, либо нет слома w": "candles on W, D or H4, or no W break",
    "свечей на d или h4, либо нет слома d": "candles on D or H4, or no D break",
    "свечей на d, h4 или m15": "candles on D, H4 or M15",
    "зона входа": "entry zone", "зона a": "zone A", "сокращено": "shortened", "карточек": "cards", "из": "of",
    "открытый риск": "open risk", "убыток за день": "loss for the day", "убыток за неделю": "loss for the week",
    "диапазон": "range", "не сформирован": "not formed", "цена вне диапазона": "price outside the range", "уровень": "level",
    "за дальней границей": "beyond the far edge", "за дальней границей зоны": "beyond the far edge of zone",
    "локальный фрактал": "local fractal", "буфер": "buffer", "вход отпал": "entry dropped", "нет цели": "no target",
    "нет точки a": "no point A", "точка a на": "point A on",
    "цена не в poi и нет свежего рейда": "price not in POI and no fresh raid", "нет зоны и свежего рейда": "no zone and no fresh raid",
    "короткая история": "short history", "стоп ближе": "stop closer than", "протестирована": "tested",
    "свип перед snr": "sweep before SNR", "мало фракталов или нет слома на d": "few fractals or no break on D",
    "до входа": "to entry", "положение входа": "entry position", "режим": "mode",
    "только свечи, закрытые к этому моменту": "only candles closed by that time", "вне лимита": "over the limit",
    "нет стопа": "no stop", "рейд": "raid", "верх диапазона": "range top", "низ диапазона": "range bottom",
    "нет poi": "no POI", "порядок событий не определён": "event order undetermined", "порядок не определён": "order undetermined",
    "тест старше истории m15": "test older than M15 history", "нет snr": "no SNR",
    "был": "yes", "не был": "no", "свипа не было": "no sweep", "вариант бэктеста": "backtest variant",
    "открытие месяца": "month open", "зона подтверждения": "confirmation zone", "свип": "sweep",
    "нет истории на дату": "no history at that date", "листинг позже": "listed later",
    "нет фрактала против тренда": "no fractal against the trend", "подтверждение": "confirmation",
    "вход выше диапазона": "entry above the range", "вход ниже диапазона": "entry below the range", "реакция": "reaction",
    "младшего тф нет": "no lower TF", "одно движение": "one move", "сигналов": "signals",
    "стратегия не найдена": "strategy not found", "нет истории": "no history", "пропущена": "skipped",
    "ошибка данных": "data error", "прошита": "pierced", "ждём зону подтверждения в зоне a": "waiting for a confirmation zone in zone A",
    "нет свечей после теста": "no candles after the test", "тренд не определён": "trend undetermined",
    "фаза": "phase", "слом": "break",
    # fetch_candles.py
    "боковик": "range-bound", "вне диапазона": "outside the range", "начало": "start", "старт": "start", "импульс": "impulse",
    "от_уровня": "from_level", "открытие_месяца": "month_open", "закрытых свечей": "closed candles",
    "вяло": "weak", "нейтрально": "neutral", "тело_последней": "last_body", "тренд_по_закрытиям": "trend_by_closes",
    "неснятые_хаи_выше": "unraided_highs_above", "неснятые_лоу_ниже": "unraided_lows_below", "ресвип": "re-sweep",
    "отпал": "dropped", "нет позиции": "no position", "да": "yes", "дата": "date", "даты": "dates",
    "ключевой_фрактал": "key_fractal", "цена_в": "price_in", "зона прошита": "zone pierced", "входа нет": "no entry",
    "использован вход": "entry used", "не проходит": "does not pass", "внутри": "inside", "последний": "last",
    "_для_стопа": "_for_stop", "стоп не за входом": "stop not beyond entry",
    # journal.py
    "сторона: long или short, а не": "side must be long or short, not",
    "стоп должен быть за входом: ниже для long, выше для short": "stop must be beyond entry: below for long, above for short",
    "записей": "entries", "закрыто": "closed", "закрытых сделок нет: статистики нет": "no closed trades: no statistics",
    "все закрытые": "all closed", "по стратегии": "by strategy", "по тегам": "by tags", "по флагам": "by flags",
    "правила × решение": "rules × decision",
    "журнал trade-ai-analyst (только по просьбе пользователя)": "trade-ai-analyst journal (only on user request)",
    "записано": "recorded", "результат": "result", "счёта": "of account", "отменено": "cancelled",
    "взял": "took", "не брал": "skipped", "ди95": "CI95", "ди": "CI",
    # build_plugin.py
    "скрипты": "scripts", "плагин собран": "plugin built", "файлов": "files",
    # research/
    "не исполнен": "not filled", "отменён": "cancelled", "открыт": "open", "средний r до": "average R before fees",
    "после": "after", "новая": "new", "старая": "old", "безубыточный % побед": "breakeven win %", "фактический": "actual",
    "стоп близко": "stop close", "баланс": "balance", "макс. просадка": "max drawdown", "открыто на конец": "open at end",
    "серия убытков": "losing streak", "помесячно": "monthly", "пропущено": "skipped",
    "спорных свечей m15: разобрано по m1": "ambiguous M15 candles: resolved by M1", "m1 нет (консервативно)": "no M1 (conservative)",
    "частота статусов по стратегиям (доля от пар монета-дата)": "status frequency by strategy (share of coin-date pairs)",
    "симуляция того же входа": "simulating the same entry", "диагностика": "diagnostics", "рабочий период": "working period",
    "mfe считается по свечам после свечи входа (минутам после входа, если разобрано по m1); свеча выхода по стопу не входит":
        "MFE is computed on candles after the entry candle (minutes after entry if resolved by M1); the stop exit candle is excluded",
    "не вывод о прибыльности": "not a conclusion about profitability", "проверка правил на истории": "a rules check on history",
    "журнал не меняется": "the journal is not changed", "funding не учитывается": "funding is not included",
    "правила в файлах не менялись": "rule files were not changed",
    "портфель": "portfolio", "депозит": "deposit", "рабочий": "working", "контрольный": "control",
    "комиссии: новая модель": "fees: new model", "комиссии как в этапе 8": "fees as in stage 8", "как в этапе 8": "as in stage 8",
    "лимит и тейк maker": "limit entry and TP maker", "рынок и стоп taker": "market entry and stop taker",
    "проскальзывание стопа": "stop slippage", "везде": "everywhere",
    "на каждый прогон": "per run", "варианты": "variants", "периоды": "periods", "после дедупликации": "after deduplication",
    "при риске": "at risk", "от баланса": "of balance", "вариант": "variant", "период": "period", "сделок закрыто": "trades closed",
    "ступень метода 2": "method 2 step", "квартал": "quarter", "стратегия": "strategy", "дат": "dates", "монет": "coins",
    "пар монета-дата": "coin-date pairs", "без истории": "without history", "строк стратегий": "strategy rows",
    "стратегий": "strategies", "сделки": "trades", "закрытые": "closed", "по невыполненному условию": "by unmet condition",
    "mfe закрытых": "MFE of closed", "макс. ход к цели после входа до выхода, в r": "max move toward target after entry until exit, in R",
    "из них стопов": "of them stops", "случайных сделок": "random trades", "для сверки с tradingview": "for cross-check with TradingView",
    "бэктест правил": "rules backtest", "дней": "days", "время": "time", "загрузка": "loading", "расчёт": "compute",
    "фиксированно": "fixed", "этап": "stage", "та же идея": "same idea", "флаг": "flag", "нет r:r": "no R:R",
    "упали только по r:r": "failed only on R:R", "сигналы": "signals", "с зоной входа": "with an entry zone",
    "дожили до исполнения": "survived to fill", "исход": "outcome", "дошли до": "reached", "среди стопов": "among stops",
    "закрыт": "closed", "нет свободного слота": "no free slot", "строк": "rows", "лимит убытка дня": "daily loss limit",
    "лимит убытка недели": "weekly loss limit", "с r:r": "with R:R",
    "пн": "Mon", "вт": "Tue", "ср": "Wed", "чт": "Thu", "пт": "Fri", "сб": "Sat", "вс": "Sun",
    "открытие месяца не цель ближе": "month open is not a target closer than", "не ниже": "not below",
    "без choch на h4": "without CHoCH on H4",
    "вход только на дату, когда остальные условия выполнены": "entry only on the date when the other conditions are met",
    "вердикт": "verdict", "по правилу решений": "by the decision rule", "принят": "accepted", "каждый год": "every year",
    "объединённой выборки": "of the pooled sample",
    "средний r правил пересчитан той же быстрой симуляцией без m1, что и контрольные":
        "rules average R recomputed with the same fast simulation without M1 as the controls",
    "правило решений для срезов v2": "decision rule for V2 slices",
    "золото и форекс": "gold and forex", "форекс на bybit": "forex on Bybit", "не найдены": "not found", "золото": "gold",
    "токены на золото": "gold-backed crypto assets", "не спот-золото эталонного рынка": "not the reference market's spot gold",
    "ликвидность ниже основных монет": "liquidity below major coins",
    "диагностика v2 (без правил): по времени входа utc, дню недели входа, фазе btc на d на дату сигнала":
        "V2 diagnostics (outside the rules): by entry time UTC, entry weekday, BTC phase on D at the signal date",
    "позиции": "positions", "день": "day", "неделю": "week", "каждый прогон с": "each run starts with",
    "вверх": "up", "вниз": "down", "все 4 года": "all 4 years", "средний r по годам": "average R by year",
    "преимущество не доказано": "edge not proven", "ежедневно": "daily", "монеты": "coins",
    "даты без 50 закрытых свечей d пропускаются": "dates without 50 closed D candles are skipped",
    "контроль случайных входов": "random-entry control",
    "наборов: на каждую закрытую сделку одна контрольная в каждом наборе": "sets: one control trade per closed trade in each set",
    "по стратегии × типу": "by strategy × type", "ди с поправкой bonferroni на": "Bonferroni-corrected CI for",
    "сравнений": "comparisons", "час входа": "entry hour", "день недели входа": "entry weekday", "фаза btc d": "BTC D phase",
    "bootstrap по месяцам": "bootstrap by month", "повторов": "resamples", "месяцев": "months",
    "блоки — календарные месяцы из": "blocks are calendar months from", "результат за 12 месяцев": "12-month result",
    "медиана": "median", "вероятность убытка за год": "probability of a losing year", "вероятность месяца": "probability of a month",
    "вероятность просадки": "probability of drawdown", "за 12 месяцев": "within 12 months", "по факту месяцев": "actual months",
    "выполнено сравнений": "comparisons made",
    "правило решений для вариантов и срезов, контроль случайных входов, срезы с bonferroni, золото, диагностика":
        "decision rule for variants and slices, random-entry control, Bonferroni slices, gold, diagnostics",
    "результаты, не прошедшие правило решений, нельзя считать подтверждением; при таком числе сравнений часть «хороших» срезов ожидаема случайно":
        "results that did not pass the decision rule cannot be treated as confirmation; with this many comparisons some «good» slices are expected by chance",
    "год": "year", "пропущено дат без истории": "dates skipped without history", "правило решений": "decision rule",
    "средний r правил": "rules average R", "контрольные": "controls", "процентиль правил": "rules percentile",
    "ди bonferroni": "Bonferroni CI", "срез": "slice", "готово": "done",
    # служебные слова
    "и": "and", "или": "or", "с": "since", "по": "by", "на": "on", "до": "to", "за": "beyond", "в": "in", "к": "to",
    "от": "from", "не": "not", "для": "for", "при": "at", "без": "without", "чем": "than", "ко": "to",
}

BLOCKS = {
    "evaluate_usage": """Strategy statuses are computed in code. Rule definitions are in the code.
The main user entry point is analyst.py (/analyst); evaluate.py is for computation and debugging.
Usage:
  python evaluate.py TICKER [...]              one line per coin and a list of cards
  python evaluate.py --cards [ticker ...]      the same plus the cards themselves, at most 8 per run
  python evaluate.py --card TICKER "strategy"  one card, up to 2000 characters
  python evaluate.py --detail TICKER           conditions of all strategies for the coin
  python evaluate.py --why TICKER [strategy]   all fail and unknown by strategy (the coin line shows only the first one for NO)
  --asof "YYYY-MM-DD HH:MM" with any command: check at a date (UTC) on candles closed by that time;
                                              history from data/cache/ with top-up, a coin without history at the date is skipped
A run over a list writes a raw log to runs/ with all single fails, unfiltered.
Calculation functions receive the data and the time as arguments and never read "now":
the time is taken only in main().
""",
    "fetch_candles_usage": """Bybit candles (public API v5, no keys) and a short numeric summary of levels.
Usage:
  python fetch_candles.py --check TICKER [...]    check tickers on Bybit (linear, spot)
  python fetch_candles.py TICKER [...]            level summary per coin
  python fetch_candles.py --setup BTCUSDT D short LOW HIGH [STOP_TF]  stop, target and R:R for an entry zone
TFs in arguments: M W D 240 60 15. Dates in UTC, by candle open.
Only closed candles are counted.
""",
    "plugin_readme": """# trade-ai-analyst
Claude Code plugin: on-demand analysis of one trade against the user's rules, without scanning coin lists.
Command: `/trade-ai-analyst:analyst TICKER long|short [entry] [stop] [tp1 tp2 ...]`. Data: public Bybit API
(perpetual, otherwise spot), no keys needed. Scripts use the Python standard library only: scripts/analyst.py, evaluate.py,
fetch_candles.py, journal.py, i18n.py. Rules and glossary: skills/trading-analyst/.
The journal and stats live outside the plugin: TRADE_ANALYST_HOME (default ~/trade-ai-analyst): journal/journal.jsonl,
stats/base_rates.json. The journal is written only on the user's request. Output is a rules check, not financial advice.
Built from the project with `python build_plugin.py`; do not edit files in this folder by hand.
Output language: --lang en|ru or ANALYST_LANG (default en).

Install and reinstall (including after renaming the project folder):
1. Rename the project folder (if needed).
2. In the new folder: `python build_plugin.py` (builds plugin/, copies scripts/ and stats/ to ~/trade-ai-analyst).
3. Add the marketplace at the new path: `claude plugin marketplace add "<project folder>\\plugin"`
   (if the old trade-ai-analyst-local marketplace is already added, remove it in /plugin → Marketplaces and add it again),
   then `claude plugin install trade-ai-analyst@trade-ai-analyst-local --scope user` and /reload-plugins.
The short personal command /analyst (~/.claude/commands/analyst.md) runs ~/trade-ai-analyst/scripts/analyst.py
and does not depend on the project path; after code changes run `python build_plugin.py` again.
""",
    "plugin_command": """---
description: Check one trade against the user's rules (trade-ai-analyst)
argument-hint: TICKER long|short [entry] [stop] [tp1 tp2 ...]
allowed-tools: Bash(python:*), Bash(py:*), PowerShell(python:*), PowerShell(py:*)
---
Run the plugin script: `python "${CLAUDE_PLUGIN_ROOT}/scripts/analyst.py" $ARGUMENTS`
(if `python` is not found, use `py "${CLAUDE_PLUGIN_ROOT}/scripts/analyst.py" $ARGUMENTS`).
1. Show the script output as is: statuses, levels, R:R and frequencies are computed by the code; do not recompute them or add numbers.
2. If the output contains "For analyst review", you may briefly add inducement and strong high/low, only marked
   "analyst's judgement, less reliable". Do not suggest an entry from inducement without that mark.
3. Do not write a "trade success percentage". Do not open trades or write to the journal unless explicitly asked
   (journal: `python "${CLAUDE_PLUGIN_ROOT}/scripts/journal.py" stats|add|close|cancel`).
4. Rules and glossary: ${CLAUDE_PLUGIN_ROOT}/skills/trading-analyst/. Keep the answer under 2500 characters.
""",
    "plugin_description": "Trade analyst by the user's rules: /trade-ai-analyst:analyst TICKER long|short [entry] [stop] [TPs]",
    "market_description": "Personal local marketplace: trade-ai-analyst plugin, not published",
    "market_plugin_description": "Trade analyst by the user's rules",
}

_TABLE = {k.lower(): v for k, v in PHRASES.items()}
_PATTERN = re.compile(r"(?<![А-Яа-яЁё])(" + "|".join(re.escape(k) for k in sorted(_TABLE, key=len, reverse=True))
                      + r")(?![А-Яа-яЁё])", re.IGNORECASE)
_RULES = [(re.compile(p), r) for p, r in RULES]
_CUT = re.compile(r"(?:\s*[А-Яа-яЁё]+)+…")


def _case(found, en):
    letters = [ch for ch in found if ch.isalpha()]
    if len(letters) > 1 and all(ch.isupper() for ch in letters):
        return en.upper()
    if found[:1].isupper():
        return en[:1].upper() + en[1:]
    return en


def tr(text, lang=None):
    """Строка вывода на языке lang (по умолчанию выбранном); для ru — без изменений."""
    if (lang or LANG) != "en" or not text:
        return text
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    text = _PATTERN.sub(lambda m: _case(m.group(0), _TABLE[m.group(0).lower()]), text)
    return _CUT.sub("…", text)          # строка обрезана посреди русского слова: обрывок не переводится, убираем


def block(name, ru_text, lang=None):
    """Целый текст: ru_text для ru, перевод из BLOCKS для en."""
    return BLOCKS[name] if (lang or LANG) == "en" else ru_text


def pick(argv, env):
    """Язык из argv (--lang X или --lang=X, удаляется из argv), затем env["ANALYST_LANG"], затем en."""
    value = None
    for i, a in enumerate(argv):
        if a == "--lang" and i + 1 < len(argv):
            value = argv[i + 1]
            del argv[i:i + 2]
            break
        if a.startswith("--lang="):
            value = a.split("=", 1)[1]
            del argv[i]
            break
    value = (value or env.get("ANALYST_LANG") or DEFAULT).strip().lower()
    if value not in LANGS:
        raise SystemExit(f"--lang / ANALYST_LANG: en or ru, not {value!r}")
    return value


class _Translated:
    """Поток вывода, переводящий каждую запись."""

    def __init__(self, raw):
        self._raw = raw

    def write(self, s):
        return self._raw.write(tr(s))

    def __getattr__(self, name):
        return getattr(self._raw, name)


def setup(argv=None):
    """Для запуска скрипта: выбрать язык, убрать --lang из sys.argv, при en переводить stdout и stderr."""
    global LANG
    LANG = pick(sys.argv if argv is None else argv, os.environ)
    for name in ("stdout", "stderr"):
        raw = getattr(sys, name)
        if hasattr(raw, "reconfigure"):
            raw.reconfigure(encoding="utf-8")
        if LANG == "en" and not isinstance(raw, _Translated):
            setattr(sys, name, _Translated(raw))
    return LANG
