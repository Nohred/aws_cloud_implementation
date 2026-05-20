variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

resource "aws_s3_bucket" "raw" {
  bucket = "${var.project_name}-${var.environment}-raw-v3"

  force_destroy = true
}

resource "aws_s3_bucket" "processed" {
  bucket = "${var.project_name}-${var.environment}-processed-v3"

    force_destroy = true 
}

resource "aws_s3_bucket" "code" {
  bucket = "${var.project_name}-${var.environment}-code-v3"

    force_destroy = true
}
