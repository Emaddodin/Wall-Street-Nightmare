/* Gold Desk chart cheat sheet: every word, line, box and arrow the chart can show, in English and Persian.
   Used by the "?" panel on the chart (with an English / فارسی switch); the same list builds static/cheatsheet.html.
   Swatches: tag (the label as it looks on the chart, `label` overrides the text), text, line, band, box, arrow, col. */
window.GLOSSARY = (() => {
  const BUY = "#2f7bf5", SELL = "#e5533c", UP = "#2fb67c", DOWN = "#e5484d", GOLD = "#d6ad52", LIQ = "#e8b23a", FG = "#ece8df", GREY = "#8e8a80";
  const sections = [
    { title: "Your trades", fa: "معامله‌های شما", items: [
      { term: "B 0.05", fa: "خرید شما", sw: { t: "tag", c: BUY },
        text: "Your open BUY and its lots. The blue line is your entry price; the price itself is on the right axis.",
        faText: "خرید باز شما و حجمش (لات). خط آبی قیمت ورود شماست؛ عدد قیمت روی محور سمت راست است." },
      { term: "S 0.05", fa: "فروش شما", sw: { t: "tag", c: SELL },
        text: "Your open SELL and its lots. The red line is your entry price.",
        faText: "فروش باز شما و حجمش. خط قرمز قیمت ورود شماست." },
      { term: "SL", fa: "حد ضرر", sw: { t: "tag", c: DOWN },
        text: "Stop loss: the trade closes here with a loss.",
        faText: "حد ضرر (Stop Loss): معامله اینجا با ضرر بسته می‌شود." },
      { term: "TP", fa: "حد سود", sw: { t: "tag", c: UP },
        text: "Take profit: the trade closes here with a profit.",
        faText: "حد سود (Take Profit): معامله اینجا با سود بسته می‌شود." },
      { term: "Spread", fa: "اسپرد", sw: { t: "text", c: FG, label: "0.20" },
        text: "Gap between the BUY (ask) and SELL (bid) price, under the ticket. Smaller is better; it widens around news and at the open.",
        faText: "فاصله قیمت خرید (Ask) و فروش (Bid)، زیر دکمه‌ها. هرچه کمتر بهتر. موقع اخبار و باز شدن بازار زیاد می‌شود." },
    ] },
    { title: "Kronos forecast", fa: "پیش‌بینی کرونوس (هوش مصنوعی)", items: [
      { term: "Dashed gold line", fa: "مسیر پیش‌بینی", sw: { t: "line", c: GOLD, dash: true },
        text: "Kronos's average guess of where price goes next (2 h on 1m / 5m, 24 h on 1h). A guess, not proven on gold.",
        faText: "خط‌چین طلایی: میانگین مسیرهای پیش‌بینی برای ۲ ساعت آینده (روی ۱m و ۵m) یا ۲۴ ساعت (روی ۱h). فقط حدس است و روی طلا ثابت نشده." },
      { term: "Gold shading", fa: "محدوده احتمالی قیمت", sw: { t: "band" },
        text: "The spread of its sample paths. Light = lowest to highest path, darker = the middle half. Wide shading = unsure.",
        faText: "پخش مسیرها: سایه کم‌رنگ = از پایین‌ترین تا بالاترین مسیر، سایه پررنگ = نیمه وسط مسیرها. هرچه پهن‌تر، نامطمئن‌تر." },
      { term: "K ▲ 2h", fa: "هدف کرونوس", sw: { t: "tag", c: GOLD },
        text: "Where the forecast ends: ▲ up, ▼ down, then how far ahead (2h, 24h).",
        faText: "قیمت پایان پیش‌بینی: ▲ بالا، ▼ پایین، و بعد چقدر جلوتر (۲ ساعت، ۲۴ ساعت)." },
      { term: "Up % · Down %", fa: "درصد بالا / پایین", sw: { t: "text", c: UP, label: "63%" },
        text: "On the Kronos card: the share of its paths that end above / below today's price.",
        faText: "روی کارت Kronos: درصد مسیرهایی که بالاتر یا پایین‌تر از قیمت فعلی تمام می‌شوند." },
      { term: "Volatility jump", fa: "جهش نوسان", sw: { t: "text", c: FG, label: "47%" },
        text: "Share of paths that move more than price has lately. High = a sharp move may be coming.",
        faText: "درصد مسیرهایی که پرنوسان‌تر از نوسان اخیرند. عدد بالا یعنی ممکن است حرکت تندی در راه باشد." },
      { term: "Forecast vs actual", fa: "پیش‌بینی در برابر واقعیت", sw: { t: "line", c: FG },
        text: "Kronos's report card: the last finished forecast (dashed) against what price really did (solid), and how often it got the direction right.",
        faText: "کارنامه Kronos: آخرین پیش‌بینی تمام‌شده (خط‌چین) در برابر حرکت واقعی قیمت (خط پر)، و چند بار جهت را درست گفته." },
    ] },
    { title: "Boom / Crash and heads-up", fa: "بوم / کرش و هشدار", items: [
      { term: "BOOM", fa: "بوم (جهش صعودی)", sw: { t: "arrow", up: true, c: UP },
        text: "Green arrow under a candle: a Boom call, a fast move UP expected within 30 min. Untested, demo only.",
        faText: "فلش سبز زیر کندل: سیگنال بوم، انتظار حرکت تند رو به بالا در ۳۰ دقیقه. تست نشده، فقط دمو." },
      { term: "CRASH", fa: "کرش (ریزش)", sw: { t: "arrow", up: false, c: DOWN },
        text: "Red arrow over a candle: a Crash call, a fast move DOWN expected.",
        faText: "فلش قرمز بالای کندل: سیگنال کرش، انتظار ریزش تند." },
      { term: "+1.9R", fa: "نتیجه به ضریب ریسک", sw: { t: "text", c: UP },
        text: "Result of a finished call in R. 1R = the distance to its stop. +1.9R won 1.9 times the risk; −1R hit the stop.",
        faText: "نتیجه سیگنال تمام‌شده بر حسب R. هر R یعنی فاصله ورود تا حد ضرر. ‎+1.9R یعنی ۱٫۹ برابر ریسک سود؛ ‎−1R یعنی حد ضرر خورده. اگر ریسک ۱۰ دلار باشد، ‎+2R یعنی ۲۰ دلار سود." },
      { term: "BOOM in / SL / TP", fa: "ورود، حد ضرر و حد سود بوم", sw: { t: "tag", c: GOLD, label: "BOOM in" },
        text: "Entry, stop and target of the call that is live now (CRASH in / SL / TP for a Crash).",
        faText: "ورود، حد ضرر و حد سود سیگنالی که الان فعال است (برای کرش: CRASH in / SL / TP)." },
      { term: "WATCH ▲", fa: "هشدار ستاپ", sw: { t: "box", c: UP, dash: true },
        text: "Dashed box: a setup may form in this price area soon (the phone heads-up). Not a trade signal by itself.",
        faText: "کادر خط‌چین: احتمالاً به‌زودی در این محدوده قیمت ستاپ می‌آید (همان هشدار گوشی). هنوز سیگنال نیست؛ فقط آماده باش." },
      { term: "UNTESTED", fa: "تست‌نشده", sw: { t: "tag", c: LIQ },
        text: "This kind of call has no reliable result on real gold data yet. Small size or demo only.",
        faText: "این نوع سیگنال هنوز روی داده واقعی طلا نتیجه قابل اعتماد نداشته. با حجم کم یا روی دمو." },
    ] },
    { title: "Market structure", fa: "ساختار بازار", items: [
      { term: "HH", fa: "سقف بالاتر", sw: { t: "tag", c: "#bebab0" },
        text: "Higher high: a swing top above the last one.", faText: "Higher High: سقف چرخشی بالاتر از سقف قبلی." },
      { term: "HL", fa: "کف بالاتر", sw: { t: "tag", c: "#bebab0" },
        text: "Higher low: a swing bottom above the last one. HH + HL = uptrend.", faText: "Higher Low: کف چرخشی بالاتر از کف قبلی. HH و HL با هم = روند صعودی." },
      { term: "LH", fa: "سقف پایین‌تر", sw: { t: "tag", c: "#bebab0" },
        text: "Lower high: a swing top below the last one.", faText: "Lower High: سقف چرخشی پایین‌تر از سقف قبلی." },
      { term: "LL", fa: "کف پایین‌تر", sw: { t: "tag", c: "#bebab0" },
        text: "Lower low: a swing bottom below the last one. LH + LL = downtrend.", faText: "Lower Low: کف چرخشی پایین‌تر از کف قبلی. LH و LL با هم = روند نزولی." },
      { term: "BOS", fa: "شکست ساختار", sw: { t: "tag", c: UP },
        text: "Break of structure: price closed past the last swing in the trend's direction. The trend continues. Green = up, red = down.",
        faText: "Break of Structure: شکست سقف یا کف در جهت روند فعلی؛ یعنی روند ادامه دارد. سبز = صعودی، قرمز = نزولی." },
      { term: "CHoCH", fa: "تغییر کاراکتر (تغییر روند)", sw: { t: "tag", c: LIQ },
        text: "Change of character: the first break against the trend (dashed line). A possible reversal.",
        faText: "Change of Character: اولین شکست خلاف روند (خط‌چین زرد). هشدار برگشت روند." },
    ] },
    { title: "Zones (boxes)", fa: "نواحی (کادرها)", items: [
      { term: "FVG", fa: "شکاف ارزش منصفانه (گپ)", sw: { t: "box", c: UP },
        text: "Fair value gap: a 3-candle gap price jumped over. Price often comes back to fill it. Green = bullish, red = bearish.",
        faText: "Fair Value Gap: شکاف سه‌کندلی که بازار سریع از آن رد شده. قیمت معمولاً برمی‌گردد پرش کند. سبز = حمایتی، قرمز = مقاومتی." },
      { term: "IFVG", fa: "گپ معکوس‌شده", sw: { t: "box", c: DOWN },
        text: "Inverse FVG: a gap price closed straight through. It now works the other way (old support becomes resistance).",
        faText: "Inverse FVG: گپی که قیمت از آن عبور کرده و حالا نقش برعکس دارد (حمایت قبلی ← مقاومت)." },
      { term: "OB", fa: "اوردر بلاک", sw: { t: "box", c: BUY },
        text: "Order block: the last opposite candle before a strong move. Price often reacts when it returns. Blue = bullish, red = bearish.",
        faText: "Order Block: آخرین کندل مخالف قبل از حرکت قوی که ساختار را شکست؛ جای سفارش‌های بزرگ. قیمت اغلب به آن واکنش می‌دهد. آبی = صعودی، قرمز = نزولی." },
      { term: "BB", fa: "بریکر بلاک", sw: { t: "box", c: SELL },
        text: "Breaker block: an order block price broke through. It flips sides.",
        faText: "Breaker Block: اوردربلاکی که قیمت از آن رد شده و حالا نقش برعکس دارد." },
      { term: "OTE", fa: "ناحیه ورود بهینه", sw: { t: "box", c: GOLD },
        text: "Optimal trade entry: 62-79% back into the last big leg. The classic ICT pullback entry area.",
        faText: "Optimal Trade Entry: ناحیه ۶۲ تا ۷۹ درصد اصلاح آخرین موج؛ بهترین جای ورود هم‌جهت با روند." },
      { term: "Faded box", fa: "ناحیه مصرف‌شده", sw: { t: "box", c: GREY, faint: true },
        text: "A zone price already used or filled. Kept a while for reference, less important.",
        faText: "ناحیه‌ای که قیمت قبلاً به آن رسیده یا پرش کرده. مدتی برای مرجع می‌ماند؛ اهمیتش کمتر است." },
      { term: "PREM", fa: "ناحیه گران (پریمیوم)", sw: { t: "tag", c: DOWN },
        text: "Premium: the top half of the current range. Price is expensive; ICT prefers selling here.",
        faText: "Premium: نیمه بالای محدوده فعلی. قیمت گران است؛ جای فروش." },
      { term: "EQ", fa: "نقطه تعادل ۵۰٪", sw: { t: "tag", c: FG },
        text: "Equilibrium: the 50% line of the range (dashed).", faText: "Equilibrium: خط ۵۰٪ وسط محدوده (خط‌چین)." },
      { term: "DISC", fa: "ناحیه ارزان (دیسکانت)", sw: { t: "tag", c: UP },
        text: "Discount: the bottom half of the range. Price is cheap; ICT prefers buying here.",
        faText: "Discount: نیمه پایین محدوده. قیمت ارزان است؛ جای خرید." },
    ] },
    { title: "Liquidity (dotted lines)", fa: "نقدینگی (خطوط نقطه‌چین)", items: [
      { term: "BSL $", fa: "نقدینگی سمت خرید", sw: { t: "tag", c: LIQ },
        text: "Buy-side liquidity: stop orders resting above a swing high. Price is often pulled up to take them.",
        faText: "Buy-Side Liquidity: استاپ‌های جمع‌شده بالای سقف‌ها. قیمت اغلب بالا کشیده می‌شود تا آنها را بزند." },
      { term: "SSL $", fa: "نقدینگی سمت فروش", sw: { t: "tag", c: LIQ },
        text: "Sell-side liquidity: stop orders resting below a swing low.",
        faText: "Sell-Side Liquidity: استاپ‌های جمع‌شده زیر کف‌ها؛ هدف احتمالی قیمت." },
      { term: "EQH $", fa: "سقف‌های هم‌سطح", sw: { t: "tag", c: LIQ },
        text: "Equal highs: two tops at almost the same price. A magnet for price.",
        faText: "سقف‌های برابر: دو سقف تقریباً هم‌قیمت. پشتشان استاپ زیاد است و بازار دوست دارد آنها را بزند." },
      { term: "EQL $", fa: "کف‌های هم‌سطح", sw: { t: "tag", c: LIQ },
        text: "Equal lows: two bottoms at almost the same price.", faText: "کف‌های برابر: دو کف تقریباً هم‌قیمت." },
      { term: "$", fa: "نقدینگی دست‌نخورده", sw: { t: "text", c: LIQ },
        text: "Still there: price has not reached it yet.", faText: "هنوز دست‌نخورده: قیمت هنوز به آن نرسیده." },
      { term: "✕", fa: "نقدینگی جاروب‌شده", sw: { t: "tag", c: GREY, faint: true },
        text: "Swept: price already took it (grey). Often followed by a move the other way.",
        faText: "جاروب شده (Swept): قیمت آن را زده (خاکستری). معمولاً بعدش برگشت می‌آید." },
    ] },
    { title: "Key levels (long dashed lines)", fa: "سطوح کلیدی (خط‌چین بلند)", items: [
      { term: "PDH", fa: "سقف روز قبل", sw: { t: "tag", c: FG },
        text: "Previous day high (New York day, 17:00 to 17:00).", faText: "سقف روز قبل (روز معاملاتی نیویورک، ۱۷:۰۰ تا ۱۷:۰۰)." },
      { term: "PDL", fa: "کف روز قبل", sw: { t: "tag", c: FG }, text: "Previous day low.", faText: "کف روز قبل." },
      { term: "PWH", fa: "سقف هفته قبل", sw: { t: "tag", c: FG }, text: "Previous week high.", faText: "سقف هفته قبل." },
      { term: "PWL", fa: "کف هفته قبل", sw: { t: "tag", c: FG }, text: "Previous week low.", faText: "کف هفته قبل." },
      { term: "NMO", fa: "قیمت باز شدن نیمه‌شب نیویورک", sw: { t: "tag", c: FG },
        text: "New York midnight open. ICT's line for the day: above it = premium for the day, below = discount.",
        faText: "New York Midnight Open: قیمت ساعت ۰۰:۰۰ نیویورک؛ مرجع ارزان یا گران بودن امروز. بالایش گران، پایینش ارزان." },
    ] },
    { title: "Killzones (shaded columns, 1m to 15m)", fa: "کیل‌زون‌ها (ستون‌های رنگی، ۱m تا ۱۵m)",
      note: "Tehran times while New York is on summer time; after the US clock change on 1 November each starts 1 hour later.",
      faNote: "ساعت‌ها به وقت تهران و برای الان است (نیویورک ۷٫۵ ساعت عقب‌تر). بعد از تغییر ساعت آمریکا در ۱ نوامبر، همه یک ساعت دیرتر می‌شوند.", items: [
      { term: "Asia", fa: "سشن آسیا", sw: { t: "col", c: "rgb(120,110,230)" },
        text: "03:30-07:30 Tehran. Usually quiet; its high and low are often taken later.",
        faText: "۰۳:۳۰ تا ۰۷:۳۰ تهران. معمولاً آرام؛ سقف و کفش اغلب بعداً در لندن زده می‌شود." },
      { term: "London", fa: "کیل‌زون لندن", sw: { t: "col", c: "rgb(47,123,245)" },
        text: "09:30-12:30 Tehran. Often makes the day's first big move.",
        faText: "۰۹:۳۰ تا ۱۲:۳۰ تهران. اغلب اولین حرکت بزرگ روز اینجاست." },
      { term: "NY AM", fa: "صبح نیویورک", sw: { t: "col", c: "rgb(214,173,82)" },
        text: "14:30-17:30 Tehran. The busiest window for gold.",
        faText: "۱۴:۳۰ تا ۱۷:۳۰ تهران. پرحرکت‌ترین زمان روز برای طلا." },
      { term: "NY Lunch", fa: "ناهار نیویورک", sw: { t: "col", c: "rgb(142,138,128)" },
        text: "19:30-20:30 Tehran. Slow and choppy; ICT avoids trading it.",
        faText: "۱۹:۳۰ تا ۲۰:۳۰ تهران. بازار کند و فریبنده؛ بهتر است معامله نکنی." },
      { term: "NY PM", fa: "عصر نیویورک", sw: { t: "col", c: "rgb(214,120,82)" },
        text: "21:00-23:30 Tehran. A second, smaller push, after your 10:30-21:00 session.",
        faText: "۲۱:۰۰ تا ۲۳:۳۰ تهران. حرکت دوم و کوچک‌تر؛ بعد از پایان سشن روزانه تو (۱۰:۳۰ تا ۲۱:۰۰)." },
    ] },
    { title: "Scalper (off by default)", fa: "اسکالپر (پیش‌فرض خاموش)",
      note: "Turn on with the Scalper button. It lost money in the backtest.",
      faNote: "با دکمه Scalper روشن می‌شود. در بک‌تست ضرر داده است.", items: [
      { term: "Blue / red arrow", fa: "سیگنال خرید / فروش", sw: { t: "arrow", up: true, c: BUY },
        text: "Where the scalper signalled a BUY (blue, under the candle) or SELL (red, over it).",
        faText: "جایی که اسکالپر سیگنال خرید (آبی، زیر کندل) یا فروش (قرمز، بالای کندل) داده." },
      { term: "SIG ▲", fa: "قیمت ورود سیگنال", sw: { t: "tag", c: GREY },
        text: "Entry of the scalper's live signal. SIG SL is its stop.", faText: "ورود سیگنال فعال اسکالپر. SIG SL حد ضرر آن است." },
      { term: "TP1 / TP2", fa: "حد سود اول و دوم", sw: { t: "tag", c: UP },
        text: "The signal's first and second targets.", faText: "هدف اول و دوم سیگنال." },
      { term: "M15 HI / LO", fa: "سقف و کف سوئینگ ۱۵ دقیقه", sw: { t: "tag", c: "#6f6a60" },
        text: "The last 15-minute swing high and low the scalper uses for direction.",
        faText: "آخرین سقف و کف چرخشی ۱۵ دقیقه‌ای که اسکالپر برای جهت از آن استفاده می‌کند." },
    ] },
    { title: "Top-left pill", fa: "خلاصه بالای چارت", items: [
      { term: "▲ SMC 5m", fa: "خلاصه اسمارت مانی", sw: { t: "tag", c: UP },
        text: "One-line read of the chart: trend (from the last BOS / CHoCH), premium or discount, and the killzone you are in.",
        faText: "خلاصه یک‌خطی چارت: روند (از آخرین BOS یا CHoCH)، گران یا ارزان بودن قیمت، و کیل‌زونی که الان در آن هستی." },
    ] },
  ];
  const guide = {
    title: "A simple way to read the chart", fa: "یک روش ساده برای خواندن چارت",
    steps: [
      ["Find the trend from BOS / CHoCH and HH / LL.", "روند را از BOS/CHoCH و HH/LL بفهم."],
      ["In an uptrend look for buys only in DISC and OTE, on a green FVG or blue OB. In a downtrend the opposite.", "در روند صعودی فقط در DISC و OTE دنبال خرید باش، روی FVG سبز یا OB آبی؛ در روند نزولی برعکس."],
      ["Aim for the nearest liquidity: $ lines, PDH / PDL.", "هدف را نزدیک‌ترین نقدینگی بگذار: خطوط $ و PDH/PDL."],
      ["Trade inside the killzones; skip NY Lunch.", "فقط داخل کیل‌زون‌ها معامله کن؛ ناهار نیویورک را رد کن."],
      ["Treat BOOM / CRASH and Kronos as a second opinion, never the main reason.", "BOOM/CRASH و Kronos را فقط تأیید بدان، نه دلیل اصلی."],
    ],
  };
  return { sections, guide };
})();

// HTML for the cheat sheet in "en" or "fa", with lg-* classes (styled by index.html and cheatsheet.html)
window.glossaryHtml = (lang) => {
  const fa = lang === "fa", G = window.GLOSSARY;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const sw = (w, term) => {
    const c = w.c || "#d6ad52", txt = esc(w.label || term);
    if (w.t === "tag") return `<span class="lg-tag" style="color:${c};border-color:${c}">${txt}</span>`;
    if (w.t === "text") return `<span class="lg-txt" style="color:${c}">${txt}</span>`;
    if (w.t === "line") return `<span class="lg-line" style="border-top:2px ${w.dash ? "dashed" : "solid"} ${c}"></span>`;
    if (w.t === "band") return `<span class="lg-band"><i></i></span>`;
    if (w.t === "arrow") return `<span class="lg-arrow ${w.up ? "up" : "dn"}" style="--c:${c}"></span>`;
    if (w.t === "col") return `<span class="lg-col" style="--c:${c}"></span>`;
    return `<span class="lg-box${w.faint ? " faint" : ""}" style="--c:${c};border-style:${w.dash ? "dashed" : "solid"}"></span>`;
  };
  const per = (s) => `<span class="fa" lang="fa" dir="rtl">${esc(s)}</span>`, lat = (s) => `<b dir="ltr">${esc(s)}</b>`;
  const head = (g) => fa ? `${per(g.fa)} <span class="lg-en" dir="ltr">${esc(g.title)}</span>` : `${esc(g.title)} ${per(g.fa)}`;
  const guide = `<section class="lg-guide"><h3>${head(G.guide)}</h3><ol>${G.guide.steps.map(([en, p]) => `<li>${fa ? per(p) : esc(en)}</li>`).join("")}</ol></section>`;
  return `<div class="lg-${fa ? "fa" : "en"}" dir="${fa ? "rtl" : "ltr"}">` + G.sections.map((g) => `<section><h3>${head(g)}</h3>
    ${g.note ? `<p class="lg-note">${fa ? per(g.faNote || g.note) : esc(g.note)}</p>` : ""}
    ${g.items.map((i) => `<div class="lg-row"><span class="lg-sw" dir="ltr">${sw(i.sw || {}, i.term)}</span>
      <div><div class="lg-name">${fa ? per(i.fa) + lat(i.term) : lat(i.term) + per(i.fa)}</div><p>${fa ? per(i.faText || i.text) : esc(i.text)}</p></div></div>`).join("")}
  </section>`).join("") + guide + `</div>`;
};
