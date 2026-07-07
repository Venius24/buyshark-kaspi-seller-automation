#!/bin/bash

# Terminate background processes on exit
trap 'kill $(jobs -p)' EXIT

echo "🚀 Starting Kaspi SaaS Wrapper (FastAPI + React)..."

# Start Python FastAPI backend
echo "🐍 Starting Python FastAPI Backend on http://localhost:5000..."
./venv/Scripts/python -m uvicorn backend.main:app --host 127.0.0.1 --port 5000 &

# Start Vite frontend
echo "💻 Starting React Frontend..."
(cd frontend && bun run dev) &

# Keep script running
wait
