# Automatic kill switch. If the API Lambda is invoked more than 20,000 times in an hour, something is
# hammering it: real traffic for this venue is a few hundred a day. The alarm publishes to SNS, which
# emails the owner and runs a tiny Lambda that sets the API function's reserved concurrency to zero.
# From then on every invocation is throttled at no cost until a person restores it with the
# restore_concurrency output below. Nothing here touches the money path: a tripped switch refuses
# requests, it never alters a booking.

resource "aws_sns_topic" "killswitch" {
  name = "${var.project}-killswitch"
}

resource "aws_sns_topic_subscription" "killswitch_email" {
  topic_arn = aws_sns_topic.killswitch.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "invocation_spike" {
  alarm_name          = "${var.project}-invocation-spike"
  alarm_description   = "API Lambda invoked more than 20000 times in one hour. Trips the kill switch."
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 20000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.api.function_name
  }

  alarm_actions = [aws_sns_topic.killswitch.arn]
}

# ---- the Lambda that pulls the plug ----

data "archive_file" "killswitch" {
  type        = "zip"
  source_file = "${path.module}/killswitch/handler.py"
  output_path = "${path.module}/build/killswitch.zip"
}

resource "aws_iam_role" "killswitch" {
  name = "${var.project}-killswitch-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "killswitch_logs" {
  role       = aws_iam_role.killswitch.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# One action on one function. It cannot read, invoke or reconfigure anything else.
resource "aws_iam_role_policy" "killswitch_concurrency" {
  name = "${var.project}-killswitch-concurrency"
  role = aws_iam_role.killswitch.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:PutFunctionConcurrency"
      Resource = aws_lambda_function.api.arn
    }]
  })
}

resource "aws_cloudwatch_log_group" "killswitch" {
  name              = "/aws/lambda/${var.project}-killswitch"
  retention_in_days = 14
}

resource "aws_lambda_function" "killswitch" {
  function_name    = "${var.project}-killswitch"
  role             = aws_iam_role.killswitch.arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = "handler.handler"
  filename         = data.archive_file.killswitch.output_path
  source_code_hash = data.archive_file.killswitch.output_base64sha256
  memory_size      = 128
  timeout          = 30

  environment {
    variables = {
      TARGET_FUNCTION_NAME = aws_lambda_function.api.function_name
    }
  }

  depends_on = [aws_cloudwatch_log_group.killswitch]
}

resource "aws_sns_topic_subscription" "killswitch_lambda" {
  topic_arn = aws_sns_topic.killswitch.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.killswitch.arn
}

resource "aws_lambda_permission" "killswitch_from_sns" {
  statement_id  = "AllowInvokeFromKillswitchTopic"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.killswitch.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.killswitch.arn
}

output "restore_concurrency" {
  description = "Run this after the kill switch trips, once you know why."
  value       = "aws lambda put-function-concurrency --function-name ${aws_lambda_function.api.function_name} --reserved-concurrent-executions 30 --region ${var.region}"
}
