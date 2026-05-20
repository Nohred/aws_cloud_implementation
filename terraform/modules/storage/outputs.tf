output "raw_bucket_id" {
  value = aws_s3_bucket.raw.id
}

output "processed_bucket_id" {
  value = aws_s3_bucket.processed.id
}

output "code_bucket_id" {
  value = aws_s3_bucket.code.id
}