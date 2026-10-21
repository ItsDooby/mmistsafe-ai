"""Vendor the open fonts used by the bilingual presentation."""
import re
import urllib.request
from pathlib import Path
root=Path(__file__).resolve().parents[1]
folder=root/'public'/'fonts'
folder.mkdir(parents=True,exist_ok=True)
css=urllib.request.urlopen('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap').read().decode()
for block in re.findall(r'@font-face\s*\{[^}]+\}',css):
    family=re.search(r"font-family: '([^']+)'",block)[1]
    weight=re.search(r'font-weight: (\d+)',block)[1]
    url=re.search(r'url\(([^)]+)\)',block)[1]
    name=family.lower().replace(' ','-')+'-'+weight+'.ttf'
    urllib.request.urlretrieve(url,folder/name)
    css=css.replace(url,'./'+name)
for family,path in [('ibm-plex-sans-arabic','ibmplexsansarabic'),('manrope','manrope')]:
    urllib.request.urlretrieve('https://raw.githubusercontent.com/google/fonts/main/ofl/'+path+'/OFL.txt',folder/(family+'-LICENSE.txt'))
(folder/'fonts.css').write_text(css)
print('Downloaded fonts and OFL licences.')
