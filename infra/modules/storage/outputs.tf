output "bucket_name" {
  value = aws_s3_bucket.weights.id
}

output "bucket_arn" {
  value = aws_s3_bucket.weights.arn
}

output "fsx_id" {
  value = aws_fsx_lustre_file_system.weights_cache.id
}

output "fsx_dns_name" {
  value = aws_fsx_lustre_file_system.weights_cache.dns_name
}

output "fsx_mount_name" {
  value = aws_fsx_lustre_file_system.weights_cache.mount_name
}
