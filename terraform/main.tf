terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "audio" {
  bucket        = "polly-podcast-verify-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

output "bucket" {
  value = aws_s3_bucket.audio.bucket
}
