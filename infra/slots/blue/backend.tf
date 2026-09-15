terraform {
  required_version = ">= 1.11.0"
  backend "s3" {
    key          = "battlevive-bot/blue.tfstate"
    region       = "eu-north-1"
    encrypt      = true
    use_lockfile = true
  }
}
