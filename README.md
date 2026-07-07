# BO2 GSC Live Injector

A BO2-themed Windows GUI for injecting compiled Xbox 360 Black Ops II GSC into a running Xenia process.

Current flow:

1. Launch Xenia and reach the MP or Zombies main menu.
2. Choose `ZM` or `MP`.
3. Write GSC functions in the editor. The default entry function is `codex_main`.
4. Click `Compile + Inject`.
5. Load or restart the map.

The tool preserves the stock `_callbacksetup.gsc` template for the selected mode, inserts a thread call to your entry function inside `codecallback_startgametype`, compiles through bundled `gsc-tool`, scans the running Xenia guest memory for the live `_callbacksetup` GSC object, backs it up, and patches the compiled object in place.

Injection writes are bounded by the live GSC object's own header size field.

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
- Live inspector panel for process, target object, object size, and blob size
- Bottom console log

## Default Script

```gsc
codex_main()
{
    for (;;)
    {
        wait 3;
        iprintlnbold( "Hello from BO2 GSC Live Injector" );
    }
}
```

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
