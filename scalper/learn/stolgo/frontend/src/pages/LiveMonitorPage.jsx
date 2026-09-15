import { useEffect, useState } from "react";
import { MetricCard } from "../components/MetricCard";

export function LiveMonitorPage() {
  const [metrics, setMetrics] = useState({
    ofi_mean: 0.0,
    hawkes_buy: 0.0,
    hawkes_sell: 0.0,
    maker_spread_bps: 0.0,
    inventory_skew: 0.0,
    dynamic_leverage: 0,
    atr_ratchet_mult: 0.0,
  });
  const [logs, setLogs] = useState([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let ws;
    let reconnectTimeout;

    const connect = () => {
      ws = new WebSocket("ws://localhost:8765");
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        reconnectTimeout = setTimeout(connect, 3000);
      };
      ws.onerror = (e) => console.error("WS error:", e);
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === "INIT") {
          setMetrics(msg.data);
        } else if (msg.type === "AS_DYNAMICS") {
          setMetrics(prev => ({ ...prev, maker_spread_bps: msg.data.spread, inventory_skew: msg.data.skew }));
        } else if (msg.type === "ALPHA_TRIGGER") {
          setMetrics(msg.data);
          setLogs(prev => [`[${new Date().toLocaleTimeString()}] Alpha Trigger (Aggressive Taker Order)`, ...prev].slice(0, 10));
        } else if (msg.type === "RATCHET_SHIFT") {
          setMetrics(prev => ({ ...prev, atr_ratchet_mult: msg.data.mult }));
          setLogs(prev => [`[${new Date().toLocaleTimeString()}] ATR Ratchet tightened to ${msg.data.mult}`, ...prev].slice(0, 10));
        } else if (msg.type === "EXIT") {
          setLogs(prev => [`[${new Date().toLocaleTimeString()}] Scale-Out/Exit ${msg.data.side} | PnL: ${msg.data.pnl_usdt.toFixed(2)} USDT (${msg.data.reason})`, ...prev].slice(0, 10));
        }
      };
    };

    connect();

    return () => {
      clearTimeout(reconnectTimeout);
      if (ws) ws.close();
    };
  }, []);

  return (
    <div className="report-canvas" style={{ padding: '2rem' }}>
      <header className="page-header" style={{ marginBottom: '2rem', display: 'flex', justifyContent: 'space-between' }}>
        <div>
          <h1 className="title" style={{ fontSize: '24px', fontWeight: 'bold' }}>Live Execution Telemetry</h1>
          <p className="subtitle" style={{ color: '#888' }}>Avellaneda-Stoikov & High-Frequency Alpha Dashboard</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <span style={{ height: 10, width: 10, borderRadius: '50%', backgroundColor: connected ? '#149a5a' : '#c83f3a', marginRight: '8px' }} />
          <span style={{ fontWeight: 'bold' }}>{connected ? "LIVE" : "DISCONNECTED"}</span>
        </div>
      </header>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px', marginBottom: '2rem' }}>
        <MetricCard label="Maker Spread (bps)" value={metrics.maker_spread_bps.toFixed(2)} />
        <MetricCard label="Inventory Skew (Base)" value={metrics.inventory_skew.toFixed(4)} />
        <MetricCard label="OFI Mean (L1-L5)" value={metrics.ofi_mean.toFixed(3)} />
        <MetricCard label="Hawkes Buy" value={metrics.hawkes_buy.toFixed(2)} />
        <MetricCard label="Hawkes Sell" value={metrics.hawkes_sell.toFixed(2)} />
        <MetricCard label="Dynamic Leverage" value={`${metrics.dynamic_leverage}x`} />
        <MetricCard label="ATR Ratchet Mult" value={metrics.atr_ratchet_mult.toFixed(2)} />
      </div>

      <div style={{ backgroundColor: 'var(--panel-bg)', border: '1px solid var(--border)', borderRadius: '8px', padding: '16px' }}>
        <h3 style={{ marginBottom: '12px', borderBottom: '1px solid var(--border)', paddingBottom: '8px' }}>Execution Event Log</h3>
        <div style={{ fontFamily: 'monospace', fontSize: '13px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {logs.length === 0 ? <span style={{ color: '#888' }}>Waiting for signals...</span> : null}
          {logs.map((log, i) => (
            <div key={i} style={{ color: log.includes('Alpha') ? '#0f8ea8' : log.includes('Exit') ? (log.includes('-') ? '#c83f3a' : '#149a5a') : 'inherit' }}>
              {log}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
