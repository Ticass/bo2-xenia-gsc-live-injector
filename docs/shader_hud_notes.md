# BO2 Xenia Shader HUD Notes

These notes document the shader HUD pattern that worked with this injector on BO2/Xenia, and the pattern that caused freezes or `pipeline not ready` spam.

## Working Pattern

- Use `newclienthudelem( self )` for both shader boxes and text rows.
- Use lowercase HUD fields:
  - `horzalign`
  - `vertalign`
  - `alignx`
  - `aligny`
- Set `foreground = true` on HUD elements.
- Use `setshader( "black", width, height )` for dark panels.
- Use `setshader( "white", width, 1 )` only for thin divider lines.
- Build the HUD once when the menu opens.
- Update only row text/glow while navigating.
- Destroy all stored HUD elements when the menu closes.
- Store HUD elements in an array such as `self.menu_hud["bg"]`, then destroy by iterating `getarraykeys()`.

## Avoid

- Do not recreate/destroy all HUD elements every cursor move.
- Do not use camelCase fields like `horzAlign`, `vertAlign`, `alignX`, or `alignY`.
- Do not make large black panels by tinting `setshader( "white", ... )` black.
- Do not mix shader elements from `newclienthudelem()` with text from `createFontString()` unless tested on the exact build.
- Avoid repeated large `setshader()` calls during the menu loop.

The bad pattern can make Xenia spam:

```text
Skipping draw - pipeline not ready
```

and may freeze the game once the menu opens.

## Minimal Safe Shader Menu Pattern

```gsc
build_menu_hud()
{
    self.menu_hud = [];

    bg = newclienthudelem( self );
    bg.horzalign = "center";
    bg.vertalign = "middle";
    bg.alignx = "center";
    bg.aligny = "middle";
    bg.x = 0;
    bg.y = -40;
    bg.sort = 1;
    bg.alpha = 0.55;
    bg.foreground = true;
    bg setshader( "black", 230, 95 );
    self.menu_hud["bg"] = bg;

    title = newclienthudelem( self );
    title.horzalign = "center";
    title.vertalign = "middle";
    title.alignx = "center";
    title.aligny = "middle";
    title.x = 0;
    title.y = -75;
    title.sort = 2;
    title.alpha = 1;
    title.foreground = true;
    title.font = "objective";
    title.fontscale = 1.4;
    title.glowcolor = ( 0.6, 0, 1 );
    title.glowalpha = 0.8;
    title setText( "^5CRYBABY MENU" );
    self.menu_hud["title"] = title;

    line = newclienthudelem( self );
    line.horzalign = "center";
    line.vertalign = "middle";
    line.alignx = "center";
    line.aligny = "middle";
    line.x = 0;
    line.y = -58;
    line.sort = 2;
    line.alpha = 0.9;
    line.foreground = true;
    line setshader( "white", 205, 1 );
    self.menu_hud["line"] = line;

    for ( i = 0; i < 3; i++ )
    {
        row = newclienthudelem( self );
        row.horzalign = "center";
        row.vertalign = "middle";
        row.alignx = "left";
        row.aligny = "middle";
        row.x = -92;
        row.y = -35 + ( i * 20 );
        row.sort = 2;
        row.alpha = 1;
        row.foreground = true;
        row.font = "default";
        row.fontscale = 1.1;
        self.menu_hud[ "row" + i ] = row;
    }

    self.menu_hud_built = true;
}

destroy_menu_hud()
{
    if ( !isdefined( self.menu_hud_built ) || !self.menu_hud_built )
    {
        return;
    }

    keys = getarraykeys( self.menu_hud );

    for ( i = 0; i < keys.size; i++ )
    {
        elem = self.menu_hud[ keys[i] ];

        if ( isdefined( elem ) )
        {
            elem destroy();
        }
    }

    self.menu_hud = [];
    self.menu_hud_built = false;
}
```

## Menu Open/Update Pattern

```gsc
draw_menu()
{
    if ( !self.menu_open )
    {
        self destroy_menu_hud();
        return;
    }

    if ( !isdefined( self.menu_hud_built ) || !self.menu_hud_built )
    {
        self build_menu_hud();
    }

    labels = [];
    labels[0] = "Save Position";
    labels[1] = "Load Position";
    labels[2] = "Safe Noclip";

    for ( i = 0; i < 3; i++ )
    {
        row = self.menu_hud[ "row" + i ];

        if ( i == self.menu_cursor )
        {
            row setText( "^3> " + labels[i] );
            row.glowcolor = ( 1, 0.8, 0 );
            row.glowalpha = 0.9;
        }
        else
        {
            row setText( "^7   " + labels[i] );
            row.glowalpha = 0;
        }
    }
}
```

## Spawn Initialization

Initialize these fields when the player spawns:

```gsc
self.menu_hud = [];
self.menu_hud_built = false;
```

