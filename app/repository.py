import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from app.models import ExerciseContent, LearnerProfile, Transcription

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS learner_profiles (user_id TEXT PRIMARY KEY REFERENCES users(id), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exercises (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), content TEXT NOT NULL, difficulty TEXT NOT NULL, audio_name TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, exercise_id TEXT NOT NULL REFERENCES exercises(id), transcription TEXT NOT NULL, confirmed_text TEXT, status TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE UNIQUE INDEX IF NOT EXISTS one_evaluation_per_exercise ON attempts(exercise_id) WHERE status='evaluated';
CREATE TABLE IF NOT EXISTS evaluation_results (attempt_id TEXT PRIMARY KEY REFERENCES attempts(id), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS adaptation_events (id TEXT PRIMARY KEY, attempt_id TEXT UNIQUE NOT NULL REFERENCES attempts(id), data TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
'''

class ProgressRepository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute('INSERT OR IGNORE INTO users(id) VALUES (?)', ('local',))
            db.execute('INSERT OR IGNORE INTO learner_profiles VALUES (?, ?)', ('local', LearnerProfile().model_dump_json()))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def profile(self):
        with self.connect() as db:
            return LearnerProfile.model_validate_json(db.execute('SELECT data FROM learner_profiles WHERE user_id=?', ('local',)).fetchone()[0])

    def save_profile(self, profile):
        with self.connect() as db:
            db.execute('UPDATE learner_profiles SET data=? WHERE user_id=?', (profile.model_dump_json(), 'local'))

    def current_exercise(self):
        with self.connect() as db:
            row = db.execute('SELECT * FROM exercises WHERE user_id=? ORDER BY rowid DESC LIMIT 1', ('local',)).fetchone()
            return dict(row) if row else None

    def exercise(self, exercise_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM exercises WHERE id=? AND user_id=?', (exercise_id, 'local')).fetchone()
            return dict(row) if row else None

    def save_exercise(self, exercise_id, content, difficulty, audio_name):
        with self.connect() as db:
            db.execute('INSERT INTO exercises(id,user_id,content,difficulty,audio_name) VALUES (?,?,?,?,?)',
                       (exercise_id, 'local', content.model_dump_json(), difficulty.model_dump_json(), audio_name))

    def add_attempt(self, exercise_id, transcription: Transcription):
        attempt_id = str(uuid4())
        with self.connect() as db:
            db.execute('INSERT INTO attempts(id,exercise_id,transcription,status) VALUES (?,?,?,?)',
                       (attempt_id, exercise_id, transcription.model_dump_json(), 'transcribed'))
        return attempt_id

    def attempt(self, attempt_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM attempts WHERE id=?', (attempt_id,)).fetchone()
            return dict(row) if row else None

    def completed(self, exercise_id):
        with self.connect() as db:
            row = db.execute("SELECT id FROM attempts WHERE exercise_id=? AND status='evaluated' LIMIT 1", (exercise_id,)).fetchone()
            return row['id'] if row else None

    def result(self, attempt_id):
        with self.connect() as db:
            evaluation = db.execute('SELECT data FROM evaluation_results WHERE attempt_id=?', (attempt_id,)).fetchone()
            event = db.execute('SELECT data FROM adaptation_events WHERE attempt_id=?', (attempt_id,)).fetchone()
            return {'evaluation': json.loads(evaluation[0]), 'adaptation': json.loads(event[0])} if evaluation else None

    def complete(self, attempt_id, text, evaluation, event, profile):
        with self.connect() as db:
            # Atomic compare-and-set, even if a second process races this instance.
            db.execute('BEGIN IMMEDIATE')
            attempt = db.execute('SELECT * FROM attempts WHERE id=?', (attempt_id,)).fetchone()
            duplicate = db.execute("SELECT id FROM attempts WHERE exercise_id=? AND status='evaluated'", (attempt['exercise_id'],)).fetchone()
            if duplicate:
                raise ValueError('Exercise already evaluated')
            db.execute("UPDATE attempts SET status='evaluated', confirmed_text=? WHERE id=?", (text, attempt_id))
            db.execute('INSERT INTO evaluation_results VALUES (?,?)', (attempt_id, evaluation.model_dump_json()))
            db.execute('INSERT INTO adaptation_events(id,attempt_id,data) VALUES (?,?,?)', (str(uuid4()), attempt_id, event.model_dump_json()))
            db.execute('UPDATE learner_profiles SET data=? WHERE user_id=?', (profile.model_dump_json(), 'local'))

    def history(self):
        with self.connect() as db:
            rows = db.execute('SELECT a.id,a.exercise_id,a.created_at,e.data AS evaluation,v.data AS adaptation FROM attempts a JOIN evaluation_results e ON e.attempt_id=a.id JOIN adaptation_events v ON v.attempt_id=a.id ORDER BY a.rowid').fetchall()
            return [{**dict(r), 'evaluation': json.loads(r['evaluation']), 'adaptation': json.loads(r['adaptation'])} for r in rows]
