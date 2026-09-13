# EEE Live Quiz

A classroom quiz system designed for one synchronized projector + many participant phones.

## Core behavior

- Host laptop is the authority for the active round, question and deadline.
- Every phone/projector receives the same live state over WebSockets.
- Each question has its own:
  - time limit
  - correct marks
  - wrong/negative marks
  - unanswered marks field
  - MCQ or short-answer type
- A participant can submit only once for the active question.
- Late submissions are rejected by the server, even if a phone's timer is inaccurate.
- When the server timer expires, the whole room advances together if Auto-advance is enabled.
- Live scoreboard and result export are included.

## Before the event

1. Install Python 3.11+ on the host laptop.
2. Connect the laptop and all participant phones to the SAME Wi-Fi network.
   - A normal router is strongly recommended for a large class.
   - A laptop/phone hotspot may work but can have client limits.
3. Extract this folder.
4. Double-click `RUN_QUIZ.bat`.
5. Windows Firewall may ask for permission. Allow Python on **Private networks**.
6. On the laptop open:
   - Host: `http://localhost:8000/host`
   - Projector: `http://localhost:8000/projector`
7. The Host page shows possible participant URLs such as:
   - `http://192.168.1.25:8000/`
8. Share that URL or make a QR code from it.
9. Ask everyone to join BEFORE starting Round 1.

## Questions

Open the Host page and use the Question Bank Editor.

Question example:

```json
{
  "id": "R1Q1",
  "type": "mcq",
  "text": "Which component stores energy in an electric field?",
  "options": ["Inductor", "Capacitor", "Resistor", "Diode"],
  "answer": "Capacitor",
  "time_limit": 15,
  "marks_correct": 2,
  "marks_wrong": -1,
  "marks_unanswered": 0
}
```

Short answer example:

```json
{
  "id": "R1Q2",
  "type": "short",
  "text": "Write the SI unit of electrical resistance.",
  "accepted_answers": ["ohm", "ohms", "Ω"],
  "time_limit": 12,
  "marks_correct": 2,
  "marks_wrong": -1,
  "marks_unanswered": 0
}
```

## For 50-100+ students

The software is lightweight, but your **network** is the important part.

Recommended:
- use a proper Wi-Fi router/access point
- laptop connected to the router by Ethernet if possible
- disable laptop sleep
- plug laptop into power
- close heavy downloads/cloud sync
- test with at least 10-15 phones before the actual event

This version sends small JSON WebSocket messages, so bandwidth use is low.

## Synchronization design

The host server creates `question_started_at` and `question_deadline` timestamps. Phones calculate their visible countdown against the server clock. The server independently rejects a submission after the deadline. This means the student's phone does not decide when the question ends.

A 700 ms start buffer is intentionally added before each question. The server broadcasts the question first, then the common timer starts. This helps devices with small network-delay differences start more fairly.

## Important note

The included sample question bank contains only a few sample questions per round. Replace/add questions until you have your full 4 x 15 = 60 questions.

## Results

The Host page shows a live leaderboard. Press **Download Results JSON** to save complete result data.

