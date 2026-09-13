@echo off
title EEE Live Quiz Server
echo ==========================================
echo        EEE LIVE QUIZ SERVER
echo ==========================================
echo.
echo Installing/checking required packages...
py -m pip install -r requirements.txt
echo.
echo Starting server...
echo Host dashboard: http://localhost:8000/host
echo Projector view: http://localhost:8000/projector
echo Participant join: http://YOUR-LAPTOP-IP:8000/
echo.
echo Keep this window OPEN during the quiz.
echo.
py -m uvicorn app:app --host 0.0.0.0 --port 8000
pause
