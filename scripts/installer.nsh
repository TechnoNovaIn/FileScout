; scripts/installer.nsh
; Custom NSIS script — runs after FileScout installs on Windows
; Automatically registers the right-click context menu for folders

!macro customInstall
  ; Register "Open with FileScout" on right-click for folders
  WriteRegStr HKCU "Software\Classes\Directory\shell\FileScout" "" "Open with FileScout"
  WriteRegStr HKCU "Software\Classes\Directory\shell\FileScout" "Icon" "$INSTDIR\FileScout.exe,0"
  WriteRegStr HKCU "Software\Classes\Directory\shell\FileScout\command" "" '"$INSTDIR\FileScout.exe" "%V"'

  ; Also register for right-clicking inside an open folder window
  WriteRegStr HKCU "Software\Classes\Directory\Background\shell\FileScout" "" "Open with FileScout"
  WriteRegStr HKCU "Software\Classes\Directory\Background\shell\FileScout" "Icon" "$INSTDIR\FileScout.exe,0"
  WriteRegStr HKCU "Software\Classes\Directory\Background\shell\FileScout\command" "" '"$INSTDIR\FileScout.exe" "%V"'
!macroend

!macro customUnInstall
  ; Clean up registry entries on uninstall
  DeleteRegKey HKCU "Software\Classes\Directory\shell\FileScout"
  DeleteRegKey HKCU "Software\Classes\Directory\Background\shell\FileScout"
!macroend
