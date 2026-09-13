
import asyncio
import json
import os
import socket
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
QUESTIONS_FILE = DATA_DIR / "questions.json"
RESULTS_FILE = DATA_DIR / "results.json"

app = FastAPI(title="EEE Live Quiz")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# -----------------------------
# In-memory live quiz state
# -----------------------------
state_lock = asyncio.Lock()
host_connections = set()
projector_connections = set()
participant_connections: Dict[str, WebSocket] = {}

quiz = {
    "status": "idle",              # idle | running | finished
    "round_index": -1,
    "question_index": -1,
    "question_started_at": None,
    "question_deadline": None,
    "question_serial": 0,
    "auto_advance": True,
    "show_leaderboard": False,
}

participants: Dict[str, Dict[str, Any]] = {}
timer_task: Optional[asyncio.Task] = None

def now_ms() -> int:
    return int(time.time() * 1000)

def load_questions():
    if not QUESTIONS_FILE.exists():
        return {"title": "EEE Technical Challenge", "rounds": []}
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_questions(data):
    DATA_DIR.mkdir(exist_ok=True)
    with open(QUESTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def public_question(q):
    data = {
        "id": q.get("id"),
        "text": q.get("text", ""),
        "type": q.get("type", "mcq"),
        "time_limit": q.get("time_limit", 20),
        "marks_correct": q.get("marks_correct", 2),
        "marks_wrong": q.get("marks_wrong", -1),
        "marks_unanswered": q.get("marks_unanswered", 0),
        "image": q.get("image"),
    }
    if q.get("type") == "mcq":
        data["options"] = q.get("options", [])
    return data

def normalize_answer(s: str) -> str:
    return " ".join((s or "").strip().lower().split())

def is_correct(q, answer: str) -> bool:
    if q.get("type") == "mcq":
        return normalize_answer(answer) == normalize_answer(str(q.get("answer", "")))
    accepted = q.get("accepted_answers")
    if accepted is None:
        accepted = [q.get("answer", "")]
    return normalize_answer(answer) in {normalize_answer(str(x)) for x in accepted}

def current_context():
    data = load_questions()
    r_idx = quiz["round_index"]
    q_idx = quiz["question_index"]
    if r_idx < 0 or r_idx >= len(data.get("rounds", [])):
        return data, None, None
    rnd = data["rounds"][r_idx]
    if q_idx < 0 or q_idx >= len(rnd.get("questions", [])):
        return data, rnd, None
    return data, rnd, rnd["questions"][q_idx]

def participant_score(p):
    return sum(x.get("score", 0) for x in p.get("answers", {}).values())

def leaderboard():
    rows = []
    for pid, p in participants.items():
        rows.append({
            "id": pid,
            "name": p["name"],
            "roll": p["roll"],
            "score": participant_score(p),
            "connected": p.get("connected", False),
            "answered_current": str(quiz["question_serial"]) in p.get("answers", {}),
        })
    rows.sort(key=lambda x: (-x["score"], x["name"].lower()))
    return rows

def participant_view(pid: str):
    data, rnd, q = current_context()
    p = participants.get(pid)
    payload = {
        "type": "state",
        "server_time": now_ms(),
        "quiz_title": data.get("title", "Live Quiz"),
        "status": quiz["status"],
        "round_index": quiz["round_index"],
        "question_index": quiz["question_index"],
        "question_serial": quiz["question_serial"],
        "started_at": quiz["question_started_at"],
        "deadline": quiz["question_deadline"],
        "round": None,
        "question": None,
        "participant": None,
    }
    if rnd:
        payload["round"] = {
            "title": rnd.get("title", f"Round {quiz['round_index']+1}"),
            "description": rnd.get("description", ""),
            "question_count": len(rnd.get("questions", [])),
        }
    if q and quiz["status"] == "running":
        payload["question"] = public_question(q)
    if p:
        payload["participant"] = {
            "name": p["name"],
            "roll": p["roll"],
            "score": participant_score(p),
            "has_answered": str(quiz["question_serial"]) in p.get("answers", {}),
            "current_answer": p.get("answers", {}).get(str(quiz["question_serial"])),
        }
    return payload

def public_view():
    data, rnd, q = current_context()
    return {
        "type": "public_state",
        "server_time": now_ms(),
        "quiz_title": data.get("title", "Live Quiz"),
        "status": quiz["status"],
        "round_index": quiz["round_index"],
        "question_index": quiz["question_index"],
        "question_serial": quiz["question_serial"],
        "started_at": quiz["question_started_at"],
        "deadline": quiz["question_deadline"],
        "auto_advance": quiz["auto_advance"],
        "round": {
            "title": rnd.get("title", f"Round {quiz['round_index']+1}"),
            "description": rnd.get("description", ""),
            "question_count": len(rnd.get("questions", [])),
        } if rnd else None,
        "question": public_question(q) if q and quiz["status"] == "running" else None,
        "participants": len(participants),
        "connected": sum(1 for p in participants.values() if p.get("connected")),
        "answered": sum(1 for p in participants.values()
                        if str(quiz["question_serial"]) in p.get("answers", {})),
        "leaderboard": leaderboard(),
    }

async def safe_send(ws: WebSocket, payload: dict):
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        return False

async def broadcast_state():
    dead_hosts = []
    payload = public_view()
    for ws in list(host_connections):
        if not await safe_send(ws, payload):
            dead_hosts.append(ws)
    for ws in dead_hosts:
        host_connections.discard(ws)

    dead_proj = []
    for ws in list(projector_connections):
        if not await safe_send(ws, payload):
            dead_proj.append(ws)
    for ws in dead_proj:
        projector_connections.discard(ws)

    dead_pids = []
    for pid, ws in list(participant_connections.items()):
        if not await safe_send(ws, participant_view(pid)):
            dead_pids.append(pid)
    for pid in dead_pids:
        participant_connections.pop(pid, None)
        if pid in participants:
            participants[pid]["connected"] = False

async def schedule_auto_advance(serial: int, deadline_ms: int):
    try:
        delay = max(0, (deadline_ms - now_ms()) / 1000)
        await asyncio.sleep(delay + 0.05)
        async with state_lock:
            if quiz["status"] != "running" or quiz["question_serial"] != serial:
                return
            if quiz["auto_advance"]:
                await _advance_question_locked()
            else:
                await broadcast_state()
    except asyncio.CancelledError:
        pass

def start_timer(serial: int, deadline_ms: int):
    global timer_task
    if timer_task and not timer_task.done():
        timer_task.cancel()
    timer_task = asyncio.create_task(schedule_auto_advance(serial, deadline_ms))

async def _start_question_locked(round_idx: int, q_idx: int):
    data = load_questions()
    rounds = data.get("rounds", [])
    if round_idx < 0 or round_idx >= len(rounds):
        quiz["status"] = "finished"
        quiz["question_deadline"] = None
        quiz["question_started_at"] = None
        await broadcast_state()
        return
    questions = rounds[round_idx].get("questions", [])
    if q_idx < 0 or q_idx >= len(questions):
        # Advance to next round automatically
        next_round = round_idx + 1
        if next_round >= len(rounds):
            quiz["status"] = "finished"
            quiz["round_index"] = len(rounds) - 1
            quiz["question_index"] = len(questions) - 1 if questions else -1
            quiz["question_started_at"] = None
            quiz["question_deadline"] = None
            await persist_results()
            await broadcast_state()
            return
        await _start_question_locked(next_round, 0)
        return

    q = questions[q_idx]
    quiz["status"] = "running"
    quiz["round_index"] = round_idx
    quiz["question_index"] = q_idx
    quiz["question_serial"] += 1
    quiz["question_started_at"] = now_ms() + 700  # small sync buffer
    quiz["question_deadline"] = quiz["question_started_at"] + int(q.get("time_limit", 20) * 1000)
    await broadcast_state()
    start_timer(quiz["question_serial"], quiz["question_deadline"])

async def _advance_question_locked():
    data = load_questions()
    r = quiz["round_index"]
    q = quiz["question_index"]
    if r < 0:
        await _start_question_locked(0, 0)
        return
    rounds = data.get("rounds", [])
    if r >= len(rounds):
        return
    qs = rounds[r].get("questions", [])
    if q + 1 < len(qs):
        await _start_question_locked(r, q + 1)
    else:
        if r + 1 < len(rounds):
            await _start_question_locked(r + 1, 0)
        else:
            quiz["status"] = "finished"
            quiz["question_started_at"] = None
            quiz["question_deadline"] = None
            await persist_results()
            await broadcast_state()

async def persist_results():
    snapshot = {
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "quiz": quiz.copy(),
        "leaderboard": leaderboard(),
        "participants": participants,
    }
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

# -----------------------------
# Pages
# -----------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("join.html", {"request": request})

@app.post("/join")
async def join(name: str = Form(...), roll: str = Form(...)):
    name = name.strip()
    roll = roll.strip()
    if not name or not roll:
        raise HTTPException(400, "Name and roll number are required")
    # Reuse participant if same roll reconnects
    pid = None
    for existing_id, p in participants.items():
        if p["roll"].lower() == roll.lower():
            pid = existing_id
            p["name"] = name
            break
    if not pid:
        pid = str(uuid.uuid4())
        participants[pid] = {
            "name": name,
            "roll": roll,
            "joined_at": now_ms(),
            "connected": False,
            "answers": {},
        }
    return RedirectResponse(url=f"/play/{pid}", status_code=303)

@app.get("/play/{pid}", response_class=HTMLResponse)
async def play(request: Request, pid: str):
    if pid not in participants:
        return RedirectResponse("/")
    return templates.TemplateResponse("participant.html", {"request": request, "pid": pid})

@app.get("/host", response_class=HTMLResponse)
async def host(request: Request):
    return templates.TemplateResponse("host.html", {"request": request})

@app.get("/projector", response_class=HTMLResponse)
async def projector(request: Request):
    return templates.TemplateResponse("projector.html", {"request": request})

@app.get("/api/questions")
async def get_questions():
    return load_questions()

@app.post("/api/questions")
async def set_questions(request: Request):
    data = await request.json()
    if not isinstance(data, dict) or "rounds" not in data:
        raise HTTPException(400, "Invalid questions JSON")
    save_questions(data)
    return {"ok": True}

@app.post("/api/control/{action}")
async def control(action: str, request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    async with state_lock:
        if action == "start":
            await _start_question_locked(0, 0)
        elif action == "next":
            await _advance_question_locked()
        elif action == "restart_current":
            if quiz["round_index"] >= 0 and quiz["question_index"] >= 0:
                await _start_question_locked(quiz["round_index"], quiz["question_index"])
        elif action == "jump":
            r = int(body.get("round_index", 0))
            q = int(body.get("question_index", 0))
            await _start_question_locked(r, q)
        elif action == "finish":
            quiz["status"] = "finished"
            quiz["question_started_at"] = None
            quiz["question_deadline"] = None
            await persist_results()
            await broadcast_state()
        elif action == "reset":
            global timer_task
            if timer_task and not timer_task.done():
                timer_task.cancel()
            quiz.update({
                "status": "idle",
                "round_index": -1,
                "question_index": -1,
                "question_started_at": None,
                "question_deadline": None,
                "question_serial": quiz["question_serial"] + 1,
            })
            participants.clear()
            participant_connections.clear()
            await broadcast_state()
        elif action == "toggle_auto":
            quiz["auto_advance"] = bool(body.get("enabled", True))
            await broadcast_state()
        else:
            raise HTTPException(404, "Unknown action")
    return {"ok": True, "state": public_view()}

@app.get("/api/results")
async def results():
    return public_view()

@app.get("/api/results/download")
async def results_download():
    await persist_results()
    return FileResponse(str(RESULTS_FILE), media_type="application/json", filename="quiz_results.json")

@app.get("/api/network")
async def network():
    hostname = socket.gethostname()
    ips = []
    try:
        for item in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            ip = item[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return {"ips": ips, "port": 8000}

# -----------------------------
# WebSockets
# -----------------------------
@app.websocket("/ws/participant/{pid}")
async def ws_participant(ws: WebSocket, pid: str):
    await ws.accept()
    if pid not in participants:
        await ws.close(code=1008)
        return
    participant_connections[pid] = ws
    participants[pid]["connected"] = True
    await safe_send(ws, participant_view(pid))
    await broadcast_state()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") != "answer":
                continue
            async with state_lock:
                _, _, q = current_context()
                if quiz["status"] != "running" or q is None:
                    continue
                serial = int(msg.get("question_serial", -1))
                if serial != quiz["question_serial"]:
                    continue
                # Server time is authoritative
                t = now_ms()
                if t < (quiz["question_started_at"] or 0) - 200:
                    continue
                if t > (quiz["question_deadline"] or 0) + 250:
                    continue
                key = str(serial)
                if key in participants[pid]["answers"]:
                    continue  # one submission only
                answer = str(msg.get("answer", "")).strip()
                correct = is_correct(q, answer)
                score = q.get("marks_correct", 2) if correct else q.get("marks_wrong", -1)
                participants[pid]["answers"][key] = {
                    "round_index": quiz["round_index"],
                    "question_index": quiz["question_index"],
                    "question_id": q.get("id"),
                    "answer": answer,
                    "correct": correct,
                    "score": score,
                    "submitted_at": t,
                }
                await safe_send(ws, participant_view(pid))
                await broadcast_state()
    except WebSocketDisconnect:
        pass
    finally:
        if participant_connections.get(pid) is ws:
            participant_connections.pop(pid, None)
        if pid in participants:
            participants[pid]["connected"] = False
        await broadcast_state()

@app.websocket("/ws/host")
async def ws_host(ws: WebSocket):
    await ws.accept()
    host_connections.add(ws)
    await safe_send(ws, public_view())
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        host_connections.discard(ws)

@app.websocket("/ws/projector")
async def ws_projector(ws: WebSocket):
    await ws.accept()
    projector_connections.add(ws)
    await safe_send(ws, public_view())
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        projector_connections.discard(ws)
