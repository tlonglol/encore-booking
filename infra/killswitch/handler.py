"""Kill switch. Subscribed to the alarm topic; sets the API function's reserved concurrency to zero so
no further invocation can run or bill. Restoring is a human decision: see the restore_concurrency output.
"""

import json
import logging
import os
from typing import Any

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

FUNCTION_NAME = os.environ["TARGET_FUNCTION_NAME"]


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    reasons = []
    for record in event.get("Records", []):
        try:
            message = json.loads(record["Sns"]["Message"])
            reasons.append(f"{message.get('AlarmName')}: {message.get('NewStateReason')}")
        except (KeyError, ValueError, TypeError):
            reasons.append(str(record.get("Sns", {}).get("Message"))[:200])

    boto3.client("lambda").put_function_concurrency(
        FunctionName=FUNCTION_NAME, ReservedConcurrentExecutions=0
    )
    log.warning("Set reserved concurrency of %s to 0. Trigger: %s", FUNCTION_NAME, "; ".join(reasons) or "unknown")
    return {"function": FUNCTION_NAME, "reserved_concurrent_executions": 0}
