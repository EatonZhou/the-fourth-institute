#!/usr/bin/env python3
"""Start common services from their single source tree, in either pool."""
import argparse
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.portal_dev_generation import read_component_build_ids
from scripts.portal_source_layout import SHARED_ROOT, SHARED_MODULES as MODULES
ROOT = SHARED_ROOT

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('component', choices=MODULES)
args = parser.parse_args()
module, key = MODULES[args.component]
os.environ['PORTAL_BUILD_ID'] = read_component_build_ids().get(key, 'shared')
os.chdir(ROOT)
os.execv(sys.executable, [sys.executable, '-m', module])
