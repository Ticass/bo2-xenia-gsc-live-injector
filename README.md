# BO2 GSC Live Injector

A BO2-themed Windows GUI for injecting compiled Xbox 360 Black Ops II GSC into a running Xenia process.

Current flow:

1. Launch Xenia and reach the MP or Zombies main menu.
2. Choose `ZM` or `MP`.
3. Choose a script file. Use `_callbacksetup.gsc` by default. For MP alpha/system-link builds that do not load `_callbacksetup.gsc`, use `_globallogic_player.gsc`.
4. Write GSC functions in the editor. The default entry function is `codex_main`.
5. Click `Compile + Inject`.
6. Load or restart the map.

## Freeze Probe

If a build freezes during map/session load, choose the same target you injected and click `Freeze Probe`.
It records for 120 seconds while you reproduce the freeze, then writes a report under:

```text
Documents\BO2 GSC Live Injector\freeze_reports
```

The probe samples the live GSC table entry, object pointer, object size/name, Xenia process CPU time, the last injection config, and recent Xenia log output. Send the `.txt` summary first; keep the matching `.json` file for deeper debugging.

The tool preserves the stock `_callbacksetup.gsc` template for the selected mode, inserts a thread call to your entry function inside `codecallback_startgametype`, compiles through bundled `gsc-tool`, scans the running Xenia guest memory for the live `_callbacksetup` GSC object, and injects the compiled object.
For the MP `_globallogic_player.gsc` fallback, the tool preserves the stock player gametype logic and inserts the entry thread inside `_globallogic_player::callback_playerconnect()` instead. `_objpoints.gsc` remains available for diagnostics, but the app blocks relocated MP startup-target injections because they can crash during system-link map load.
When multiple Xenia processes are open, the injector checks each one and connects to the process that actually has an Xbox guest image mapped.

Small compiled scripts are written in place after backing up the original object. MP startup targets can also expand in place into verified writable zero padding, keeping the same object pointer and updating only the live size field. Larger compiled scripts are relocated to a free guest-memory buffer and the live GSC table entry is patched to point at the relocated object, including its new size.
Compiled objects are normalized after the internal script name is added so the GSC header size fields match the final blob length before injection.

## Interface

The PySide6/Qt interface includes:

- Line numbers
- Current-line highlight
- GSC syntax highlighting
- String, comment, number, brace, function, keyword, and builtin coloring
- Basic autocomplete with `Ctrl+Space`
- Snippets for common player/spawn loop patterns
- Smart indentation on Enter and four-space Tab insertion
- BO2-style dark/orange theme
- Target/sidebar controls
- Freeze Probe capture button
- Live inspector panel for process, target object, table entry, active buffer, object size, and blob size
- Bottom console log

## Injection Modes

- `in-place`: used when the compiled blob fits inside the loaded `_callbacksetup.gsc` object. Restore writes the original object backup back into memory.
- `relocated`: used when the compiled blob is larger than the loaded object. The blob is written to `0x40300000`, then the live GSC table entry's size and buffer pointer are updated. Restore puts the table entry back to the original object pointer and size.

If you inject a relocated script multiple times in one Xenia session, click `Restore Backup` first or restart Xenia before injecting again.

## Notes

- [Shader HUD notes](docs/shader_hud_notes.md)

## Default Script

The editor opens with a tiny test menu script. On player spawn it prints `GSC menu loaded - press Dpad Left`.

Controls:

- Dpad Left: open or close the menu
- Dpad Up/Down: move selection
- X/use: toggle the selected option

The default options are Infinite Ammo and Godmode.
The menu renders as one compact bold line so it works with BO2's single-line bold print behavior.

## Restore

Click `Restore`, or restore from the generated backup in:

```text
Documents\BO2 GSC Live Injector\last_injection.json
```

## Build

```powershell
py -m pip install -r requirements.txt
.\build.ps1
```

The release artifact is written under `release\`.
