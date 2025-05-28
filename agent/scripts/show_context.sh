#!/bin/zsh

print_info() {
  echo
  echo "Description: $1"
  echo "+ $2"
  eval "$2"
}

print_info "Determine the current username." \
           "whoami"

print_info "Get current user's UID." \
           "id -u"

print_info "Get current user's GID." \
           "id -g"

print_info "List all groups the current user belongs to." \
           "groups"

print_info "Display full identity info: UID, GID, groups." \
           "id"

print_info "Check System Integrity Protection (SIP) status." \
           "csrutil status"

print_info "Get macOS version and kernel info." \
           "uname -a"

print_info "Alternative OS version info." \
           "sw_vers"

print_info "Check owner and group of the current directory." \
           "stat -f \"%Su %Sg\" ."

print_info "Show the home directory path." \
           "echo \$HOME"
