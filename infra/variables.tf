variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "encore-booking"
}

variable "lambda_memory_mb" {
  type        = number
  default     = 1024
  description = "Memory also buys CPU, which is what a cold start actually waits on."
}

variable "lambda_timeout_s" {
  type        = number
  default     = 30
  description = <<-EOT
    The slowest request is the Square webhook: it commits the booking, then attempts the owner SMS, the
    parent SMS and the parent email inline, each with its own 10-second HTTP timeout. The booking is
    committed before any of that, so a timeout here loses notifications, never a booking, and Square's
    redelivery re-attempts the ones left pending.
  EOT
}

variable "warmer_enabled" {
  type        = bool
  default     = true
  description = <<-EOT
    Ping the Lambda every 5 minutes with {"warmer": true}, which app/lambda_handler.py answers before the
    app loads. The demo is a phone in the owner's hand; a cold start on the first date tap is the one
    moment the system looks slow. Turn it off to stop the schedule.
  EOT
}

# ---- The one address-shaped setting ----
variable "public_base_url" {
  type        = string
  default     = ""
  description = <<-EOT
    Where the system lives, with no trailing slash, e.g. https://abc123.lambda-url.us-east-1.on.aws.
    FRONTEND_ORIGIN and SQUARE_WEBHOOK_NOTIFICATION_URL are both derived from it, so the Square checkout
    redirect, the CORS allowlist and the webhook signature cannot disagree about the address.

    Leave it empty on the very first apply: the Function URL does not exist yet. Then set it to the
    function_url output and apply again, and register that same URL + /api/webhooks/square in the Square
    dashboard. While it is empty, webhook signature verification fails closed (every delivery is a 401),
    which is the correct answer when no subscription has been registered yet.
  EOT

  validation {
    condition     = var.public_base_url == "" || !endswith(var.public_base_url, "/")
    error_message = "public_base_url must not end in a slash: the webhook URL is built by appending a path, and Square's signature is over the registered URL character for character."
  }
}

# ---- Secrets. Set in terraform.tfvars (gitignored) or TF_VAR_*; never committed, never in the zip ----
variable "database_url" {
  type        = string
  sensitive   = true
  description = "Neon POOLED connection string (the -pooler host), as postgresql+psycopg://... . Migrations use the direct string from the laptop, never this one."
}

variable "admin_password" {
  type      = string
  sensitive = true
}

variable "session_secret" {
  type        = string
  sensitive   = true
  description = "Signs the admin session cookie. A long random string; changing it logs the owner out."
}

variable "square_environment" {
  type    = string
  default = "sandbox"

  validation {
    condition     = contains(["sandbox", "production"], var.square_environment)
    error_message = "square_environment must be sandbox or production."
  }
}

variable "square_access_token" {
  type      = string
  sensitive = true
}

variable "square_application_id" {
  type        = string
  default     = ""
  description = "Public identifier the Web Payments SDK needs in the browser to tokenize a card. Not a secret, and not the application secret. Empty means the funnel renders no card fields."
}

variable "square_location_id" {
  type = string
}

variable "square_webhook_signature_key" {
  type        = string
  sensitive   = true
  description = "From the webhook subscription in the Square dashboard, not the app's application secret."
}

variable "demo_reset_enabled" {
  type        = bool
  default     = false
  description = "The one-tap demo reset deletes bookings. It also refuses outright against production Square credentials, but it stays off unless deliberately turned on."
}

variable "turnstile_enabled" {
  type    = bool
  default = false
}

variable "turnstile_site_key" {
  type    = string
  default = ""
}

variable "turnstile_secret_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "twilio_account_sid" {
  type    = string
  default = ""
}

variable "twilio_auth_token" {
  type      = string
  sensitive = true
  default   = ""
}

variable "twilio_from_number" {
  type    = string
  default = ""
}

variable "owner_phone" {
  type        = string
  default     = ""
  description = "Where the NEW PARTY text goes. On a Twilio trial this must be a verified number."
}

variable "discord_webhook_url" {
  type        = string
  sensitive   = true
  default     = ""
  description = "The URL is the credential, which is why it is sensitive and never logged."
}

variable "google_review_url" {
  type    = string
  default = ""
}

variable "smtp_host" {
  type    = string
  default = ""
}

variable "smtp_port" {
  type    = number
  default = 587
}

variable "smtp_username" {
  type    = string
  default = ""
}

variable "smtp_password" {
  type      = string
  sensitive = true
  default   = ""
}

variable "email_from" {
  type    = string
  default = ""
}

variable "extra_env" {
  type        = map(string)
  default     = {}
  sensitive   = true
  description = <<-EOT
    Any other app setting, by its env name, e.g. { MIN_LEAD_DAYS = "3" }. The business rules in
    app/config.py keep their code defaults on the deploy rather than being restated here, so there is
    one place a price-or-rule default lives. This is the escape hatch for changing one without a code
    change, and it wins over everything this file sets.
  EOT
}
