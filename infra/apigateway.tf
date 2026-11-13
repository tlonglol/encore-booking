# The public front door. One HTTP API, one $default route, one $default stage, so the path the Lambda
# sees is the path the browser asked for, with no stage prefix to strip.
#
# This replaced a Lambda Function URL. The Function URL was correct and still 403'd: AWS blocks public
# access to function URLs by default on accounts created after about 2024, and that account-level block
# overrides the resource policy. See SPEC.md's deployment notes for why the usual workaround for that
# (granting lambda:InvokeFunction to "*") is not acceptable in this app.
resource "aws_apigatewayv2_api" "api" {
  name          = "${var.project}-api"
  protocol_type = "HTTP"

  # No cors_configuration block on purpose. CORS is FastAPI's CORSMiddleware (app/main.py), which also
  # covers local dev and ngrok. Configuring it here too makes both layers add
  # Access-Control-Allow-Origin, and a browser rejects the doubled value. On this one-origin deploy
  # nothing is cross-site anyway.
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id             = aws_apigatewayv2_api.api.id
  integration_type   = "AWS_PROXY"
  integration_uri    = aws_lambda_function.api.invoke_arn
  integration_method = "POST"

  # 2.0 is what Mangum reads and what tests/test_lambda_handler.py builds its events in. Pinned rather
  # than left to the default: a silent switch to 1.0 would change where the client IP and the path live
  # in the event, and the hold caps read the client IP.
  payload_format_version = "2.0"

  # The ceiling an HTTP API allows. The slowest request is the Square webhook, which commits the booking
  # before attempting any notification, so a timeout here loses a text and never a booking.
  timeout_milliseconds = 30000
}

resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true

  # The first ceiling on compute spend. A cold page load is eleven or twelve parallel asset fetches, so
  # the burst has to clear that with room for a second visitor; a sustained 20 a second is far beyond
  # anything a party-booking funnel sees from real people. Excess is answered 429 by API Gateway and never
  # reaches the Lambda.
  default_route_settings {
    throttling_rate_limit  = 20
    throttling_burst_limit = 50
  }
}

# Scoped to this API, unlike the function URL's grant to "*": only API Gateway may invoke the function,
# and only through this one API. Nothing can hand the Lambda an event of its own devising.
resource "aws_lambda_permission" "apigateway" {
  statement_id  = "AllowInvokeFromHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}
