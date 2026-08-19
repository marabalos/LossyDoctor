package main

import (
	"archive/zip"
	"bufio"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

const bootstrapVersion = "1.1.0-bootstrap.1"

const (
	errorAlreadyExists      = syscall.Errno(183)
	moveFileReplaceExisting = 0x1
	moveFileWriteThrough    = 0x8
)

type ToolSpec struct {
	Version       string `json:"version"`
	URL           string `json:"url,omitempty"`
	SHA256        string `json:"sha256,omitempty"`
	Distribution  string `json:"distribution,omitempty"`
	ManagedBy     string `json:"managed_by,omitempty"`
	IntegrityMode string `json:"integrity_mode,omitempty"`
	BinarySHA256  string `json:"binary_sha256,omitempty"`
}

type PackageSpec struct {
	RequirementsFile string `json:"requirements_file"`
	InstallMode      string `json:"install_mode"`
	OnlyBinary       bool   `json:"only_binary"`
}

type DiskSpec struct {
	InitialFreeMB uint64 `json:"initial_free_mb"`
	RepairFreeMB  uint64 `json:"repair_free_mb"`
}

type Manifest struct {
	SchemaVersion      int         `json:"schema_version"`
	Policy             string      `json:"policy"`
	LossyDoctorVersion string      `json:"lossydoctor_version"`
	Platform           string      `json:"platform"`
	MinimumWindows     string      `json:"minimum_windows"`
	Disk               DiskSpec    `json:"disk"`
	UV                 ToolSpec    `json:"uv"`
	Python             ToolSpec    `json:"python"`
	FFmpeg             ToolSpec    `json:"ffmpeg"`
	MPG123             ToolSpec    `json:"mpg123"`
	PythonPackages     PackageSpec `json:"python_packages"`
}

type bootstrapState struct {
	SchemaVersion      int    `json:"schema_version"`
	BootstrapVersion   string `json:"bootstrap_version"`
	LossyDoctorVersion string `json:"lossydoctor_version"`
	Policy             string `json:"policy"`
	UVVersion          string `json:"uv_version"`
	UVArchiveSHA       string `json:"uv_archive_sha256"`
	PythonVersion      string `json:"python_version"`
	FFmpegVersion      string `json:"ffmpeg_version"`
	FFmpegArchiveSHA   string `json:"ffmpeg_archive_sha256"`
	MPG123Version      string `json:"mpg123_version"`
	MPG123ArchiveSHA   string `json:"mpg123_archive_sha256"`
	MPG123BinarySHA    string `json:"mpg123_binary_sha256"`
	RequirementsSHA    string `json:"requirements_sha256"`
	ManifestSHA        string `json:"bootstrap_manifest_sha256"`
	Root               string `json:"root"`
	PreparedAt         string `json:"prepared_at"`
}

type app struct {
	root          string
	runtimeRoot   string
	cacheRoot     string
	downloadsRoot string
	stateRoot     string
	logPath       string
	manifestPath  string
	manifest      Manifest
}

func main() {
	os.Exit(runMain())
}

func runMain() int {
	exe, err := os.Executable()
	if err != nil {
		fmt.Fprintln(os.Stderr, "BOOTSTRAP_FATAL: no se pudo resolver la ruta del ejecutable:", err)
		return 10
	}
	root := filepath.Dir(exe)
	a := &app{
		root:          root,
		runtimeRoot:   filepath.Join(root, "runtime"),
		cacheRoot:     filepath.Join(root, "cache"),
		downloadsRoot: filepath.Join(root, "cache", "downloads"),
		stateRoot:     filepath.Join(root, "state"),
		logPath:       filepath.Join(root, "state", "bootstrap.log"),
		manifestPath:  filepath.Join(root, "bootstrap_manifest.json"),
	}

	for _, d := range []string{a.runtimeRoot, a.cacheRoot, a.downloadsRoot, a.stateRoot, filepath.Join(root, "logs")} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			fmt.Fprintln(os.Stderr, "BOOTSTRAP_FATAL: no se puede escribir dentro de la carpeta de LossyDoctor:", err)
			return 10
		}
	}
	if err := a.checkWritable(); err != nil {
		a.fail("escritura", err)
		return 10
	}

	a.log("========== LossyDoctor Bootstrap %s =========", bootstrapVersion)
	a.log("Root: %s", a.root)

	if err := a.loadManifest(); err != nil {
		a.fail("manifest", err)
		return 10
	}
	if err := a.checkPlatform(); err != nil {
		a.fail("plataforma", err)
		return 11
	}

	args := os.Args[1:]
	prepareOnly := false
	runTests := false
	diagnose := false
	var inputs []string
	for _, arg := range args {
		switch arg {
		case "--prepare-only":
			prepareOnly = true
		case "--run-unit-tests":
			runTests = true
		case "--diagnose":
			diagnose = true
		default:
			inputs = append(inputs, arg)
		}
	}
	if len(inputs) > 0 {
		a.log("Entradas recibidas (%d): %s", len(inputs), strings.Join(inputs, " | "))
	}

	pythonExe, ffmpegExe, ffprobeExe, mpg123Exe, err := a.prepareWithLock()
	if err != nil {
		a.fail("preparacion", err)
		fmt.Fprintln(os.Stderr, "")
		fmt.Fprintln(os.Stderr, "Si es la primera ejecucion, revise README.md -> Requisitos de conectividad web.")
		fmt.Fprintln(os.Stderr, "El detalle queda en:", a.logPath)
		return 12
	}

	if diagnose {
		a.printDiagnosis(pythonExe, ffmpegExe, ffprobeExe, mpg123Exe)
		return 0
	}
	if prepareOnly {
		a.step("Entorno portable preparado. No se requieren descargas mientras permanezca intacto.")
		return 0
	}
	if runTests {
		return a.runUnitTests(pythonExe, ffmpegExe, ffprobeExe)
	}
	if len(inputs) == 0 {
		fmt.Println("")
		fmt.Println("Uso: arrastre uno o mas archivos/carpetas sobre LossyDoctor.bat")
		fmt.Println("Para preparar dependencias sin auditar: LossyDoctorBootstrap.exe --prepare-only")
		return 2
	}
	return a.runLossyDoctor(pythonExe, ffmpegExe, ffprobeExe, mpg123Exe, inputs)
}

func (a *app) checkWritable() error {
	p := filepath.Join(a.stateRoot, ".write_test")
	if err := os.WriteFile(p, []byte("ok"), 0o644); err != nil {
		return fmt.Errorf("la carpeta no es escribible: %w", err)
	}
	if err := os.Remove(p); err != nil {
		return fmt.Errorf("no se pudo completar la prueba de escritura: %w", err)
	}
	return nil
}

func (a *app) loadManifest() error {
	raw, err := os.ReadFile(a.manifestPath)
	if err != nil {
		return fmt.Errorf("no existe %s: %w", a.manifestPath, err)
	}
	if err := json.Unmarshal(raw, &a.manifest); err != nil {
		return fmt.Errorf("manifest invalido: %w", err)
	}
	if a.manifest.SchemaVersion != 2 {
		return fmt.Errorf("bootstrap_manifest schema %d no soportado", a.manifest.SchemaVersion)
	}
	if a.manifest.Policy != "validated-recommended-pinned" {
		return fmt.Errorf("policy no soportada: %s", a.manifest.Policy)
	}
	if a.manifest.UV.Version == "" || a.manifest.Python.Version == "" || a.manifest.FFmpeg.Version == "" || a.manifest.MPG123.Version == "" {
		return errors.New("faltan versiones fijadas en bootstrap_manifest.json")
	}
	if !validSHA256(a.manifest.UV.SHA256) || !validSHA256(a.manifest.FFmpeg.SHA256) || !validSHA256(a.manifest.MPG123.SHA256) || !validSHA256(a.manifest.MPG123.BinarySHA256) {
		return errors.New("uv, FFmpeg y mpg123 deben declarar SHA-256 exactos en bootstrap_manifest.json")
	}
	if err := validatePinnedDownloadURL("uv", a.manifest.UV.URL); err != nil {
		return err
	}
	if err := validatePinnedDownloadURL("FFmpeg", a.manifest.FFmpeg.URL); err != nil {
		return err
	}
	if a.manifest.MPG123.IntegrityMode != "pinned-sha256" {
		return errors.New("mpg123 debe declarar integrity_mode pinned-sha256")
	}
	if a.manifest.MPG123.Version != "1.33.7" {
		return fmt.Errorf("mpg123 requiere version exacta 1.33.7")
	}
	if err := validatePinnedDownloadURL("mpg123", a.manifest.MPG123.URL); err != nil {
		return err
	}
	if a.manifest.Disk.InitialFreeMB == 0 || a.manifest.Disk.RepairFreeMB == 0 {
		return errors.New("faltan umbrales de espacio libre en bootstrap_manifest.json")
	}
	a.step("Perfil validado: uv %s | Python %s | FFmpeg %s | mpg123 %s", a.manifest.UV.Version, a.manifest.Python.Version, a.manifest.FFmpeg.Version, a.manifest.MPG123.Version)
	a.step("Politica: versiones recomendadas y validadas; NO se consulta ni instala 'latest'.")
	return nil
}

func (a *app) checkPlatform() error {
	if runtime.GOOS != "windows" {
		return fmt.Errorf("este bootstrap solo soporta Windows")
	}
	if runtime.GOARCH != "amd64" {
		return fmt.Errorf("esta distribucion requiere Windows x64")
	}
	major, minor, build, err := windowsVersion()
	if err != nil {
		a.log("Advertencia: no se pudo consultar RtlGetVersion: %v", err)
		return nil
	}
	a.step("Windows detectado: %d.%d build %d x64", major, minor, build)
	if major < 10 {
		return fmt.Errorf("Windows %d.%d no soportado; minimo Windows 10 x64", major, minor)
	}
	return nil
}

func (a *app) prepareWithLock() (string, string, string, string, error) {
	h, err := acquireBootstrapMutex(a.root)
	if err != nil {
		return "", "", "", "", err
	}
	defer syscall.CloseHandle(syscall.Handle(h))
	return a.prepare()
}

func (a *app) prepare() (pythonExe, ffmpegExe, ffprobeExe, mpg123Exe string, err error) {
	if err = a.preflightDiskIfNeeded(); err != nil {
		return
	}
	if err = a.ensureUV(); err != nil {
		return
	}
	pythonExe, err = a.ensurePython()
	if err != nil {
		return
	}
	if err = a.ensurePythonPackages(pythonExe); err != nil {
		return
	}
	ffmpegExe, ffprobeExe, err = a.ensureFFmpeg()
	if err != nil {
		return
	}
	mpg123Exe, err = a.ensureMPG123()
	if err != nil {
		return
	}
	if err = a.writeState(); err != nil {
		return
	}
	return
}

func (a *app) preflightDiskIfNeeded() error {
	if a.runtimeReady() {
		return nil
	}
	minimum := a.manifest.Disk.InitialFreeMB
	mode := "preparacion inicial"
	if a.runtimeHasAnyComponent() {
		minimum = a.manifest.Disk.RepairFreeMB
		mode = "reparacion"
	}
	free, err := freeSpaceBytes(a.root)
	if err != nil {
		a.log("Advertencia: no se pudo consultar espacio libre: %v", err)
		return nil
	}
	freeMB := free / (1024 * 1024)
	a.step("Espacio libre: %d MB; minimo para %s: %d MB", freeMB, mode, minimum)
	if freeMB < minimum {
		return fmt.Errorf("DISK_SPACE: espacio insuficiente: %d MB libres; se requieren al menos %d MB para %s", freeMB, minimum, mode)
	}
	return nil
}

func (a *app) runtimeHasAnyComponent() bool {
	for _, p := range []string{
		filepath.Join(a.runtimeRoot, "uv", "uv.exe"),
		filepath.Join(a.runtimeRoot, "python"),
		filepath.Join(a.runtimeRoot, "site-packages"),
		filepath.Join(a.runtimeRoot, "ffmpeg", "ffmpeg.exe"),
		filepath.Join(a.runtimeRoot, "mpg123", "mpg123.exe"),
	} {
		if _, err := os.Stat(p); err == nil {
			return true
		}
	}
	return false
}

func (a *app) runtimeReady() bool {
	uv := filepath.Join(a.runtimeRoot, "uv", "uv.exe")
	if !versionMatches(uv, []string{"--version"}, a.manifest.UV.Version) {
		return false
	}
	py := findPythonVersion(filepath.Join(a.runtimeRoot, "python"), a.manifest.Python.Version)
	if py == "" {
		return false
	}
	req := filepath.Join(a.root, a.manifest.PythonPackages.RequirementsFile)
	raw, err := os.ReadFile(req)
	if err != nil {
		return false
	}
	sig := sha256Hex(append([]byte(a.manifest.Python.Version+"\n"), raw...))
	sigRaw, err := os.ReadFile(filepath.Join(a.stateRoot, "python_deps.sha256"))
	if err != nil || strings.TrimSpace(string(sigRaw)) != sig {
		return false
	}
	if !a.verifyImports(py, filepath.Join(a.runtimeRoot, "site-packages")) {
		return false
	}
	ffmpeg := filepath.Join(a.runtimeRoot, "ffmpeg", "ffmpeg.exe")
	ffprobe := filepath.Join(a.runtimeRoot, "ffmpeg", "ffprobe.exe")
	return versionMatches(ffmpeg, []string{"-version"}, a.manifest.FFmpeg.Version) && versionMatches(ffprobe, []string{"-version"}, a.manifest.FFmpeg.Version) && a.mpg123Ready()
}

func (a *app) ensureUV() error {
	targetDir := filepath.Join(a.runtimeRoot, "uv")
	if err := recoverDirectorySwap(targetDir); err != nil {
		return err
	}
	exe := filepath.Join(targetDir, "uv.exe")
	if versionMatches(exe, []string{"--version"}, a.manifest.UV.Version) {
		a.step("uv %s OK (local)", a.manifest.UV.Version)
		return nil
	}
	a.step("uv %s no disponible; preparando copia local", a.manifest.UV.Version)
	archive := filepath.Join(a.downloadsRoot, "uv-"+a.manifest.UV.Version+"-windows-x64.zip")
	if err := a.ensureDownload(a.manifest.UV.URL, archive, a.manifest.UV.SHA256); err != nil {
		return err
	}
	stage := targetDir + ".new"
	os.RemoveAll(stage)
	unpack := stage + ".unpack"
	os.RemoveAll(unpack)
	if err := unzipSafe(archive, unpack); err != nil {
		return fmt.Errorf("cache uv no extraible aunque su SHA-256 es correcto: %w", err)
	}
	uvFound, err := findFile(unpack, "uv.exe")
	if err != nil {
		os.RemoveAll(unpack)
		return err
	}
	if err := os.MkdirAll(stage, 0o755); err != nil {
		return err
	}
	if err := copyFile(uvFound, filepath.Join(stage, "uv.exe")); err != nil {
		return err
	}
	os.RemoveAll(unpack)
	if !versionMatches(filepath.Join(stage, "uv.exe"), []string{"--version"}, a.manifest.UV.Version) {
		return fmt.Errorf("uv descargado no corresponde a la version fijada %s", a.manifest.UV.Version)
	}
	return activateDirectory(stage, targetDir)
}

func (a *app) uvExe() string { return filepath.Join(a.runtimeRoot, "uv", "uv.exe") }

func (a *app) uvEnv(pythonInstallDir string) []string {
	if pythonInstallDir == "" {
		pythonInstallDir = filepath.Join(a.runtimeRoot, "python")
	}
	env := append([]string{}, os.Environ()...)
	env = setEnv(env, "UV_CACHE_DIR", filepath.Join(a.cacheRoot, "uv"))
	env = setEnv(env, "UV_PYTHON_INSTALL_DIR", pythonInstallDir)
	env = setEnv(env, "UV_PYTHON_BIN_DIR", filepath.Join(a.runtimeRoot, "python-bin"))
	env = setEnv(env, "UV_MANAGED_PYTHON", "1")
	env = setEnv(env, "UV_NO_MODIFY_PATH", "1")
	return env
}

func (a *app) ensurePython() (string, error) {
	pyRoot := filepath.Join(a.runtimeRoot, "python")
	if err := recoverDirectorySwap(pyRoot); err != nil {
		return "", err
	}
	if py := findPythonVersion(pyRoot, a.manifest.Python.Version); py != "" {
		a.step("Python %s OK (local)", a.manifest.Python.Version)
		return py, nil
	}
	a.step("Instalando Python %s administrado por uv dentro de runtime\\python", a.manifest.Python.Version)
	stage := pyRoot + ".new"
	os.RemoveAll(stage)
	if err := os.MkdirAll(stage, 0o755); err != nil {
		return "", err
	}
	cmd := exec.Command(a.uvExe(), "python", "install", a.manifest.Python.Version, "--install-dir", stage)
	cmd.Env = a.uvEnv(stage)
	cmd.Dir = a.root
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		os.RemoveAll(stage)
		return "", fmt.Errorf("uv no pudo instalar Python %s: %w", a.manifest.Python.Version, err)
	}
	if findPythonVersion(stage, a.manifest.Python.Version) == "" {
		os.RemoveAll(stage)
		return "", fmt.Errorf("Python %s no quedo disponible en staging", a.manifest.Python.Version)
	}
	if err := activateDirectory(stage, pyRoot); err != nil {
		return "", err
	}
	py := findPythonVersion(pyRoot, a.manifest.Python.Version)
	if py == "" {
		return "", fmt.Errorf("Python %s no quedo disponible en %s", a.manifest.Python.Version, pyRoot)
	}
	return py, nil
}

func (a *app) ensurePythonPackages(pythonExe string) error {
	req := filepath.Join(a.root, a.manifest.PythonPackages.RequirementsFile)
	raw, err := os.ReadFile(req)
	if err != nil {
		return fmt.Errorf("requirements no disponible: %w", err)
	}
	sig := sha256Hex(append([]byte(a.manifest.Python.Version+"\n"), raw...))
	sigPath := filepath.Join(a.stateRoot, "python_deps.sha256")
	site := filepath.Join(a.runtimeRoot, "site-packages")
	if err := os.MkdirAll(site, 0o755); err != nil {
		return err
	}
	if !a.verifyImports(pythonExe, site) {
		return errors.New("Python stdlib no supero self-test")
	}
	oldSig, _ := os.ReadFile(sigPath)
	if strings.TrimSpace(string(oldSig)) == sig {
		a.step("Dependencias Python OK (stdlib-only; sin consulta web)")
		return nil
	}
	if err := writeFileAtomic(sigPath, []byte(sig+"\r\n")); err != nil {
		return err
	}
	a.step("Dependencias Python OK (stdlib-only; sin consulta web)")
	return nil
}

func (a *app) verifyImports(pythonExe, site string) bool {
	code := "import sys,json,hashlib,tomllib,pathlib; print('OK')"
	cmd := exec.Command(pythonExe, "-c", code)
	cmd.Env = a.pythonEnv(site, "", "")
	out, err := cmd.CombinedOutput()
	if err != nil {
		a.log("Verificacion stdlib fallo: %v: %s", err, strings.TrimSpace(string(out)))
		return false
	}
	return strings.Contains(string(out), "OK")
}

func (a *app) ensureFFmpeg() (string, string, error) {
	dir := filepath.Join(a.runtimeRoot, "ffmpeg")
	if err := recoverDirectorySwap(dir); err != nil {
		return "", "", err
	}
	ffmpeg := filepath.Join(dir, "ffmpeg.exe")
	ffprobe := filepath.Join(dir, "ffprobe.exe")
	if versionMatches(ffmpeg, []string{"-version"}, a.manifest.FFmpeg.Version) && versionMatches(ffprobe, []string{"-version"}, a.manifest.FFmpeg.Version) {
		a.step("FFmpeg %s OK (local)", a.manifest.FFmpeg.Version)
		return ffmpeg, ffprobe, nil
	}
	a.step("FFmpeg %s no disponible; preparando build validado", a.manifest.FFmpeg.Version)
	archive := filepath.Join(a.downloadsRoot, "ffmpeg-"+a.manifest.FFmpeg.Version+"-essentials_build.zip")
	if err := a.ensureDownload(a.manifest.FFmpeg.URL, archive, a.manifest.FFmpeg.SHA256); err != nil {
		return "", "", err
	}
	stage := dir + ".new"
	os.RemoveAll(stage)
	unpack := stage + ".unpack"
	os.RemoveAll(unpack)
	if err := unzipSafe(archive, unpack); err != nil {
		return "", "", fmt.Errorf("cache FFmpeg no extraible aunque su SHA-256 es correcto: %w", err)
	}
	srcFFmpeg, err := findFile(unpack, "ffmpeg.exe")
	if err != nil {
		os.RemoveAll(unpack)
		return "", "", err
	}
	srcFFprobe, err := findFile(unpack, "ffprobe.exe")
	if err != nil {
		os.RemoveAll(unpack)
		return "", "", err
	}
	if err := os.MkdirAll(stage, 0o755); err != nil {
		return "", "", err
	}
	if err := copyFile(srcFFmpeg, filepath.Join(stage, "ffmpeg.exe")); err != nil {
		return "", "", err
	}
	if err := copyFile(srcFFprobe, filepath.Join(stage, "ffprobe.exe")); err != nil {
		return "", "", err
	}
	os.RemoveAll(unpack)
	if !versionMatches(filepath.Join(stage, "ffmpeg.exe"), []string{"-version"}, a.manifest.FFmpeg.Version) || !versionMatches(filepath.Join(stage, "ffprobe.exe"), []string{"-version"}, a.manifest.FFmpeg.Version) {
		return "", "", fmt.Errorf("FFmpeg/ffprobe descargados no corresponden a la version fijada %s", a.manifest.FFmpeg.Version)
	}
	if err := activateDirectory(stage, dir); err != nil {
		return "", "", err
	}
	return filepath.Join(dir, "ffmpeg.exe"), filepath.Join(dir, "ffprobe.exe"), nil
}

func (a *app) mpg123Ready() bool {
	exe := filepath.Join(a.runtimeRoot, "mpg123", "mpg123.exe")
	if !versionMatches(exe, []string{"--version"}, a.manifest.MPG123.Version) {
		return false
	}
	got, err := sha256File(exe)
	return err == nil && strings.EqualFold(got, a.manifest.MPG123.BinarySHA256)
}

func (a *app) ensureMPG123() (string, error) {
	dir := filepath.Join(a.runtimeRoot, "mpg123")
	if err := recoverDirectorySwap(dir); err != nil {
		return "", err
	}
	exe := filepath.Join(dir, "mpg123.exe")
	if a.mpg123Ready() {
		a.step("mpg123 %s OK (local; PINNED_SHA256; binary SHA-256 %s)", a.manifest.MPG123.Version, strings.ToLower(a.manifest.MPG123.BinarySHA256))
		return exe, nil
	}
	archive := filepath.Join(a.downloadsRoot, "mpg123-"+a.manifest.MPG123.Version+"-static-x86-64.zip")
	if err := a.ensureDownload(a.manifest.MPG123.URL, archive, a.manifest.MPG123.SHA256); err != nil {
		return "", err
	}
	stage := dir + ".new"
	os.RemoveAll(stage)
	unpack := stage + ".unpack"
	os.RemoveAll(unpack)
	if err := unzipSafe(archive, unpack); err != nil {
		return "", err
	}
	src, err := findFile(unpack, "mpg123.exe")
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(stage, 0o755); err != nil {
		return "", err
	}
	if err := copyFile(src, filepath.Join(stage, "mpg123.exe")); err != nil {
		return "", err
	}
	os.RemoveAll(unpack)
	staged := filepath.Join(stage, "mpg123.exe")
	if !versionMatches(staged, []string{"--version"}, a.manifest.MPG123.Version) {
		return "", fmt.Errorf("mpg123 descargado no corresponde a %s", a.manifest.MPG123.Version)
	}
	binSHA, err := sha256File(staged)
	if err != nil {
		return "", err
	}
	if !strings.EqualFold(binSHA, a.manifest.MPG123.BinarySHA256) {
		return "", fmt.Errorf("INTEGRITY_MPG123_BINARY: esperado %s obtenido %s", a.manifest.MPG123.BinarySHA256, binSHA)
	}
	if err := activateDirectory(stage, dir); err != nil {
		return "", err
	}
	a.step("mpg123 %s instalado con archive/binary SHA-256 verificados", a.manifest.MPG123.Version)
	return filepath.Join(dir, "mpg123.exe"), nil
}

func (a *app) ensureDownload(downloadURL, dst, expectedSHA string) error {
	if !validSHA256(expectedSHA) {
		return fmt.Errorf("INTEGRITY_CONFIG: SHA-256 invalido para %s", downloadURL)
	}
	if fileExists(dst) {
		actual, err := sha256File(dst)
		if err == nil && strings.EqualFold(actual, expectedSHA) {
			a.step("Reutilizando cache verificado SHA-256: %s", filepath.Base(dst))
			return nil
		}
		a.log("Cache invalido para %s; esperado %s, obtenido %s. Se elimina.", filepath.Base(dst), expectedSHA, actual)
		if err := os.Remove(dst); err != nil {
			return fmt.Errorf("no se pudo eliminar cache invalido %s: %w", dst, err)
		}
	}

	tmp := dst + ".part"
	if fileExists(tmp) {
		a.log("Se encontro descarga interrumpida previa; se descarta: %s", tmp)
		_ = os.Remove(tmp)
	}

	a.step("Descargando asset versionado: %s", downloadURL)
	f, err := os.Create(tmp)
	if err != nil {
		return fmt.Errorf("no se pudo crear descarga temporal: %w", err)
	}
	completed := false
	defer func() {
		_ = f.Close()
		if !completed {
			_ = os.Remove(tmp)
		}
	}()

	transport := &http.Transport{
		Proxy:           http.ProxyFromEnvironment,
		TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12},
	}
	client := &http.Client{Timeout: 45 * time.Minute, Transport: transport}
	req, err := http.NewRequest("GET", downloadURL, nil)
	if err != nil {
		return fmt.Errorf("NETWORK_URL: URL invalida: %w", err)
	}
	req.Header.Set("User-Agent", "LossyDoctorBootstrap/"+bootstrapVersion)
	resp, err := client.Do(req)
	if err != nil {
		return classifyNetworkError(downloadURL, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("HTTP_STATUS_%d: el servidor respondio %s para %s", resp.StatusCode, resp.Status, downloadURL)
	}

	pr := &progressReader{r: resp.Body, total: resp.ContentLength}
	if _, err := io.Copy(f, pr); err != nil {
		return fmt.Errorf("NETWORK_INCOMPLETE: descarga interrumpida: %w", err)
	}
	if err := f.Sync(); err != nil {
		return fmt.Errorf("no se pudo sincronizar descarga temporal: %w", err)
	}
	if err := f.Close(); err != nil {
		return err
	}
	actual, err := sha256File(tmp)
	if err != nil {
		return err
	}
	if !strings.EqualFold(actual, expectedSHA) {
		return fmt.Errorf("INTEGRITY_SHA256: hash incorrecto para %s; esperado %s, obtenido %s", filepath.Base(dst), expectedSHA, actual)
	}
	if err := replaceFileAtomic(tmp, dst); err != nil {
		return fmt.Errorf("no se pudo activar descarga verificada: %w", err)
	}
	completed = true
	a.step("SHA-256 verificado: %s", filepath.Base(dst))
	return nil
}

func classifyNetworkError(downloadURL string, err error) error {
	lower := strings.ToLower(err.Error())
	if strings.Contains(lower, "proxyconnect") || strings.Contains(lower, "proxy") && strings.Contains(lower, "connect") {
		return fmt.Errorf("NETWORK_PROXY: no se pudo usar el proxy para %s: %w", downloadURL, err)
	}
	var dnsErr *net.DNSError
	if errors.As(err, &dnsErr) {
		return fmt.Errorf("NETWORK_DNS: no se pudo resolver el host para %s: %w", downloadURL, err)
	}
	var unknownAuthority x509.UnknownAuthorityError
	if errors.As(err, &unknownAuthority) {
		return fmt.Errorf("NETWORK_TLS_CERT: certificado TLS no confiable para %s: %w", downloadURL, err)
	}
	var certInvalid x509.CertificateInvalidError
	if errors.As(err, &certInvalid) {
		return fmt.Errorf("NETWORK_TLS_CERT: certificado TLS invalido para %s: %w", downloadURL, err)
	}
	var recordErr tls.RecordHeaderError
	if errors.As(err, &recordErr) {
		return fmt.Errorf("NETWORK_TLS: negociacion TLS fallo para %s: %w", downloadURL, err)
	}
	var netErr net.Error
	if errors.As(err, &netErr) && netErr.Timeout() {
		return fmt.Errorf("NETWORK_TIMEOUT: timeout accediendo a %s: %w", downloadURL, err)
	}
	var urlErr *url.Error
	if errors.As(err, &urlErr) {
		return fmt.Errorf("NETWORK_HTTPS: conexion HTTPS fallo para %s: %w", downloadURL, err)
	}
	return fmt.Errorf("NETWORK_HTTPS: descarga fallo para %s: %w", downloadURL, err)
}

type progressReader struct {
	r     io.Reader
	total int64
	n     int64
	last  int
}

func (p *progressReader) Read(b []byte) (int, error) {
	n, err := p.r.Read(b)
	p.n += int64(n)
	if p.total > 0 {
		pct := int(float64(p.n) * 100 / float64(p.total))
		bucket := pct / 10 * 10
		if bucket >= p.last+10 || pct == 100 {
			p.last = bucket
			fmt.Printf("  descarga: %d%%\n", pct)
		}
	}
	return n, err
}

func (a *app) runLossyDoctor(pythonExe, ffmpegExe, ffprobeExe, mpg123Exe string, inputs []string) int {
	config := filepath.Join(a.root, "config.toml")
	args := []string{"-m", "app.main", "--config", config, "--ffmpeg", ffmpegExe, "--ffprobe", ffprobeExe, "--mpg123", mpg123Exe}
	args = append(args, inputs...)
	a.step("Iniciando LossyDoctor %s", a.manifest.LossyDoctorVersion)
	cmd := exec.Command(pythonExe, args...)
	cmd.Dir = a.root
	cmd.Env = a.pythonEnv(filepath.Join(a.runtimeRoot, "site-packages"), ffmpegExe, ffprobeExe)
	cmd.Env = setEnv(cmd.Env, "LOSSYDOCTOR_MPG123", mpg123Exe)
	cmd.Env = setEnv(cmd.Env, "LOSSYDOCTOR_MPG123_TRUST", "PINNED_SHA256")
	cmd.Stdout, cmd.Stderr, cmd.Stdin = os.Stdout, os.Stderr, os.Stdin
	if err := cmd.Run(); err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			a.step("LossyDoctor termino con codigo %d", ee.ExitCode())
			return ee.ExitCode()
		}
		a.fail("ejecucion", err)
		return 20
	}
	a.step("LossyDoctor termino correctamente.")
	return 0
}

func (a *app) runUnitTests(pythonExe, ffmpegExe, ffprobeExe string) int {
	a.step("Ejecutando tests unitarios")
	cmd := exec.Command(pythonExe, "-m", "unittest", "discover", "-s", filepath.Join(a.root, "tests"), "-v")
	cmd.Dir = a.root
	cmd.Env = a.pythonEnv(filepath.Join(a.runtimeRoot, "site-packages"), ffmpegExe, ffprobeExe)
	cmd.Stdout, cmd.Stderr, cmd.Stdin = os.Stdout, os.Stderr, os.Stdin
	if err := cmd.Run(); err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			return ee.ExitCode()
		}
		return 20
	}
	return 0
}

func (a *app) pythonEnv(site, ffmpeg, ffprobe string) []string {
	env := append([]string{}, os.Environ()...)
	pp := a.root
	if site != "" {
		pp += ";" + site
	}
	env = setEnv(env, "PYTHONPATH", pp)
	env = setEnv(env, "PYTHONUTF8", "1")
	if ffmpeg != "" {
		env = setEnv(env, "LOSSYDOCTOR_FFMPEG", ffmpeg)
	}
	if ffprobe != "" {
		env = setEnv(env, "LOSSYDOCTOR_FFPROBE", ffprobe)
	}
	return env
}

func (a *app) writeState() error {
	reqRaw, _ := os.ReadFile(filepath.Join(a.root, a.manifest.PythonPackages.RequirementsFile))
	manifestRaw, _ := os.ReadFile(a.manifestPath)
	st := bootstrapState{
		SchemaVersion:      2,
		BootstrapVersion:   bootstrapVersion,
		LossyDoctorVersion: a.manifest.LossyDoctorVersion,
		Policy:             a.manifest.Policy,
		UVVersion:          a.manifest.UV.Version,
		UVArchiveSHA:       strings.ToLower(a.manifest.UV.SHA256),
		PythonVersion:      a.manifest.Python.Version,
		FFmpegVersion:      a.manifest.FFmpeg.Version,
		FFmpegArchiveSHA:   strings.ToLower(a.manifest.FFmpeg.SHA256),
		MPG123Version:      a.manifest.MPG123.Version,
		MPG123ArchiveSHA:   strings.ToLower(a.manifest.MPG123.SHA256),
		MPG123BinarySHA:    strings.ToLower(a.manifest.MPG123.BinarySHA256),
		RequirementsSHA:    sha256Hex(reqRaw),
		ManifestSHA:        sha256Hex(manifestRaw),
		Root:               a.root,
		PreparedAt:         time.Now().Format(time.RFC3339),
	}
	raw, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return err
	}
	return writeFileAtomic(filepath.Join(a.stateRoot, "bootstrap_state.json"), append(raw, '\n'))
}

func (a *app) printDiagnosis(pythonExe, ffmpegExe, ffprobeExe, mpg123Exe string) {
	free, _ := freeSpaceBytes(a.root)
	fmt.Println("")
	fmt.Println("LossyDoctor bootstrap listo.")
	fmt.Println("LossyDoctor:", a.manifest.LossyDoctorVersion)
	fmt.Println("Bootstrap:", bootstrapVersion)
	fmt.Println("Root:", a.root)
	fmt.Println("Policy:", a.manifest.Policy)
	fmt.Println("Python:", pythonExe)
	fmt.Println("FFmpeg:", ffmpegExe)
	fmt.Println("ffprobe:", ffprobeExe)
	fmt.Println("mpg123:", mpg123Exe)
	fmt.Println("Cache:", a.cacheRoot)
	fmt.Println("State:", a.stateRoot)
	if free > 0 {
		fmt.Printf("Espacio libre: %d MB\n", free/(1024*1024))
	}
	fmt.Println("Integridad uv archive SHA-256:", strings.ToLower(a.manifest.UV.SHA256))
	fmt.Println("Integridad FFmpeg archive SHA-256:", strings.ToLower(a.manifest.FFmpeg.SHA256))
	fmt.Println("Integridad mpg123 archive SHA-256:", strings.ToLower(a.manifest.MPG123.SHA256))
	fmt.Println("Integridad mpg123 binary SHA-256:", strings.ToLower(a.manifest.MPG123.BinarySHA256))
}

func findPythonVersion(root, expected string) string {
	var candidates []string
	_ = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err == nil && info != nil && !info.IsDir() && strings.EqualFold(info.Name(), "python.exe") {
			candidates = append(candidates, path)
		}
		return nil
	})
	sort.Strings(candidates)
	for _, p := range candidates {
		out, err := exec.Command(p, "--version").CombinedOutput()
		if err == nil && strings.Contains(string(out), "Python "+expected) {
			return p
		}
	}
	return ""
}

func versionMatches(exe string, args []string, version string) bool {
	if !fileExists(exe) {
		return false
	}
	out, err := exec.Command(exe, args...).CombinedOutput()
	return err == nil && strings.Contains(string(out), version)
}

func fileExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

func findFile(root, name string) (string, error) {
	var found string
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return nil
		}
		if info != nil && !info.IsDir() && strings.EqualFold(info.Name(), name) {
			found = path
			return io.EOF
		}
		return nil
	})
	if errors.Is(err, io.EOF) && found != "" {
		return found, nil
	}
	if found != "" {
		return found, nil
	}
	return "", fmt.Errorf("%s no encontrado tras extraer %s", name, root)
}

func unzipSafe(src, dst string) error {
	r, err := zip.OpenReader(src)
	if err != nil {
		return err
	}
	defer r.Close()
	cleanDst, _ := filepath.Abs(dst)
	for _, f := range r.File {
		p := filepath.Join(dst, f.Name)
		abs, _ := filepath.Abs(p)
		if !strings.HasPrefix(strings.ToLower(abs), strings.ToLower(cleanDst+string(os.PathSeparator))) && !strings.EqualFold(abs, cleanDst) {
			return fmt.Errorf("entrada ZIP insegura: %s", f.Name)
		}
		if f.FileInfo().IsDir() {
			if err := os.MkdirAll(p, 0o755); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			return err
		}
		in, err := f.Open()
		if err != nil {
			return err
		}
		out, err := os.Create(p)
		if err != nil {
			in.Close()
			return err
		}
		_, cpErr := io.Copy(out, in)
		in.Close()
		out.Close()
		if cpErr != nil {
			return cpErr
		}
	}
	return nil
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	_, err = io.Copy(out, in)
	if err == nil {
		err = out.Sync()
	}
	closeErr := out.Close()
	if err != nil {
		return err
	}
	return closeErr
}

func recoverDirectorySwap(target string) error {
	stage := target + ".new"
	backup := target + ".old"
	_ = os.RemoveAll(stage)
	_ = os.RemoveAll(stage + ".unpack")
	if _, err := os.Stat(target); os.IsNotExist(err) {
		if _, oldErr := os.Stat(backup); oldErr == nil {
			if err := os.Rename(backup, target); err != nil {
				return fmt.Errorf("no se pudo recuperar %s tras interrupcion: %w", target, err)
			}
		}
	}
	if _, err := os.Stat(target); err == nil {
		_ = os.RemoveAll(backup)
	}
	return nil
}

func activateDirectory(stage, target string) error {
	if _, err := os.Stat(stage); err != nil {
		return fmt.Errorf("staging inexistente %s: %w", stage, err)
	}
	backup := target + ".old"
	_ = os.RemoveAll(backup)
	hadTarget := false
	if _, err := os.Stat(target); err == nil {
		hadTarget = true
		if err := os.Rename(target, backup); err != nil {
			return fmt.Errorf("no se pudo apartar runtime anterior %s: %w", target, err)
		}
	}
	if err := os.Rename(stage, target); err != nil {
		if hadTarget {
			_ = os.Rename(backup, target)
		}
		return fmt.Errorf("no se pudo activar runtime nuevo %s: %w", target, err)
	}
	_ = os.RemoveAll(backup)
	return nil
}

func writeFileAtomic(dst string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return err
	}
	tmp := dst + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	if err := replaceFileAtomic(tmp, dst); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}

func replaceFileAtomic(src, dst string) error {
	srcPtr, err := syscall.UTF16PtrFromString(src)
	if err != nil {
		return err
	}
	dstPtr, err := syscall.UTF16PtrFromString(dst)
	if err != nil {
		return err
	}
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	moveFileEx := kernel32.NewProc("MoveFileExW")
	r, _, callErr := moveFileEx.Call(
		uintptr(unsafe.Pointer(srcPtr)),
		uintptr(unsafe.Pointer(dstPtr)),
		uintptr(moveFileReplaceExisting|moveFileWriteThrough),
	)
	if r == 0 {
		return fmt.Errorf("MoveFileExW fallo: %v", callErr)
	}
	return nil
}

func sha256File(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func validatePinnedDownloadURL(label, raw string) error {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.Host == "" {
		return fmt.Errorf("INTEGRITY_CONFIG: %s debe usar una URL HTTPS valida", label)
	}
	if strings.Contains(strings.ToLower(raw), "latest") {
		return fmt.Errorf("INTEGRITY_CONFIG: %s no puede usar alias latest; debe fijar una release versionada", label)
	}
	return nil
}

func validSHA256(s string) bool {
	if len(s) != 64 {
		return false
	}
	_, err := hex.DecodeString(s)
	return err == nil
}

func setEnv(env []string, key, value string) []string {
	prefix := strings.ToUpper(key) + "="
	out := make([]string, 0, len(env)+1)
	for _, e := range env {
		if !strings.HasPrefix(strings.ToUpper(e), prefix) {
			out = append(out, e)
		}
	}
	return append(out, key+"="+value)
}

func sha256Hex(b []byte) string {
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}

func (a *app) step(format string, args ...interface{}) {
	msg := fmt.Sprintf(format, args...)
	fmt.Println("[LossyDoctor]", msg)
	a.log("[STEP] %s", msg)
}

func (a *app) fail(stage string, err error) {
	msg := fmt.Sprintf("BOOTSTRAP_FATAL [%s]: %v", stage, err)
	fmt.Fprintln(os.Stderr, msg)
	a.log(msg)
}

func (a *app) log(format string, args ...interface{}) {
	_ = os.MkdirAll(filepath.Dir(a.logPath), 0o755)
	f, err := os.OpenFile(a.logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	w := bufio.NewWriter(f)
	defer w.Flush()
	all := append([]interface{}{time.Now().Format(time.RFC3339)}, args...)
	fmt.Fprintf(w, "%s "+format+"\r\n", all...)
}

func acquireBootstrapMutex(root string) (uintptr, error) {
	hash := sha256.Sum256([]byte(strings.ToLower(filepath.Clean(root))))
	name := "Local\\LossyDoctorBootstrap_" + hex.EncodeToString(hash[:8])
	namePtr, err := syscall.UTF16PtrFromString(name)
	if err != nil {
		return 0, err
	}
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	createMutex := kernel32.NewProc("CreateMutexW")
	r, _, callErr := createMutex.Call(0, 0, uintptr(unsafe.Pointer(namePtr)))
	if r == 0 {
		return 0, fmt.Errorf("BOOTSTRAP_LOCK: no se pudo crear mutex: %v", callErr)
	}
	if errno, ok := callErr.(syscall.Errno); ok && errno == errorAlreadyExists {
		_ = syscall.CloseHandle(syscall.Handle(r))
		return 0, errors.New("BOOTSTRAP_LOCKED: otra preparacion de LossyDoctor ya esta ejecutandose sobre esta carpeta")
	}
	return r, nil
}

func freeSpaceBytes(path string) (uint64, error) {
	ptr, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return 0, err
	}
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	proc := kernel32.NewProc("GetDiskFreeSpaceExW")
	var available uint64
	var total uint64
	var totalFree uint64
	r, _, callErr := proc.Call(
		uintptr(unsafe.Pointer(ptr)),
		uintptr(unsafe.Pointer(&available)),
		uintptr(unsafe.Pointer(&total)),
		uintptr(unsafe.Pointer(&totalFree)),
	)
	if r == 0 {
		return 0, fmt.Errorf("GetDiskFreeSpaceExW fallo: %v", callErr)
	}
	return available, nil
}

// RtlGetVersion is used instead of GetVersion so Windows compatibility shims do not lie about the OS version.
type rtlOSVersionInfo struct {
	Size, Major, Minor, Build, PlatformID uint32
	CSDVersion                            [128]uint16
}

func windowsVersion() (uint32, uint32, uint32, error) {
	dll := syscall.NewLazyDLL("ntdll.dll")
	proc := dll.NewProc("RtlGetVersion")
	var v rtlOSVersionInfo
	v.Size = uint32(unsafe.Sizeof(v))
	r, _, e := proc.Call(uintptr(unsafe.Pointer(&v)))
	if r != 0 {
		return 0, 0, 0, fmt.Errorf("RtlGetVersion status %d (%v)", r, e)
	}
	return v.Major, v.Minor, v.Build, nil
}
