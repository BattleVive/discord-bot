terraform {
  backend "s3" {
    key          = "battlevive-bot/green.tfstate"
    region       = "eu-north-1"
    encrypt      = true
    use_lockfile = true
  }
}
