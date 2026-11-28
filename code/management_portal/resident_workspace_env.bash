# Portal BASH_ENV hook for shared Provider servers (Kimi Code, Antigravity).
#
# One server process runs the shells of several Residents, so its own
# environment cannot carry a Resident's Workspace path variables. Before each
# turn the Worker writes them to PORTAL_SHARED_SHELL_ENV_DIR; every non-interactive
# bash the server starts sources the file of its session or Resident here.
# The variables are navigation only; nothing here grants access.
__portal_workspace_env() {
  local dir="${PORTAL_SHARED_SHELL_ENV_DIR:-}" key="${ANTIGRAVITY_CONVERSATION_ID:-}" path parent name
  [ -n "$dir" ] && [ -d "$dir" ] || return 0
  # Shells started by a command inherit the first shell's values.
  [ -z "${PORTAL_SHARED_SHELL_ENV_SOURCED:-}" ] || return 0
  if [[ "$key" =~ ^[A-Za-z0-9-]{1,80}$ ]] && [ -f "$dir/session-$key.sh" ]; then
    . "$dir/session-$key.sh"
    export PORTAL_SHARED_SHELL_ENV_SOURCED=1
    return 0
  fi
  # Otherwise the Resident whose folder holds the command's working directory.
  path="$PWD"
  while [ -n "$path" ] && [ "$path" != "/" ]; do
    name="${path##*/}"
    parent="${path%/*}"
    if [[ "$name" =~ ^resident_[0-9a-f]{12}$ ]] && [ "${parent##*/}" = "residents" ] && [ -f "$dir/$name.sh" ]; then
      . "$dir/$name.sh"
      export PORTAL_SHARED_SHELL_ENV_SOURCED=1
      return 0
    fi
    path="$parent"
  done
  return 0
}
__portal_workspace_env
unset -f __portal_workspace_env
