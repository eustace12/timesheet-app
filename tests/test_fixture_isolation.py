"""Development/test tooling must not consume an inherited deployment database."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_ui_renderer_isolated_before_import_even_with_deployment_environment():
    root = Path(__file__).resolve().parents[1]
    script = '''import runpy,sys
from pathlib import Path
from unittest.mock import patch
import db
original = db.configure
root = Path.cwd()
def checked(path):
    assert not Path(path).is_relative_to(root), 'fixture touched development persistence'
    return original(path)
with patch.object(db, 'configure', side_effect=checked), patch.object(db, '_client', side_effect=AssertionError('fixture used external persistence')):
    runpy.run_path('tests/ui/render_fixtures.py', run_name='__main__')
'''
    environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'LANG') if key in os.environ}
    environment.update(APP_MODE='production', SUPABASE_URL='https://unexpected.invalid',
                       SUPABASE_SERVICE_ROLE_KEY='synthetic-nonworking-fixture-key')
    result = subprocess.run([sys.executable, '-c', script], cwd=root, env=environment,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    pages = json.loads(result.stdout)
    assert 'approved_leave_admin' in pages
    assert 'production' in pages
