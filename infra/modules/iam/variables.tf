variable "project" {
  type = string
}

variable "hf_token_parameter_name" {
  type = string
}

variable "weights_bucket_arn" {
  description = "ARN of the S3 model-weights staging bucket, for scoped read access."
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
