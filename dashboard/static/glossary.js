/* Gold Desk chart cheat sheet: every word, line, box and arrow the chart can show, in English with the Persian name.
   Used by the "?" panel on the chart; the same list builds the printable cheat sheet. Swatches: tag (the label as it
   looks on the chart), line, band, box, arrow, col (a shaded column). */
window.GLOSSARY = (() => {
  const BUY = "#2f7bf5", SELL = "#e5533c", UP = "#2fb67c", DOWN = "#e5484d", GOLD = "#d6ad52", LIQ = "#e8b23a", FG = "#ece8df", GREY = "#8e8a80";
  return [
    { title: "Your trades", fa: "معامله‌های شما", items: [
      { term: "B 0.05", fa: "خرید شما", sw: { t: "tag", c: BUY }, text: "Your open BUY and its lots. The blue line is your entry price; the price itself is on the right axis." },
      { term: "S 0.05", fa: "فروش شما", sw: { t: "tag", c: SELL }, text: "Your open SELL and its lots. The red line is your entry price." },
      { term: "SL", fa: "حد ضرر", sw: { t: "tag", c: DOWN }, text: "Stop loss: the trade closes here with a loss." },
      { term: "TP", fa: "حد سود", sw: { t: "tag", c: UP }, text: "Take profit: the trade closes here with a profit." },
    ] },
    { title: "Kronos forecast", fa: "پیش‌بینی کرونوس", items: [
      { term: "Dashed gold line", fa: "مسیر پیش‌بینی", sw: { t: "line", c: GOLD, dash: true }, text: "Kronos's average guess of where price goes next (2 h on 1m / 5m, 24 h on 1h). A guess, not proven on gold." },
      { term: "Gold shading", fa: "محدوده احتمالی قیمت", sw: { t: "band" }, text: "The spread of its sample paths. Light = lowest to highest path, darker = the middle half. Wide shading = unsure." },
      { term: "K ▲ 2h", fa: "هدف کرونوس", sw: { t: "tag", c: GOLD }, text: "Where the forecast ends: ▲ up, ▼ down, then how far ahead (2h, 24h)." },
    ] },
    { title: "Boom / Crash and heads-up", fa: "بوم / کرش و هشدار", items: [
      { term: "BOOM", fa: "بوم (جهش صعودی)", sw: { t: "arrow", up: true, c: UP }, text: "Green arrow under a candle: a Boom call, a fast move UP expected. Untested, demo only." },
      { term: "CRASH", fa: "کرش (ریزش)", sw: { t: "arrow", up: false, c: DOWN }, text: "Red arrow over a candle: a Crash call, a fast move DOWN expected." },
      { term: "+1.9R", fa: "نتیجه به ضریب ریسک", sw: { t: "text", c: UP }, text: "Result of a finished call in R. 1R = the distance to its stop. +1.9R won 1.9 times the risk; −1R hit the stop." },
      { term: "BOOM in / SL / TP", fa: "ورود، حد ضرر و حد سود بوم", sw: { t: "tag", c: GOLD, label: "BOOM in" }, text: "Entry, stop and target of the call that is live now (CRASH in / SL / TP for a Crash)." },
      { term: "WATCH ▲", fa: "هشدار ستاپ", sw: { t: "box", c: UP, dash: true }, text: "Dashed box: a setup may form in this price area soon (the phone heads-up). Not a trade signal by itself." },
    ] },
    { title: "Market structure", fa: "ساختار بازار", items: [
      { term: "HH", fa: "سقف بالاتر", sw: { t: "tag", c: "#bebab0" }, text: "Higher high: a swing top above the last one." },
      { term: "HL", fa: "کف بالاتر", sw: { t: "tag", c: "#bebab0" }, text: "Higher low: a swing bottom above the last one. HH + HL = uptrend." },
      { term: "LH", fa: "سقف پایین‌تر", sw: { t: "tag", c: "#bebab0" }, text: "Lower high: a swing top below the last one." },
      { term: "LL", fa: "کف پایین‌تر", sw: { t: "tag", c: "#bebab0" }, text: "Lower low: a swing bottom below the last one. LH + LL = downtrend." },
      { term: "BOS", fa: "شکست ساختار", sw: { t: "tag", c: UP }, text: "Break of structure: price closed past the last swing in the trend's direction. The trend continues. Green = up, red = down." },
      { term: "CHoCH", fa: "تغییر کاراکتر (تغییر روند)", sw: { t: "tag", c: LIQ }, text: "Change of character: the first break against the trend (dashed line). A possible reversal." },
    ] },
    { title: "Zones (boxes)", fa: "ناحیه‌ها", items: [
      { term: "FVG", fa: "شکاف ارزش منصفانه (گپ)", sw: { t: "box", c: UP }, text: "Fair value gap: a 3-candle gap price jumped over. Price often comes back to fill it. Green = bullish, red = bearish." },
      { term: "IFVG", fa: "گپ معکوس‌شده", sw: { t: "box", c: DOWN }, text: "Inverse FVG: a gap price closed straight through. It now works the other way (old support becomes resistance)." },
      { term: "OB", fa: "اوردر بلاک", sw: { t: "box", c: BUY }, text: "Order block: the last opposite candle before a strong move. Price often reacts when it returns. Blue = bullish, red = bearish." },
      { term: "BB", fa: "بریکر بلاک", sw: { t: "box", c: SELL }, text: "Breaker block: an order block price broke through. It flips sides." },
      { term: "OTE", fa: "ناحیه ورود بهینه", sw: { t: "box", c: GOLD }, text: "Optimal trade entry: 62-79% back into the last big leg. The classic ICT pullback entry area." },
      { term: "Faded box", fa: "ناحیه مصرف‌شده", sw: { t: "box", c: GREY, faint: true }, text: "A zone price already used or filled. Kept a while for reference, less important." },
      { term: "PREM", fa: "ناحیه گران (پریمیوم)", sw: { t: "tag", c: DOWN }, text: "Premium: the top half of the current range. Price is expensive; ICT prefers selling here." },
      { term: "EQ", fa: "نقطه تعادل ۵۰٪", sw: { t: "tag", c: FG }, text: "Equilibrium: the 50% line of the range (dashed)." },
      { term: "DISC", fa: "ناحیه ارزان (دیسکانت)", sw: { t: "tag", c: UP }, text: "Discount: the bottom half of the range. Price is cheap; ICT prefers buying here." },
    ] },
    { title: "Liquidity (dotted lines)", fa: "نقدینگی", items: [
      { term: "BSL $", fa: "نقدینگی سمت خرید", sw: { t: "tag", c: LIQ }, text: "Buy-side liquidity: stop orders resting above a swing high. Price is often pulled up to take them." },
      { term: "SSL $", fa: "نقدینگی سمت فروش", sw: { t: "tag", c: LIQ }, text: "Sell-side liquidity: stop orders resting below a swing low." },
      { term: "EQH $", fa: "سقف‌های هم‌سطح", sw: { t: "tag", c: LIQ }, text: "Equal highs: two tops at almost the same price. A magnet for price." },
      { term: "EQL $", fa: "کف‌های هم‌سطح", sw: { t: "tag", c: LIQ }, text: "Equal lows: two bottoms at almost the same price." },
      { term: "$", fa: "نقدینگی دست‌نخورده", sw: { t: "text", c: LIQ }, text: "Still there: price has not reached it yet." },
      { term: "✕", fa: "نقدینگی جمع‌شده", sw: { t: "tag", c: GREY, faint: true }, text: "Swept: price already took it (grey). Often followed by a move the other way." },
    ] },
    { title: "Key levels (long dashed lines)", fa: "سطوح کلیدی", items: [
      { term: "PDH", fa: "سقف روز قبل", sw: { t: "tag", c: FG }, text: "Previous day high (New York day, 17:00 to 17:00)." },
      { term: "PDL", fa: "کف روز قبل", sw: { t: "tag", c: FG }, text: "Previous day low." },
      { term: "PWH", fa: "سقف هفته قبل", sw: { t: "tag", c: FG }, text: "Previous week high." },
      { term: "PWL", fa: "کف هفته قبل", sw: { t: "tag", c: FG }, text: "Previous week low." },
      { term: "NMO", fa: "قیمت باز شدن نیمه‌شب نیویورک", sw: { t: "tag", c: FG }, text: "New York midnight open. ICT's line for the day: above it = premium for the day, below = discount." },
    ] },
    { title: "Killzones (shaded columns, 1m to 15m)", fa: "کیل‌زون‌ها (ساعات پرتحرک)", note: "Tehran times while New York is on summer time; from early November to March each starts 1 hour later.", items: [
      { term: "Asia", fa: "سشن آسیا", sw: { t: "col", c: "rgb(120,110,230)" }, text: "03:30-07:30 Tehran. Usually quiet; its high and low are often taken later." },
      { term: "London", fa: "سشن لندن", sw: { t: "col", c: "rgb(47,123,245)" }, text: "09:30-12:30 Tehran. Often makes the day's first big move." },
      { term: "NY AM", fa: "صبح نیویورک", sw: { t: "col", c: "rgb(214,173,82)" }, text: "14:30-17:30 Tehran. The busiest window for gold." },
      { term: "NY Lunch", fa: "ناهار نیویورک", sw: { t: "col", c: "rgb(142,138,128)" }, text: "19:30-20:30 Tehran. Slow and choppy; ICT avoids trading it." },
      { term: "NY PM", fa: "عصر نیویورک", sw: { t: "col", c: "rgb(214,120,82)" }, text: "21:00-23:30 Tehran. A second, smaller push." },
    ] },
    { title: "Scalper (off by default)", fa: "اسکالپر", note: "Turn on with the Scalper button. It lost money in the backtest.", items: [
      { term: "Blue / red arrow", fa: "سیگنال خرید / فروش", sw: { t: "arrow", up: true, c: BUY }, text: "Where the scalper signalled a BUY (blue, under the candle) or SELL (red, over it)." },
      { term: "SIG ▲", fa: "قیمت ورود سیگنال", sw: { t: "tag", c: GREY }, text: "Entry of the scalper's live signal. SIG SL is its stop." },
      { term: "TP1 / TP2", fa: "حد سود اول و دوم", sw: { t: "tag", c: UP }, text: "The signal's first and second targets." },
      { term: "M15 HI / LO", fa: "سقف و کف سوئینگ ۱۵ دقیقه", sw: { t: "tag", c: "#6f6a60" }, text: "The last 15-minute swing high and low the scalper uses for direction." },
    ] },
    { title: "Top-left pill", fa: "خلاصه بالای چارت", items: [
      { term: "▲ SMC 5m", fa: "خلاصه اسمارت مانی", sw: { t: "tag", c: UP }, text: "One-line read of the chart: trend (from the last BOS / CHoCH), premium or discount, and the killzone you are in." },
    ] },
  ];
})();
