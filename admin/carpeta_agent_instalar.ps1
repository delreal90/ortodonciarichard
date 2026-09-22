# carpeta_agent_instalar.ps1
#
# Deja el ayudante de carpetas andando en ESTE PC y que arranque solo con
# Windows. No pide permiso de administrador: todo va al perfil del usuario.
#
# Se ejecuta con doble clic en `carpeta_agent_instalar.bat`, que esta al lado.
#
# Que hace, en orden:
#   1. Busca Python.
#   2. COPIA los 3 archivos que el ayudante necesita a una carpeta propia del
#      usuario, para que despues se pueda borrar el pendrive o la carpeta de
#      donde salio.
#   3. Comprueba que se alcance \\DIGITAL1\Registros Pacientes.
#   4. Crea el acceso directo en la carpeta de Inicio de Windows.
#   5. Lo arranca y muestra la LLAVE que hay que pegar en la extension.

$ErrorActionPreference = 'Stop'

$ORIGEN  = Split-Path -Parent $MyInvocation.MyCommand.Path
$DESTINO = Join-Path $env:LOCALAPPDATA 'OrtodonciaRichard\ayudante-carpetas'
$RAIZ    = '\\DIGITAL1\Registros Pacientes'
$PUERTO  = 8777

# Los tres archivos del ayudante. `carpetas.py` trae el cerebro y `texto.py` la
# normalizacion de tildes que ese cerebro usa.
$ARCHIVOS = @('carpeta_agent.py', 'carpetas.py', 'texto.py')

function Titulo($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function Bien($t)   { Write-Host "   OK  $t" -ForegroundColor Green }
function Aviso($t)  { Write-Host "   !!  $t" -ForegroundColor Yellow }
function Malo($t)   { Write-Host "   XX  $t" -ForegroundColor Red }

Write-Host ""
Write-Host "  Ayudante de carpetas de pacientes - instalacion" -ForegroundColor White
Write-Host "  (abre el Explorador en la carpeta del paciente desde el F2)"

# ── 1. Python ────────────────────────────────────────────────────────────────
Titulo "Buscando Python"

# `pythonw.exe` es el que NO abre una ventana negra. Si solo esta el lanzador
# `py`, su gemelo sin consola es `pyw`.
$pythonw = $null
foreach ($cmd in @('pythonw.exe', 'pyw.exe')) {
    $encontrado = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($encontrado) { $pythonw = $encontrado.Source; break }
}
if (-not $pythonw) {
    Malo "No hay Python en este PC."
    Write-Host ""
    Write-Host "   Instalalo desde https://www.python.org/downloads/ y marca"
    Write-Host "   la casilla 'Add python.exe to PATH' en la primera pantalla."
    Write-Host "   Despues vuelve a hacer doble clic en este instalador."
    Write-Host ""
    Read-Host "   Enter para cerrar"
    exit 1
}
Bien $pythonw

# ── 2. Copiar los archivos ───────────────────────────────────────────────────
Titulo "Copiando el ayudante a $DESTINO"
foreach ($a in $ARCHIVOS) {
    if (-not (Test-Path (Join-Path $ORIGEN $a))) {
        Malo "Falta '$a' junto a este instalador."
        Write-Host "   Copia la carpeta completa y vuelve a intentarlo."
        Read-Host "   Enter para cerrar"
        exit 1
    }
}
New-Item -ItemType Directory -Force -Path $DESTINO | Out-Null
foreach ($a in $ARCHIVOS) {
    Copy-Item (Join-Path $ORIGEN $a) -Destination $DESTINO -Force
    Bien $a
}

# ── 3. El servidor de fotos ──────────────────────────────────────────────────
Titulo "Comprobando $RAIZ"
if (Test-Path -LiteralPath $RAIZ) {
    $n = @(Get-ChildItem -LiteralPath $RAIZ -Directory -ErrorAction SilentlyContinue).Count
    Bien "se alcanza ($n carpetas en el primer nivel)"
} else {
    Aviso "no se alcanza desde este PC."
    Write-Host "   El ayudante igual queda instalado y lo va a decir en pantalla"
    Write-Host "   cuando alguien apriete F2. Revisa que DIGITAL1 este encendido"
    Write-Host "   y que este PC tenga acceso a la carpeta compartida."
}

# ── 4. Arranque automatico ───────────────────────────────────────────────────
Titulo "Dejandolo en el arranque de Windows"
$inicio = [Environment]::GetFolderPath('Startup')
$lnk    = Join-Path $inicio 'Ayudante carpetas Ortodoncia Richard.lnk'

$ws = New-Object -ComObject WScript.Shell
$acceso = $ws.CreateShortcut($lnk)
$acceso.TargetPath       = $pythonw
$acceso.Arguments        = '"' + (Join-Path $DESTINO 'carpeta_agent.py') + '"'
$acceso.WorkingDirectory = $DESTINO
$acceso.Description      = 'Abre la carpeta de fotos del paciente desde el F2'
$acceso.Save()
Bien $lnk

# ── 5. Arrancarlo ahora ──────────────────────────────────────────────────────
Titulo "Arrancandolo"
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='pyw.exe'" |
    Where-Object { $_.CommandLine -like '*carpeta_agent.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Process -FilePath $pythonw `
              -ArgumentList ('"' + (Join-Path $DESTINO 'carpeta_agent.py') + '"') `
              -WorkingDirectory $DESTINO -WindowStyle Hidden

Start-Sleep -Seconds 2
$llave = ''
# ⚠️ La llave se lee DEL ARCHIVO, no de lo que el programa imprime. Capturar la
# salida era lo que en un PC virgen metia "Llave nueva generada en ..." dentro de
# config.js y lo dejaba sin ser JavaScript valido. Ver log() en carpeta_agent.py.
try {
    & $pythonw.Replace('pythonw', 'python').Replace('pyw', 'py') `
      (Join-Path $DESTINO 'carpeta_agent.py') --token | Out-Null
} catch { }
$llave = ''
try { $llave = (Get-Content (Join-Path $DESTINO 'carpeta_token.txt') -Raw).Trim() } catch { }
if ($llave -notmatch '^[0-9a-f]{32}$') {
    Aviso "La llave no tiene la forma esperada. Revisa carpeta_token.txt."
}

$responde = $false
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$PUERTO/estado" -TimeoutSec 5 `
                           -Headers @{ 'X-Carpeta-Token' = $llave } -UseBasicParsing
    $responde = ($r.StatusCode -eq 200)
} catch { }

if ($responde) { Bien "responde en http://127.0.0.1:$PUERTO" }
else { Aviso "no contesto todavia. Reinicia el PC y prueba el F2." }

# ── Final ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ------------------------------------------------------------" -ForegroundColor White
Write-Host "  LISTO. Falta un paso, una sola vez por PC:" -ForegroundColor White
Write-Host ""
Write-Host "  Abre el archivo  config.js  de la carpeta 'dentidesk-assistant'"
Write-Host "  y pega esta llave en 'carpetaToken':"
Write-Host ""
Write-Host "      $llave" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Despues recarga la extension en chrome://extensions y prueba F2"
Write-Host "  sobre una cita: tiene que aparecer 'Carpeta del paciente'."
Write-Host "  ------------------------------------------------------------" -ForegroundColor White
Write-Host ""
Read-Host "  Enter para cerrar"
