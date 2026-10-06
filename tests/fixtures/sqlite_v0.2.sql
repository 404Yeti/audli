
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS learner_profiles (user_id TEXT PRIMARY KEY REFERENCES users(id), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exercises (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), content TEXT NOT NULL, difficulty TEXT NOT NULL, audio_name TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, exercise_id TEXT NOT NULL REFERENCES exercises(id), transcription TEXT NOT NULL, confirmed_text TEXT, status TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE UNIQUE INDEX IF NOT EXISTS one_evaluation_per_exercise ON attempts(exercise_id) WHERE status='evaluated';
CREATE TABLE IF NOT EXISTS evaluation_results (attempt_id TEXT PRIMARY KEY REFERENCES attempts(id), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversations (exercise_id TEXT PRIMARY KEY REFERENCES exercises(id), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS coach_audio (exercise_id TEXT NOT NULL REFERENCES exercises(id), cue_id TEXT NOT NULL, audio_name TEXT NOT NULL, PRIMARY KEY(exercise_id,cue_id));
CREATE TABLE IF NOT EXISTS adaptation_events (id TEXT PRIMARY KEY, attempt_id TEXT UNIQUE NOT NULL REFERENCES attempts(id), data TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
