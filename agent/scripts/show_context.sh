#!/bin/zsh

run_check() {
  echo "- $1: $2"
  echo "  - $(eval $2)"
}

run_check "Determine the current username" "whoami"
run_check "Get current user's UID" "id -u"
run_check "Get current user's GID" "id -g"
run_check "List all groups the current user belongs to" "groups"
run_check "Display full identity info: UID, GID, groups" "id"
run_check "Check System Integrity Protection (SIP) status" "csrutil status"
run_check "Get OS version and kernel info" "uname -a"
run_check "Alternative OS version info" "sw_vers"
run_check "Check owner and group of the current directory" "stat -f \"%Su %Sg\" ."
run_check "Show the home directory path" "echo \$HOME"
