# Controls and sessions

| Action | DualSense | Player 1 keyboard | Player 2 keyboard |
|---|---|---|---|
| Move / aim | Left stick or D-pad | Arrows | WASD |
| Shot (original SW1) | Cross | Z | F |
| Lob (original SW2) | Circle | X | G |
| Insert coin | L1 | 5 | 6 |
| Start | Options | 1 or Return | 2 |
| Host pause / resume | Create (small button left of touchpad) | P or Escape | Shared |

Wait for the original title screen before inserting credit and pressing Start. Move to choose a player; Shot confirms the original selection. The game supports singles against the computer and a second human player. Both players use the original two tennis buttons.

The NAOMI arcade game will begin a serve after waiting with no input, including
the opening serve. A fresh-session replay with all controls released after
player selection reproduced this in both the original-code reference and the
fixed port. This behavior is not a controller press inserted by the Mac host;
the Dreamcast release's timing has not been compared. See
[pause and serve validation](pause-and-serve.md).

Up to two extended gamepads receive stable player assignments. Connecting an unused extra controller does not reorder players. The keyboard can be used alongside controllers. Keyboard and controller input for the same player are combined; opposing directions cancel. Short taps are retained until the engine actually consumes an input step.

Focus loss, sleep and disconnecting an assigned controller pause the host. Release held gameplay buttons and sticks before resuming. Start resumes a host-paused game without also sending an arcade Start press. The Game menu provides Pause/Resume, Reset Game and Mute. Command-R resets, Command-M changes mute, and Command-F changes full-screen mode.

Normal preferences and saves use `local.william.virtuatennis`. The writable data folder is `~/Library/Application Support/local.william.virtuatennis`. It includes a private BIOS cache and any files created by the original hardware backend. Do not copy diagnostic saves over a normal session. Reset preserves persistent data; Quit lets the backend close its files normally.
