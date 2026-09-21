#!/usr/bin/env bash
# stop.sh - Gracefully terminate the HyperPredator Replay Simulator
cd "$(dirname "$0")"

PID_FILE="logs/replay_streamer.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Stopping HyperPredator Replay Simulator (PID: $PID)..."
        kill -SIGINT "$PID" 2>/dev/null || kill -SIGTERM "$PID" 2>/dev/null
        sleep 2
        if ps -p "$PID" > /dev/null 2>&1; then
            kill -9 "$PID" 2>/dev/null
        fi
        echo "✓ Process stopped."
    else
        echo "Process with PID $PID was already stopped."
    fi
    rm -f "$PID_FILE"
else
    echo "No PID file found. Replay simulator is not running."
fi
