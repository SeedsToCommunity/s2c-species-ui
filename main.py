# main.py — single-file Flask app for Replit/iPhone
from flask import Flask, request
import os, csv

app = Flask(__name__)

DEMO_SPECIES = [
    {"botanical": "Asclepias syriaca", "common": "Common milkweed",
     "family": "Apocynaceae", "seed_window": "Aug–Sep", "has_guide": True},
    {"botanical": "Asclepias tuberosa", "common": "Butterfly milkweed",
     "family": "Apocynaceae", "seed_window": "Aug–Sep", "has_guide": True},
    {"botanical": "Rudbeckia hirta", "common": "Black-eyed Susan",
     "family": "Asteraceae", "seed_window": "Sep–Oct", "has_guide": False},
    {"botanical": "Corydalis flavula", "common": "Yellow fumewort",
     "family": "Papaveraceae", "seed_window": "May–Jun", "has_guide": False},
]

def _to_bool(v):
    s = str(v).strip().lower()
    return s in ("1","true","yes","y")

def load_species():
    path = "plants.csv"
    if not os.path.exists(path):
        return DEMO_SPECIES
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            out.append({
                "botanical": r.get("