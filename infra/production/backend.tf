terraform {
  backend "s3" {
    key          = "battlevive-bot/control.tfstate"
    region       = "eu-north-1"
    encrypt      = true
    use_lockfile = true
  }
}
