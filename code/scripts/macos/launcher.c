/* Native Finder entry point; keep all arguments out of a command shell. */
#include <mach-o/dyld.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char executable[PATH_MAX], resolved[PATH_MAX], payload[PATH_MAX];
    char python[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) || !realpath(executable, resolved)) return 1;
    char *slash = strrchr(resolved, '/');
    if (!slash) return 1;
    *slash = '\0'; /* Contents/MacOS */
    slash = strrchr(resolved, '/');
    if (!slash) return 1;
    *slash = '\0'; /* Contents */
    if (snprintf(payload, sizeof(payload), "%s/Resources/platform", resolved) >= sizeof(payload) ||
        snprintf(python, sizeof(python), "%s/dependencies/python/bin/python3", payload) >= sizeof(python) ||
        snprintf(script, sizeof(script), "%s/code/scripts/tfi_macos_launcher.py", payload) >= sizeof(script)) return 1;
    setenv("PYTHONUTF8", "1", 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
    setenv("PYTHONNOUSERSITE", "1", 1);
    unsetenv("PYTHONHOME");
    unsetenv("PYTHONPATH");
    char **args = calloc((size_t)argc + 5, sizeof(char *));
    if (!args) return 1;
    args[0] = python; args[1] = script; args[2] = "--bundle"; args[3] = payload;
    int count = 4;
    for (int i = 1; i < argc; ++i) {
        if (strncmp(argv[i], "-psn_", 5)) args[count++] = argv[i];
    }
    execv(python, args);
    perror("Cannot launch The Fourth Institute");
    free(args);
    return 1;
}
