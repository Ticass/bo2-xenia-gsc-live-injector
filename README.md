# BO2 GSC Live Injector

A BO2-themed Windows GUI for injecting compiled Xbox 360 Black Ops II GSC into a running Xenia process.

Current flow:

1. Launch Xenia and reach the MP or Zombies main menu.
2. Choose `ZM` or `MP`.
3. Choose `_callbacksetup.gsc`, or `_objpoints.gsc` for the July 2012 MP Alpha.
4. Write GSC functions in the editor. The default entry function is `codex_main`.
5. Click `Compile + Inject`.
6. Load or restart the map.

## July 20, 2012 Alpha

The Multiplayer Alpha (`Multiplayer/Milestones/Alpha,1276000`) uses T6 GSC VM
revision 5 (`80 47 53 43 0D 0A 00 05`). Retail Xbox BO2 and gsc-tool use
revision 6. The injector detects the live object's signature and converts
gsc-tool output to revision 5 only for this verified pair. Unknown VM revision
combinations are blocked before memory is written.

The revision-5 opcode map was recovered from the compiler-enabled Alpha
`CoDMP.exe`. Its complete `0x00` through `0x7C` opcode assignments and
`GSC_OBJ` layout match the retail T6 backend, so the serialized signature is
the only required conversion.

The **GSC VM** selector offers `Auto Detect`, `Alpha v5`, and `Retail v6`.
Auto remains the default. Forced modes select the compiler output revision but
still require the resolved address to contain a real `80 47 53 43` GSC object;
they never bypass stale-address protection.

For the Alpha MP build, use `_objpoints.gsc`. Larger scripts are relocated in
the same way as other supported builds after compiler/VM compatibility is
validated. The earlier Alpha map-load freeze was caused by revision-6 bytecode
being passed to its revision-5 VM, not by relocation.

## Freeze Probe

If a build freezes during map/session load, choose the same target you injected and click `Freeze Probe`.
It records for 120 seconds while you reproduce the freeze, then writes a report under:

```text
Documents\BO2 GSC Live Injector\freeze_reports
```

The probe samples the live GSC table entry, object pointer, object size/name, Xenia process CPU time, the last injection config, and recent Xenia log output. Send the `.txt` summary first; keep the matching `.json` file for deeper debugging.

The tool preserves the stock `_callbacksetup.gsc` template for the selected mode, inserts a thread call to your entry function inside `codecallback_startgametype`, compiles through bundled `gsc-tool`, scans the running Xenia guest memory for the live `_callbacksetup` GSC object, and injects the compiled object.
`_objpoints.gsc` is the recommended startup target for the July 2012 Alpha.
When multiple Xenia processes are open, the injector checks each one and connects to the process that actually has an Xbox guest image mapped.

Small compiled scripts are written in place after backing up the original object. Larger compiled scripts are relocated to a free guest-memory buffer and the live GSC table entry is patched to point at the relocated object, including its new size.
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
