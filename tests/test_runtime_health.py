import db
import document_storage


def test_ready_checks_schema_and_liveness_survives_persistence_outage(client, monkeypatch):
    assert client.get('/health/ready').status_code == 200
    def broken():
        raise RuntimeError('unavailable')
    monkeypatch.setattr(db, 'connect', broken)
    assert client.get('/health/ready').status_code == 503
    assert client.get('/health/live').status_code == 200


def test_document_bucket_must_be_private(monkeypatch):
    class Storage:
        def get_bucket(self, name):
            return {'public': True}
    class Client:
        storage = Storage()
    monkeypatch.setattr(document_storage, '_client', Client())
    monkeypatch.setattr(document_storage, '_bucket', lambda: object())
    import pytest
    with pytest.raises(document_storage.DocumentStorageError):
        document_storage.check_ready()
