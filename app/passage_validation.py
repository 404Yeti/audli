"""Offline human approval and source-integrity gate; never synthesizes audio."""
import csv
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class OwnerVoiceApproval(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    voice_id: str = Field(pattern=r'^[A-Za-z0-9]{20}$')
    name: str = Field(min_length=1)
    sample_hashes: dict[str, str]


class OwnerApproval(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Literal[1]
    decision_id: str = Field(min_length=10)
    authority: Literal['product_owner']
    decision: Literal['approved']
    scope: Literal['all_authenticated_learners']
    approved_at: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00$')
    source: str = Field(min_length=20)
    statement: str = Field(min_length=40)
    numerical_ratings_waived: Literal[True]
    results_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    voices: dict[Literal['australian', 'british', 'irish'], OwnerVoiceApproval]


def owner_approval(output, ledger_bytes):
    path = output / 'product-owner-approval.json'
    if not path.exists():
        return None
    try:
        approval = OwnerApproval.model_validate(json.loads(path.read_text()))
        if approval.results_sha256 != hashlib.sha256(ledger_bytes).hexdigest():
            return None
        return approval
    except (OSError, ValueError, ValidationError):
        return None

def approved_configuration(output):
    ledger_bytes=(output/'results.json').read_bytes()
    ledger=json.loads(ledger_bytes)
    approval=owner_approval(output, ledger_bytes)
    rating_path=output/'ratings.csv'
    ratings=list(csv.DictReader(rating_path.open())) if rating_path.exists() else []
    approved={}
    for accent in ('australian','british','irish'):
        clips=[row for row in ledger['rows'] if row['accent']==accent]
        valid=(len(clips)==4
            and {(c.get('passage'),c.get('rate')) for c in clips}=={('coast',.75),('coast',1.0),('library',.75),('library',1.0)}
            and all(c.get('model')=='eleven_flash_v2_5' for c in clips)
            and len({c.get('voice_id') for c in clips})==1)
        owner_voice=approval.voices.get(accent) if approval else None
        owner_passed=bool(owner_voice and valid
            and owner_voice.voice_id==clips[0]['voice_id']
            and owner_voice.name==clips[0].get('name')
            and owner_voice.sample_hashes=={c['sample_id']:c.get('sha256') for c in clips}
            and len(owner_voice.sample_hashes)==4)
        for clip in clips:
            path=output/(clip['sample_id']+'.mp3')
            if clip['status']!='saved' or not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=clip.get('sha256'):
                valid=False;continue
            reviews=[r for r in ratings if r['sample_id']==clip['sample_id']]
            def passes(r):
                try:
                    return (bool(r['rater'].strip()) and len(r['accent_familiarity'].strip())>=10
                        and r['heard_accent'].strip().lower()==accent
                        and r['critical_errors'].strip().lower()=='none' and len(r['notes'].strip())>=10
                        and all(4<=float(r[f])<=5 for f in ('clarity','consistency','naturalness','esl_suitability')))
                except (ValueError,KeyError): return False
            if not owner_passed and (not reviews or not all(passes(r) for r in reviews)): valid=False
        if valid:
            approved[accent]={'voice_id':clips[0]['voice_id'],
                'validation':(str(output/'product-owner-approval.json')+'; '+approval.decision_id+
                    '; explicit product-owner qualitative approval; numerical ratings waived; source hashes verified'
                    if owner_passed else str(rating_path)+'; all four Flash passage samples approved by documented human review')}
    return approved

