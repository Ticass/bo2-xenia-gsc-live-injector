# BO2 GSC Live Injector

A small Windows GUI for injecting compiled Xbox 360 Black Ops II GSC into a running Xenia process.

Current flow:

1. Launch Xenia and reach the MP or Zombies main menu.
2. Choose `ZM` or `MP`.
3. Write GSC functions in the editor. The default entry function is `codex_main`.
4. Click `Compile + Inject`.
5. Load or restart the map.

The tool preserves the stock `_callbacksetup.gsc` template for the selected mode, inserts a thread call to your entry function inside `codecallback_startgametype`, compiles through bundled `gsc-tool`, scans the running Xenia guest memory for the live `_callbacksetup` GSC object, backs it up, and patches the compiled object in place.

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
.\build.ps1
```

The release artifact is written under `release\`.

