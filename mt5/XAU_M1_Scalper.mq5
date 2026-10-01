//+------------------------------------------------------------------+
//|                                               XAU_M1_Scalper.mq5 |
//|   Gold M1 scalping signals computed from THIS terminal's data.   |
//|                                                                  |
//|   H1  : EMA 50/200 regime + ATR velocity-shock guard             |
//|   M15 : BOS / CHoCH market structure + EMA 21/55 channel         |
//|   M5  : Fair Value Gaps, Order Blocks, RSI(14) with SD bands     |
//|   M1  : liquidity sweeps, engulfing (+volume), rejection pins,   |
//|         inside-bar breakouts -> entry, SL, TP1, TP2              |
//|                                                                  |
//|   Zero repaint: every signal is evaluated once, on a CLOSED M1   |
//|   bar, using only higher-timeframe bars that had CLOSED by then. |
//|   Display only: this indicator never places or changes orders.   |
//+------------------------------------------------------------------+
#property copyright   "Wall-Street-Nightmare"
#property version     "1.00"
#property description "XAUUSD M1 scalper on your own broker's MT5 data."
#property description "H1 regime + M15 structure + M5 zones/RSI gate M1 triggers. Closed bars only, no repaint."
#property description "Display only: it never places or changes orders."

#property indicator_chart_window
#property indicator_buffers 8
#property indicator_plots   6

#property indicator_label1  "Buy"
#property indicator_type1   DRAW_ARROW
#property indicator_color1  clrDodgerBlue
#property indicator_width1  2
#property indicator_label2  "Sell"
#property indicator_type2   DRAW_ARROW
#property indicator_color2  clrOrangeRed
#property indicator_width2  2
#property indicator_label3  "Entry"
#property indicator_type3   DRAW_NONE
#property indicator_label4  "Stop loss"
#property indicator_type4   DRAW_NONE
#property indicator_label5  "TP1"
#property indicator_type5   DRAW_NONE
#property indicator_label6  "TP2"
#property indicator_type6   DRAW_NONE

//--- enums --------------------------------------------------------------
enum ENUM_SIG_MODE
  {
   MODE_STRICT   = 0,   // Strict: needs M5 zone touch or M1 sweep
   MODE_BALANCED = 1    // Balanced: HTF alignment + M1 pattern only
  };

enum ENUM_SIZING
  {
   SIZE_RISK_PCT   = 0, // Risk % of balance per trade
   SIZE_FIXED_LOTS = 1  // Fixed lot size
  };

enum ENUM_UTC_MODE
  {
   UTC_AUTO  = 0,       // Auto (live: terminal clock, tester: NY+7 server)
   UTC_FIXED = 1        // Fixed offset (hours below)
  };

//--- inputs -------------------------------------------------------------
input group "Strategy Settings"
input ENUM_SIG_MODE InpMode            = MODE_STRICT; // Signal mode
input bool   InpUseSessionFilter       = true;  // Only signal inside the sessions below
input bool   InpTradeLondon            = true;  // London Open 07:00-10:00 UTC
input bool   InpTradeNY                = true;  // NY / US session 12:30-16:00 UTC
input ENUM_UTC_MODE InpUtcMode         = UTC_AUTO; // Broker server clock
input int    InpUtcOffsetHours         = 3;     // Server UTC offset when fixed (hours)
input int    InpLookbackBars           = 5000;  // M1 bars to analyse and backtest on chart
input int    InpSweepLen               = 10;    // M1 micro swing lookback for sweeps
input bool   InpUseEngulf              = true;  // Trigger: engulfing with volume
input double InpVolMult                = 1.5;   // Engulfing tick volume > x * SMA(20)
input bool   InpUsePin                 = true;  // Trigger: liquidity rejection pin
input bool   InpUseInsideBreak         = true;  // Trigger: inside-bar breakout
input bool   InpUseSweepTrigger        = true;  // Trigger: sweep + close back inside
input int    InpTouchBars              = 3;     // Zone touch counts for N M1 bars
input int    InpCooldownBars           = 3;     // M1 bars to wait after a trade closes

input group "Timeframes"
input ENUM_TIMEFRAMES InpTfMacro       = PERIOD_H1;  // Macro regime timeframe
input ENUM_TIMEFRAMES InpTfStruct      = PERIOD_M15; // Market structure timeframe
input ENUM_TIMEFRAMES InpTfZone        = PERIOD_M5;  // Liquidity / momentum timeframe
input int    InpMacroFastEma           = 50;    // H1 fast EMA
input int    InpMacroSlowEma           = 200;   // H1 slow EMA
input double InpShockAtrMult           = 1.8;   // Shock: H1 ATR > x * its 50-bar average
input double InpShockRangeMult         = 2.5;   // Shock: one H1 range > x * ATR
input int    InpShockHoldBars          = 2;     // H1 bars a shock blocks counter-trend trades
input int    InpStructFastEma          = 21;    // M15 fast EMA
input int    InpStructSlowEma          = 55;    // M15 slow EMA
input int    InpSwingLen               = 3;     // M15 swing pivot strength (bars each side)
input int    InpRsiLen                 = 14;    // M5 RSI length
input int    InpRsiBandLen             = 50;    // M5 RSI band lookback
input double InpRsiBandK               = 1.5;   // M5 RSI band width (std devs)
input double InpFvgMinAtr              = 0.10;  // M5 FVG minimum size (x ATR)
input double InpObDispAtr              = 1.20;  // M5 order block displacement body (x ATR)
input int    InpZoneMaxAge             = 48;    // M5 zone lifetime (M5 bars)

input group "Risk Management"
input ENUM_SIZING InpSizing            = SIZE_RISK_PCT; // Position sizing
input double InpRiskPct                = 1.0;   // Risk per trade (% of balance)
input double InpFixedLots              = 0.01;  // Fixed lots (when fixed sizing)
input double InpBacktestBalance        = 0.0;   // On-chart backtest start balance (0 = account balance)
input int    InpSlLookback             = 8;     // SL: structural swing lookback (M1 bars)
input double InpSlBufferAtr            = 0.10;  // SL: buffer beyond swing (x ATR)
input double InpMinSlAtr               = 0.80;  // SL: min distance, else use ATR stop (x ATR)
input double InpAtrSlMult              = 1.50;  // SL: ATR stop distance (x ATR 14)
input double InpMaxSlAtr               = 3.00;  // SL: skip trade if wider than (x ATR)
input double InpTp1R                   = 1.5;   // TP1 (R multiple)
input double InpTp2R                   = 2.5;   // TP2 (R multiple, 2.5 to 3)
input double InpPartialPct             = 50.0;  // Close % at TP1, rest to breakeven
input double InpTrailAtr               = 1.0;   // Runner trailing stop after TP1 (x ATR)
input bool   InpUseBrokerSpread        = true;  // Use the spread your broker recorded per bar
input double InpFixedSpread            = 0.25;  // Fixed spread in price ($0.25 = 2.5 pips)
input double InpCommission             = 7.0;   // Commission per 1.0 lot round turn (account ccy)
input int    InpSlippagePts            = 1;     // Slippage on market fills (points)

input group "Alerts"
input bool   InpRadarAlerts            = true;  // Radar: setup forming (bar still open)
input bool   InpExecAlerts             = true;  // Execute: signal on bar close
input bool   InpPopup                  = true;  // Popup alert in the terminal
input bool   InpPush                   = true;  // Push to MT5 mobile app (set MetaQuotes ID)
input string InpAlertTag               = "XAUUSD"; // Alert prefix

input group "Dashboard Aesthetics"
input int    InpPanelX                 = 12;    // Panel X offset
input int    InpPanelY                 = 24;    // Panel Y offset
input int    InpFontSize               = 9;     // Panel font size
input color  InpPanelBg                = C'18,22,30';    // Panel background
input color  InpPanelBorder            = C'60,70,90';    // Panel border
input color  InpTextColor              = C'215,220,230'; // Panel text
input color  InpDimColor               = C'130,140,160'; // Panel labels
input color  InpBullColor              = clrDodgerBlue;  // Bull / buy colour
input color  InpBearColor              = clrOrangeRed;   // Bear / sell colour
input color  InpWarnColor              = clrGold;        // Warning colour
input color  InpGoodColor              = clrMediumSeaGreen; // Good / TP colour
input color  InpSlColor                = clrCrimson;     // Stop loss colour
input bool   InpDrawZones              = true;  // Draw active M5 FVG / OB zones
input bool   InpDrawSessions           = true;  // Draw London / NY session lines
input bool   InpDrawHistory            = true;  // Draw past trades' levels
input int    InpMaxDrawnTrades         = 40;    // Past trades to draw

//--- buffers ------------------------------------------------------------
double BuyBuf[], SellBuf[], EntryBuf[], SlBuf[], Tp1Buf[], Tp2Buf[], AtrBuf[], VolBuf[];

#define PFX "XAUS_"

//--- higher-timeframe caches (oldest bar first) -------------------------
datetime gMacT[]; double gMacO[], gMacH[], gMacL[], gMacC[], gMacEmaF[], gMacEmaS[], gMacAtr[];
int      gMacReg[], gMacShock[]; int gMacN = 0; datetime gMacStamp = 0;

datetime gStrT[]; double gStrO[], gStrH[], gStrL[], gStrC[], gStrEmaF[], gStrEmaS[], gStrSwH[], gStrSwL[];
int      gStrStruct[], gStrEvent[], gStrBias[]; int gStrN = 0; datetime gStrStamp = 0;

datetime gZnT[]; double gZnO[], gZnH[], gZnL[], gZnC[], gZnAtr[], gZnRsi[], gZnRsiMid[], gZnRsiUp[], gZnRsiLo[];
int      gZnN = 0; datetime gZnStamp = 0;

//--- M5 reaction zones --------------------------------------------------
struct Zone
  {
   int      dir;     // 1 bull (demand), -1 bear (supply)
   int      kind;    // 1 FVG, 2 order block
   double   top;
   double   bottom;
   datetime born;
  };
Zone gZones[];
int  gZoneN = 0;

//--- context at a point in time -----------------------------------------
struct Ctx
  {
   bool   ok;
   int    regime;    // H1: 1 bull, -1 bear, 0 neutral
   int    shock;     // H1: direction of an active velocity shock, 0 none
   double atrMacro;
   int    struct15;  // M15 structure: 1 / -1
   int    event15;   // last event: 1 BOS up, 2 CHoCH up, -1 BOS down, -2 CHoCH down
   bool   emaUp15;
   int    bias15;    // structure and EMA channel agree
   double swH;
   double swL;
   double rsi;
   double rsiMid;
   double rsiUp;
   double rsiLo;
   bool   m5Long;
   bool   m5Short;
  };
Ctx gNow;

//--- simulated trades (on-chart backtest) -------------------------------
struct Trade
  {
   int      dir;
   int      why;     // 1 engulf, 2 pin, 3 inside break, 4 sweep
   int      barIdx;
   datetime tIn;
   datetime tOut;
   double   entry;
   double   sl0;
   double   sl;
   double   tp1;
   double   tp2;
   double   R;
   double   lots;
   double   remain;
   bool     partial;
   bool     open;
   bool     minLotCapped;
   double   pnl;
   double   exitPx;
  };
Trade gTrades[];
int   gTradeN = 0;
bool  gInTrade = false;
int   gCur = -1;
int   gLastExitBar = -100000;

//--- backtest statistics ------------------------------------------------
double gStartEq = 0, gEquity = 0, gPeak = 0, gMaxDD = 0, gGrossW = 0, gGrossL = 0, gSumR = 0;
int    gWins = 0, gLosses = 0;

//--- run state ----------------------------------------------------------
int      gStart = 0, gWarm = 30, gLastProcessed = -1;
datetime gLastProcessedTime = 0, gStartTime = 0;
bool     gHistoryDone = false, gTester = false, gDraw = true;
double   gVpu = 100.0;          // account money per 1.0 price move per 1.0 lot
int      gUtcOffset = 0;        // live server-minus-UTC seconds
int      gLastTouchL = -100000, gLastTouchS = -100000;
long     gAsianDay = -1;
double   gAsianHi = 0, gAsianLo = 0, gLastAtr = 0;
datetime gRadarLongBar = 0, gRadarShortBar = 0;
string   gRadarText = "";
string   gLastSignalText = "";
uint     gLastPaint = 0;

//+------------------------------------------------------------------+
//| Math helpers (arrays oldest-first)                               |
//+------------------------------------------------------------------+
void CalcEMA(const double &src[], const int n, const int len, double &out[])
  {
   ArrayResize(out, n);
   if(n <= 0)
      return;
   double k = 2.0 / (len + 1.0);
   out[0] = src[0];
   for(int i = 1; i < n; i++)
      out[i] = src[i] * k + out[i - 1] * (1.0 - k);
  }

void CalcATR(const double &h[], const double &l[], const double &c[], const int n, const int len, double &out[])
  {
   ArrayResize(out, n);
   if(n <= 0)
      return;
   out[0] = h[0] - l[0];
   for(int i = 1; i < n; i++)
     {
      double tr = MathMax(h[i] - l[i], MathMax(MathAbs(h[i] - c[i - 1]), MathAbs(l[i] - c[i - 1])));
      out[i] = (out[i - 1] * (len - 1) + tr) / len;
     }
  }

void CalcSMA(const double &src[], const int n, const int len, double &out[])
  {
   ArrayResize(out, n);
   double sum = 0;
   for(int i = 0; i < n; i++)
     {
      sum += src[i];
      if(i >= len)
         sum -= src[i - len];
      out[i] = sum / MathMin(i + 1, len);
     }
  }

void CalcRSI(const double &c[], const int n, const int len, double &out[])
  {
   ArrayResize(out, n);
   double ag = 0, al = 0;
   for(int i = 0; i < n; i++)
     {
      if(i == 0)
        {
         out[i] = 50.0;
         continue;
        }
      double ch = c[i] - c[i - 1];
      double g = (ch > 0) ? ch : 0.0;
      double d = (ch < 0) ? -ch : 0.0;
      if(i <= len)
        {
         ag += g;
         al += d;
         if(i < len)
           {
            out[i] = 50.0;
            continue;
           }
         ag /= len;
         al /= len;
        }
      else
        {
         ag = (ag * (len - 1) + g) / len;
         al = (al * (len - 1) + d) / len;
        }
      out[i] = (al == 0.0) ? 100.0 : 100.0 - 100.0 / (1.0 + ag / al);
     }
  }

void CalcBands(const double &src[], const int n, const int len, const double k, double &mid[], double &up[], double &lo[])
  {
   CalcSMA(src, n, len, mid);
   ArrayResize(up, n);
   ArrayResize(lo, n);
   for(int i = 0; i < n; i++)
     {
      int cnt = MathMin(i + 1, len);
      double ss = 0;
      for(int j = i - cnt + 1; j <= i; j++)
         ss += (src[j] - mid[i]) * (src[j] - mid[i]);
      double sd = MathSqrt(ss / cnt);
      up[i] = mid[i] + k * sd;
      lo[i] = mid[i] - k * sd;
     }
  }

//--- index of the last bar that had CLOSED at time `at` (-1 if none)
int LastClosed(const datetime &t[], const int n, const int secs, const datetime at)
  {
   int lo = 0, hi = n - 1, ans = -1;
   while(lo <= hi)
     {
      int mid = (lo + hi) / 2;
      if(t[mid] + secs <= at)
        {
         ans = mid;
         lo = mid + 1;
        }
      else
         hi = mid - 1;
     }
   return ans;
  }

bool LoadTf(const ENUM_TIMEFRAMES tf, const int count, datetime &t[], double &o[], double &h[], double &l[], double &c[], int &n)
  {
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int got = CopyRates(_Symbol, tf, 0, count, r);
   if(got < 30)
      return false;
   n = got;
   ArrayResize(t, n);
   ArrayResize(o, n);
   ArrayResize(h, n);
   ArrayResize(l, n);
   ArrayResize(c, n);
   for(int i = 0; i < n; i++)
     {
      t[i] = r[i].time;
      o[i] = r[i].open;
      h[i] = r[i].high;
      l[i] = r[i].low;
      c[i] = r[i].close;
     }
   return true;
  }

int BarsNeeded(const ENUM_TIMEFRAMES tf, const int warmup)
  {
   int ps = PeriodSeconds(tf);
   if(ps <= 0)
      ps = 60;
   return (int)((long)InpLookbackBars * PeriodSeconds(_Period) / ps) + warmup + 10;
  }

//+------------------------------------------------------------------+
//| H1: EMA regime + velocity shock                                  |
//+------------------------------------------------------------------+
bool RefreshMacro()
  {
   datetime stamp = iTime(_Symbol, InpTfMacro, 0);
   if(stamp == 0)
      return false;
   if(stamp == gMacStamp && gMacN > 0)
      return true;
   if(!LoadTf(InpTfMacro, BarsNeeded(InpTfMacro, InpMacroSlowEma * 5), gMacT, gMacO, gMacH, gMacL, gMacC, gMacN))
      return false;
   CalcEMA(gMacC, gMacN, InpMacroFastEma, gMacEmaF);
   CalcEMA(gMacC, gMacN, InpMacroSlowEma, gMacEmaS);
   CalcATR(gMacH, gMacL, gMacC, gMacN, 14, gMacAtr);
   double atrAvg[];
   CalcSMA(gMacAtr, gMacN, 50, atrAvg);
   ArrayResize(gMacReg, gMacN);
   ArrayResize(gMacShock, gMacN);
   int lastIdx = -100000, lastDir = 0;
   for(int j = 0; j < gMacN; j++)
     {
      double c = gMacC[j], f = gMacEmaF[j], s = gMacEmaS[j];
      gMacReg[j] = (c > f && f > s) ? 1 : ((c < f && f < s) ? -1 : 0);
      bool shock = (j >= 50 && gMacAtr[j] > InpShockAtrMult * atrAvg[j]) ||
                   (j >= 1 && (gMacH[j] - gMacL[j]) > InpShockRangeMult * gMacAtr[j - 1]);
      if(shock)
        {
         lastIdx = j;
         lastDir = (gMacC[j] >= gMacO[j]) ? 1 : -1;
        }
      gMacShock[j] = (j - lastIdx < InpShockHoldBars) ? lastDir : 0;
     }
   gMacStamp = stamp;
   return true;
  }

//+------------------------------------------------------------------+
//| M15: BOS / CHoCH structure + EMA channel                         |
//+------------------------------------------------------------------+
bool RefreshStruct()
  {
   datetime stamp = iTime(_Symbol, InpTfStruct, 0);
   if(stamp == 0)
      return false;
   if(stamp == gStrStamp && gStrN > 0)
      return true;
   if(!LoadTf(InpTfStruct, BarsNeeded(InpTfStruct, InpStructSlowEma * 6), gStrT, gStrO, gStrH, gStrL, gStrC, gStrN))
      return false;
   CalcEMA(gStrC, gStrN, InpStructFastEma, gStrEmaF);
   CalcEMA(gStrC, gStrN, InpStructSlowEma, gStrEmaS);
   ArrayResize(gStrStruct, gStrN);
   ArrayResize(gStrEvent, gStrN);
   ArrayResize(gStrBias, gStrN);
   ArrayResize(gStrSwH, gStrN);
   ArrayResize(gStrSwL, gStrN);
   int L = MathMax(1, InpSwingLen);
   double swH = 0, swL = 0;
   bool hBroken = true, lBroken = true;
   int st = 0, lastEv = 0;
   for(int j = 0; j < gStrN; j++)
     {
      // a pivot centred L bars back is confirmed now (uses bars up to j only)
      int c = j - L;
      if(c - L >= 0)
        {
         bool ph = true, pl = true;
         for(int k = c - L; k <= c + L; k++)
           {
            if(k == c)
               continue;
            if(gStrH[k] > gStrH[c])
               ph = false;
            if(gStrL[k] < gStrL[c])
               pl = false;
           }
         if(ph)
           {
            swH = gStrH[c];
            hBroken = false;
           }
         if(pl)
           {
            swL = gStrL[c];
            lBroken = false;
           }
        }
      if(!hBroken && gStrC[j] > swH)
        {
         lastEv = (st == -1) ? 2 : 1;
         st = 1;
         hBroken = true;
        }
      else
         if(!lBroken && gStrC[j] < swL)
           {
            lastEv = (st == 1) ? -2 : -1;
            st = -1;
            lBroken = true;
           }
      bool up = gStrEmaF[j] > gStrEmaS[j];
      gStrStruct[j] = st;
      gStrEvent[j]  = lastEv;
      gStrSwH[j]    = swH;
      gStrSwL[j]    = swL;
      gStrBias[j]   = (st == 1 && up) ? 1 : ((st == -1 && !up) ? -1 : 0);
     }
   gStrStamp = stamp;
   return true;
  }

//+------------------------------------------------------------------+
//| M5: ATR, RSI and its standard-deviation bands                    |
//+------------------------------------------------------------------+
bool RefreshZoneTf()
  {
   datetime stamp = iTime(_Symbol, InpTfZone, 0);
   if(stamp == 0)
      return false;
   if(stamp == gZnStamp && gZnN > 0)
      return true;
   if(!LoadTf(InpTfZone, BarsNeeded(InpTfZone, InpRsiBandLen * 4 + InpZoneMaxAge), gZnT, gZnO, gZnH, gZnL, gZnC, gZnN))
      return false;
   CalcATR(gZnH, gZnL, gZnC, gZnN, 14, gZnAtr);
   CalcRSI(gZnC, gZnN, InpRsiLen, gZnRsi);
   CalcBands(gZnRsi, gZnN, InpRsiBandLen, InpRsiBandK, gZnRsiMid, gZnRsiUp, gZnRsiLo);
   gZnStamp = stamp;
   return true;
  }

//+------------------------------------------------------------------+
//| M5 zones: rebuilt from the last InpZoneMaxAge closed M5 bars     |
//+------------------------------------------------------------------+
void AddZone(const int dir, const int kind, const double top, const double bottom, const datetime born)
  {
   if(top <= bottom)
      return;
   if(gZoneN >= 30)
     {
      for(int z = 1; z < gZoneN; z++)
         gZones[z - 1] = gZones[z];
      gZoneN--;
     }
   ArrayResize(gZones, gZoneN + 1, 32);
   gZones[gZoneN].dir    = dir;
   gZones[gZoneN].kind   = kind;
   gZones[gZoneN].top    = top;
   gZones[gZoneN].bottom = bottom;
   gZones[gZoneN].born   = born;
   gZoneN++;
  }

void RemoveZone(const int idx)
  {
   for(int z = idx + 1; z < gZoneN; z++)
      gZones[z - 1] = gZones[z];
   gZoneN--;
   ArrayResize(gZones, gZoneN, 32);
  }

void BuildZones(const int zj)
  {
   gZoneN = 0;
   ArrayResize(gZones, 0, 32);
   int zsec = PeriodSeconds(InpTfZone);
   int from = MathMax(3, zj - InpZoneMaxAge);
   for(int j = from; j <= zj; j++)
     {
      // a close through the far side mitigates the zone
      for(int z = gZoneN - 1; z >= 0; z--)
        {
         bool dead = (gZones[z].dir == 1 && gZnC[j] < gZones[z].bottom) ||
                     (gZones[z].dir == -1 && gZnC[j] > gZones[z].top);
         if(dead)
            RemoveZone(z);
        }
      double a = gZnAtr[j];
      datetime born = gZnT[j] + zsec;
      // fair value gaps
      if(gZnL[j] > gZnH[j - 2] && gZnL[j] - gZnH[j - 2] >= InpFvgMinAtr * a)
         AddZone(1, 1, gZnL[j], gZnH[j - 2], born);
      if(gZnH[j] < gZnL[j - 2] && gZnL[j - 2] - gZnH[j] >= InpFvgMinAtr * a)
         AddZone(-1, 1, gZnL[j - 2], gZnH[j], born);
      // order blocks: last opposite candle before a displacement candle
      double body = MathAbs(gZnC[j] - gZnO[j]);
      if(body >= InpObDispAtr * a)
        {
         bool bullDisp = gZnC[j] > gZnO[j];
         for(int k = j - 1; k >= MathMax(0, j - 3); k--)
           {
            if(bullDisp && gZnC[k] < gZnO[k])
              {
               AddZone(1, 2, gZnH[k], gZnL[k], born);
               break;
              }
            if(!bullDisp && gZnC[k] > gZnO[k])
              {
               AddZone(-1, 2, gZnH[k], gZnL[k], born);
               break;
              }
           }
        }
     }
  }

bool ZoneTouch(const int dir, const double lo, const double hi)
  {
   for(int z = 0; z < gZoneN; z++)
      if(gZones[z].dir == dir && lo <= gZones[z].top && hi >= gZones[z].bottom)
         return true;
   return false;
  }

int ZoneCount(const int dir)
  {
   int n = 0;
   for(int z = 0; z < gZoneN; z++)
      if(gZones[z].dir == dir)
         n++;
   return n;
  }

//+------------------------------------------------------------------+
//| Higher-timeframe context using only bars closed by `at`          |
//+------------------------------------------------------------------+
bool GetContext(const datetime at, Ctx &c)
  {
   c.ok = false;
   int hj = LastClosed(gMacT, gMacN, PeriodSeconds(InpTfMacro), at);
   int sj = LastClosed(gStrT, gStrN, PeriodSeconds(InpTfStruct), at);
   int zj = LastClosed(gZnT, gZnN, PeriodSeconds(InpTfZone), at);
   if(hj < 1 || sj < 1 || zj < 3)
      return false;
   c.regime   = gMacReg[hj];
   c.shock    = gMacShock[hj];
   c.atrMacro = gMacAtr[hj];
   c.struct15 = gStrStruct[sj];
   c.event15  = gStrEvent[sj];
   c.emaUp15  = gStrEmaF[sj] > gStrEmaS[sj];
   c.bias15   = gStrBias[sj];
   c.swH      = gStrSwH[sj];
   c.swL      = gStrSwL[sj];
   c.rsi      = gZnRsi[zj];
   c.rsiMid   = gZnRsiMid[zj];
   c.rsiUp    = gZnRsiUp[zj];
   c.rsiLo    = gZnRsiLo[zj];
   // momentum turning in our favour and not yet stretched past its own band
   c.m5Long   = gZnRsi[zj] > gZnRsi[zj - 1] && gZnRsi[zj] < gZnRsiUp[zj];
   c.m5Short  = gZnRsi[zj] < gZnRsi[zj - 1] && gZnRsi[zj] > gZnRsiLo[zj];
   BuildZones(zj);
   c.ok = true;
   return true;
  }

//+------------------------------------------------------------------+
//| Clock and sessions                                               |
//+------------------------------------------------------------------+
bool IsUsDst(const datetime t)
  {
   MqlDateTime d;
   TimeToStruct(t, d);
   if(d.mon < 3 || d.mon > 11)
      return false;
   if(d.mon > 3 && d.mon < 11)
      return true;
   int lastSunday = d.day - d.day_of_week;
   if(d.mon == 3)
      return lastSunday >= 8;   // on or after the second Sunday of March
   return lastSunday < 1;       // before the first Sunday of November
  }

int OffsetAt(const datetime serverTime)
  {
   if(InpUtcMode == UTC_FIXED)
      return InpUtcOffsetHours * 3600;
   if(gTester)
      return (IsUsDst(serverTime) ? 3 : 2) * 3600;
   return gUtcOffset;
  }

void UpdateUtcOffset()
  {
   if(gTester)
      return;
   long d = (long)TimeTradeServer() - (long)TimeGMT();
   gUtcOffset = (int)(MathRound(d / 1800.0) * 1800);
  }

int UtcMinutes(const datetime serverTime)
  {
   long u = (long)serverTime - OffsetAt(serverTime);
   return (int)((u % 86400) / 60);
  }

// 1 London open, 2 NY / US, 3 Asian, 0 between sessions
int SessionOf(const datetime serverTime)
  {
   int m = UtcMinutes(serverTime);
   if(m >= 420 && m < 600)
      return 1;
   if(m >= 750 && m < 960)
      return 2;
   if(m < 420)
      return 3;
   return 0;
  }

bool SessionOK(const datetime serverTime)
  {
   if(!InpUseSessionFilter)
      return true;
   int s = SessionOf(serverTime);
   return (s == 1 && InpTradeLondon) || (s == 2 && InpTradeNY);
  }

string SessionName(const int s)
  {
   if(s == 1)
      return "London Open";
   if(s == 2)
      return "NY / US";
   if(s == 3)
      return "Asian";
   return "Between sessions";
  }

//+------------------------------------------------------------------+
//| Money and lot sizing from the broker's own contract specs        |
//+------------------------------------------------------------------+
void UpdateVpu()
  {
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tv <= 0)
      tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tv > 0 && ts > 0)
      gVpu = tv / ts;
   else
     {
      double cs = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
      gVpu = (cs > 0) ? cs : 100.0;
     }
  }

double LotStep()
  {
   double s = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   return (s > 0) ? s : 0.01;
  }

double MinLot()
  {
   double m = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   return (m > 0) ? m : 0.01;
  }

double NormLots(const double lots)
  {
   double step = LotStep();
   double v = MathFloor(lots / step + 1e-9) * step;
   double mx = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(mx > 0 && v > mx)
      v = mx;
   return NormalizeDouble(v, 2);
  }

double CalcLots(const double riskMoney, const double slDist, bool &capped)
  {
   capped = false;
   if(InpSizing == SIZE_FIXED_LOTS)
      return MathMax(MinLot(), NormLots(InpFixedLots));
   double perLot = slDist * gVpu + InpCommission;
   if(perLot <= 0)
      return MinLot();
   double lots = NormLots(riskMoney / perLot);
   if(lots < MinLot())
     {
      lots = MinLot();
      capped = true;
     }
   return lots;
  }

double BarSpread(const int sp)
  {
   if(InpUseBrokerSpread && sp > 0)
      return sp * _Point;
   return InpFixedSpread;
  }

//+------------------------------------------------------------------+
//| Alerts                                                           |
//+------------------------------------------------------------------+
void Notify(const string msg)
  {
   if(gTester)
      return;
   if(InpPopup)
      Alert(msg);
   else
      Print(msg);
   if(InpPush)
      SendNotification(msg);
  }

string Px(const double p)
  {
   return DoubleToString(p, _Digits);
  }

string Money(const double v)
  {
   return (v < 0 ? "-$" : "$") + DoubleToString(MathAbs(v), 2);
  }

string TfName(const ENUM_TIMEFRAMES tf)
  {
   return StringSubstr(EnumToString(tf), 7);   // PERIOD_H1 -> H1
  }

string WhyName(const int w)
  {
   if(w == 1)
      return "Engulfing";
   if(w == 2)
      return "Rejection pin";
   if(w == 3)
      return "Inside-bar break";
   if(w == 4)
      return "Liquidity sweep";
   return "-";
  }

//+------------------------------------------------------------------+
//| Trade simulation                                                 |
//+------------------------------------------------------------------+
void CloseTrade(const int k, const double px, const datetime t, const int bar)
  {
   int d = gTrades[k].dir;
   gTrades[k].pnl   += (px - gTrades[k].entry) * d * gTrades[k].remain * gVpu;
   gTrades[k].remain = 0;
   gTrades[k].exitPx = px;
   gTrades[k].tOut   = t;
   gTrades[k].open   = false;
   gInTrade     = false;
   gCur         = -1;
   gLastExitBar = bar;

   double pnl = gTrades[k].pnl;
   gEquity += pnl;
   if(gEquity > gPeak)
      gPeak = gEquity;
   if(gPeak > 0)
      gMaxDD = MathMax(gMaxDD, (gPeak - gEquity) / gPeak * 100.0);
   double riskMoney = gTrades[k].lots * gTrades[k].R * gVpu;
   if(riskMoney > 0)
      gSumR += pnl / riskMoney;
   if(pnl > 0)
     {
      gWins++;
      gGrossW += pnl;
     }
   else
     {
      gLosses++;
      gGrossL += -pnl;
     }
  }

void TakePartial(const int k, const double px)
  {
   double leg = NormLots(gTrades[k].lots * InpPartialPct / 100.0);
   // a partial close is only possible when both halves are at least the minimum lot
   if(leg < MinLot() || gTrades[k].lots - leg < MinLot() - 1e-9)
      leg = 0;
   gTrades[k].pnl    += (px - gTrades[k].entry) * gTrades[k].dir * leg * gVpu;
   gTrades[k].remain -= leg;
   gTrades[k].partial = true;
   if(gTrades[k].dir == 1)
      gTrades[k].sl = MathMax(gTrades[k].sl, gTrades[k].entry);
   else
      gTrades[k].sl = MathMin(gTrades[k].sl, gTrades[k].entry);
  }

// Walk the bar along its likely path: bullish bars O-L-H-C, bearish O-H-L-C.
void ManageTrade(const int i, const datetime t, const double o, const double h, const double l, const double c,
                 const double spr, const double atr, const int bias15)
  {
   int k = gCur;
   int d = gTrades[k].dir;
   double slip = InpSlippagePts * _Point;
   double path[4];
   path[0] = o;
   if(c >= o)
     {
      path[1] = l;
      path[2] = h;
     }
   else
     {
      path[1] = h;
      path[2] = l;
     }
   path[3] = c;
   for(int p = 0; p < 4 && gInTrade; p++)
     {
      // longs exit on the bid (chart price), shorts on the ask
      double px = (d == 1) ? path[p] : path[p] + spr;
      bool stop = (d == 1) ? px <= gTrades[k].sl : px >= gTrades[k].sl;
      if(stop)
        {
         double fill = (p == 0) ? px : gTrades[k].sl;   // a gap through the stop fills at the open
         CloseTrade(k, fill - d * slip, t, i);
         return;
        }
      if(!gTrades[k].partial && ((d == 1 && px >= gTrades[k].tp1) || (d == -1 && px <= gTrades[k].tp1)))
         TakePartial(k, gTrades[k].tp1);
      if(gTrades[k].partial && ((d == 1 && px >= gTrades[k].tp2) || (d == -1 && px <= gTrades[k].tp2)))
        {
         CloseTrade(k, gTrades[k].tp2, t, i);
         return;
        }
     }
   if(gInTrade && gTrades[k].partial)
     {
      if(bias15 == -d)
        {
         // M15 structure flipped against the runner: structural invalidation
         double px = (d == 1) ? c : c + spr;
         CloseTrade(k, px - d * slip, t + PeriodSeconds(_Period), i);
         return;
        }
      if(d == 1)
         gTrades[k].sl = MathMax(gTrades[k].sl, c - InpTrailAtr * atr);
      else
         gTrades[k].sl = MathMin(gTrades[k].sl, c + spr + InpTrailAtr * atr);
     }
  }

bool OpenTrade(const int dir, const int why, const int i, const datetime t, const double c,
               const double swingLo, const double swingHi, const double spr, const double atr)
  {
   double slip = InpSlippagePts * _Point;
   double entry, sl;
   if(dir == 1)
     {
      entry = c + spr + slip;                         // buy at the ask
      sl = swingLo - InpSlBufferAtr * atr;            // beyond the structural swing
      if(entry - sl < InpMinSlAtr * atr)
         sl = entry - InpAtrSlMult * atr;             // swing too close: ATR stop
     }
   else
     {
      entry = c - slip;                               // sell at the bid
      sl = swingHi + InpSlBufferAtr * atr + spr;      // stop triggers on the ask
      if(sl - entry < InpMinSlAtr * atr)
         sl = entry + InpAtrSlMult * atr;
     }
   double R = MathAbs(entry - sl);
   if(R <= 0 || R > InpMaxSlAtr * atr)
      return false;
   bool capped = false;
   double lots = CalcLots(gEquity * InpRiskPct / 100.0, R, capped);

   int k = gTradeN;
   gTradeN++;
   ArrayResize(gTrades, gTradeN, 256);
   gTrades[k].dir          = dir;
   gTrades[k].why          = why;
   gTrades[k].barIdx       = i;
   gTrades[k].tIn          = t;
   gTrades[k].tOut         = 0;
   gTrades[k].entry        = entry;
   gTrades[k].sl0          = sl;
   gTrades[k].sl           = sl;
   gTrades[k].tp1          = entry + dir * InpTp1R * R;
   gTrades[k].tp2          = entry + dir * InpTp2R * R;
   gTrades[k].R            = R;
   gTrades[k].lots         = lots;
   gTrades[k].remain       = lots;
   gTrades[k].partial      = false;
   gTrades[k].open         = true;
   gTrades[k].minLotCapped = capped;
   gTrades[k].pnl          = -InpCommission * lots;
   gTrades[k].exitPx       = 0;
   gInTrade = true;
   gCur = k;
   return true;
  }

//+------------------------------------------------------------------+
//| Process one CLOSED M1 bar, exactly once                          |
//+------------------------------------------------------------------+
void ProcessBar(const int i, const int rates_total, const datetime &time[], const double &open[], const double &high[],
                const double &low[], const double &close[], const long &tick_volume[], const int &spread[])
  {
   BuyBuf[i] = EMPTY_VALUE;
   SellBuf[i] = EMPTY_VALUE;
   EntryBuf[i] = EMPTY_VALUE;
   SlBuf[i] = EMPTY_VALUE;
   Tp1Buf[i] = EMPTY_VALUE;
   Tp2Buf[i] = EMPTY_VALUE;

   // M1 ATR(14), Wilder
   double tr = MathMax(high[i] - low[i], MathMax(MathAbs(high[i] - close[i - 1]), MathAbs(low[i] - close[i - 1])));
   AtrBuf[i] = (i <= gStart) ? tr : (AtrBuf[i - 1] * 13.0 + tr) / 14.0;
   // M1 tick-volume SMA(20)
   double vs = 0;
   int vn = 0;
   for(int vk = i; vk > i - 20 && vk >= 0; vk--)
     {
      vs += (double)tick_volume[vk];
      vn++;
     }
   VolBuf[i] = (vn > 0) ? vs / vn : 0;
   gLastAtr = AtrBuf[i];

   // Asian range of the current UTC day
   long u = (long)time[i] - OffsetAt(time[i]);
   long day = u / 86400;
   int mins = (int)((u % 86400) / 60);
   if(day != gAsianDay)
     {
      gAsianDay = day;
      gAsianHi = 0;
      gAsianLo = 0;
     }
   if(mins < 420)
     {
      if(gAsianHi == 0 || high[i] > gAsianHi)
         gAsianHi = high[i];
      if(gAsianLo == 0 || low[i] < gAsianLo)
         gAsianLo = low[i];
     }

   if(i < gStart + gWarm)
      return;

   datetime barClose = time[i] + PeriodSeconds(_Period);
   Ctx c;
   if(!GetContext(barClose, c))
      return;
   if(i == rates_total - 2)
      gNow = c;

   double spr = BarSpread(spread[i]);
   double atr = AtrBuf[i];

   if(gInTrade && i > gTrades[gCur].barIdx)
      ManageTrade(i, time[i], open[i], high[i], low[i], close[i], spr, atr, c.bias15);

   // ----- M1 microstructure -----
   double o = open[i], h = high[i], l = low[i], cl = close[i];
   double minPrev = low[i - 1], maxPrev = high[i - 1];
   for(int sk = 2; sk <= InpSweepLen; sk++)
     {
      minPrev = MathMin(minPrev, low[i - sk]);
      maxPrev = MathMax(maxPrev, high[i - sk]);
     }
   bool sweepL = l < minPrev && cl > minPrev;
   bool sweepS = h > maxPrev && cl < maxPrev;

   bool touchL = ZoneTouch(1, l, h);
   bool touchS = ZoneTouch(-1, l, h);
   if(touchL)
      gLastTouchL = i;
   if(touchS)
      gLastTouchS = i;

   double body = MathAbs(cl - o), rng = h - l;
   double pBody = MathAbs(close[i - 1] - open[i - 1]);
   bool volOk = (double)tick_volume[i] > InpVolMult * VolBuf[i - 1];
   bool engL = InpUseEngulf && cl > o && close[i - 1] < open[i - 1] && cl >= open[i - 1] && o <= close[i - 1] && body > pBody && volOk;
   bool engS = InpUseEngulf && cl < o && close[i - 1] > open[i - 1] && cl <= open[i - 1] && o >= close[i - 1] && body > pBody && volOk;

   double lw = MathMin(o, cl) - l, uw = h - MathMax(o, cl);
   bool pinL = InpUsePin && rng > 0 && lw >= 2.0 * body && lw >= 0.6 * rng && (sweepL || touchL);
   bool pinS = InpUsePin && rng > 0 && uw >= 2.0 * body && uw >= 0.6 * rng && (sweepS || touchS);

   bool inside = high[i - 1] <= high[i - 2] && low[i - 1] >= low[i - 2];
   bool ibL = InpUseInsideBreak && inside && cl > high[i - 2] && cl > o;
   bool ibS = InpUseInsideBreak && inside && cl < low[i - 2] && cl < o;

   bool swL = InpUseSweepTrigger && sweepL && cl > o;
   bool swS = InpUseSweepTrigger && sweepS && cl < o;

   int whyL = engL ? 1 : (pinL ? 2 : (ibL ? 3 : (swL ? 4 : 0)));
   int whyS = engS ? 1 : (pinS ? 2 : (ibS ? 3 : (swS ? 4 : 0)));

   // ----- confluence -----
   bool sess = SessionOK(time[i]);
   bool htfL = c.regime == 1 && c.shock != -1 && c.bias15 == 1 && c.m5Long;
   bool htfS = c.regime == -1 && c.shock != 1 && c.bias15 == -1 && c.m5Short;
   bool confL = InpMode == MODE_BALANCED || (i - gLastTouchL) < InpTouchBars || sweepL;
   bool confS = InpMode == MODE_BALANCED || (i - gLastTouchS) < InpTouchBars || sweepS;
   bool sigL = sess && htfL && whyL > 0 && confL;
   bool sigS = sess && htfS && whyS > 0 && confS;

   if(gInTrade || (i - gLastExitBar) <= InpCooldownBars || sigL == sigS)
      return;

   int dir = sigL ? 1 : -1;
   double swingLo = low[i], swingHi = high[i];
   for(int wk = 1; wk < InpSlLookback; wk++)
     {
      swingLo = MathMin(swingLo, low[i - wk]);
      swingHi = MathMax(swingHi, high[i - wk]);
     }
   if(!OpenTrade(dir, sigL ? whyL : whyS, i, barClose, cl, swingLo, swingHi, spr, atr))
      return;

   int ti = gCur;
   if(dir == 1)
      BuyBuf[i] = l - 0.3 * atr;
   else
      SellBuf[i] = h + 0.3 * atr;
   EntryBuf[i] = gTrades[ti].entry;
   SlBuf[i]    = gTrades[ti].sl0;
   Tp1Buf[i]   = gTrades[ti].tp1;
   Tp2Buf[i]   = gTrades[ti].tp2;

   gLastSignalText = StringFormat("%s SCALP EXECUTE: %s @ %s | SL: %s | TP1: %s | TP2: %s | Lots: %s",
                                  InpAlertTag, dir == 1 ? "BUY" : "SELL", Px(gTrades[ti].entry), Px(gTrades[ti].sl0),
                                  Px(gTrades[ti].tp1), Px(gTrades[ti].tp2), DoubleToString(gTrades[ti].lots, 2));
   if(gHistoryDone && InpExecAlerts && i == rates_total - 2)
      Notify(gLastSignalText);
  }

//+------------------------------------------------------------------+
void ResetState(const int rates_total)
  {
   ArrayInitialize(BuyBuf, EMPTY_VALUE);
   ArrayInitialize(SellBuf, EMPTY_VALUE);
   ArrayInitialize(EntryBuf, EMPTY_VALUE);
   ArrayInitialize(SlBuf, EMPTY_VALUE);
   ArrayInitialize(Tp1Buf, EMPTY_VALUE);
   ArrayInitialize(Tp2Buf, EMPTY_VALUE);
   ArrayInitialize(AtrBuf, 0.0);
   ArrayInitialize(VolBuf, 0.0);
   gWarm = MathMax(30, MathMax(InpSweepLen, InpSlLookback) + 5);
   gStart = MathMax(1, rates_total - 1 - MathMax(InpLookbackBars, gWarm + 10));
   gLastProcessed = gStart - 1;
   gLastProcessedTime = 0;
   gTradeN = 0;
   ArrayResize(gTrades, 0, 256);
   gInTrade = false;
   gCur = -1;
   gLastExitBar = -100000;
   gLastTouchL = -100000;
   gLastTouchS = -100000;
   gAsianDay = -1;
   gStartEq = (InpBacktestBalance > 0) ? InpBacktestBalance : AccountInfoDouble(ACCOUNT_BALANCE);
   if(gStartEq <= 0)
      gStartEq = 1000.0;
   gEquity = gStartEq;
   gPeak = gStartEq;
   gMaxDD = 0;
   gGrossW = 0;
   gGrossL = 0;
   gSumR = 0;
   gWins = 0;
   gLosses = 0;
   gHistoryDone = false;
   gNow.ok = false;
   gLastSignalText = "";
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   SetIndexBuffer(0, BuyBuf, INDICATOR_DATA);
   SetIndexBuffer(1, SellBuf, INDICATOR_DATA);
   SetIndexBuffer(2, EntryBuf, INDICATOR_DATA);
   SetIndexBuffer(3, SlBuf, INDICATOR_DATA);
   SetIndexBuffer(4, Tp1Buf, INDICATOR_DATA);
   SetIndexBuffer(5, Tp2Buf, INDICATOR_DATA);
   SetIndexBuffer(6, AtrBuf, INDICATOR_CALCULATIONS);
   SetIndexBuffer(7, VolBuf, INDICATOR_CALCULATIONS);
   PlotIndexSetInteger(0, PLOT_ARROW, 233);
   PlotIndexSetInteger(1, PLOT_ARROW, 234);
   PlotIndexSetInteger(0, PLOT_LINE_COLOR, InpBullColor);
   PlotIndexSetInteger(1, PLOT_LINE_COLOR, InpBearColor);
   for(int p = 0; p < 6; p++)
      PlotIndexSetDouble(p, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   IndicatorSetString(INDICATOR_SHORTNAME, "XAU M1 Scalper");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);

   gTester = (bool)MQLInfoInteger(MQL_TESTER);
   gDraw = !gTester || (bool)MQLInfoInteger(MQL_VISUAL_MODE);
   UpdateUtcOffset();
   if(gDraw)
      EventSetTimer(1);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   ObjectsDeleteAll(0, PFX);
   ChartRedraw();
  }

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
  {
   if(rates_total < 200)
      return 0;
   UpdateVpu();
   UpdateUtcOffset();

   bool reset = (prev_calculated == 0) || gLastProcessed < 0 || gLastProcessed > rates_total - 2 ||
                (gLastProcessedTime != 0 && time[gLastProcessed] != gLastProcessedTime);
   if(reset)
      ResetState(rates_total);

   if(!RefreshMacro() || !RefreshStruct() || !RefreshZoneTf())
      return 0;   // higher-timeframe history still loading: try again on the next tick

   bool newBars = false;
   for(int i = gLastProcessed + 1; i <= rates_total - 2; i++)
     {
      ProcessBar(i, rates_total, time, open, high, low, close, tick_volume, spread);
      gLastProcessed = i;
      gLastProcessedTime = time[i];
      newBars = true;
     }
   if(!gHistoryDone)
     {
      gHistoryDone = true;
      gStartTime = time[MathMin(gStart + gWarm, rates_total - 1)];
     }

   if(gDraw)
     {
      if(newBars)
         DrawChartObjects();
      CheckRadar();
      uint now = GetTickCount();
      if(newBars || now - gLastPaint > 250)
        {
         DrawPanel();
         gLastPaint = now;
        }
     }
   return rates_total;
  }

void OnTimer()
  {
   if(!gDraw || !gHistoryDone)
      return;
   CheckRadar();
   DrawPanel();
  }

//+------------------------------------------------------------------+
//| Radar: bar still open, HTF aligned, price at a reaction zone     |
//+------------------------------------------------------------------+
void CheckRadar()
  {
   gRadarText = "";
   if(!gNow.ok || gInTrade)
      return;
   datetime t0 = iTime(_Symbol, _Period, 0);
   if(t0 == 0)
      return;
   double lo = iLow(_Symbol, _Period, 0), hi = iHigh(_Symbol, _Period, 0);
   int li = iLowest(_Symbol, _Period, MODE_LOW, InpSweepLen, 1);
   int hiIdx = iHighest(_Symbol, _Period, MODE_HIGH, InpSweepLen, 1);
   if(li < 0 || hiIdx < 0)
      return;
   double minPrev = iLow(_Symbol, _Period, li), maxPrev = iHigh(_Symbol, _Period, hiIdx);
   bool sess = SessionOK(t0);
   bool zoneL = ZoneTouch(1, lo, hi) || lo < minPrev;
   bool zoneS = ZoneTouch(-1, lo, hi) || hi > maxPrev;
   bool htfL = gNow.regime == 1 && gNow.shock != -1 && gNow.bias15 == 1 && gNow.m5Long;
   bool htfS = gNow.regime == -1 && gNow.shock != 1 && gNow.bias15 == -1 && gNow.m5Short;
   string base = InpAlertTag + " SCALP RADAR: Setup forming on M1. HTF Confluence verified. Prepare for entry.";
   if(sess && htfL && zoneL)
     {
      gRadarText = "BUY setup forming";
      if(InpRadarAlerts && gRadarLongBar != t0)
        {
         gRadarLongBar = t0;
         Notify(base + " [BUY]");
        }
     }
   if(sess && htfS && zoneS)
     {
      gRadarText = (gRadarText == "") ? "SELL setup forming" : "Both sides at a zone";
      if(InpRadarAlerts && gRadarShortBar != t0)
        {
         gRadarShortBar = t0;
         Notify(base + " [SELL]");
        }
     }
  }

//+------------------------------------------------------------------+
//| Chart objects                                                    |
//+------------------------------------------------------------------+
void Seg(const string name, const datetime t1, const datetime t2, const double p, const color clr, const int style, const int width)
  {
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_TREND, 0, t1, p, t2, p);
   else
     {
      ObjectMove(0, name, 0, t1, p);
      ObjectMove(0, name, 1, t2, p);
     }
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, name, OBJPROP_RAY_LEFT, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
  }

void Txt(const string name, const datetime t, const double p, const string text, const color clr)
  {
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_TEXT, 0, t, p);
   else
      ObjectMove(0, name, 0, t, p);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, InpFontSize - 1);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }

void DrawChartObjects()
  {
   ObjectsDeleteAll(0, PFX + "T");
   ObjectsDeleteAll(0, PFX + "Z");
   ObjectsDeleteAll(0, PFX + "S");
   int ps = PeriodSeconds(_Period);
   datetime now = iTime(_Symbol, _Period, 0);

   // trades: past ones as short segments, the open one projected to the right
   int from = InpDrawHistory ? MathMax(0, gTradeN - InpMaxDrawnTrades) : MathMax(0, gTradeN - 1);
   for(int k = from; k < gTradeN; k++)
     {
      if(!InpDrawHistory && !gTrades[k].open)
         continue;
      string id = PFX + "T" + IntegerToString(k) + "_";
      datetime t1 = gTrades[k].tIn - ps;
      datetime t2 = gTrades[k].open ? now + 15 * ps : gTrades[k].tOut;
      if(t2 <= t1)
         t2 = t1 + ps;
      color dc = (gTrades[k].dir == 1) ? InpBullColor : InpBearColor;
      Seg(id + "E", t1, t2, gTrades[k].entry, dc, STYLE_SOLID, 1);
      Seg(id + "S", t1, t2, gTrades[k].open ? gTrades[k].sl : gTrades[k].sl0, InpSlColor, STYLE_SOLID, gTrades[k].open ? 2 : 1);
      Seg(id + "1", t1, t2, gTrades[k].tp1, InpGoodColor, STYLE_DOT, 1);
      Seg(id + "2", t1, t2, gTrades[k].tp2, InpGoodColor, STYLE_SOLID, gTrades[k].open ? 2 : 1);
      if(gTrades[k].open)
        {
         Txt(id + "LE", t2, gTrades[k].entry, " " + (gTrades[k].dir == 1 ? "BUY " : "SELL ") + Px(gTrades[k].entry), dc);
         Txt(id + "LS", t2, gTrades[k].sl, " SL " + Px(gTrades[k].sl), InpSlColor);
         Txt(id + "L1", t2, gTrades[k].tp1, " TP1 " + Px(gTrades[k].tp1), InpGoodColor);
         Txt(id + "L2", t2, gTrades[k].tp2, " TP2 " + Px(gTrades[k].tp2), InpGoodColor);
        }
      else
        {
         double r = (gTrades[k].lots * gTrades[k].R * gVpu > 0) ? gTrades[k].pnl / (gTrades[k].lots * gTrades[k].R * gVpu) : 0;
         Txt(id + "LR", gTrades[k].tOut, gTrades[k].exitPx, StringFormat(" %+.1fR", r), r > 0 ? InpGoodColor : InpSlColor);
        }
     }

   // active M5 zones as outlined boxes
   if(InpDrawZones)
     {
      for(int z = 0; z < gZoneN; z++)
        {
         string name = PFX + "Z" + IntegerToString(z);
         ObjectCreate(0, name, OBJ_RECTANGLE, 0, gZones[z].born, gZones[z].top, now + 5 * ps, gZones[z].bottom);
         ObjectSetInteger(0, name, OBJPROP_COLOR, gZones[z].dir == 1 ? InpBullColor : InpBearColor);
         ObjectSetInteger(0, name, OBJPROP_STYLE, gZones[z].kind == 1 ? STYLE_DOT : STYLE_DASH);
         ObjectSetInteger(0, name, OBJPROP_FILL, false);
         ObjectSetInteger(0, name, OBJPROP_BACK, true);
         ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
         ObjectSetString(0, name, OBJPROP_TOOLTIP, (gZones[z].dir == 1 ? "Bull " : "Bear ") + (gZones[z].kind == 1 ? "M5 FVG" : "M5 order block"));
        }
     }

   // London and NY session windows, last 3 days
   if(InpDrawSessions)
     {
      int off = OffsetAt(now);
      long dayUtc = ((long)now - off) / 86400 * 86400;
      int marks[4] = {420, 600, 750, 960};
      for(int d = 0; d < 3; d++)
         for(int m = 0; m < 4; m++)
           {
            datetime ts = (datetime)(dayUtc - (long)d * 86400 + (long)marks[m] * 60 + off);
            if(ts > now)
               continue;
            string name = PFX + "S" + IntegerToString(d) + "_" + IntegerToString(m);
            ObjectCreate(0, name, OBJ_VLINE, 0, ts, 0);
            ObjectSetInteger(0, name, OBJPROP_COLOR, InpDimColor);
            ObjectSetInteger(0, name, OBJPROP_STYLE, (m % 2 == 0) ? STYLE_DASH : STYLE_DOT);
            ObjectSetInteger(0, name, OBJPROP_BACK, true);
            ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
            ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
            string what = (m < 2) ? "London " : "NY ";
            ObjectSetString(0, name, OBJPROP_TOOLTIP, what + ((m % 2 == 0) ? "open" : "close"));
           }
     }
   ChartRedraw();
  }

//+------------------------------------------------------------------+
//| Dashboard panel                                                  |
//+------------------------------------------------------------------+
int gRow = 0;
int RowH()
  {
   return (int)MathRound(InpFontSize * 1.9);
  }

void Lbl(const string name, const int x, const int y, const string text, const color clr)
  {
   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, false);
      ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, InpFontSize);
     }
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
  }

void Row(const string key, const string val, const color vc)
  {
   int y = InpPanelY + 10 + gRow * RowH();
   Lbl(PFX + "PK" + IntegerToString(gRow), InpPanelX + 10, y, key, InpDimColor);
   Lbl(PFX + "PV" + IntegerToString(gRow), InpPanelX + 10 + InpFontSize * 13, y, val, vc);
   gRow++;
  }

void Head(const string title)
  {
   int y = InpPanelY + 10 + gRow * RowH();
   Lbl(PFX + "PK" + IntegerToString(gRow), InpPanelX + 10, y, title, InpWarnColor);
   Lbl(PFX + "PV" + IntegerToString(gRow), InpPanelX + 10 + InpFontSize * 13, y, " ", InpTextColor);
   gRow++;
  }

color DirColor(const int d)
  {
   return (d > 0) ? InpBullColor : ((d < 0) ? InpBearColor : InpDimColor);
  }

string DirWord(const int d)
  {
   return (d > 0) ? "BULL" : ((d < 0) ? "BEAR" : "NEUTRAL");
  }

void DrawPanel()
  {
   gRow = 0;
   int width = InpFontSize * 40;
   string bg = PFX + "PBG";
   if(ObjectFind(0, bg) < 0)
     {
      ObjectCreate(0, bg, OBJ_RECTANGLE_LABEL, 0, 0, 0);
      ObjectSetInteger(0, bg, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, bg, OBJPROP_BORDER_TYPE, BORDER_FLAT);
      ObjectSetInteger(0, bg, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, bg, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, bg, OBJPROP_BACK, false);
     }
   ObjectSetInteger(0, bg, OBJPROP_XDISTANCE, InpPanelX);
   ObjectSetInteger(0, bg, OBJPROP_YDISTANCE, InpPanelY);
   ObjectSetInteger(0, bg, OBJPROP_XSIZE, width);
   ObjectSetInteger(0, bg, OBJPROP_BGCOLOR, InpPanelBg);
   ObjectSetInteger(0, bg, OBJPROP_COLOR, InpPanelBorder);

   MqlTick tk;
   bool haveTick = SymbolInfoTick(_Symbol, tk);
   datetime srv = haveTick ? (datetime)tk.time : TimeCurrent();
   double sprNow = haveTick ? tk.ask - tk.bid : 0;

   // --- feed ---
   Head("XAU M1 SCALPER  " + _Symbol + "  " + TfName(_Period));
   Row("Data", AccountInfoString(ACCOUNT_SERVER), InpTextColor);
   Row("Feed check", "Your broker's own quotes", InpGoodColor);
   bool goldName = StringFind(_Symbol, "XAU") >= 0 || StringFind(_Symbol, "GOLD") >= 0 || StringFind(_Symbol, "Gold") >= 0;
   if(!goldName)
      Row("Warning", "Symbol does not look like gold", InpWarnColor);
   if(_Period != PERIOD_M1)
      Row("Warning", "Built for M1: switch the chart to M1", InpWarnColor);
   double fixedSpr = InpFixedSpread;
   Row("Spread now", Px(sprNow) + "  (" + IntegerToString((int)MathRound(sprNow / _Point)) + " pts)",
       sprNow > 2.0 * fixedSpr ? InpWarnColor : InpTextColor);

   int s = SessionOf(srv);
   int um = UtcMinutes(srv);
   string clock = StringFormat("%02d:%02d UTC  ", um / 60, um % 60) + SessionName(s);
   Row("Session", clock, SessionOK(srv) ? InpGoodColor : InpWarnColor);
   if(gAsianHi > 0 && gAsianLo > 0)
     {
      double ar = gAsianHi - gAsianLo;
      bool contracted = gNow.ok && gNow.atrMacro > 0 && ar < 2.0 * gNow.atrMacro;
      Row("Asian range", Px(ar) + (contracted ? "  contracted, expect a breakout" : "  normal"),
          contracted ? InpWarnColor : InpTextColor);
     }

   // --- confluence ---
   Head("CONFLUENCE  (closed bars only)");
   if(!gNow.ok)
      Row("Status", "Loading higher-timeframe history...", InpWarnColor);
   else
     {
      Row(TfName(InpTfMacro) + " regime", DirWord(gNow.regime) + StringFormat("  EMA%d/%d", InpMacroFastEma, InpMacroSlowEma), DirColor(gNow.regime));
      if(gNow.shock != 0)
         Row(TfName(InpTfMacro) + " shock", (gNow.shock > 0 ? "UP shock: shorts blocked" : "DOWN shock: longs blocked"), InpWarnColor);
      else
         Row(TfName(InpTfMacro) + " shock", "None", InpTextColor);
      string ev = (gNow.event15 == 1) ? "BOS up" : (gNow.event15 == 2) ? "CHoCH up" :
                  (gNow.event15 == -1) ? "BOS down" : (gNow.event15 == -2) ? "CHoCH down" : "no break yet";
      Row(TfName(InpTfStruct) + " structure", DirWord(gNow.struct15) + "  last " + ev, DirColor(gNow.struct15));
      Row(TfName(InpTfStruct) + " EMA", StringFormat("%d %s %d", InpStructFastEma, gNow.emaUp15 ? ">" : "<", InpStructSlowEma),
          gNow.emaUp15 ? InpBullColor : InpBearColor);
      string mom = gNow.m5Long ? "  rising" : (gNow.m5Short ? "  falling" : "  stretched");
      Row(TfName(InpTfZone) + " RSI", StringFormat("%.1f  band %.1f-%.1f", gNow.rsi, gNow.rsiLo, gNow.rsiUp) + mom,
          gNow.m5Long ? InpBullColor : (gNow.m5Short ? InpBearColor : InpDimColor));
      Row(TfName(InpTfZone) + " zones", StringFormat("%d demand / %d supply", ZoneCount(1), ZoneCount(-1)), InpTextColor);
      bool okL = gNow.regime == 1 && gNow.shock != -1 && gNow.bias15 == 1;
      bool okS = gNow.regime == -1 && gNow.shock != 1 && gNow.bias15 == -1;
      string bias = okL ? "LONGS ONLY" : (okS ? "SHORTS ONLY" : "STAND ASIDE");
      Row("Bias", bias, okL ? InpBullColor : (okS ? InpBearColor : InpDimColor));
      string st = gInTrade ? "In a trade: manage it" : (gRadarText != "" ? "RADAR: " + gRadarText : "Waiting for an M1 trigger");
      Row("M1 status", st, gRadarText != "" ? InpWarnColor : InpTextColor);
     }

   // --- trade ---
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskMoney = bal * InpRiskPct / 100.0;
   if(gInTrade && gCur >= 0)
     {
      int k = gCur;
      int d = gTrades[k].dir;
      bool capped = false;
      double liveLots = CalcLots(riskMoney, gTrades[k].R, capped);
      Head("ACTIVE SIGNAL  " + TimeToString(gTrades[k].tIn, TIME_MINUTES));
      Row(d == 1 ? "BUY" : "SELL", "@ " + Px(gTrades[k].entry) + "  " + WhyName(gTrades[k].why), DirColor(d));
      Row("Stop loss", Px(gTrades[k].sl) + StringFormat("  (%s%s)", d == 1 ? "-" : "+", Px(MathAbs(gTrades[k].entry - gTrades[k].sl))) +
          (gTrades[k].partial ? "  at BE/trail" : ""), InpSlColor);
      Row("TP1 " + DoubleToString(InpTp1R, 1) + "R", Px(gTrades[k].tp1) + StringFormat("  close %.0f%%", InpPartialPct) +
          (gTrades[k].partial ? "  HIT" : ""), InpGoodColor);
      Row("TP2 " + DoubleToString(InpTp2R, 1) + "R", Px(gTrades[k].tp2), InpGoodColor);
      double riskNow = liveLots * gTrades[k].R * gVpu + InpCommission * liveLots;
      Row("Your size", DoubleToString(liveLots, 2) + " lots  risk " + Money(riskNow), capped ? InpWarnColor : InpTextColor);
      if(capped)
         Row("Warning", "Min lot risks more than " + DoubleToString(InpRiskPct, 1) + "%", InpWarnColor);
     }
   else
     {
      Head("NEXT TRADE SIZE");
      double estR = InpAtrSlMult * gLastAtr;
      bool capped = false;
      double lots = (estR > 0) ? CalcLots(riskMoney, estR, capped) : 0;
      Row("Balance", Money(bal) + "  risk " + DoubleToString(InpRiskPct, 1) + "% = " + Money(riskMoney), InpTextColor);
      Row("Typical stop", Px(estR) + "  -> " + DoubleToString(lots, 2) + " lots", capped ? InpWarnColor : InpTextColor);
      if(capped)
         Row("Warning", "Min lot risks more than " + DoubleToString(InpRiskPct, 1) + "%", InpWarnColor);
     }

   // --- backtest ---
   int n = gWins + gLosses;
   Head("BACKTEST  since " + TimeToString(gStartTime, TIME_DATE | TIME_MINUTES));
   if(n == 0)
      Row("Trades", "None yet in this window", InpDimColor);
   else
     {
      double wr = 100.0 * gWins / n;
      double pf = (gGrossL > 0) ? gGrossW / gGrossL : 0;
      double aw = (gWins > 0) ? gGrossW / gWins : 0;
      double al = (gLosses > 0) ? gGrossL / gLosses : 0;
      Row("Trades / win", StringFormat("%d  /  %.1f%%", n, wr), InpTextColor);
      Row("Profit factor", (gGrossL > 0) ? DoubleToString(pf, 2) : "no losses", pf >= 1.0 || gGrossL == 0 ? InpGoodColor : InpSlColor);
      Row("Max drawdown", StringFormat("%.2f%%", gMaxDD), gMaxDD > 10 ? InpWarnColor : InpTextColor);
      Row("Avg win/loss", Money(aw) + " / " + Money(al) + ((al > 0) ? StringFormat("  (%.2f)", aw / al) : ""), InpTextColor);
      double net = gEquity - gStartEq;
      Row("Net PnL", Money(net) + StringFormat("  %+.1fR", gSumR), net >= 0 ? InpGoodColor : InpSlColor);
     }
   Row("Costs", "spread " + (InpUseBrokerSpread ? "per bar" : Px(InpFixedSpread)) + ", " + Money(InpCommission) + "/lot, " +
       IntegerToString(InpSlippagePts) + " pt slip", InpDimColor);

   // clear rows left over from a longer previous paint
   for(int r = gRow; r < gRow + 6; r++)
     {
      ObjectDelete(0, PFX + "PK" + IntegerToString(r));
      ObjectDelete(0, PFX + "PV" + IntegerToString(r));
     }
   ObjectSetInteger(0, bg, OBJPROP_YSIZE, 20 + gRow * RowH());
   ChartRedraw();
  }
//+------------------------------------------------------------------+
