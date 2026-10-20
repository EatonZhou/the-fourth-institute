"""Portal Web and applications with explicit shared-runtime package ownership."""
from pathlib import Path
from scripts.portal_source_layout import activate_shared_imports

activate_shared_imports(Path(__file__).resolve().parent.parent)
