"""Exercise a built company image using only the isolated demo's persistence.

Runtime credentials are inherited only by the container, never printed or saved.
An optional loopback-only GET preview supplies a synthetic SSO identity; it is a
test adapter, not a company identity provider or a deployment configuration.
"""
import argparse
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import subprocess
import time
from urllib.parse import urlsplit

parser = argparse.ArgumentParser()
parser.add_argument('--image', default='urchin-people:company-20261102')
parser.add_argument('--preview', action='store_true')
args = parser.parse_args()
if urlsplit(os.environ['SUPABASE_URL']).hostname != 'rdoamaeixdzkddvnlvto.supabase.co':
    raise SystemExit('This smoke test accepts only the isolated fictional-data demo.')
environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT') if key in os.environ}
environment.update({key: os.environ[key] for key in ('SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY')})
environment.update(SECRET_KEY=secrets.token_urlsafe(48), SSO_PROXY_SECRET=secrets.token_urlsafe(48),
                   APP_MODE='production', DEMO_MODE='0',
                   SSO_EMAIL_MAPPING_JSON=json.dumps({'release.employee@example.test': 'alice'}))
command = ['docker', 'run', '--detach', '--rm', '--platform', 'linux/amd64', '--read-only',
           '--cap-drop=ALL', '--security-opt=no-new-privileges',
           '--tmpfs', '/tmp:rw,noexec,nosuid,size=536870912', '-p', '127.0.0.1:8084:8080']
for name in ('APP_MODE', 'DEMO_MODE', 'SECRET_KEY', 'SSO_PROXY_SECRET', 'SSO_EMAIL_MAPPING_JSON',
             'SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY'):
    command.extend(['--env', name])
command.append(args.image)
result = subprocess.run(command, env=environment, capture_output=True, text=True)
if result.returncode:
    raise SystemExit('Container launch failed; no environment or Docker error payload was printed.')
container = result.stdout.strip()
headers = {'X-Urchin-Proxy-Secret': environment['SSO_PROXY_SECRET'],
           'X-Auth-Request-Email': 'release.employee@example.test'}


def get(path, supplied=None):
    connection = HTTPConnection('127.0.0.1', 8084, timeout=25)
    try:
        connection.request('GET', path, headers=supplied or {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


try:
    for attempt in range(45):
        try:
            if get('/health/live')[0] == 200:
                break
        except (OSError, TimeoutError):
            pass
        time.sleep(1)
    else:
        raise RuntimeError('The image did not become live.')
    assert get('/health/ready')[0] == 200, 'Persistence readiness failed.'
    assert get('/')[0] == 401
    assert get('/', {'X-Auth-Request-Email': 'release.employee@example.test'})[0] == 401
    assert get('/admin', headers)[0] == 403
    assert get('/download/legacy.pdf', headers)[0] == 404
    office_status, office_page = get('/office', headers)
    assert office_status == 200 and b'Optional' in office_page and b'no approval needed' in office_page
    assert get('/office/data', headers)[0] == 200
    assert get('/office/data')[0] == 401
    status, page = get('/timesheet?year=2026&month=11', headers)
    assert status == 200 and b'company-identity' in page and b'id="demo-role-selector"' not in page
    user = subprocess.run(['docker', 'exec', container, 'id', '-u'], env=environment,
                          capture_output=True, text=True, check=True).stdout.strip()
    assert user == '10001'
    print(json.dumps({'image': args.image, 'platform': 'linux/amd64', 'uid': user,
                      'read_only_root': True, 'readiness': 200, 'unauthenticated': 401,
                      'forged_email': 401, 'employee_admin': 403, 'legacy_download': 404,
                      'employee_monthly': 200, 'employee_office': 200}), flush=True)
    if args.preview:
        class Preview(BaseHTTPRequestHandler):
            def log_message(self, *unused):
                pass

            def do_GET(self):
                connection = HTTPConnection('127.0.0.1', 8084, timeout=30)
                try:
                    connection.request('GET', self.path, headers=headers)
                    response = connection.getresponse()
                    self.send_response(response.status)
                    for name, value in response.getheaders():
                        if name.lower() not in {'transfer-encoding', 'connection', 'content-length'}:
                            self.send_header(name, value)
                    body = response.read()
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                finally:
                    connection.close()
        print('Synthetic SSO employee preview: http://127.0.0.1:8085/ (GET only)', flush=True)
        ThreadingHTTPServer(('127.0.0.1', 8085), Preview).serve_forever()
finally:
    subprocess.run(['docker', 'stop', container], env=environment, capture_output=True)
