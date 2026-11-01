import boto3
from botocore.config import Config

# Bounded so a hung AWS call fails the step instead of blocking the pipeline.
AWS_CONFIG = Config(
    connect_timeout=5, read_timeout=15, retries={"max_attempts": 3, "mode": "standard"}
)


def make_client(service_name: str, region_name: str | None = None):
    """The only place the app creates boto3 clients."""
    return boto3.client(service_name, region_name=region_name, config=AWS_CONFIG)
