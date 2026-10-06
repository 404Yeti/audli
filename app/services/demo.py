import asyncio
from app.evaluation import expected_units
from app.models import ExerciseContent, EvaluationJudgments, Transcription, EvidenceAssessment

SCRIPT = ('On Monday, Maya and her team launched an update to their office scheduling app. '
          'Soon, several customers said they could not save new appointments. The team discovered '
          'that a database migration had removed a field the app still needed. They rolled back '
          'the update, and customers could save appointments again. Maya postponed the next release '
          'until Friday. Before trying again, the team will test the migration on a copy of the '
          'database. The problem was fixed quickly, but Maya wants better checks to prevent it from happening again.')

class DemoProvider:
    async def generate(self, profile):
        d = profile.difficulty
        return ExerciseContent(title='A postponed update', topic='technology', script=SCRIPT,
            target_duration_seconds=d.duration_seconds, vocabulary_level=d.vocabulary_level,
            speech_rate=d.speech_rate, information_density=d.information_density,
            main_idea='A team fixed a failed app update by rolling it back and plans safer testing.',
            important_details=['A database migration removed a needed field.', 'Customers could not save appointments.', 'The next release was postponed until Friday.'],
            inference_points=['The team wants to prevent the same failure by testing before release.'],
            vocabulary_items=[{'phrase': 'rolled back', 'meaning_in_context': 'returned to the previous app version'}, {'phrase': 'postponed', 'meaning_in_context': 'delayed until later'}],
            questions=[{'question': 'What went wrong and what did the team do?', 'expected_information': ['database migration', 'rolled back']}])

    async def speech(self, exercise):
        return await self.speak(exercise.script, exercise.speech_rate)

    async def speak(self, text, speech_rate=.9):
        process = await asyncio.create_subprocess_exec('espeak', '--stdout', '-s', str(round(150*speech_rate)),
            text, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        audio, _ = await process.communicate()
        if process.returncode:
            raise ValueError('espeak failed')
        return audio

    async def transcribe(self, audio, filename):
        return Transcription(text='', source='demo_manual', uncertainty=['Demo mode has no speech recognition. Type what you said in the recording.'])

    async def evaluate(self, exercise, transcript):
        # Explicitly synthetic: validates flow, not comprehension or AI quality.
        return EvaluationJudgments(units=[{'dimension': dim, 'index': i, 'status': 'partial', 'evidence': transcript}
            for dim, units in expected_units(exercise).items() for i in range(len(units))],
            feedback='Demo result only: every unit receives a fixed partial score. Use OpenAI mode to evaluate comprehension.',
            transcription_concern=False, concern_reason='')

    async def assess(self, exercise, turns):
        return EvidenceAssessment(units=[{'dimension': dim, 'index': i, 'status': 'partially_demonstrated',
            'evidence': turns[0].text[:1000], 'question': ''} for dim, units in expected_units(exercise).items()
            for i in range(len(units))], feedback='Demo feedback only. Real comprehension needs the OpenAI provider.',
            transcription_concern=False, concern_reason='')
