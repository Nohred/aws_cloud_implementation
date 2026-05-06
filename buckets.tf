provider "aws" {
  region = "us-east-1"
}

# --- 1. RECURSOS DE ALMACENAMIENTO Y BD ---
resource "aws_s3_bucket" "source" { 
  bucket = "bucket-source-garbage-classification" 
}

resource "aws_s3_bucket" "target" {
    bucket = "bucket-target-garbage-classification"   
}

resource "aws_s3_bucket" "code" {
    bucket = "bucket-code-garbage-classification"
}

resource "aws_s3_object" "glue_script" {
  bucket = aws_s3_bucket.code.id
  key    = "scripts/data_transformation.py"
  source = "scripts/data_transformation.py"
  etag   = filemd5("scripts/data_transformation.py") # Detecta cambios en el archivo  
}

# Esto es lo que activa el "sensor" del bucket
resource "aws_s3_bucket_notification" "bucket_notification" {
  bucket      = aws_s3_bucket.source.id
  eventbridge = true 
}
