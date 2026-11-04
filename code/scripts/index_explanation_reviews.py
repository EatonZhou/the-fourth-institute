#!/usr/bin/env python3
"""Index existing authenticated explanation submissions without reading source assets."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_portal.home_explanation_review import ReviewService

if __name__ == '__main__':
    print(json.dumps(ReviewService().index_existing_packages(), ensure_ascii=False))
