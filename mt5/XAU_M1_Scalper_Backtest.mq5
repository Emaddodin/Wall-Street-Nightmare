//+------------------------------------------------------------------+
//|                                      XAU_M1_Scalper_Backtest.mq5 |
//|   Strategy Tester runner for the XAU_M1_Scalper indicator.       |
//|                                                                  |
//|   Trades the indicator's closed-bar signals in the MT5 Strategy  |
//|   Tester so you get the full MT5 report on your broker's real    |
//|   tick history: market entry, SL, 50% off at TP1, rest to        |
//|   breakeven, ATR trail on the runner, hard TP2.                  |
//|                                                                  |
//|   It refuses to run on a live or demo chart: you trade by hand.  |
//+------------------------------------------------------------------+
#property copyright   "Wall-Street-Nightmare"
#property version     "1.00"
#property description "Backtests XAU_M1_Scalper in the Strategy Tester on your broker's real ticks."
#property description "Only runs inside the Strategy Tester. It never trades a live or demo account."
#property tester_indicator "XAU_M1_Scalper.ex5"

#include <Trade\Trade.mqh>

input group "Position sizing"
input double InpRiskPct      = 1.0;     // Risk per trade (% of balance)
input double InpFixedLots    = 0.0;     // Fixed lots (> 0 overrides risk %)

input group "Trade management"
input double InpPartialPct   = 50.0;    // Close % at TP1, rest to breakeven
input double InpTrailAtr     = 1.0;     // Runner trailing stop after TP1 (x M1 ATR 14)
input int    InpMaxSpreadPts = 0;       // Skip entries when spread > points (0 = off)
input ulong  InpMagic        = 26100101; // Magic number

CTrade   trade;
int      hSig = INVALID_HANDLE;
int      hAtr = INVALID_HANDLE;
datetime lastBar = 0;
ulong    posTicket = 0;
bool     partialDone = false;
double   tp1Price = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(!MQLInfoInteger(MQL_TESTER))
     {
      Alert("XAU_M1_Scalper_Backtest only runs in the Strategy Tester. Use the XAU_M1_Scalper indicator on your live chart.");
      return INIT_FAILED;
     }
   if(_Period != PERIOD_M1)
     {
      Print("Set the Strategy Tester timeframe to M1.");
      return INIT_PARAMETERS_INCORRECT;
     }
   hSig = iCustom(_Symbol, PERIOD_M1, "XAU_M1_Scalper");
   hAtr = iATR(_Symbol, PERIOD_M1, 14);
   if(hSig == INVALID_HANDLE || hAtr == INVALID_HANDLE)
     {
      Print("Could not load XAU_M1_Scalper. Compile it into MQL5\\Indicators first.");
      return INIT_FAILED;
     }
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(30);
   trade.SetTypeFillingBySymbol(_Symbol);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(hSig != INVALID_HANDLE)
      IndicatorRelease(hSig);
   if(hAtr != INVALID_HANDLE)
      IndicatorRelease(hAtr);
  }

//+------------------------------------------------------------------+
bool SelectOurPosition()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && (ulong)PositionGetInteger(POSITION_MAGIC) == InpMagic)
        {
         posTicket = ticket;
         return true;
        }
     }
   posTicket = 0;
   return false;
  }

double NormLots(double lots)
  {
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double mn   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double mx   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0)
      step = 0.01;
   lots = MathFloor(lots / step + 1e-9) * step;
   if(mx > 0 && lots > mx)
      lots = mx;
   if(lots < mn)
      lots = 0;
   return NormalizeDouble(lots, 2);
  }

double LotsForRisk(const double slDist)
  {
   if(InpFixedLots > 0)
      return NormLots(InpFixedLots);
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tv <= 0)
      tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tv <= 0 || ts <= 0 || slDist <= 0)
      return 0;
   double risk = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPct / 100.0;
   double lots = NormLots(risk / (slDist * tv / ts));
   if(lots <= 0)
      lots = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);   // same rule as the indicator: never below min lot
   return lots;
  }

//+------------------------------------------------------------------+
//| Every tick: take the partial at TP1 and move to breakeven        |
//+------------------------------------------------------------------+
void ManageTp1()
  {
   if(partialDone || tp1Price <= 0 || !SelectOurPosition())
      return;
   long   type = PositionGetInteger(POSITION_TYPE);
   double vol  = PositionGetDouble(POSITION_VOLUME);
   double open = PositionGetDouble(POSITION_PRICE_OPEN);
   double tp   = PositionGetDouble(POSITION_TP);
   double bid  = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask  = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   bool hit = (type == POSITION_TYPE_BUY) ? bid >= tp1Price : ask <= tp1Price;
   if(!hit)
      return;
   double part = NormLots(vol * InpPartialPct / 100.0);
   if(part > 0 && vol - part >= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN) - 1e-9)
      trade.PositionClosePartial(posTicket, part);
   if(SelectOurPosition())
      trade.PositionModify(posTicket, NormalizeDouble(open, _Digits), tp);
   partialDone = true;
  }

//+------------------------------------------------------------------+
//| New bar: trail the runner, then act on the last closed bar       |
//+------------------------------------------------------------------+
void TrailRunner()
  {
   if(!partialDone || !SelectOurPosition())
      return;
   double atr[1];
   if(CopyBuffer(hAtr, 0, 1, 1, atr) != 1)
      return;
   double c1  = iClose(_Symbol, PERIOD_M1, 1);
   double spr = SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID);
   long   type = PositionGetInteger(POSITION_TYPE);
   double sl  = PositionGetDouble(POSITION_SL);
   double tp  = PositionGetDouble(POSITION_TP);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double stopsLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   if(type == POSITION_TYPE_BUY)
     {
      double ns = NormalizeDouble(c1 - InpTrailAtr * atr[0], _Digits);
      if(ns > sl && ns < bid - stopsLevel)
         trade.PositionModify(posTicket, ns, tp);
     }
   else
     {
      double ns = NormalizeDouble(c1 + spr + InpTrailAtr * atr[0], _Digits);
      if((sl == 0 || ns < sl) && ns > ask + stopsLevel)
         trade.PositionModify(posTicket, ns, tp);
     }
  }

void OnTick()
  {
   ManageTp1();

   datetime t = iTime(_Symbol, PERIOD_M1, 0);
   if(t == lastBar)
      return;
   lastBar = t;

   TrailRunner();

   if(SelectOurPosition())
      return;
   partialDone = false;
   tp1Price = 0;

   double buy[1], sell[1], sl[1], tp1[1], tp2[1];
   if(CopyBuffer(hSig, 0, 1, 1, buy) != 1 || CopyBuffer(hSig, 1, 1, 1, sell) != 1 ||
      CopyBuffer(hSig, 3, 1, 1, sl) != 1 || CopyBuffer(hSig, 4, 1, 1, tp1) != 1 ||
      CopyBuffer(hSig, 5, 1, 1, tp2) != 1)
      return;
   bool isBuy  = buy[0] != EMPTY_VALUE && buy[0] != 0;
   bool isSell = sell[0] != EMPTY_VALUE && sell[0] != 0;
   if(isBuy == isSell || sl[0] == EMPTY_VALUE || tp2[0] == EMPTY_VALUE)
      return;
   if(InpMaxSpreadPts > 0 && SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) > InpMaxSpreadPts)
      return;

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double slP = NormalizeDouble(sl[0], _Digits);
   double tpP = NormalizeDouble(tp2[0], _Digits);
   if(isBuy && (slP >= bid || tpP <= ask))
      return;
   if(isSell && (slP <= ask || tpP >= bid))
      return;
   double lots = LotsForRisk(isBuy ? ask - slP : slP - bid);
   if(lots <= 0)
      return;
   bool ok = isBuy ? trade.Buy(lots, _Symbol, ask, slP, tpP, "XAU M1 scalper")
                   : trade.Sell(lots, _Symbol, bid, slP, tpP, "XAU M1 scalper");
   if(ok)
     {
      tp1Price = tp1[0];
      partialDone = false;
     }
  }
//+------------------------------------------------------------------+
