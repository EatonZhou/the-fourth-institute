"""Page application calls keep endpoint, identity and retry semantics separate."""
from pathlib import Path
import shutil
import subprocess


def test_application_transport_and_isa_do_not_share_catalog_or_endpoint():
    script = Path(__file__).with_name('javascript') / 'application_tool_client.cjs'
    subprocess.run([shutil.which('node') or 'node', str(script)], check=True, timeout=20)
