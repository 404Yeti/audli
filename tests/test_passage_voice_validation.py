import csv
import hashlib
import json
import pytest
from scripts.validate_passage_voices import jobs, approved_configuration, MODEL


def discovery():
    return {'voices':[{'accent':accent,'voice':{'voice_id':str(index)*20,'name':accent,
        'sharing':{'rate':1},'high_quality_base_model_ids':[MODEL]}} for index,accent in enumerate(('australian','british','irish'),1)],
        'subscription':{'character_count':0,'character_limit':10000,'status':'active'},
        'models':[{'model_id':MODEL,'can_do_text_to_speech':True,'token_cost_factor':1}]}


def test_plan_bounded_no_tutor_consistent_passages():
    rows=jobs(discovery())
    assert len(rows)==12 and sum(r['characters'] for r in rows)==5442
    for passage in ('coast','library'):
        assert len({r['text'] for r in rows if r['passage']==passage})==1
    assert {r['rate'] for r in rows}=={.75,1.0}
    assert all(r['voice_id']!='yKYzqEa22xh5PdidhN70' for r in rows)

@pytest.mark.parametrize('kind',['premium','credits','model','tutor'])
def test_preflight_rejects_extra_cost_and_tutor(kind):
    data=discovery()
    if kind=='premium': data['voices'][0]['voice']['sharing']['rate']=2
    elif kind=='credits': data['subscription']['character_limit']=1
    elif kind=='model': data['models'][0]['token_cost_factor']=2
    else: data['voices'][0]['voice']['voice_id']='yKYzqEa22xh5PdidhN70'
    with pytest.raises(ValueError): jobs(data)


def reviewed(tmp_path,ratings=True):
    rows=jobs(discovery())
    for row in rows:
        data=row['sample_id'].encode();(tmp_path/(row['sample_id']+'.mp3')).write_bytes(data)
        row.update(status='saved',sha256=hashlib.sha256(data).hexdigest())
    (tmp_path/'results.json').write_text(json.dumps({'rows':rows}))
    fields=['sample_id','rater','accent_familiarity','heard_accent','clarity','consistency','naturalness','esl_suitability','critical_errors','notes']
    values=[]
    for row in rows:
        values.append(dict(sample_id=row['sample_id'],rater='Fixture reviewer' if ratings else '',
            accent_familiarity='Fixture evidence only',heard_accent=row['accent'],clarity='4',consistency='4',naturalness='4',
            esl_suitability='4',critical_errors='none',notes='Fixture evidence, never real approval'))
    def save():
        with (tmp_path/'ratings.csv').open('w',newline='') as file:
            writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader();writer.writerows(values)
    save()
    return values,save


def test_blank_ratings_never_enable_offers(tmp_path):
    reviewed(tmp_path,False)
    assert approved_configuration(tmp_path)=={}


def test_complete_documented_review_can_emit_minimal_config(tmp_path):
    values,save=reviewed(tmp_path)
    values[8]['heard_accent']='british';save()
    config=approved_configuration(tmp_path)
    assert set(config)=={'australian','british'}
    assert config['australian']['voice_id']=='1'*20

@pytest.mark.parametrize('failure',['low','critical','missing','changed_audio','nan'])
def test_approval_rejects_incomplete_or_changed_evidence(tmp_path,failure):
    values,save=reviewed(tmp_path)
    if failure=='low': values[0]['clarity']='3'
    if failure=='critical': values[0]['critical_errors']='wrong meeting time'
    if failure=='missing': values.pop(0)
    if failure=='changed_audio': (tmp_path/'P01.mp3').write_bytes(b'changed')
    if failure=='nan': values[0]['clarity']='nan'
    save()
    assert 'australian' not in approved_configuration(tmp_path)


def owner_record(tmp_path):
    ledger_bytes=(tmp_path/'results.json').read_bytes()
    rows=json.loads(ledger_bytes)['rows']
    record=dict(version=1, decision_id='fixture-owner-decision', authority='product_owner',
        decision='approved', scope='all_authenticated_learners', approved_at='2026-10-11T00:00:00+00:00',
        source='Explicit fixture owner instruction', statement='Fixture approval only, never approval of production voices.',
        numerical_ratings_waived=True, results_sha256=hashlib.sha256(ledger_bytes).hexdigest(),
        voices={accent:dict(voice_id=next(r['voice_id'] for r in rows if r['accent']==accent),
            name=next(r['name'] for r in rows if r['accent']==accent),
            sample_hashes={r['sample_id']:r['sha256'] for r in rows if r['accent']==accent})
            for accent in ('australian','british','irish')})
    (tmp_path/'product-owner-approval.json').write_text(json.dumps(record))
    return record


def test_owner_approval_preserves_blank_csv_and_supports_absent_csv(tmp_path):
    reviewed(tmp_path,False)
    original=(tmp_path/'ratings.csv').read_bytes()
    owner_record(tmp_path)
    config=approved_configuration(tmp_path)
    assert set(config)=={'australian','british','irish'}
    assert all('numerical ratings waived' in item['validation'] for item in config.values())
    assert (tmp_path/'ratings.csv').read_bytes()==original
    (tmp_path/'ratings.csv').unlink()
    assert approved_configuration(tmp_path)==config


@pytest.mark.parametrize('failure',['authority','waiver','scope','ledger_hash','unknown_field','voice','sample_hash','audio','model','rate','missing_audio'])
def test_owner_approval_cannot_bypass_identity_integrity_or_model_gates(tmp_path,failure):
    reviewed(tmp_path,False)
    record=owner_record(tmp_path)
    if failure=='authority': record['authority']='ai'
    elif failure=='waiver': record['numerical_ratings_waived']=False
    elif failure=='scope': record['scope']='pilot'
    elif failure=='ledger_hash': record['results_sha256']='0'*64
    elif failure=='unknown_field': record['automatic_approval']=True
    elif failure=='voice': record['voices']['australian']['voice_id']='9'*20
    elif failure=='sample_hash': record['voices']['australian']['sample_hashes']['P01']='0'*64
    elif failure=='audio': (tmp_path/'P01.mp3').write_bytes(b'changed')
    elif failure=='missing_audio': (tmp_path/'P01.mp3').unlink()
    else:
        ledger=json.loads((tmp_path/'results.json').read_bytes())
        ledger['rows'][0]['model' if failure=='model' else 'rate']='unsupported'
        (tmp_path/'results.json').write_text(json.dumps(ledger))
        record['results_sha256']=hashlib.sha256((tmp_path/'results.json').read_bytes()).hexdigest()
    (tmp_path/'product-owner-approval.json').write_text(json.dumps(record))
    assert 'australian' not in approved_configuration(tmp_path)
