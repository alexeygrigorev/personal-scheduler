"""Read imported SecretString bytes from the DataOps team store."""
from dataops_secrets import SecretReader
from . import config


def read_value(name):
    return SecretReader(table_name=config.SECRETS_TABLE_NAME,
                        key_id=config.SECRETS_KEY_ID,
                        region=config.SECRETS_REGION).read(name)["value"]
