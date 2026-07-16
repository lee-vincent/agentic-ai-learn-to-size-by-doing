variable "project" {
  type = string
}

variable "subnet_id" {
  type = string
}

variable "security_group_id" {
  type = string
}

variable "gpu_instance_type" {
  type = string
}

variable "instance_profile_name" {
  type = string
}

variable "root_volume_size_gb" {
  type = number
}

variable "ssh_key_name" {
  type    = string
  default = null
}

variable "tags" {
  type    = map(string)
  default = {}
}
