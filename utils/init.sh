#!/usr/bin/env bash
pip install -r "requirements.txt"
playwright install chromium
python3 env_gen.py

