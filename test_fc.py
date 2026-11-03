"""Тесты расчётов fetch_candles.py на синтетических свечах: python test_fc.py"""
import fetch_candles as fc


def B(o, h, l, c, t=0):
    return dict(t=t, o=o, h=h, l=l, c=c)


# фрактал: 5 свечей, хай в центре
bars = [B(10, 11, 9, 10), B(10, 12, 9, 11), B(11, 15, 10, 12), B(12, 13, 10, 11), B(11, 12, 9, 10)]
f = fc.fractals(bars)
assert [(x["side"], x["i"], x["p"]) for x in f] == [("high", 2, 15)], f

# рейд: фитиль за 15 и закрытие обратно; затем пробой закрытием
bars2 = bars + [B(10, 14, 9, 13), B(13, 16, 12, 14), B(14, 18, 13, 17)]
f = fc.fractals(bars2)
fc.mark_raids(bars2, f)
h = [x for x in f if x["side"] == "high" and x["p"] == 15][0]
assert h["raids"] == [6] and h["broken"] == 7, h

# FVG вверх и его слом закрытием
bars3 = [B(10, 11, 9, 10.5), B(10.5, 15, 10, 14.5), B(14.5, 16, 13, 15.5), B(15.5, 16, 12, 13), B(13, 13.5, 8, 9)]
z = fc.fvgs(bars3)
assert z[0]["dir"] == "up" and z[0]["lo"] == 11 and z[0]["hi"] == 13, z
assert z[0]["tested"] and z[0]["broken"] == 4, z
assert fc.ifvgs(bars3, z)[0]["dir"] == "down"

# BOS и CHoCH
seq = [B(10, 11, 9, 10), B(10, 12, 9, 11), B(11, 15, 10, 12), B(12, 13, 8, 9), B(9, 10, 5, 6), B(6, 8, 3, 7),
       B(7, 9, 4, 8), B(8, 10, 5, 9), B(9, 16, 8, 15.5), B(15.5, 17, 15, 16), B(16, 18, 14, 15), B(15, 16, 13, 14),
       B(14, 15, 12, 13), B(13, 14, 2, 2.5)]
f = fc.fractals(seq)
trend, events = fc.structure(seq, f)
assert events[0]["kind"] is None and events[0]["dir"] == "up" and events[0]["level"] == 15, events
assert events[-1]["kind"] == "CHoCH" and events[-1]["dir"] == "down" and trend == "down", events

# SNR: buy-to-sell, затем пробитие вверх закрытием; зона по телам
snr_bars = [B(10, 12, 9, 11), B(11, 11.5, 8, 9), B(9, 14, 8.5, 13)]
s = fc.snrs(snr_bars, [], [], None, 0)
assert s and s[0]["dir"] == "up" and s[0]["lo"] == 9 and s[0]["hi"] == 11, s
assert s[0]["fvg_lower"] is None

# RB шорт: снят фрактал 15, обе закрылись ниже, вторая медвежья после бычьей
rb = bars + [B(10, 11, 9, 10.5), B(10.5, 16, 10, 14), B(14, 14.5, 11, 12)]
f = fc.fractals(rb)
fc.mark_raids(rb, f)
r = fc.rejection_blocks(rb, f, [])
assert any(x["dir"] == "down" and x["hi"] == 16 for x in r), r

# ATR 14
a = fc.atr([B(10, 11, 9, 10)] * 20)
assert a[12] is None and abs(a[13] - 2) < 1e-9

# R:R по правилам и вариант бэктеста после комиссий; по умолчанию FEES выключен
assert fc.FEES is None and fc.rr_value(100, 110, 85, 10, False) == 1.5
print("OK")
