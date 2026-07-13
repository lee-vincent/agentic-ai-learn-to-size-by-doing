output "instance_profile_name" {
  value = aws_iam_instance_profile.gpu_node.name
}

output "instance_profile_arn" {
  value = aws_iam_instance_profile.gpu_node.arn
}

output "role_arn" {
  value = aws_iam_role.gpu_node.arn
}

output "hf_token_parameter_arn" {
  value = aws_ssm_parameter.hf_token.arn
}

output "hf_token_parameter_name" {
  value = aws_ssm_parameter.hf_token.name
}

output "hf_token_kms_key_arn" {
  value = aws_kms_key.hf_token.arn
}

output "hf_token_kms_alias" {
  value = aws_kms_alias.hf_token.name
}
