# Zip the Linux-built package produced by scripts/build_lambda.sh (manylinux wheels + app/ + frontend/).
# Run that script before every apply: Terraform zips whatever is in build/lambda, it does not build it.
data "archive_file" "api" {
  type        = "zip"
  source_dir  = "${path.module}/../build/lambda"
  output_path = "${path.module}/build/api.zip"
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${var.project}-api"
  retention_in_days = 14
}

locals {
  # The app's only source of settings here is this map: scripts/build_lambda.sh copies app/ and
  # frontend/ and nothing else, so there is no .env in the bundle for pydantic-settings to find.
  app_env = {
    DATABASE_URL = var.database_url

    # One origin: the app serves the frontend and the API off the same Function URL, so FRONTEND_ORIGIN
    # is this deploy's own address. It is what the Square checkout redirects back to and the only origin
    # CORS allows.
    SERVE_FRONTEND  = "true"
    FRONTEND_ORIGIN = var.public_base_url

    # Square's signature is HMAC over (notification URL + raw body), so this has to equal what is
    # registered in the Square dashboard character for character or every delivery is a 401.
    SQUARE_WEBHOOK_NOTIFICATION_URL = var.public_base_url == "" ? "" : "${var.public_base_url}/api/webhooks/square"

    # API Gateway adds its own X-Forwarded-For, but it is not a proxy we put there to carry a client's
    # real address: the true client is already in the event at requestContext.http.sourceIp, which is
    # where Mangum reads it from. A client-sent X-Forwarded-For is attacker-controlled, so it is ignored.
    TRUST_PROXY_HEADERS = "false"

    SQUARE_ENVIRONMENT           = var.square_environment
    SQUARE_ACCESS_TOKEN          = var.square_access_token
    SQUARE_LOCATION_ID           = var.square_location_id
    SQUARE_WEBHOOK_SIGNATURE_KEY = var.square_webhook_signature_key

    # The fake checkout is a local-only convenience. Explicit here so a reader of the console can see
    # that the deploy takes real sandbox payments; the app also refuses it whenever a token is present.
    DEV_FAKE_SQUARE = "false"

    # TEST_MODE is deliberately not set: left unset the ribbon follows SQUARE_ENVIRONMENT, so sandbox
    # credentials can never be demoed without the notice and going live cannot leave the notice up.

    ADMIN_PASSWORD     = var.admin_password
    SESSION_SECRET     = var.session_secret
    DEMO_RESET_ENABLED = tostring(var.demo_reset_enabled)

    TURNSTILE_ENABLED    = tostring(var.turnstile_enabled)
    TURNSTILE_SITE_KEY   = var.turnstile_site_key
    TURNSTILE_SECRET_KEY = var.turnstile_secret_key

    TWILIO_ACCOUNT_SID  = var.twilio_account_sid
    TWILIO_AUTH_TOKEN   = var.twilio_auth_token
    TWILIO_FROM_NUMBER  = var.twilio_from_number
    OWNER_PHONE         = var.owner_phone
    DISCORD_WEBHOOK_URL = var.discord_webhook_url
    GOOGLE_REVIEW_URL   = var.google_review_url

    SMTP_HOST     = var.smtp_host
    SMTP_PORT     = tostring(var.smtp_port)
    SMTP_USERNAME = var.smtp_username
    SMTP_PASSWORD = var.smtp_password
    EMAIL_FROM    = var.email_from
  }
}

resource "aws_lambda_function" "api" {
  function_name = "${var.project}-api"
  role          = aws_iam_role.lambda.arn
  runtime       = "python3.12"
  # x86_64 is the architecture scripts/build_lambda.sh downloads wheels for. The two are one choice
  # written twice: change both, or the first request is an ImportError on a compiled module.
  architectures    = ["x86_64"]
  handler          = "app.lambda_handler.handler"
  filename         = data.archive_file.api.output_path
  source_code_hash = data.archive_file.api.output_base64sha256
  memory_size      = var.lambda_memory_mb
  timeout          = var.lambda_timeout_s

  environment {
    variables = merge(local.app_env, var.extra_env)
  }

  depends_on = [aws_cloudwatch_log_group.api]
}
