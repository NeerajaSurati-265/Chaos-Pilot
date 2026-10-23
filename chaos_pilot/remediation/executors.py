import boto3
from botocore.exceptions import ClientError

from chaos_pilot.remediation.engine import RemediationContext


def _require(context: RemediationContext, *fields: str):
    missing = [name for name in fields if not getattr(context, name)]
    if missing:
        raise RuntimeError(f"Not recorded before injection: {', '.join(missing)}")


def ingress_permission(source_security_group_id: str, port: int) -> dict:
    return {
        "IpProtocol": "tcp",
        "FromPort": port,
        "ToPort": port,
        "UserIdGroupPairs": [{"GroupId": source_security_group_id}],
    }


def restore_ingress_rule(ec2, security_group_id: str, source_security_group_id: str, port: int):
    try:
        ec2.authorize_security_group_ingress(
            GroupId=security_group_id,
            IpPermissions=[ingress_permission(source_security_group_id, port)],
        )
    except ClientError as error:
        # Already there means the desired state holds.
        if error.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise


class RollbackToPreviousRevision:
    """Puts the service back on the task definition it ran before injection."""

    action = "rollback_to_previous_revision"

    def __init__(self, ecs_client=None):
        self._ecs = ecs_client

    @property
    def ecs(self):
        # Created on first use, so wiring the engine needs no AWS credentials.
        if self._ecs is None:
            self._ecs = boto3.client("ecs")
        return self._ecs

    def execute(self, context: RemediationContext) -> str:
        _require(context, "cluster", "service", "known_good_task_definition")
        self.ecs.update_service(
            cluster=context.cluster,
            service=context.service,
            taskDefinition=context.known_good_task_definition,
        )
        return f"Rolled back to {context.known_good_task_definition}"


class RestoreSecurityGroupRule:
    """Re-adds the exact ingress rule that was removed."""

    action = "restore_security_group_rule"

    def __init__(self, ec2_client=None):
        self._ec2 = ec2_client

    @property
    def ec2(self):
        if self._ec2 is None:
            self._ec2 = boto3.client("ec2")
        return self._ec2

    def execute(self, context: RemediationContext) -> str:
        _require(context, "security_group_id", "source_security_group_id", "port")
        restore_ingress_rule(
            self.ec2,
            context.security_group_id,
            context.source_security_group_id,
            context.port,
        )
        return (
            f"Restored tcp/{context.port} from {context.source_security_group_id} "
            f"on {context.security_group_id}"
        )
