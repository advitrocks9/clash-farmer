#SingleInstance, Ignore
#NoEnv                ; Recommended for performance and compatibility
SendMode Input        ; Recommended for faster, more reliable sending of keystrokes
SetWorkingDir %A_ScriptDir%  ; Ensures a consistent1 starting directory

; Remap Middle button (Mouse Button 3) → “q”
MButton::
    Send, q
return

; Remap XButton1 (Mouse Button 4) → “1”
XButton1::
    Send, 1
return

; Remap XButton2 (Mouse Button 5) → “8”
XButton2::
    Send, 8
return
