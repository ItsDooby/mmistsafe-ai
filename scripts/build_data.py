"""Build reproducible static analysis. Research-generated labels are not lab truth.

Only training rows fit normalization and logistic regression. Existing test rows
are held out; validation rows are unused. No image classifier is fabricated.
"""
from pathlib import Path
import collections, hashlib, json, math, xml.etree.ElementTree as ET, zipfile

ROOT=Path(__file__).resolve().parents[1]
NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
def read(path, name):
    with zipfile.ZipFile(path) as z:
        ss=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in z.namelist() else []
        rel={x.attrib['Id']:x.attrib['Target'] for x in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        sheet=next(s for s in ET.fromstring(z.read('xl/workbook.xml')).find('m:sheets',NS) if s.attrib['name']==name)
        target=rel[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
        target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
        result=[]
        for r in ET.fromstring(z.read(target)).findall('.//m:sheetData/m:row',NS):
            row={}
            for c in r:
                v=c.find('m:v',NS);value=v.text if v is not None else ''
                if c.attrib.get('t')=='s':value=ss[int(value)]
                elif c.attrib.get('t')=='inlineStr':value=''.join(c.find('m:is',NS).itertext())
                row[''.join(ch for ch in c.attrib['r'] if ch.isalpha())]=value
            result.append(row)
        return result[1:]

features=['temperature','ph','ec','turbidity','flow','stagnation','chlorine']
columns=list('EFGHIJK')
rows=read(ROOT/'public/files/simulated-400.xlsx','SIMULATED_RECORDS')
train=[r for r in rows if r['U']=='train'];test=[r for r in rows if r['U']=='test']
assert not {r['A'] for r in train}&{r['A'] for r in test}
means=[sum(float(r[c]) for r in train)/len(train) for c in columns]
scales=[math.sqrt(sum((float(r[c])-means[i])**2 for r in train)/len(train)) or 1 for i,c in enumerate(columns)]
def vector(r):return [(float(r[c])-means[i])/scales[i] for i,c in enumerate(columns)]
def sigmoid(v):return 1/(1+math.exp(-max(-40,min(40,v))))
x=[vector(r) for r in train];y=[int(r['M']=='SIM_POS') for r in train]
weights=[0.0]*7;intercept=0.0
for epoch in range(1800):
    errors=[sigmoid(intercept+sum(a*b for a,b in zip(v,weights)))-label for v,label in zip(x,y)]
    intercept-=.08*sum(errors)/len(x)
    weights=[w-.08*(sum(e*v[j] for e,v in zip(errors,x))/len(x)+.02*w) for j,w in enumerate(weights)]
def prediction(r):
    terms=[a*b for a,b in zip(vector(r),weights)]
    return sigmoid(intercept+sum(terms)),terms
test_predictions=[dict(id=r['A'].replace('SIM-',''),actual=int(r['M']=='SIM_POS'),probability=prediction(r)[0]) for r in test]
tn=fp=fn=tp=0
for r in test_predictions:
    pred=int(r['probability']>=.5)
    if r['actual'] and pred:tp+=1
    elif r['actual']:fn+=1
    elif pred:fp+=1
    else:tn+=1
def divide(a,b):return a/b if b else None
def interval(a,n):
    if not n:return None
    z=1.96;p=a/n;den=1+z*z/n
    centre=(p+z*z/(2*n))/den;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [centre-half,centre+half]
samples=[]
for row in read(ROOT/'public/files/operational-80.xlsx','MistSafe_One_Sheet'):
    score,terms=prediction(row)
    samples.append(dict(id=row['A'].replace('-SIM',''),category=row['D'],**{f:float(row[c]) for f,c in zip(features,columns)},uv=row['L']=='1',sensorOk=row['M']=='1',stop=row['L']!='1' or row['M']!='1' or float(row['I'])<=0,referencePositive=row['S']=='SIM_POS',method=row['R'],sensorScore=round(score*100,3),contributions=[round(v,5) for v in terms],imageScore=None,imageUrl=None,imageQuality='unavailable'))
corr=[]
for i,f in enumerate(features):
    av=sum(float(r[columns[i]]) for r in rows)/len(rows);riskavg=sum(prediction(r)[0] for r in rows)/len(rows)
    a=[float(r[columns[i]])-av for r in rows];b=[prediction(r)[0]-riskavg for r in rows]
    corr.append(dict(feature=f,value=divide(sum(v*w for v,w in zip(a,b)),math.sqrt(sum(v*v for v in a)*sum(w*w for w in b)))))
model=dict(version='S1.0',type='L2 logistic regression',features=features,weights=weights,intercept=intercept,means=means,scales=scales,importance=[round(abs(w)/sum(abs(v) for v in weights)*100,3) for w in weights],correlations=corr)
performance=dict(modelVersion='S1.0',trainCount=len(train),validationCount=sum(r['U']=='validation' for r in rows),testCount=len(test),matrix=dict(tn=tn,fp=fp,fn=fn,tp=tp),sensitivity=divide(tp,tp+fn),specificity=divide(tn,tn+fp),falseNegativeRate=divide(fn,tp+fn),accuracy=divide(tp+tn,len(test)),intervals=dict(sensitivity=interval(tp,tp+fn),specificity=interval(tn,tn+fp)),predictions=test_predictions,trainingIds=[r['A'].replace('SIM-','') for r in train],labelBasis='Research-generated labels; not independently verified laboratory outcomes.')
out=ROOT/'public/analysis';out.mkdir(parents=True,exist_ok=True)
for name,value in [('samples',samples),('model',model),('performance',performance),('microscopy',dict(version=None,trained=False,images=[],reason='No matched image files or quality labels supplied.'))]:
    (out/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'public/files').glob('*.xlsx')}
(out/'provenance.json').write_text(json.dumps(dict(inputs=manifest,labels='Research-generated; not verified laboratory outcomes',method='Train-only standardization; fixed existing hold-out; no image model'),indent=2))
assert len(samples)==80 and len(test)==60 and tp+tn+fp+fn==len(test)
print(f'Built {len(samples)} analysis records; training={len(train)}, hold-out={len(test)}; TP={tp}, TN={tn}, FP={fp}, FN={fn}.')
