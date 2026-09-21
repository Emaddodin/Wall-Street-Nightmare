#!/usr/bin/env bash
# status.sh - Check status of the HyperPredator Replay Simulator
cd "$(dirname "$0")"

PID_FILE="logs/replay_streamer.pid"

echo "========================================================"
echo "    HYPER-PREDATOR LIVE REPLAY SIMULATOR STATUS"
echo "========================================================"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "🟢 Process State: RUNNING (PID: $PID)"
        echo "🌐 Command Center: http://localhost:8000"
        echo ""
        echo "--- Live Replay API State ---"
        curl -s http://localhost:8000/api/state | python3 -m json.tool 2>/dev/null || curl -s http://localhost:8000/api/state
        echo ""
        echo "--- Recent Logs (tail -n 15) ---"
        tail -n 15 logs/replay_streamer.log
    else
        echo "🔴 Process State: STOPPED (Stale PID file $PID)"
    fi
else
    echo "🔴 Process State: NOT RUNNING (No PID file found)"
fi
echo "========================================================"
