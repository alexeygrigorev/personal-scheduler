import os

import boto3
import pytest
from moto import mock_aws

# Everything the app reads from the environment, so `pytest` needs no wrapper
# and no exported variables. Config is read at import time, and pytest loads
# this file before any test module.
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("TABLE_NAME", "personal-scheduler-test")
os.environ.setdefault("SITE_URL", "https://scheduler.test")
os.environ.setdefault("ROOT_ADMIN", "host@datatalks.club")
os.environ.setdefault("AUTH_CLIENT_ID", "test-client")
os.environ.setdefault("AUTH_ISSUER", "https://issuer.test")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")
# moto refuses to start without credentials present, real or otherwise.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")


@pytest.fixture
def table():
    with mock_aws():
        client = boto3.resource("dynamodb", region_name="eu-west-1")
        client.create_table(
            TableName="personal-scheduler-test",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        from scheduler import store

        store._table = None
        yield client.Table("personal-scheduler-test")
        store._table = None
