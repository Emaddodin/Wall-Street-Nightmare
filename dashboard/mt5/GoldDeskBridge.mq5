//+------------------------------------------------------------------+
//| GoldDeskBridge.mq5                                               |
//| Connects Gold Desk (the dashboard on this computer) to this MT5. |
//|                                                                  |
//| - Writes gold's live price, the last candles, your account and   |
//|   your open gold trades to a small file every time they change.  |
//| - Places, closes and moves trades ONLY when Gold Desk asks,      |
//|   which happens only when you click Buy / Sell / Close there.    |
//|   It never trades by itself.                                     |
//|                                                                  |
//| Files live in MT5's shared folder (File > Open Data Folder, then |
//| one level up: Common\Files\GoldDesk). Nothing goes on the web.   |
//| Put it on any one chart; it finds gold (XAUUSD / GOLD) itself.   |
//+------------------------------------------------------------------+
#property copyright "Gold Desk"
#property version   "1.11"
#property description "Feeds Gold Desk this terminal's gold prices, account and trades, and places your Gold Desk clicks here."

#include <Trade\Trade.mqh>

input string InpSymbol    = "";        // Gold symbol (empty = find it)
input int    InpTimerMs   = 5;         // How often to look for Gold Desk clicks (ms)
input int    InpDeviation = 30;        // Max slippage (points)
input long   InpMagic     = 26100102;  // Tag on trades placed from Gold Desk

#define DIR      "GoldDesk\\"
#define DIR_IN   "GoldDesk\\in\\"
#define DIR_OUT  "GoldDesk\\out\\"

CTrade   g_trade;
string   g_sym = "";
bool     g_owner = false;          // only one chart's bridge works at a time
double   g_lastBid = 0, g_lastAsk = 0;
ulong    g_lastWrite = 0, g_lastSlow = 0, g_lastOwner = 0;
string   g_slow = "";              // account, spec and trades part of the state (rebuilt every 200 ms or after a trade)
bool     g_dirty = true;
ENUM_TIMEFRAMES g_tfs[6] = {PERIOD_M1, PERIOD_M5, PERIOD_M15, PERIOD_H1, PERIOD_H4, PERIOD_D1};
string   g_tfn[6] = {"M1", "M5", "M15", "H1", "H4", "D1"};

//+------------------------------------------------------------------+
string Js(const string s)
  {
   string r = s;
   StringReplace(r, "\\", "\\\\");
   StringReplace(r, "\"", "\\\"");
   StringReplace(r, "\r", " ");
   StringReplace(r, "\n", " ");
   StringReplace(r, "\t", " ");
   return "\"" + r + "\"";
  }

string D(const double v, const int digits)
  {
   if(!MathIsValidNumber(v))
      return "null";
   return DoubleToString(v, digits);
  }

string B(const bool v) { return v ? "true" : "false"; }

ulong NowMs() { return GetMicrosecondCount() / 1000; }

//+------------------------------------------------------------------+
string FindGold()
  {
   if(InpSymbol != "")
      return InpSymbol;
   string up = _Symbol;
   StringToUpper(up);
   if(StringFind(up, "XAU") == 0 || StringFind(up, "GOLD") == 0)
      return _Symbol;
   string tries[] = {"XAUUSD", "XAUUSDm", "XAUUSD.a", "XAUUSD_i", "XAUUSD.pro", "XAUUSD+", "GOLD", "XAUUSD.r"};
   for(int i = 0; i < ArraySize(tries); i++)
      if(SymbolSelect(tries[i], true))
         return tries[i];
   int n = SymbolsTotal(false);
   for(int i = 0; i < n; i++)
     {
      string name = SymbolName(i, false), u = name;
      StringToUpper(u);
      if(StringFind(u, "XAUUSD") == 0 || StringFind(u, "GOLD") == 0)
        {
         SymbolSelect(name, true);
         return name;
        }
     }
   return "";
  }

//+------------------------------------------------------------------+
//| Write a whole file, then swap it in, so Gold Desk never reads    |
//| half of one.                                                     |
//+------------------------------------------------------------------+
bool WriteAtomic(const string name, const string text)
  {
   string tmp = name + ".tmp";
   int h = FileOpen(tmp, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return false;
   FileWriteString(h, text);
   FileClose(h);
   if(!FileMove(tmp, FILE_COMMON, name, FILE_COMMON | FILE_REWRITE))
     {
      FileDelete(tmp, FILE_COMMON);
      return false;
     }
   return true;
  }

//+------------------------------------------------------------------+
//| One bridge per terminal: the chart that wrote owner.txt in the   |
//| last 5 s keeps it.                                               |
//+------------------------------------------------------------------+
void CheckOwner()
  {
   string me = IntegerToString(ChartID());
   long now = (long)TimeGMT();
   string other = "";
   long beat = 0;
   int h = FileOpen(DIR + "owner.txt", FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h != INVALID_HANDLE)
     {
      string line = FileReadString(h);
      FileClose(h);
      string parts[];
      if(StringSplit(line, ' ', parts) == 2)
        {
         other = parts[0];
         beat = StringToInteger(parts[1]);
        }
     }
   if(other != "" && other != me && now - beat <= 5)
     {
      if(g_owner)
         Print("Gold Desk bridge: another chart took over.");
      g_owner = false;
      Comment("Gold Desk bridge: already running on another chart. This copy waits.");
      return;
     }
   WriteAtomic(DIR + "owner.txt", me + " " + IntegerToString(now));
   if(!g_owner)
     {
      g_owner = true;
      g_dirty = true;
      Comment("Gold Desk bridge: on (" + g_sym + ")");
     }
  }

//+------------------------------------------------------------------+
string SlowPart()
  {
   int dg = (int)SymbolInfoInteger(g_sym, SYMBOL_DIGITS);
   string s = "\"spec\":{";
   s += "\"point\":" + D(SymbolInfoDouble(g_sym, SYMBOL_POINT), 10);
   s += ",\"digits\":" + IntegerToString(dg);
   s += ",\"contract\":" + D(SymbolInfoDouble(g_sym, SYMBOL_TRADE_CONTRACT_SIZE), 4);
   s += ",\"tick_value\":" + D(SymbolInfoDouble(g_sym, SYMBOL_TRADE_TICK_VALUE), 8);
   s += ",\"tick_value_loss\":" + D(SymbolInfoDouble(g_sym, SYMBOL_TRADE_TICK_VALUE_LOSS), 8);
   s += ",\"tick_size\":" + D(SymbolInfoDouble(g_sym, SYMBOL_TRADE_TICK_SIZE), 10);
   s += ",\"vmin\":" + D(SymbolInfoDouble(g_sym, SYMBOL_VOLUME_MIN), 4);
   s += ",\"vstep\":" + D(SymbolInfoDouble(g_sym, SYMBOL_VOLUME_STEP), 4);
   s += ",\"vmax\":" + D(SymbolInfoDouble(g_sym, SYMBOL_VOLUME_MAX), 4);
   s += ",\"filling\":" + IntegerToString(SymbolInfoInteger(g_sym, SYMBOL_FILLING_MODE));
   s += ",\"trade_mode\":" + IntegerToString(SymbolInfoInteger(g_sym, SYMBOL_TRADE_MODE));
   s += ",\"stops\":" + IntegerToString(SymbolInfoInteger(g_sym, SYMBOL_TRADE_STOPS_LEVEL));
   s += "},\"acct\":{";
   s += "\"login\":" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
   s += ",\"server\":" + Js(AccountInfoString(ACCOUNT_SERVER));
   s += ",\"company\":" + Js(AccountInfoString(ACCOUNT_COMPANY));
   s += ",\"currency\":" + Js(AccountInfoString(ACCOUNT_CURRENCY));
   s += ",\"balance\":" + D(AccountInfoDouble(ACCOUNT_BALANCE), 2);
   s += ",\"equity\":" + D(AccountInfoDouble(ACCOUNT_EQUITY), 2);
   s += ",\"margin\":" + D(AccountInfoDouble(ACCOUNT_MARGIN), 2);
   s += ",\"free\":" + D(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2);
   s += ",\"mode\":" + IntegerToString(AccountInfoInteger(ACCOUNT_TRADE_MODE));
   s += ",\"leverage\":" + IntegerToString(AccountInfoInteger(ACCOUNT_LEVERAGE));
   s += ",\"trade_allowed\":" + B(AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) != 0);
   s += ",\"trade_expert\":" + B(AccountInfoInteger(ACCOUNT_TRADE_EXPERT) != 0);
   s += "},\"pos\":[";
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || PositionGetString(POSITION_SYMBOL) != g_sym)
         continue;
      if(n++ > 0)
         s += ",";
      s += "{\"ticket\":" + IntegerToString((long)ticket);
      s += ",\"type\":" + IntegerToString(PositionGetInteger(POSITION_TYPE));
      s += ",\"volume\":" + D(PositionGetDouble(POSITION_VOLUME), 2);
      s += ",\"open\":" + D(PositionGetDouble(POSITION_PRICE_OPEN), dg);
      s += ",\"sl\":" + D(PositionGetDouble(POSITION_SL), dg);
      s += ",\"tp\":" + D(PositionGetDouble(POSITION_TP), dg);
      s += ",\"price\":" + D(PositionGetDouble(POSITION_PRICE_CURRENT), dg);
      s += ",\"profit\":" + D(PositionGetDouble(POSITION_PROFIT), 2);
      s += ",\"swap\":" + D(PositionGetDouble(POSITION_SWAP), 2);
      s += ",\"time\":" + IntegerToString(PositionGetInteger(POSITION_TIME));
      s += ",\"magic\":" + IntegerToString(PositionGetInteger(POSITION_MAGIC));
      s += ",\"comment\":" + Js(PositionGetString(POSITION_COMMENT)) + "}";
     }
   s += "]";
   return s;
  }

//+------------------------------------------------------------------+
string BarsPart(const int dg)
  {
   string s = "\"bars\":{";
   MqlRates r[];
   ArraySetAsSeries(r, false);
   for(int k = 0; k < 6; k++)
     {
      if(k > 0)
         s += ",";
      s += "\"" + g_tfn[k] + "\":[";
      int got = CopyRates(g_sym, g_tfs[k], 0, 3, r);
      for(int i = 0; i < got; i++)
        {
         if(i > 0)
            s += ",";
         s += "[" + IntegerToString((long)r[i].time) + "," + D(r[i].open, dg) + "," + D(r[i].high, dg) + "," +
              D(r[i].low, dg) + "," + D(r[i].close, dg) + "," + IntegerToString(r[i].tick_volume) + "," +
              IntegerToString(r[i].spread) + "]";
        }
      s += "]";
     }
   return s + "}";
  }

//+------------------------------------------------------------------+
void WriteState(bool force)
  {
   if(!g_owner || g_sym == "")
      return;
   MqlTick t;
   bool have = SymbolInfoTick(g_sym, t);
   ulong now = NowMs();
   bool moved = have && (t.bid != g_lastBid || t.ask != g_lastAsk);
   if(now - g_lastSlow >= 200 || g_dirty)
     {
      g_slow = SlowPart();
      g_lastSlow = now;
      g_dirty = false;
      force = true;
     }
   if(!moved && !force && now - g_lastWrite < 1000)
      return;                                   // nothing new: a heartbeat once a second is enough
   int dg = (int)SymbolInfoInteger(g_sym, SYMBOL_DIGITS);
   string s = "{\"v\":1,\"sym\":" + Js(g_sym);
   s += ",\"chart\":" + IntegerToString(ChartID());
   s += ",\"gmt\":" + IntegerToString((long)TimeGMT());
   s += ",\"srv_off\":" + IntegerToString((long)(TimeTradeServer() - TimeGMT()));
   s += ",\"connected\":" + B(TerminalInfoInteger(TERMINAL_CONNECTED) != 0);
   s += ",\"algo\":" + B(TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0);
   s += ",\"ea_trade\":" + B(MQLInfoInteger(MQL_TRADE_ALLOWED) != 0);
   s += ",\"ping_us\":" + IntegerToString(TerminalInfoInteger(TERMINAL_PING_LAST));
   if(have)
      s += ",\"tick\":{\"bid\":" + D(t.bid, dg) + ",\"ask\":" + D(t.ask, dg) + ",\"time\":" +
           IntegerToString((long)t.time) + ",\"msc\":" + IntegerToString(t.time_msc) + "}";
   s += "," + g_slow + "," + BarsPart(dg) + "}";
   if(WriteAtomic(DIR + "state.json", s))
     {
      g_lastWrite = now;
      if(have)
        {
         g_lastBid = t.bid;
         g_lastAsk = t.ask;
        }
     }
  }

//+------------------------------------------------------------------+
//| Commands from Gold Desk: in\cmd-<id>.txt, key=value per line.    |
//+------------------------------------------------------------------+
string Val(const string &keys[], const string &vals[], const string key)
  {
   for(int i = 0; i < ArraySize(keys); i++)
      if(keys[i] == key)
         return vals[i];
   return "";
  }

ENUM_ORDER_TYPE_FILLING Filling()
  {
   long f = SymbolInfoInteger(g_sym, SYMBOL_FILLING_MODE);
   if((f & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK)
      return ORDER_FILLING_FOK;
   if((f & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC)
      return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
  }

string TradeResult(const bool sent, const ulong t0)
  {
   uint rc = g_trade.ResultRetcode();
   bool ok = sent && (rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_PLACED || rc == TRADE_RETCODE_DONE_PARTIAL);
   int dg = (int)SymbolInfoInteger(g_sym, SYMBOL_DIGITS);
   string msg = g_trade.ResultRetcodeDescription();
   string c = g_trade.ResultComment();
   if(c != "" && c != msg)
      msg += " (" + c + ")";
   if(!sent && rc == 0)
      msg = "MT5 refused the request, error " + IntegerToString(GetLastError());
   return "{\"ok\":" + B(ok) + ",\"retcode\":" + IntegerToString(rc) + ",\"message\":" + Js(msg) +
          ",\"price\":" + D(g_trade.ResultPrice(), dg) + ",\"volume\":" + D(g_trade.ResultVolume(), 2) +
          ",\"order\":" + IntegerToString((long)g_trade.ResultOrder()) + ",\"deal\":" +
          IntegerToString((long)g_trade.ResultDeal()) + ",\"ms\":" + D((GetMicrosecondCount() - t0) / 1000.0, 1) + "}";
  }

string Fail(const string why)
  {
   return "{\"ok\":false,\"message\":" + Js(why) + "}";
  }

//+------------------------------------------------------------------+
//| Candle history. sym empty: gold, the reply exactly as before.    |
//| sym given (silver for Gold Desk's SMT reading): that symbol's    |
//| candles, its name echoed in the header so Gold Desk can tell     |
//| them from gold's.                                                |
//+------------------------------------------------------------------+
void WriteBars(const string id, const string tf, const int count, const string sym)
  {
   int k = -1;
   for(int i = 0; i < 6; i++)
      if(g_tfn[i] == tf)
         k = i;
   string name = DIR_OUT + "res-" + id + ".txt";
   if(k < 0)
     {
      WriteAtomic(name, Fail("unknown timeframe " + tf));
      return;
     }
   string s = g_sym;
   if(sym != "")
     {
      bool custom = false;
      if(!SymbolExist(sym, custom))
        {
         WriteAtomic(name, "{\"ok\":false,\"nosym\":true,\"sym\":" + Js(sym) + ",\"message\":" +
                     Js("No " + sym + " in this MT5") + "}");
         return;
        }
      if(!SymbolInfoInteger(sym, SYMBOL_SELECT))
         SymbolSelect(sym, true);               // history of a symbol outside Market Watch may not load
      s = sym;
     }
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int got = CopyRates(s, g_tfs[k], 0, count, r);
   if(got <= 0)
     {
      WriteAtomic(name, Fail("MT5 is still loading " + (sym == "" ? "" : sym + " ") + tf + " history (error " +
                             IntegerToString(GetLastError()) + ")"));
      return;
     }
   int dg = (int)SymbolInfoInteger(s, SYMBOL_DIGITS);
   string tmp = name + ".tmp";
   int h = FileOpen(tmp, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;
   if(sym == "")
      FileWriteString(h, "{\"ok\":true,\"n\":" + IntegerToString(got) + "}\n");
   else
      FileWriteString(h, "{\"ok\":true,\"n\":" + IntegerToString(got) + ",\"sym\":" + Js(sym) + ",\"point\":" +
                      D(SymbolInfoDouble(s, SYMBOL_POINT), 10) + ",\"digits\":" + IntegerToString(dg) + "}\n");
   for(int i = 0; i < got; i++)
      FileWriteString(h, IntegerToString((long)r[i].time) + "," + D(r[i].open, dg) + "," + D(r[i].high, dg) + "," +
                      D(r[i].low, dg) + "," + D(r[i].close, dg) + "," + IntegerToString(r[i].tick_volume) + "," +
                      IntegerToString(r[i].spread) + "\n");
   FileClose(h);
   if(!FileMove(tmp, FILE_COMMON, name, FILE_COMMON | FILE_REWRITE))
      FileDelete(tmp, FILE_COMMON);
  }

void RunCommand(const string file)
  {
   string src = DIR_IN + file;
   string taken = src + ".taken";
   if(!FileMove(src, FILE_COMMON, taken, FILE_COMMON | FILE_REWRITE))
      return;                                   // Gold Desk withdrew it (too late) or another copy took it
   string keys[], vals[];
   int h = FileOpen(taken, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h != INVALID_HANDLE)
     {
      while(!FileIsEnding(h))
        {
         string line = FileReadString(h);
         int eq = StringFind(line, "=");
         if(eq <= 0)
            continue;
         int n = ArraySize(keys);
         ArrayResize(keys, n + 1);
         ArrayResize(vals, n + 1);
         keys[n] = StringSubstr(line, 0, eq);
         vals[n] = StringSubstr(line, eq + 1);
        }
      FileClose(h);
     }
   FileDelete(taken, FILE_COMMON);
   string id = Val(keys, vals, "id");
   if(id == "")
      return;
   string out = DIR_OUT + "res-" + id + ".txt";
   string op = Val(keys, vals, "op");
   long expires = StringToInteger(Val(keys, vals, "expires"));
   if(op != "bars" && (expires == 0 || (long)TimeGMT() > expires))
     {
      WriteAtomic(out, Fail("Gold Desk's request arrived too late, so MT5 skipped it. Nothing was sent."));
      return;
     }
   if(op == "bars")
     {
      WriteBars(id, Val(keys, vals, "tf"), (int)StringToInteger(Val(keys, vals, "count")), Val(keys, vals, "symbol"));
      return;
     }
   if(op == "ping")
     {
      WriteAtomic(out, "{\"ok\":true,\"message\":\"pong\"}");
      return;
     }
   ulong t0 = GetMicrosecondCount();
   bool sent = false;
   g_trade.SetTypeFilling(Filling());
   double sl = StringToDouble(Val(keys, vals, "sl")), tp = StringToDouble(Val(keys, vals, "tp"));
   if(op == "market")
     {
      string side = Val(keys, vals, "side");
      double lots = StringToDouble(Val(keys, vals, "lots"));
      if(side == "BUY")
         sent = g_trade.Buy(lots, g_sym, 0.0, sl, tp, "gold desk manual");
      else
         if(side == "SELL")
            sent = g_trade.Sell(lots, g_sym, 0.0, sl, tp, "gold desk manual");
         else
           {
            WriteAtomic(out, Fail("unknown side " + side));
            return;
           }
     }
   else
      if(op == "close")
        {
         ulong ticket = (ulong)StringToInteger(Val(keys, vals, "ticket"));
         double vol = StringToDouble(Val(keys, vals, "volume"));
         if(!PositionSelectByTicket(ticket))
           {
            WriteAtomic(out, Fail("Position not found (already closed?)"));
            return;
           }
         if(vol > 0 && vol < PositionGetDouble(POSITION_VOLUME))
            sent = g_trade.PositionClosePartial(ticket, vol, (ulong)InpDeviation);
         else
            sent = g_trade.PositionClose(ticket, (ulong)InpDeviation);
        }
      else
         if(op == "modify")
           {
            ulong ticket = (ulong)StringToInteger(Val(keys, vals, "ticket"));
            if(!PositionSelectByTicket(ticket))
              {
               WriteAtomic(out, Fail("Position not found"));
               return;
              }
            sent = g_trade.PositionModify(ticket, sl, tp);
           }
         else
           {
            WriteAtomic(out, Fail("unknown request " + op));
            return;
           }
   g_dirty = true;
   WriteState(true);                            // the new trade shows on Gold Desk right away
   WriteAtomic(out, TradeResult(sent, t0));
  }

void CheckCommands()
  {
   string file;
   long search = FileFindFirst(DIR_IN + "cmd-*.txt", file, FILE_COMMON);
   if(search == INVALID_HANDLE)
      return;
   string files[];
   do
     {
      int n = ArraySize(files);
      ArrayResize(files, n + 1);
      files[n] = file;
     }
   while(FileFindNext(search, file));
   FileFindClose(search);
   for(int i = 0; i < ArraySize(files); i++)
      RunCommand(files[i]);
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   g_sym = FindGold();
   if(g_sym == "")
     {
      Alert("Gold Desk bridge: no gold symbol (XAUUSD / GOLD) found. Show it in Market Watch and try again.");
      return INIT_FAILED;
     }
   FolderCreate("GoldDesk", FILE_COMMON);
   FolderCreate("GoldDesk\\in", FILE_COMMON);
   FolderCreate("GoldDesk\\out", FILE_COMMON);
   g_trade.SetExpertMagicNumber((ulong)InpMagic);
   g_trade.SetDeviationInPoints((ulong)InpDeviation);
   g_trade.SetAsyncMode(false);
   g_trade.LogLevel(LOG_LEVEL_ERRORS);
   if(!EventSetMillisecondTimer(InpTimerMs > 0 ? InpTimerMs : 5))
      EventSetTimer(1);
   CheckOwner();
   WriteState(true);
   Print("Gold Desk bridge on: ", g_sym, ". Files in the Common folder under GoldDesk. Trades only on your Gold Desk clicks.");
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   if(g_owner)
      FileDelete(DIR + "owner.txt", FILE_COMMON);
   Comment("");
  }

void OnTick()
  {
   if(!g_owner)
      return;
   CheckCommands();
   WriteState(false);
  }

void OnTimer()
  {
   ulong now = NowMs();
   if(now - g_lastOwner >= 1000)
     {
      g_lastOwner = now;
      CheckOwner();
     }
   if(!g_owner)
      return;
   CheckCommands();
   WriteState(false);
  }

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   g_dirty = true;                              // a trade opened, closed or moved (here or in MT5 itself)
  }
//+------------------------------------------------------------------+
