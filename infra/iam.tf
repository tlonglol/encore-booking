# ---- Lambda execution role ----
# Logs and nothing else. The Lambda talks to Neon, Square, Twilio and SMTP over the internet with
# credentials from its environment, so it needs no AWS permissions beyond writing its own log stream.
resource "aws_iam_role" "lambda" {
  name = "${var.project}-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# ---- EventBridge Scheduler role (keep-warm only) ----
resource "aws_iam_role" "scheduler" {
  count = var.warmer_enabled ? 1 : 0
  name  = "${var.project}-scheduler-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  count = var.warmer_enabled ? 1 : 0
  name  = "${var.project}-scheduler-invoke"
  role  = aws_iam_role.scheduler[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.api.arn
    }]
  })
}
