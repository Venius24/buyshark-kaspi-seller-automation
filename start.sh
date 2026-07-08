#!/bin/bash

# Terminate background processes on exit
trap 'kill $(jobs -p)' EXIT

echo "🚀 Starting Kaspi SaaS Wrapper (FastAPI + React)..."

# Kill any orphaned processes on port 5000
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    PID=$(netstat -ano | grep :5000 | awk '{print $5}' | head -n 1)
    if [ ! -z "$PID" ]; then
        echo "Killing orphaned process on port 5000 (PID: $PID)..."
        taskkill //F //PID $PID 2>/dev/null || true
    fi
else
    PID=$(lsof -t -i:5000 2>/dev/null)
    if [ ! -z "$PID" ]; then
        echo "Killing process on port 5000 (PID: $PID)..."
        kill -9 $PID 2>/dev/null || true
    fi
fi

# Start Python FastAPI backend
echo "🐍 Starting Python FastAPI Backend on http://localhost:5000..."
./venv/Scripts/python -m uvicorn backend.main:app --host 127.0.0.1 --port 5000 &

# Start Vite frontend
echo "💻 Starting React Frontend..."
(cd frontend && bun run dev) &

# Keep script running
wait
