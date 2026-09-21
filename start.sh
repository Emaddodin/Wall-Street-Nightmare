#!/usr/bin/env bash
# start.sh - Launch the HyperPredator 1:1 Live Replay Simulator Daemon
cd "$(dirname "$0")"

PID_FILE="logs/replay_streamer.pid"
LOG_FILE="logs/replay_streamer.log"
PORT=8000

mkdir -p logs

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "⚠️ HyperPredator is already running with PID $OLD_PID."
        echo "Use ./stop.sh to stop it first, or ./status.sh to view health."
        exit 1
    else
        rm -f "$PID_FILE"
    fi
fi

echo "========================================================"
echo "    LAUNCHING HYPER-PREDATOR LIVE REPLAY SIMULATOR"
echo "========================================================"
echo "• Target Start:   00:00:00 UTC (03:30 AM Tehran)"
echo "• Base Capital:   \$65.00"
echo "• Max Margin:     20% (Safe Micro-Account Risk Envelope)"
echo "• Exit Mode:      Pure 100% Binary Scalp (No Trailing Runners)"
echo "• Structural S/R: Lookback 50 bars (250m), Zone ±\$0.25"
echo "• Hard Shield:    -\$10.00 Max Single-Basket Drawdown"
echo "• Mobile Web App: http://localhost:${PORT}"
echo "• ntfy Channel:   tbt-96c0dc08c297676b"
echo "========================================================"

nohup python3 replay_streamer.py \
    --port "$PORT" \
    --margin-pct 0.20 \
    --sr-lookback 50 \
    --zone-eps 0.25 \
    > "$LOG_FILE" 2>&1 &

NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

echo "🟢 Simulator launched with PID: $NEW_PID"
sleep 2

if ps -p "$NEW_PID" > /dev/null 2>&1; then
    echo "✓ Health Check: RUNNING"
    echo "✓ Check status anytime via: ./status.sh"
else
    echo "❌ Failed to start. Check logs/replay_streamer.log:"
    tail -n 20 "$LOG_FILE"
    exit 1
fi
