"""Independent assertions on the generated evaluation and source fidelity."""
import json,math
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=json.loads((root/'public/analysis/performance.json').read_text())
s=json.loads((root/'public/analysis/samples.json').read_text())
m=json.loads((root/'public/analysis/model.json').read_text())
assert len(s)==80 and len({r['id'] for r in s})==80
assert len(p['predictions'])==p['testCount']==60
assert not set(p['trainingIds']) & {r['id'] for r in p['predictions']}
actual={'tn':0,'fp':0,'fn':0,'tp':0}
for r in p['predictions']:
    pred=r['probability']>=.5
    actual['tp' if r['actual'] and pred else 'fn' if r['actual'] else 'fp' if pred else 'tn']+=1
assert actual==p['matrix']
assert math.isclose(p['sensitivity'],actual['tp']/(actual['tp']+actual['fn']))
assert math.isclose(p['specificity'],actual['tn']/(actual['tn']+actual['fp']))
assert math.isclose(p['falseNegativeRate'],1-p['sensitivity'])
assert math.isclose(p['accuracy'],(actual['tp']+actual['tn'])/p['testCount'])
for r in s:
    assert r['imageScore'] is None and r['imageUrl'] is None
    assert r['stop']==(not r['uv'] or not r['sensorOk'] or r['flow']<=0)
    z=m['intercept']+sum((r[f]-m['means'][i])/m['scales'][i]*m['weights'][i] for i,f in enumerate(m['features']))
    assert abs(r['sensorScore']-100/(1+math.exp(-z)))<.001
    assert 0<=r['sensorScore']<=100
assert sum(r['category']=='Legionella' for r in s)==20
assert not json.loads((root/'public/analysis/microscopy.json').read_text())['trained']
print('PASS: held-out separation, recomputed metrics, inference, independent stop rule, missing-image handling, counts.')
