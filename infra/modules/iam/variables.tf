variable "project" {
  type = string
}

variable "hf_token_parameter_name" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
