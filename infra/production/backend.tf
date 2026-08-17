terraform {
  backend "s3" {
    key          = "battlevive-bot/production.tfstate"
    region       = "eu-north-1"
    encrypt      = true
    use_lockfile = true
  }
}
