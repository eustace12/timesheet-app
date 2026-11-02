"""Separate process liveness from durable-service readiness."""
from flask import jsonify, current_app
import db
import document_storage


def register(app):
    @app.get('/health/live')
    def live():
        return jsonify({'status': 'ok'})

    @app.get('/health/ready')
    def ready():
        try:
            if db.is_supabase_configured():
                client = db._client()
                client.table('users').select('id').limit(1).execute()
                client.table('timesheets').select('leave_snapshot').limit(1).execute()
                client.table('leave_entitlements').select('user_id').limit(1).execute()
                client.table('workflow_events').select('id').limit(1).execute()
                client.table('office_plans').select('user_id').limit(1).execute()
                if current_app.config.get('AUTH_MODE') == 'account':
                    client.table('account_sessions').select('session_hash').limit(1).execute()
                document_storage.check_ready()
            else:
                with db.connect() as connection:
                    connection.execute('SELECT leave_snapshot FROM timesheets LIMIT 1').fetchall()
                    connection.execute('SELECT id FROM workflow_events LIMIT 1').fetchall()
                    connection.execute('SELECT user_id FROM office_plans LIMIT 1').fetchall()
                    if current_app.config.get('AUTH_MODE') == 'account':
                        connection.execute('SELECT session_hash FROM account_sessions LIMIT 1').fetchall()
            return jsonify({'status': 'ready'}), 200
        except Exception:
            app.logger.error('Persistence readiness check failed.')
            return jsonify({'status': 'unavailable'}), 503
