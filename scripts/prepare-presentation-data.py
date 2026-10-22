"""Extract the user-supplied workbooks without Excel dependencies or invented records."""
import collections
import hashlib
import json
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / 'Downloads'
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def sheets(path):
    result = {}
    with zipfile.ZipFile(path) as z:
        strings = [''.join(e.itertext()) for e in ET.fromstring(z.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in z.namelist() else []
        relationships = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        for sheet in ET.fromstring(z.read('xl/workbook.xml')).find('m:sheets', NS):
            target = relationships[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
            target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            rows = []
            for row in ET.fromstring(z.read(target)).findall('.//m:sheetData/m:row', NS):
                cells = {}
                for c in row:
                    column = ''.join(ch for ch in c.attrib['r'] if ch.isalpha())
                    v = c.find('m:v', NS)
                    value = v.text if v is not None else ''
                    if c.attrib.get('t') == 's': value = strings[int(value)]
                    elif c.attrib.get('t') == 'inlineStr': value = ''.join(c.find('m:is', NS).itertext())
                    cells[column] = value
                rows.append(cells)
            result[sheet.attrib['name']] = rows
    return result

operational = 'بيانات التسلسل التشغيلي لمشروع MistSafe AI.xlsx'
research = 'MistSafe_AI_Research_Grounded_Database.xlsx'
archive = 'MistSafe_AI_Simulated_Training_400 (1).xlsx'
records = []
for row in sheets(SOURCE / operational)['MistSafe_One_Sheet'][1:]:
    def number(c): return float(row[c])
    risk = min(100, max(0, (number('E') - 25) * 1.5 + number('H') * 2 + number('J') * .25 + (15 if number('K') < .2 else 0)))
    records.append(dict(id=row['A'], time=row['B'], target=row['D'], temperature=number('E'), ph=number('F'), ec=number('G'), turbidity=number('H'), flow=number('I'), stagnation=number('J'), chlorine=number('K'), uv=row['L']=='1', sensor=row['M']=='1', imageId=row['N'], labId=row['Q'], method=row['R'], labResult=row['S'], risk=round(risk, 3), band='high' if risk>=60 else 'medium' if risk>=30 else 'low', stop=row['L']!='1' or row['M']!='1' or number('I')<=0, provenance=row['Z']))

rs = sheets(SOURCE / research)
images = [dict(id=r['A'], organism=r['B'], modality=r['C'], origin=r['E'], publisher=r['F'], url=r['H'], license=r['I'], limitation=r['M']) for r in rs['Verified_Images'][1:]]
sources = [dict(id=r['A'], name=r['B'], publisher=r['C'], description=r['D'], url=r['G'], catalog=r['H'], doi=r['I'], status=r['K'], pairing=r['L']) for r in rs['Research_Datasets'][1:]]
archive_rows = sheets(SOURCE / archive)['SIMULATED_RECORDS'][1:]
out = ROOT / 'public' / 'files'
out.mkdir(parents=True, exist_ok=True)
manifest = []
for original, dest in [(operational,'operational-80.xlsx'), (archive,'simulated-400.xlsx'), (research,'research-register.xlsx')]:
    shutil.copyfile(SOURCE / original, out / dest)
    manifest.append(dict(original=original, file=dest, sha256=hashlib.sha256((SOURCE / original).read_bytes()).hexdigest()))
data = dict(samples=records, images=images, sources=sources, archiveCount=len(archive_rows), archiveClasses=dict(collections.Counter(r['B'] for r in archive_rows)), files=manifest)
(ROOT / 'legacy' / 'ui' / 'presentation-data.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'Extracted {len(records)} operational records, {len(images)} image references, {len(sources)} research sources; archive: {len(archive_rows)} records.')
