@echo off
echo 🚀 Starting Kaspi SaaS Wrapper (FastAPI + React)...

:: Start Python FastAPI backend in the background
start "Kaspi Backend (FastAPI)" cmd /c "venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 5000"

:: Start Vite React Frontend
start "Kaspi Frontend (Vite)" cmd /c "cd frontend && bun run dev"

echo Done! Backend and Frontend processes launched in new windows.
pause
