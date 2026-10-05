"""Opt-in real-provider evaluator regression. Uses paid API calls, prints aggregate results only."""
import asyncio
import json
from pathlib import Path
from dotenv import load_dotenv
from app.config import Settings
from app.evaluation import score_judgments
from app.models import LearnerProfile
from app.services.demo import DemoProvider
from app.services.openai_provider import OpenAIProvider

async def main():
    load_dotenv()
    provider=OpenAIProvider(Settings())
    exercise=await DemoProvider().generate(LearnerProfile())
    cases=json.loads(Path('tests/fixtures/evaluator_cases.json').read_text())
    scores={}
    failed=[]
    try:
        for case in cases:
            judgments=await provider.evaluate(exercise,case['response'])
            score=score_judgments(exercise,judgments,case['response']).overall
            scores[case['name']]=score
            lo,hi=case['overall_range']
            passed=lo<=score<=hi and not judgments.transcription_concern
            print(f"{case['name']}: {score:.0%}, {'PASS' if passed else 'FAIL'}")
            if not passed:
                failed.append(case['name'])
        if scores['poor_grammar_correct_meaning'] < scores['excellent']-.1:
            failed.append('grammar fairness')
        if failed:
            raise SystemExit('Evaluator regression failed: '+', '.join(failed))
    finally:
        await provider.client.close()

if __name__=='__main__':
    asyncio.run(main())
