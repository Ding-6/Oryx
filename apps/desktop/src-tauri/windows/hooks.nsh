; Oryx 安装脚本钩子
;
; 目标：安装目录默认落在 D 盘，不占 C 盘。
;
; Tauri 的 NSIS 模板默认装到 $LOCALAPPDATA\Oryx（C 盘）。用户明确要求
; 「所有安装路径都在 D 盘」—— C 盘经常被塞满。
;
; 时机很关键：
;   · .onInit 里 Tauri 已经把 $INSTDIR 从占位符改成了 $LOCALAPPDATA\Oryx
;     （installer.nsi 的 "Set default install location"）
;   · NSIS_HOOK_PREINSTALL 在**拷文件之前**执行，此时 $INSTDIR 是最终值
;
; 所以判据是「$INSTDIR 还是不是 Tauri 的默认 C 盘路径」——
; 如果用户在目录页自己改了（改了就不是 $LOCALAPPDATA\Oryx），尊重用户的选择。

!macro NSIS_HOOK_PREINSTALL
  ; 只有还是默认路径时才换成 D 盘 —— 用户改过就尊重用户
  ${If} $INSTDIR == "$LOCALAPPDATA\${PRODUCTNAME}"
    ; D 盘在不在？在才换。拿不到就保持原样（装到 C 盘也比装失败强）。
    ;
    ; **用 NSIS 原生的 IfFileExists 指令，不是 LogicLib 的 ${FileExists}** ——
    ; ${FileExists} 是 LogicLib 的布尔宏，只对**文件**为真；
    ; D:\ 是**目录**，拿它当布尔用会得到「假」（或直接编译报错）。
    ; IfFileExists 才是官方的文件/目录存在判断。
    IfFileExists "D:\*.*" 0 done
    StrCpy $INSTDIR "D:\Oryx"
    ; **必须重新 SetOutPath** ——
    ; Tauri 模板里是 `SetOutPath $INSTDIR` 然后才插入这个钩子。
    ; 只改 $INSTDIR 的话，输出路径已经定死在 C 盘了，
    ; 文件还是全落到 C 盘（实测踩过）。
    SetOutPath $INSTDIR
    done:
  ${EndIf}
!macroend

; 卸载时**保留**数据目录。
;
; 为什么保留：D:\OryxData 里有配置、历史会话、内容索引（1.3GB）、
; 下载的模型（向量模型 + OCR 模型）。用户卸载程序往往是因为要重装或换版本，
; 不是要扔掉自己的数据。删了还得重新下模型、重新扫盘。
;
; 之前这里是无条件 `RMDir /r "D:\OryxData"` —— 实测卸载一次，
; 向量模型和索引库全没了，重装后向量检索直接不可用。
;
; 真想彻底清理：用户自己删 D:\OryxData 就行（卸载器里有「删除数据」的
; 独立入口更好，但那是另一个功能，不在本次范围）。
;
; 这里只做一件小事：把 webview profile 清掉 —— 那是纯缓存，没有保留价值。
!macro NSIS_HOOK_POSTUNINSTALL
  ; WebView2 的 profile 是纯缓存（含 localStorage，重装后登录态会丢，
  ; 但那是浏览器数据，不算用户数据）。留着只是占 12MB。
  RMDir /r "D:\OryxData\tauri"
!macroend
