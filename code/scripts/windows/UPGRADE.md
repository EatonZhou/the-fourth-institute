Windows program updates
=======================

A newer Windows package updates only the program, following the instance
protocol's "code update". There is no separate upgrade transaction, snapshot or
receipt.

1. Run the new `TheFourthInstitute-Setup-x64.exe` and choose the folder of the
   installed version. Setup first runs the installed launcher with `--stop-all`,
   which stops every registered instance whose deployments run this program.
   If any instance cannot be stopped, the program is not changed.
2. Setup replaces `code/`, `branding/`, the bundled Python runtime and its wheel
   set, and the prepared engineering bundle. Instance roots are outside the
   program folder (by default `%LOCALAPPDATA%\TFI\instances\<name>`) and are not
   touched.
3. The next desktop start finds that the instance's service definitions still
   name the previous code environment and runs
   `tfi_release_install.py update-code --instance <name>`: it prepares the new
   code's dependencies, refreshes the service definitions and restarts the
   services. Services update their database themselves when they start.

The example instance's content is used only when an instance is created; a code
update never imports it again. When a fallback is wanted, back the instance up
with backup software before updating (database dump, areas, `secrets/`,
`instance.json` and `site.json`).
