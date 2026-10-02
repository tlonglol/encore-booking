# Keep-warm. Invokes the Lambda directly (not over the Function URL) with {"warmer": true}, which
# app/lambda_handler.py answers before importing settings or touching the database. Nothing about
# correctness depends on this: hold expiry is decided by the database clock in the request that asks,
# so a container that was never warmed behaves identically, just slower on its first request.
resource "aws_scheduler_schedule" "warmer" {
  count = var.warmer_enabled ? 1 : 0
  name  = "${var.project}-warmer"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression = "rate(5 minutes)"

  target {
    arn      = aws_lambda_function.api.arn
    role_arn = aws_iam_role.scheduler[0].arn
    input    = jsonencode({ warmer = true })
  }
}
