if exists('g:vscode')
    nnoremap <silent> u :undo<CR>
    nnoremap <silent> <C-r> :redo<CR>
endif

if exists("g:neovide")
    nnoremap <silent> <D-w> :q<CR>
    vnoremap <silent> <D-w> <ESC>:q<CR>
    cnoremap <silent> <D-w> <ESC>:q<CR>

    vnoremap <silent> <D-c> "+y<CR>

    nnoremap <silent> <D-v> "+P<CR>
    vnoremap <silent> <D-v> "+P<CR>
    cnoremap <silent> <D-v> <ESC>"+P<CR>
    inoremap <silent> <D-v> <ESC>"+P<CR>

    nnoremap <silent> <D-n> :!neovide<CR>
endif


"vnoremap Y Y'[
"vnoremap y y'[
"vnoremap d d'[
"onoremap Y Y'[
"onoremap y y'[
"onoremap d d'[
"set tenc=korea
"set enc=utf-8
"set langmenu=cp949
"imap <C-L> <Esc>
inoremap <expr> <C-L> (pumvisible() <bar><bar> &insertmode) ? '<C-L>' : '<Esc>'

"noremap p p`[
"noremap P P`[

"function! RestoreRegister()
"  let @" = s:restore_reg
"  return ''
"endfunction
"
"function! s:pRepl()
"    let s:restore_reg = @"
"    return "p`[@=RestoreRegister()\<cr>"
"endfunction
"
"vnoremap <silent> <expr> p <sid>pRepl()

lang C
"set enc=utf-8
set lcs=conceal:\ ,nbsp:\ ,tab:├\ ,eol:¶,trail:·,extends:…,precedes:…
set backspace=indent,eol,start
"set columns=160
"set lines=40
set nowrap
set ts=4
set sw=4
set sts=4
set et
set ru
set si
set ai
set ru
set si
set ai
set sm
set sc
set nu
set list
set nocompatible
set guifont=KHack_NF:h13
"set guifont=Roboto\ Mono:h9:cHANGEUL
syntax on
map k g<UP>
map j g<DOWN>
let python_highlight_all=1
"set cino=h0+0(0,W2m1g1
let g:netrw_liststyle=1
"set guioptions+=b
"set fencs=cp949,ucs-bom,utf-8
color evening
"set background=dark
"match OverLength /\%80v/
set directory=/tmp
set backupdir=/tmp
"set noautoread
set hid "for undo history
"let g:NERDTreeQuitOnOpen=1
"let g:NERDTreeWinPos="right"
"let g:NERDTreeAutoCenter=1
"let g:NERDTreeHijackNetrw=1
"command! -n=? -complete=dir -bar E :NERDTreeFind
set clipboard=
"if has("clipboard")
"  set clipboard=unnamed " copy to the system clipboard
"
"  if has("unnamedplus") " X11 support
"    set clipboard+=unnamedplus
"  endif
"endif

"let g:EclimSignLevel=0
"let g:EclimEchoHighlight=0
"let g:EclimShowCurrentError=0
"nmap <f9> :call eclim#vimplugin#FeedKeys('Ctrl+B')<cr>
let clj_highlight_builtine = 1
let clj_perform_rainbow = 1
set hlsearch
"set fileformat=dos
autocmd FileType netrw setl bufhidden=wipe
filetype off
set rtp+=~/.vim/bundle/Vundle.vim
call vundle#begin()
call vundle#end()
Plugin 'vim-autoformat/vim-autoformat'
let g:autoformat_verbosemode=1
let g:formatdef_scalafmt = "'scalafmt --stdin 2>/dev/null'"
let g:formatdef_sbtfmt = "'scalafmt --stdin --assume-filename a.sbt 2>/dev/null'"
let g:formatters_scala = ['scalafmt']
let g:formatters_sbt = ['sbtfmt']
Plugin 'Yggdroot/indentLine'
Plugin 'berdandy/ansiesc.vim'
"let g:indentLine_char = '¦'
let g:indentLine_color_term = 100
let g:indentLine_color_gui = '#888888'
let g:indentLine_char_list = ['|', '¦', '┆', '┊']
"let g:indentLine_concealcursor = 'vc'
"let g:indentLine_conceallevel = 0
let g:indentLine_showFirstIndentLevel = 1
filetype plugin indent on
autocmd VimEnter,Colorscheme * :hi OverLength term=reverse cterm=reverse gui=reverse
autocmd VimEnter,Colorscheme * :hi SpecialKey ctermfg=DarkGray guifg=DarkGray guibg=background
autocmd VimEnter,Colorscheme * :hi NonText ctermfg=DarkGray guifg=DarkGray guibg=background

Plugin 'udalov/kotlin-vim'

