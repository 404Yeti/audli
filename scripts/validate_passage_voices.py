"""AUD-20 bounded passage audition. No tutor synthesis or runtime config changes."""
import argparse
from array import array
import math
import asyncio
import csv
import hashlib
import html
import json
import os
import subprocess
import wave
from pathlib import Path

import httpx
from dotenv import dotenv_values
from imageio_ffmpeg import get_ffmpeg_exe
from scripts.run_voice_evaluation import write_json

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'eleven_flash_v2_5'
RATES = (.75, 1.0)
MAX_CALLS = 12
MAX_CHARACTERS = 6000
MAX_CREDITS = 6000  # worst case 1 credit/character; no extra credit purchase
PASSAGES = {
    'coast': json.loads((ROOT/'docs/voice-evaluation/scripts.json').read_text())['accent_passage'],
    'library': 'On Friday afternoon, Sarah called the library to ask about a reading group. The meeting usually starts at four, but this week it will begin at half past five because the room is being repaired. Sarah will bring a notebook and a bottle of water. Her brother planned to drive her there, but his car is unavailable. She will take the bus instead and meet him after the discussion. The library is opposite the park, beside the corner shop.',
}


def jobs(discovery):
    rows = []
    if {r['accent'] for r in discovery['voices']} != {'australian', 'british', 'irish'}:
        raise ValueError('Need exactly the three requested candidates')
    for candidate in discovery['voices']:
        voice = candidate['voice']
        if voice['voice_id'] == 'yKYzqEa22xh5PdidhN70':
            raise ValueError('Tutor voice must not be auditioned or replaced')
        if voice.get('sharing', {}).get('rate') not in (None, 0, 1, 1.0):
            raise ValueError('Candidate has a premium multiplier')
        if voice.get('sharing', {}).get('disable_at_unix'):
            raise ValueError('Voice is scheduled for removal')
        models = voice.get('high_quality_base_model_ids') or []
        if models and MODEL not in models:
            raise ValueError('Flash compatibility not confirmed')
        for passage, text in PASSAGES.items():
            for rate in RATES:
                rows.append(dict(sample_id=f'P{len(rows)+1:02}', accent=candidate['accent'],
                    voice_id=voice['voice_id'], name=voice['name'], model=MODEL,
                    passage=passage, text=text, rate=rate, characters=len(text), status='pending'))
    if len(rows) > MAX_CALLS or sum(r['characters'] for r in rows) > MAX_CHARACTERS:
        raise ValueError('Audition budget exceeded')
    subscription = discovery['subscription']
    remaining = subscription['character_limit'] - subscription['character_count']
    if remaining < sum(r['characters'] for r in rows) or subscription['status'] != 'active':
        raise ValueError('Insufficient included credits; no overage permitted')
    model = next(m for m in discovery['models'] if m['model_id'] == MODEL)
    if not model['can_do_text_to_speech'] or model['token_cost_factor'] > 1:
        raise ValueError('Unreviewed model pricing')
    return rows


def inspect_audio(path, words):
    decoded = path.with_suffix('.wav')
    subprocess.run([get_ffmpeg_exe(),'-v','error','-nostdin','-i',str(path),'-ac','1','-ar','16000',str(decoded)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    with wave.open(str(decoded)) as audio:
        duration = audio.getnframes()/audio.getframerate()
        channels = audio.getnchannels()
        samples = array('h', audio.readframes(audio.getnframes()))
    peak = max(abs(sample) for sample in samples)/32768
    clipping = sum(abs(sample)>=32760 for sample in samples)/len(samples)
    rms = math.sqrt(sum(sample*sample for sample in samples)/len(samples))/32768
    decoded.unlink()
    if not 5 < duration < 120:
        raise ValueError('Unexpected sample duration')
    return dict(duration_seconds=round(duration,3), effective_wpm=round(words/duration*60,1),
        decoded_channels=channels, peak_dbfs=round(20*math.log10(max(peak,1e-10)),2),
        rms_dbfs=round(20*math.log10(max(rms,1e-10)),2), clipping_fraction=clipping, bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(), acoustic_validation='human_review_pending')


def review_files(output, rows):
    fields = ['sample_id','rater','accent_familiarity','heard_accent','clarity','consistency',
        'naturalness','esl_suitability','critical_errors','notes']
    with (output/'ratings.csv').open('w',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader()
        writer.writerows({'sample_id':row['sample_id']} for row in rows if row['status']=='saved')
    cards=[]
    for row in rows:
        if row['status'] != 'saved': continue
        cards.append(f'<section><h2>{row["sample_id"]} — {row["passage"]}, rate {row["rate"]}</h2>'
            f'<audio controls preload="none" src="{row["sample_id"]}.mp3"></audio>'
            f'<details><summary>Original test passage (reviewers only)</summary><p>{html.escape(row["text"])}</p></details></section>')
    (output/'review.html').write_text('<!doctype html><meta charset="utf-8"><title>AUD-20 passage review</title>'
        '<style>body{max-width:800px;margin:40px auto;font:18px system-ui}section{padding:20px;border-bottom:1px solid #ddd}audio{width:100%}</style>'
        '<h1>AUD-20 passage audition</h1><p>Identify the accent before opening results.json. Rate each clip 1–5 for clarity, accent consistency, naturalness and ESL suitability. Record specific pronunciation and vowel/rhythm observations, skipped or altered facts, unnatural pauses and clipping. Do not infer authenticity from labels. A knowledgeable human reviewer must complete ratings.csv.</p>'
        '<p>Pass: every clip scores at least 4 in every category, heard accent matches the requested variety, accent familiarity is documented, and critical_errors is explicitly “none”. These two passages are a pilot, not broad accent certification. No clips are learner exercises.</p>' + ''.join(cards))


async def generate(output, env_file):
    discovery=json.loads((output/'discovery.json').read_text())
    rows=jobs(discovery)
    values=dict(dotenv_values(ROOT/'.env'));values.update(dotenv_values(env_file));values.update(os.environ)
    key=values.get('ELEVENLABS_API_KEY') or values.get('XI_API_KEY')
    if not key: raise ValueError('Credential unavailable')
    # Exclusive marker prevents re-running billable jobs after interruption.
    with (output/'synthesis.started').open('x') as file: file.write('No automatic replay of paid requests.\n')
    ledger={'model':MODEL,'attempted':0,'characters_submitted':0,'character_cost_header_sum':0,
        'before_usage':discovery['subscription']['character_count'],'rows':rows,
        'approved_voices':{},'usd_cost':'Not established by credit usage; included credits only'}
    write_json(output/'results.json',ledger)
    async with httpx.AsyncClient(timeout=30,transport=httpx.AsyncHTTPTransport(retries=0)) as client:
        for row in rows:
            ledger['attempted']+=1;ledger['characters_submitted']+=row['characters'];row['status']='attempted'
            write_json(output/'results.json',ledger)
            try:
                async with client.stream('POST','https://api.elevenlabs.io/v1/text-to-speech/'+row['voice_id'],
                    headers={'xi-api-key':key},json={'text':row['text'],'model_id':MODEL,
                    'voice_settings':{'speed':row['rate']}}) as response:
                    row['http_status']=response.status_code
                    if response.status_code != 200: raise ValueError('Synthesis HTTP '+str(response.status_code))
                    credit=response.headers.get('character-cost')
                    row['character_cost_header']=float(credit) if credit is not None else None
                    if credit is not None: ledger['character_cost_header_sum']+=float(credit)
                    data=bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data)>8*1024*1024: raise ValueError('Audio size cap exceeded')
                path=output/(row['sample_id']+'.mp3');path.write_bytes(data)
                row.update(inspect_audio(path,len(row['text'].split())));row['status']='saved'
            except (httpx.HTTPError,ValueError,subprocess.SubprocessError):
                row['status']='failed';write_json(output/'results.json',ledger)
                break  # fail closed; never retry or switch paid voice silently
            write_json(output/'results.json',ledger);print(row['sample_id'],row['status'],flush=True)
        response=await client.get('https://api.elevenlabs.io/v1/user/subscription',headers={'xi-api-key':key})
        if response.status_code==200:
            ledger['after_usage']=response.json().get('character_count')
            if ledger['after_usage'] is not None: ledger['account_delta']=ledger['after_usage']-ledger['before_usage']
    write_json(output/'results.json',ledger);review_files(output,rows)


from app.passage_validation import approved_configuration


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['plan','generate','review','analyze'])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--env-file',type=Path,default=Path('/home/lenovo/.config/audli/elevenlabs.env'))
    args=parser.parse_args()
    if args.mode=='generate': asyncio.run(generate(args.output,args.env_file))
    elif args.mode=='analyze':
        ledger=json.loads((args.output/'results.json').read_text())
        for row in ledger['rows']:
            if row['status']=='saved': row.update(inspect_audio(args.output/(row['sample_id']+'.mp3'),len(row['text'].split())))
        write_json(args.output/'results.json',ledger)
    elif args.mode=='plan':
        rows=jobs(json.loads((args.output/'discovery.json').read_text()))
        print(json.dumps({'requests':len(rows),'characters':sum(r['characters'] for r in rows),
            'maximum_credits':sum(r['characters'] for r in rows),'self_serve_flash_estimated_credits':sum(r['characters'] for r in rows)*.5}))
    else:
        config=approved_configuration(args.output)
        write_json(args.output/'validated-passage-voices.json',config)
        print(json.dumps({'human_reviewed_accents':list(config),'runtime_configuration_changed':False}))

if __name__=='__main__':
    try: main()
    except (httpx.HTTPError,ValueError,OSError,subprocess.SubprocessError) as error:
        print(type(error).__name__+'; inspect sanitized local results; no automatic retry')
        raise SystemExit(1)
