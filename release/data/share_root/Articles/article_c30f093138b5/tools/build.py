"""Build the article with XeLaTeX. Generated files stay in build/."""
from pathlib import Path
import argparse, subprocess, shutil
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--figures',action='store_true');args=p.parse_args()
if args.figures:subprocess.run(['python3','tools/make_figures.py'],cwd=root,check=True)
(root/'build').mkdir(exist_ok=True)
for iteration in range(3):
    r=subprocess.run(['xelatex','-interaction=nonstopmode','-halt-on-error','-output-directory=build','main.tex'],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    (root/'build'/f'pass-{iteration+1}.log').write_text(r.stdout)
    if r.returncode:
        print('\n'.join(r.stdout.splitlines()[-18:]));raise SystemExit(r.returncode)
shutil.copy2(root/'build'/'main.pdf',root/'article.pdf')
print(subprocess.run(['pdfinfo','article.pdf'],cwd=root,capture_output=True,text=True).stdout)
