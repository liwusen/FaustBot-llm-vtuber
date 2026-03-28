@echo on
cd /d %~dp0
conda activate my-neuro && python configer_pyside6.py
