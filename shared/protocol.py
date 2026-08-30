# Shared message types. Canonical schema: protocol.json

REGISTER = "register"
COMMAND = "command"
RESULT = "result"
HEARTBEAT = "heartbeat"
ACTIONS = [
    "run_powershell",
    "run_shell",
    "get_hardware",
    "get_system_info",
    "install_package",
    "download_file",
    "reboot",
    "manage_service",
    "install_windows",
    "partition_disk",
]
