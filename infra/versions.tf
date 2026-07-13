terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Local backend by default so this repo has zero external state-storage
  # prerequisites. For real multi-person use, swap this for an `s3` backend
  # with a DynamoDB lock table before anyone applies concurrently.
  # backend "s3" {}
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project
      ManagedBy   = "terraform"
      Environment = var.environment
    }
  }
}
