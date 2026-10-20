SHELL := /bin/bash
.PHONY: all check clean
all:
	latexmk -xelatex -interaction=nonstopmode -halt-on-error -file-line-error -outdir=build main.tex
	cp build/main.pdf 思维论.pdf
check:
	python3 scripts/check_theory.py
	python3 scripts/check_manuscript.py
clean:
	latexmk -C -outdir=build main.tex
