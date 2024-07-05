if exists('g:vscode')
    " Keep undo/redo lists in sync with VSCode
    nmap <silent> u <Cmd>call VSCodeNotify('undo')<CR>
    nmap <silent> <C-r> <Cmd>call VSCodeNotify('redo')<CR>
endif
