"""Exercise the machine reader wire contract and existing session semantics."""
import base64
import json

import pytest

from dataops_secrets import SecretReader
from scheduler import security


class Dynamo:
    def get_item(self, **kwargs):
        assert kwargs == {
            "TableName": "dataops-v1-secrets",
            "Key": {"PK": {"S": "SECRETS"},
                    "SK": {"S": "SECRET#personal-scheduler.session"}},
            "ConsistentRead": True,
            "ProjectionExpression": "#data",
            "ExpressionAttributeNames": {"#data": "data"},
        }
        return {"Item": {"data": {"S": base64.b64encode(b"cipher").decode()}}}


class Kms:
    def decrypt(self, **kwargs):
        assert kwargs == {"KeyId": "key", "CiphertextBlob": b"cipher",
                          "EncryptionContext": {"secret": "personal-scheduler.session"}}
        return {"Plaintext": json.dumps({"value": '{"signing_secret":"test-signing"}'}).encode()}


def test_reader_binds_decryption_to_name():
    reader = SecretReader(table_name="dataops-v1-secrets", key_id="key", region="eu-west-1",
                          dynamodb=Dynamo(), kms=Kms())
    assert json.loads(reader.read("personal-scheduler.session")["value"]) == {
        "signing_secret": "test-signing"}
    with pytest.raises(ValueError, match="Invalid secret name"):
        reader.read("personal-scheduler/session")


@pytest.mark.parametrize("raw,expected", [
    ('{"signing_secret":"test-signing"}', b"test-signing"),
    ("plain-signing", b"plain-signing"),
])
def test_session_reads_imported_value_and_caches(monkeypatch, raw, expected):
    reads = []
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setattr(security, "_secret_cache", None)
    def read(name):
        reads.append(name)
        return raw
    monkeypatch.setattr("scheduler.secrets.read_value", read)
    assert security.secret() == expected
    assert security.secret() == expected
    assert reads == ["personal-scheduler.session"]
